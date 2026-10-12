from WindowsAPI import WindowsAPI
from minesweeper_helper import COVERED, DEFAULT_THRESHOLD, DESCRIPTIONS, FLAG, LABELS, SYMBOLS, UNREADABLE, Region, TemplateBank, analyse_frame, profile_path, split_tiles
import numpy as np
import json
import math
import os
import sys
from concurrent.futures import ThreadPoolExecutor


def run_gui() -> int:
    if sys.platform != "win32":
        print("The desktop overlay needs Windows. The solver tests are cross-platform.")
        return 1
    try:
        # QApplication establishes Qt's per-monitor DPI awareness BEFORE MSS
        # is instantiated. Do not import pyautogui / mouseinfo into this program.
        from PySide6.QtCore import Qt, QTimer, QRectF, QPointF, Signal
        from PySide6.QtGui import (QPainter, QPen, QColor, QImage, QFont, QKeySequence)
        from PySide6.QtWidgets import (
            QApplication, QWidget, QDialog, QLabel, QPushButton, QVBoxLayout,
            QHBoxLayout, QGridLayout, QComboBox, QSpinBox, QDoubleSpinBox,
            QCheckBox, QMessageBox, QDialogButtonBox, QButtonGroup, QGroupBox, QScrollArea,
        )
        from mss import mss
    except ImportError as exc:
        print("A GUI dependency is missing:", exc)
        print(r"Run: .\.venv\Scripts\python.exe -m pip install -r requirements.txt")
        return 1

    def qimage(rgb: np.ndarray) -> QImage:
        contiguous = np.ascontiguousarray(rgb)
        h, w = contiguous.shape[:2]
        # copy() detaches the image from temporary numpy storage.
        return QImage(contiguous.data, w, h, contiguous.strides[0],
                      QImage.Format.Format_RGB888).copy()

    def monitor_for(screen, sct) -> dict[str, int]:
        # On Windows Qt scales screen SIZE, not its virtual-desktop ORIGIN.
        # Match the origin and use actual MSS dimensions, avoiding DPI rounding.
        geometry = screen.geometry()
        matches = [dict(m) for m in sct.monitors[1:]
                   if m["left"] == geometry.x() and m["top"] == geometry.y()]
        if len(matches) != 1:
            raise ValueError("Cannot map this monitor. Restart after changing display settings.")
        monitor = matches[0]
        ratio = screen.devicePixelRatio()
        if abs(monitor["width"] - geometry.width() * ratio) > 4 or \
           abs(monitor["height"] - geometry.height() * ratio) > 4:
            raise ValueError("Display scaling changed. Restart the helper and reselect the board.")
        return monitor

    def grab_rgb(sct, box: dict[str, int]) -> np.ndarray:
        return np.ascontiguousarray(np.asarray(sct.grab(box))[:, :, :3][:, :, ::-1])

    class BoardView(QWidget):
        clicked = Signal(int, int)

        def __init__(self, parent=None):
            super().__init__(parent)
            self.frame = None
            self.image = None
            self.board = None
            self.rows = self.cols = 9
            self.show_labels = True
            self.selected = None
            self.setMinimumSize(420, 230)

        def set_board(self, frame, board, rows, cols):
            self.frame = frame
            self.image = qimage(frame)
            self.board = board
            self.rows, self.cols = rows, cols
            self.update()

        def image_rect(self) -> QRectF:
            if self.image is None:
                return QRectF()
            scale = min((self.width() - 12) / self.image.width(),
                        (self.height() - 12) / self.image.height())
            w, h = self.image.width() * scale, self.image.height() * scale
            return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

        def paintEvent(self, event):
            p = QPainter(self)
            p.fillRect(self.rect(), QColor("#18202b"))
            if self.image is None:
                p.setPen(QColor("#d8dee9"))
                p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                           "Select the board to see a recognition preview.")
                return
            box = self.image_rect()
            p.drawImage(box, self.image)
            cw, ch = box.width() / self.cols, box.height() / self.rows
            p.setPen(QPen(QColor(80, 170, 240, 120), 0.6))
            for c in range(self.cols + 1):
                x = box.x() + c * cw
                p.drawLine(QPointF(x, box.top()), QPointF(x, box.bottom()))
            for r in range(self.rows + 1):
                y = box.y() + r * ch
                p.drawLine(QPointF(box.left(), y), QPointF(box.right(), y))
            font = QFont("Consolas")
            font.setPixelSize(max(8, min(15, int(min(cw, ch) * 0.45))))
            p.setFont(font)
            if self.board is not None and self.show_labels:
                for r in range(self.rows):
                    for c in range(self.cols):
                        value = int(self.board[r, c])
                        cell = QRectF(box.x() + c * cw, box.y() + r * ch, cw, ch)
                        if value == UNREADABLE:
                            p.fillRect(cell, QColor(255, 165, 0, 75))
                        size = max(10, min(cw, ch) * 0.52)
                        tag = QRectF(cell.x(), cell.y(), size, size)
                        p.fillRect(tag, QColor(12, 17, 25, 215))
                        p.setPen(QColor("#ffcf66" if value == UNREADABLE else "#ffffff"))
                        p.drawText(tag, Qt.AlignmentFlag.AlignCenter, SYMBOLS[value])
            if self.selected is not None:
                r, c = self.selected
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(QColor("#ff4773"), 3))
                p.drawRect(QRectF(box.x() + c * cw + 1, box.y() + r * ch + 1,
                                 cw - 2, ch - 2))

        def mousePressEvent(self, event):
            box = self.image_rect()
            point = event.position()
            if self.image is not None and box.contains(point):
                c = min(self.cols - 1, int((point.x() - box.x()) / box.width() * self.cols))
                r = min(self.rows - 1, int((point.y() - box.y()) / box.height() * self.rows))
                self.selected = (r, c)
                self.clicked.emit(r, c)
                self.update()

    class RegionPicker(QDialog):
        def __init__(self, screen, frame, rows, cols):
            super().__init__(None, Qt.WindowType.FramelessWindowHint |
                             Qt.WindowType.WindowStaysOnTopHint)
            self.screen_ref = screen
            self.frame = frame
            self.image = qimage(frame)
            self.rows, self.cols = rows, cols
            self.selection = QRectF()
            self.anchor = None
            self.setCursor(Qt.CursorShape.CrossCursor)
            self.winId()
            self.windowHandle().setScreen(screen)
            self.setGeometry(screen.geometry())

        def paintEvent(self, event):
            p = QPainter(self)
            p.drawImage(QRectF(self.rect()), self.image)
            p.fillRect(self.rect(), QColor(0, 0, 0, 95))
            box = self.selection
            if box.width() > 0 and box.height() > 0:
                p.save()
                p.setClipRect(box)
                p.drawImage(QRectF(self.rect()), self.image)
                p.restore()
                p.setPen(QPen(QColor("#38df99"), 1))
                for c in range(self.cols + 1):
                    x = box.x() + box.width() * c / self.cols
                    p.drawLine(QPointF(x, box.top()), QPointF(x, box.bottom()))
                for r in range(self.rows + 1):
                    y = box.y() + box.height() * r / self.rows
                    p.drawLine(QPointF(box.left(), y), QPointF(box.right(), y))
            note = (f"Drag around ONLY the complete {self.rows}-row x {self.cols}-column grid.\n"
                    "Leave out the timer, mine counter and outer decorative frame.\n"
                    "Enter = accept   Esc = cancel   Arrows = move 1 pixel\n"
                    "Shift + arrows = adjust width/height by 1 pixel; drag again to redraw.")
            p.fillRect(QRectF(12, 12, min(740, self.width() - 24), 112), QColor(10, 17, 26, 230))
            p.setPen(QColor("white"))
            p.setFont(QFont("Segoe UI", 11))
            p.drawText(QRectF(24, 20, min(710, self.width() - 48), 95),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, note)

        def mousePressEvent(self, event):
            if event.button() == Qt.MouseButton.LeftButton:
                self.anchor = event.position()
                self.selection = QRectF(self.anchor, self.anchor)
                self.update()

        def mouseMoveEvent(self, event):
            if self.anchor is not None:
                self.selection = QRectF(self.anchor, event.position()).normalized().intersected(
                    QRectF(self.rect()))
                self.update()

        def mouseReleaseEvent(self, event):
            if event.button() == Qt.MouseButton.LeftButton:
                self.mouseMoveEvent(event)
                self.anchor = None

        def keyPressEvent(self, event):
            key = event.key()
            if key == Qt.Key.Key_Escape:
                self.reject()
                return
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                crop = self.pixel_crop()
                if crop[2] / self.cols < 10 or crop[3] / self.rows < 10:
                    QMessageBox.warning(self, "Selection too small",
                                        "Select the entire board. Each cell needs at least 10 screen pixels.")
                else:
                    self.accept()
                return
            dx = self.width() / self.frame.shape[1]
            dy = self.height() / self.frame.shape[0]
            sx, sy = {Qt.Key.Key_Left: (-dx, 0), Qt.Key.Key_Right: (dx, 0),
                      Qt.Key.Key_Up: (0, -dy), Qt.Key.Key_Down: (0, dy)}.get(key, (0, 0))
            box = QRectF(self.selection)
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                box.setWidth(max(dx, box.width() + sx))
                box.setHeight(max(dy, box.height() + sy))
            else:
                box.translate(sx, sy)
            self.selection = box.intersected(QRectF(self.rect()))
            self.update()

        def pixel_crop(self) -> tuple[int, int, int, int]:
            box = self.selection
            sx, sy = self.frame.shape[1] / self.width(), self.frame.shape[0] / self.height()
            left, top = round(box.left() * sx), round(box.top() * sy)
            right, bottom = round(box.right() * sx), round(box.bottom() * sy)
            return left, top, max(0, right - left), max(0, bottom - top)

    class TeachDialog(QDialog):
        def __init__(self, parent, frame, rows, cols, bank, threshold):
            super().__init__(parent)
            self.setWindowTitle("Teach tile appearances — frozen screenshot, not the live game")
            self.resize(940, 720)
            self.bank = bank.clone()
            self.undo_stack = []
            self.frame, self.rows, self.cols = frame, rows, cols
            self.tiles = split_tiles(frame, rows, cols)
            self.threshold = threshold
            self.label = COVERED
            layout = QVBoxLayout(self)
            note = QLabel(
                "Choose a label below (or press its key), then click one matching tile in this screenshot.\n"
                "C = still covered; 0 = already revealed and empty. These are NOT the same.\n"
                "Classic mode already knows the standard graphics. Teaching adds fallback examples for other designs.\n"
                "A ? means unrecognised, not a mine. Check every number in the preview before using hints."
            )
            note.setWordWrap(True)
            layout.addWidget(note)
            buttons = QHBoxLayout()
            self.group = QButtonGroup(self)
            for button_id, label in enumerate(LABELS):
                button = QPushButton(SYMBOLS[label])
                button.setCheckable(True)
                button.setToolTip(DESCRIPTIONS[label])
                button.setShortcut(QKeySequence(SYMBOLS[label]))
                self.group.addButton(button, button_id)
                buttons.addWidget(button)
                if label == COVERED:
                    button.setChecked(True)
            self.group.idClicked.connect(lambda button_id: self.set_label(LABELS[button_id]))
            layout.addLayout(buttons)
            self.chosen = QLabel()
            layout.addWidget(self.chosen)
            self.view = BoardView()
            self.view.clicked.connect(self.learn)
            layout.addWidget(self.view, 1)
            options = QHBoxLayout()
            show = QCheckBox("Show recognition labels / ? tiles")
            show.setChecked(True)
            show.toggled.connect(self.toggle_labels)
            options.addWidget(show)
            undo = QPushButton("Undo")
            undo.clicked.connect(self.undo)
            options.addWidget(undo)
            clear = QPushButton("Clear chosen label")
            clear.clicked.connect(self.clear_label)
            options.addWidget(clear)
            clear_all = QPushButton("Clear all examples")
            clear_all.clicked.connect(self.clear_all)
            options.addWidget(clear_all)
            layout.addLayout(options)
            self.summary = QLabel()
            self.summary.setWordWrap(True)
            layout.addWidget(self.summary)
            action = QDialogButtonBox(QDialogButtonBox.StandardButton.Save |
                                      QDialogButtonBox.StandardButton.Cancel)
            action.accepted.connect(self.accept)
            action.rejected.connect(self.reject)
            layout.addWidget(action)
            self.set_label(COVERED)
            self.refresh()

        def set_label(self, label):
            self.label = label
            self.chosen.setText(f"Current label: {SYMBOLS[label]} — {DESCRIPTIONS[label]}")

        def toggle_labels(self, value):
            self.view.show_labels = value
            self.view.update()

        def learn(self, row, col):
            old = self.bank.clone()
            try:
                self.bank.add(self.label, self.tiles[row * self.cols + col])
            except ValueError as exc:
                QMessageBox.warning(self, "Example not added", str(exc))
                return
            self.undo_stack.append(old)
            self.refresh()

        def undo(self):
            if self.undo_stack:
                self.bank = self.undo_stack.pop()
                self.refresh()

        def clear_label(self):
            self.undo_stack.append(self.bank.clone())
            self.bank.samples = [(label, image) for label, image in self.bank.samples
                                 if label != self.label]
            self.refresh()

        def clear_all(self):
            if QMessageBox.question(self, "Clear examples?", "Remove all learned tile appearances?") \
                    == QMessageBox.StandardButton.Yes:
                self.undo_stack.append(self.bank.clone())
                self.bank = TemplateBank(classic_auto=self.bank.classic_auto)
                self.refresh()

        def refresh(self):
            values, _ = self.bank.classify(self.tiles, self.threshold)
            self.view.set_board(self.frame, values.reshape(self.rows, self.cols), self.rows, self.cols)
            counts = "   ".join(f"{SYMBOLS[label]}:{sum(x == label for x, _ in self.bank.samples)}"
                                for label in LABELS)
            self.summary.setText(
                f"{int(np.sum(values == UNREADABLE))} unrecognised tiles.  Examples: {counts}\n"
                "Save keeps these fallback examples for future games. Small shifts and rendering differences are tolerated."
            )

    class Overlay(QWidget):
        def __init__(self, native):
            flags = (Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint |
                     Qt.WindowType.Tool | Qt.WindowType.WindowTransparentForInput |
                     Qt.WindowType.WindowDoesNotAcceptFocus)
            super().__init__(None, flags)
            self.native = native
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
            self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.region = None
            self.box = QRectF()
            self.mines = set()
            self.safe = set()
            self.excluded = False

        def place(self, region, screen):
            self.hide()
            self.region = region
            self.winId()
            self.windowHandle().setScreen(screen)
            g = screen.geometry()
            sx = g.width() / region.monitor["width"]
            sy = g.height() / region.monitor["height"]
            x = g.x() + (region.left - region.monitor["left"]) * sx
            y = g.y() + (region.top - region.monitor["top"]) * sy
            w, h = region.width * sx, region.height * sy
            left, top = math.floor(x), math.floor(y)
            self.setGeometry(left, top, math.ceil(x + w) - left, math.ceil(y + h) - top)
            self.box = QRectF(x - left, y - top, w, h)
            self.excluded = self.native.configure_overlay(int(self.winId()))

        def clear(self):
            self.mines.clear()
            self.safe.clear()
            self.hide()

        def display(self, mines, safe):
            self.mines, self.safe = set(mines), set(safe)
            if not self.mines and not self.safe:
                self.hide()
            else:
                self.show()
                self.update()

        def paintEvent(self, event):
            if self.region is None:
                return
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            cw, ch = self.box.width() / self.region.cols, self.box.height() / self.region.rows
            for index in self.safe:
                r, c = divmod(index, self.region.cols)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(QColor("#00e68a"), max(2.0, min(cw, ch) * 0.07)))
                p.drawRect(QRectF(self.box.x() + (c + 0.2) * cw,
                                 self.box.y() + (r + 0.2) * ch, cw * 0.6, ch * 0.6))
            for index in self.mines:
                r, c = divmod(index, self.region.cols)
                centre = QPointF(self.box.x() + (c + 0.5) * cw, self.box.y() + (r + 0.5) * ch)
                radius = min(cw, ch) * 0.19
                for colour, width in (("#ffffff", 4.5), ("#ed2546", 2.5)):
                    p.setPen(QPen(QColor(colour), min(width, max(1.5, radius * 0.65))))
                    for k in range(8):
                        angle = k * math.pi / 4
                        end = QPointF(centre.x() + math.cos(angle) * radius * 1.65,
                                      centre.y() + math.sin(angle) * radius * 1.65)
                        p.drawLine(centre, end)
                p.setPen(QPen(QColor("#ffffff"), 1.5))
                p.setBrush(QColor("#ed2546"))
                p.drawEllipse(centre, radius, radius)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor("#ffffff"))
                p.drawEllipse(QPointF(centre.x() - radius * .28, centre.y() - radius * .28),
                              radius * .2, radius * .2)

    class MainWindow(QWidget):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("Minesweeper Helper v2 — automatic classic recognition")
            available = QApplication.primaryScreen().availableGeometry()
            self.resize(min(780, available.width() - 40), max(450, min(840, available.height() - 60)))
            self.native = WindowsAPI()
            self.sct = mss()
            self.bank = TemplateBank(classic_auto=True)
            self.region = None
            self.target_hwnd = 0
            self.screen_signature = None
            self.overlay = Overlay(self.native)
            self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="solver")
            self.future = None
            self.future_context = None
            self.running = False
            self.busy = False
            self.capture_pending = False
            self.epoch = 0
            self.activity = 0
            self.last_input_down = False
            self.last_allowed = None
            self.last_f8 = self.last_f9 = False
            self.last_board = None
            self.last_frame = None
            self.last_result = None
            self.signature = None
            self.stable = 0
            self._closing = False
            self.path = profile_path()
            self._build_ui()
            self._load_profile()
            self.poll = QTimer(self)
            self.poll.timeout.connect(self._poll)
            self.poll.start(40)
            self.scan = QTimer(self)
            self.scan.timeout.connect(self._schedule_capture)
            self.scan.start(self.interval.value())

        def _build_ui(self):
            outer = QVBoxLayout(self)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            body = QWidget()
            layout = QVBoxLayout(body)
            scroll.setWidget(body)
            outer.addWidget(scroll)
            note = QLabel(
                "1. Set rows / columns and select the complete grid. Classic tiles are recognised automatically.\n"
                "3. Start; check the preview.  4. Enable the mine overlay.  F8 = pause/resume; F9 = quit."
            )
            note.setWordWrap(True)
            layout.addWidget(note)
            setup = QGroupBox("Board setup")
            grid = QGridLayout(setup)
            self.screens = QComboBox()
            for screen in QApplication.screens():
                self.screens.addItem(f"{screen.name()} — {screen.geometry().width()} x "
                                     f"{screen.geometry().height()} logical px", screen.name())
            grid.addWidget(QLabel("Monitor"), 0, 0)
            grid.addWidget(self.screens, 0, 1, 1, 3)
            self.rows = QSpinBox()
            self.rows.setRange(2, 80)
            self.rows.setValue(9)
            self.cols = QSpinBox()
            self.cols.setRange(2, 80)
            self.cols.setValue(9)
            grid.addWidget(QLabel("Rows (top to bottom)"), 1, 0)
            grid.addWidget(self.rows, 1, 1)
            grid.addWidget(QLabel("Columns (left to right)"), 1, 2)
            grid.addWidget(self.cols, 1, 3)
            self.select_button = QPushButton("1. Select board area")
            self.select_button.clicked.connect(self.select_board)
            self.teach_button = QPushButton("Optional: teach / refresh snapshot")
            self.teach_button.clicked.connect(self.teach_tiles)
            self.teach_button.setEnabled(False)
            grid.addWidget(self.select_button, 2, 0, 1, 2)
            grid.addWidget(self.teach_button, 2, 2, 1, 2)
            self.region_label = QLabel("No area selected. Keep the whole board on one monitor.")
            self.region_label.setWordWrap(True)
            grid.addWidget(self.region_label, 3, 0, 1, 4)
            layout.addWidget(setup)
            self.auto_classic = QCheckBox("Classic grey tiles: recognise automatically (no teaching needed)")
            self.auto_classic.setChecked(True)
            self.auto_classic.setToolTip(
                "Built-in colour, border and symbol recognition for the classic grey design. "
                "Existing examples remain available as fallback for unreadable tiles. "
                "Turn off for other game themes and use the optional teaching dialog."
            )
            self.auto_classic.toggled.connect(self.recognition_mode_changed)
            layout.addWidget(self.auto_classic)
            self.rows.valueChanged.connect(self.invalidate_region)
            self.cols.valueChanged.connect(self.invalidate_region)
            self.screens.currentIndexChanged.connect(self.invalidate_region)
            row = QHBoxLayout()
            self.start_button = QPushButton("3. Start monitoring (F8)")
            self.start_button.clicked.connect(self.toggle_running)
            self.start_button.setEnabled(False)
            row.addWidget(self.start_button)
            self.mine_overlay = QCheckBox("4. Enable mine overlay")
            self.mine_overlay.toggled.connect(self.refresh_overlay)
            row.addWidget(self.mine_overlay)
            self.safe_overlay = QCheckBox("Also show safe squares")
            self.safe_overlay.toggled.connect(self.refresh_overlay)
            row.addWidget(self.safe_overlay)
            layout.addLayout(row)
            options = QHBoxLayout()
            self.compatibility = QCheckBox("Compatibility capture (briefly hides markers)")
            self.compatibility.setChecked(True)
            self.compatibility.setToolTip(
                "On = hide overlay before every screenshot to prevent reading our own markers. "
                "Off = use Windows capture exclusion when available. Turn on if recognition fails."
            )
            self.compatibility.toggled.connect(self.capture_mode_changed)
            options.addWidget(self.compatibility)
            options.addWidget(QLabel("Scan interval (ms)"))
            self.interval = QSpinBox()
            self.interval.setRange(150, 10000)
            self.interval.setValue(250)
            self.interval.valueChanged.connect(lambda ms: self.scan.setInterval(ms)
                                               if hasattr(self, "scan") else None)
            options.addWidget(self.interval)
            layout.addLayout(options)
            controls = QHBoxLayout()
            self.labels_checkbox = QCheckBox("Show recognition labels")
            self.labels_checkbox.setChecked(True)
            self.labels_checkbox.toggled.connect(self.preview_labels_changed)
            controls.addWidget(self.labels_checkbox)
            controls.addStretch()
            controls.addWidget(QLabel("Template tolerance (higher = more forgiving)"))
            self.threshold = QDoubleSpinBox()
            self.threshold.setDecimals(3)
            self.threshold.setRange(0.005, 0.150)
            self.threshold.setSingleStep(0.005)
            self.threshold.setValue(DEFAULT_THRESHOLD)
            self.threshold.setToolTip(
                "Applies only to learned examples, including fallback matches. "
                "Classic recognition uses colour/geometry rules instead. A distance, not a probability."
            )
            self.threshold.valueChanged.connect(self.threshold_changed)
            controls.addWidget(self.threshold)
            layout.addLayout(controls)
            self.preview = BoardView()
            layout.addWidget(self.preview, 1)
            self.status = QLabel("Ready. Select the grid, without its surrounding frame.")
            self.status.setWordWrap(True)
            self.status.setMinimumHeight(52)
            layout.addWidget(self.status)
            caution = QLabel(
                "C = covered | F = flag | 0 = revealed empty | ? = unreadable | X = revealed mine.\n"
                "Red bombs = proved mines; optional green boxes = proved safe. Flags are not trusted.\n"
                "Recognition can be wrong: inspect the preview. Keep other windows off the game board."
            )
            caution.setWordWrap(True)
            layout.addWidget(caution)
            bottom = QHBoxLayout()
            where = QPushButton("Open saved-profile folder")
            where.clicked.connect(lambda: os.startfile(str(self.path.parent)))
            bottom.addWidget(where)
            reset = QPushButton("Clear saved examples")
            reset.clicked.connect(self.reset_bank)
            bottom.addWidget(reset)
            quit_button = QPushButton("Quit (F9)")
            quit_button.clicked.connect(self.close)
            bottom.addWidget(quit_button)
            layout.addLayout(bottom)

        def current_screen(self):
            name = self.region.screen_name if self.region else self.screens.currentData()
            for screen in QApplication.screens():
                if screen.name() == name:
                    return screen
            raise ValueError("Selected monitor is no longer connected. Restart and reselect.")

        def _load_profile(self):
            if not self.path.exists():
                return
            try:
                if self.path.stat().st_size > 12_000_000:
                    raise ValueError("Profile is too large.")
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if data.get("version") != 1:
                    raise ValueError("Unsupported profile version.")
                self.bank = TemplateBank.from_json(data["samples"])
                self.auto_classic.blockSignals(True)
                self.auto_classic.setChecked(bool(data.get("classic_auto", True)))
                self.auto_classic.blockSignals(False)
                self.bank.classic_auto = self.auto_classic.isChecked()
                self.rows.setValue(int(data.get("rows", 9)))
                self.cols.setValue(int(data.get("cols", 9)))
                # Intentionally do not restore region coordinates, overlay permission,
                # or a loosened threshold across restarts.
                self.status.setText(f"Loaded {len(self.bank.samples)} fallback tile examples. "
                                    "Select the board; classic mode needs no teaching.")
            except Exception as exc:
                self.bank = TemplateBank(classic_auto=self.auto_classic.isChecked())
                self.status.setText(f"Saved examples were not loaded: {exc}. "
                                    "Classic mode can still run without examples.")

        def save_profile(self):
            try:
                data = {"version": 1, "rows": self.rows.value(), "cols": self.cols.value(),
                        "samples": self.bank.to_json(), "classic_auto": self.bank.classic_auto}
                temporary = self.path.with_suffix(".tmp")
                temporary.write_text(json.dumps(data), encoding="utf-8")
                temporary.replace(self.path)
            except Exception as exc:
                QMessageBox.warning(self, "Could not save profile",
                                    f"Examples work for this session but could not be saved:\n{exc}")

        def invalidate_region(self, *args):
            if self.region is not None:
                self.stop()
                self.region = None
                self.mine_overlay.setChecked(False)
                self.start_button.setEnabled(False)
                self.teach_button.setEnabled(False)
                self.region_label.setText("Rows, columns or monitor changed: select the area again.")

        def _reset_stability(self):
            self.signature = None
            self.stable = 0
            self.last_result = None
            self.overlay.clear()

        def stop(self):
            self.running = False
            self.epoch += 1
            self.capture_pending = False
            self._reset_stability()
            self.start_button.setText("3. Start monitoring (F8)")
            self.status.setText("Paused. Overlay removed. Press F8 or Start to resume.")

        def select_board(self):
            if self.busy:
                return
            self.stop()
            self.busy = True
            self.mine_overlay.setChecked(True)
            self.hide()
            QTimer.singleShot(180, self._finish_selection)

        def _finish_selection(self):
            try:
                screen = self.current_screen()
                monitor = monitor_for(screen, self.sct)
                self.native.flush()
                frame = grab_rgb(self.sct, monitor)
                picker = RegionPicker(screen, frame, self.rows.value(), self.cols.value())
                accepted = picker.exec() == QDialog.DialogCode.Accepted
                if accepted:
                    left, top, width, height = picker.pixel_crop()
                picker.close()
                picker.deleteLater()
                self.native.flush()
                if accepted:
                    self.region = Region(screen.name(), monitor, monitor["left"] + left,
                                         monitor["top"] + top, width, height,
                                         self.rows.value(), self.cols.value())
                    self.target_hwnd = self.native.root_at(self.region.left + width // 2,
                                                           self.region.top + height // 2)
                    self.screen_signature = (screen.geometry().getRect(), screen.devicePixelRatio())
                    self.overlay.place(self.region, screen)
                    self.compatibility.setEnabled(self.overlay.excluded)
                    self.compatibility.setChecked(True)
                    self.last_frame = frame[top:top + height, left:left + width].copy()
                    values, _ = self.bank.classify(
                        split_tiles(self.last_frame, self.region.rows, self.region.cols),
                        self.threshold.value())
                    self.last_board = values.reshape(self.region.rows, self.region.cols)
                    self.preview.set_board(self.last_frame, self.last_board,
                                           self.region.rows, self.region.cols)
                    self.region_label.setText(
                        f"{screen.name()}: ({self.region.left}, {self.region.top}), "
                        f"{width} x {height} physical pixels; "
                        f"cells {width / self.region.cols:.1f} x {height / self.region.rows:.1f} px."
                    )
                    self.start_button.setEnabled(True)
                    self.teach_button.setEnabled(True)
                    self.status.setText("Area selected. Start monitoring and verify the preview. "
                                        "Classic mode needs no teaching; use the optional dialog for other designs.")
            except Exception as exc:
                self.status.setText(f"Could not select the board: {exc}")
            finally:
                self.busy = False
                self.show()
                self.raise_()
                self.activateWindow()

        def teach_tiles(self):
            if self.region is None or self.busy:
                return
            self.stop()
            self.busy = True
            self.mine_overlay.setChecked(True)
            self.hide()
            QTimer.singleShot(180, self._finish_teaching)

        def _finish_teaching(self):
            try:
                self.native.flush()
                frame = grab_rgb(self.sct, self.region.capture_box())
                self.last_frame = frame
                self.show()
                dialog = TeachDialog(self, frame, self.region.rows, self.region.cols,
                                     self.bank, self.threshold.value())
                if dialog.exec() == QDialog.DialogCode.Accepted:
                    self.bank = dialog.bank
                    self.save_profile()
                dialog.deleteLater()
                values, _ = self.bank.classify(
                    split_tiles(frame, self.region.rows, self.region.cols), self.threshold.value())
                self.last_board = values.reshape(self.region.rows, self.region.cols)
                self.preview.set_board(frame, self.last_board, self.region.rows, self.region.cols)
                self.status.setText("Check the preview, click Start, and enable the overlay after checking the labels.")
            except Exception as exc:
                self.status.setText(f"Teaching failed: {exc}")
            finally:
                self.busy = False
                self.show()
                self.raise_()
                self.activateWindow()

        def toggle_running(self):
            if self.busy:
                return
            if self.running:
                self.stop()
                return
            if self.region is None:
                self.status.setText("Select the board first.")
                return
            if not self.bank.samples and not self.bank.classic_auto:
                self.status.setText("Enable classic recognition or teach some tile appearances first.")
                return
            self.epoch += 1
            self._reset_stability()
            self.running = True
            self.start_button.setText("Pause monitoring (F8)")
            self.status.setText("Reading board. Wait for two matching reads; check the preview labels.")
            self._schedule_capture()

        def allowed_to_watch(self) -> bool:
            if self.region is None:
                return False
            # Do not draw over a different application after Alt+Tab. The helper's
            # own control window is allowed, provided it is not covering the grid.
            own_hwnd = int(self.winId())
            fg = self.native.foreground()
            if fg not in (self.target_hwnd, own_hwnd):
                return False
            if self.isVisible() and not self.isMinimized() and \
                    self.frameGeometry().intersects(self.overlay.geometry()):
                return False
            return True

        def _schedule_capture(self):
            if not self.running or self.busy or self.capture_pending or self.future is not None:
                return
            if not self.allowed_to_watch():
                self._reset_stability()
                self.status.setText("Bring the game to the foreground and move this control window off the board.")
                return
            if self.native.key_down(1) or self.native.key_down(2):
                return
            self.capture_pending = True
            epoch = self.epoch
            if self.compatibility.isChecked() or not self.overlay.excluded:
                self.overlay.hide()
                # Defer to the event loop, then flush DWM, so our marks really are
                # removed before capture. Never screenshot a just-hidden buffer.
                QTimer.singleShot(40, lambda: self._capture(epoch))
            else:
                self._capture(epoch)

        def _capture(self, epoch):
            if epoch != self.epoch:
                return
            self.capture_pending = False
            if not self.running or self.busy or not self.allowed_to_watch():
                self.overlay.clear()
                return
            try:
                screen = self.current_screen()
                signature = (screen.geometry().getRect(), screen.devicePixelRatio())
                if monitor_for(screen, self.sct) != self.region.monitor or signature != self.screen_signature:
                    raise ValueError("Monitor layout / scaling changed. Restart and reselect the board.")
                self.native.flush()
                frame = grab_rgb(self.sct, self.region.capture_box())
                self.future_context = (epoch, self.activity, frame)
                self.future = self.executor.submit(analyse_frame, frame, self.region.rows,
                                                   self.region.cols, self.bank.clone(),
                                                   self.threshold.value())
            except Exception as exc:
                self.stop()
                self.status.setText(f"Screen capture failed: {exc}")

        def _poll(self):
            if self._closing:
                return
            f8, f9 = self.native.key_down(0x77), self.native.key_down(0x78)
            if f9 and not self.last_f9:
                self.close()
                return
            if f8 and not self.last_f8:
                self.toggle_running()
            self.last_f8, self.last_f9 = f8, f9
            down = self.native.key_down(1) or self.native.key_down(2)
            allowed = self.allowed_to_watch() if self.region is not None else False
            if (down and not self.last_input_down) or allowed != self.last_allowed:
                self.activity += 1
                if self.running:
                    self._reset_stability()
            self.last_input_down, self.last_allowed = down, allowed
            if self.running and (down or not allowed):
                self.overlay.hide()
            if self.future is not None and self.future.done():
                future, context = self.future, self.future_context
                self.future = self.future_context = None
                epoch, activity, frame = context
                try:
                    board, result = future.result()
                except Exception as exc:
                    if epoch == self.epoch:
                        self.stop()
                        self.status.setText(f"Analysis failed: {exc}")
                    return
                if epoch != self.epoch or activity != self.activity or not self.running or down or not allowed:
                    return
                self.last_frame, self.last_board = frame, board
                self.preview.set_board(frame, board, self.region.rows, self.region.cols)
                unreadable = int(np.sum(board == UNREADABLE))
                if result.error:
                    self._reset_stability()
                    if unreadable:
                        self.status.setText(
                            f"{unreadable} unrecognised tiles (?). Overlay withheld. "
                            "Check grid alignment and the selected theme. Optional teaching can add missing designs. "
                            "Never label an unknown number as C or 0."
                        )
                    else:
                        self.status.setText(f"No hints: {result.error} Check recognition / game state.")
                    return
                signature = board.tobytes()
                self.stable = self.stable + 1 if signature == self.signature else 1
                self.signature = signature
                self.last_result = result
                if self.stable < 2:
                    self.overlay.clear()
                    self.status.setText("Board changed. Waiting for a second matching read.")
                    return
                flat = board.ravel()
                mines = sum(flat[i] == COVERED for i in result.mines)
                safe = sum(flat[i] == COVERED for i in result.safe)
                bad_flags = sum(flat[i] == FLAG for i in result.safe)
                text = f"Stable board: {mines} unflagged proved mines; {safe} proved safe squares."
                if bad_flags:
                    text += f" WARNING: {bad_flags} flag(s) are on proved-safe cells."
                if not mines and not safe:
                    text += " No forced move found from the visible clues; no guesses are shown."
                if result.limited:
                    text += " Search limit reached; some deductions may be missed."
                if not self.mine_overlay.isChecked():
                    text += " Overlay is OFF; verify the labels, then enable it."
                self.status.setText(text)
                self.refresh_overlay()

        def refresh_overlay(self, *args):
            if (not self.running or not self.mine_overlay.isChecked() or self.stable < 2 or
                self.last_result is None or self.last_board is None or not self.allowed_to_watch()):
                self.overlay.clear()
                return
            flat = self.last_board.ravel()
            mines = {i for i in self.last_result.mines if flat[i] == COVERED}
            safe = ({i for i in self.last_result.safe if flat[i] == COVERED}
                    if self.safe_overlay.isChecked() else set())
            self.overlay.display(mines, safe)
            # Count the next capture interval from showing the completed result,
            # not from the preceding capture. Preserve the longer-display fix.
            self.scan.start(self.interval.value())

        def preview_labels_changed(self, checked):
            self.preview.show_labels = checked
            self.preview.update()

        def recognition_mode_changed(self, checked):
            self.stop()
            self.bank.classic_auto = bool(checked)
            self.mine_overlay.setChecked(False)
            self.status.setText(
                "Classic recognition enabled. Start and verify the preview; teaching is optional."
                if checked else
                "Classic recognition disabled. Learned examples use tolerant, shift-aware matching."
            )
            # Keep the choice across restarts without changing saved tile images.
            self.save_profile()

        def threshold_changed(self, *args):
            self.stop()
            self.mine_overlay.setChecked(False)
            self.status.setText("Template tolerance changed. Recheck the preview before enabling hints. "
                                "Classic colour/geometry recognition is unchanged.")

        def capture_mode_changed(self, checked):
            self._reset_stability()
            if not checked and not self.overlay.excluded:
                self.compatibility.setChecked(True)

        def reset_bank(self):
            if QMessageBox.question(self, "Clear examples?", "Remove all learned examples for this game design?") \
                    == QMessageBox.StandardButton.Yes:
                self.stop()
                self.bank = TemplateBank(classic_auto=self.auto_classic.isChecked())
                self.mine_overlay.setChecked(False)
                self.save_profile()
                self.status.setText("Saved examples cleared. Built-in classic recognition remains available.")

        def closeEvent(self, event):
            self._closing = True
            self.stop()
            self.poll.stop()
            self.scan.stop()
            self.overlay.close()
            self.executor.shutdown(wait=False, cancel_futures=True)
            self.sct.close()
            event.accept()
            QApplication.instance().quit()

    app = QApplication(sys.argv)
    # Selection temporarily hides the main window and closes a parentless dialog.
    # Do not let that intermediate state terminate the application.
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Minesweeper Helper")
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    return app.exec()
#!/usr/bin/env python3
"""Local, read-only Minesweeper screen helper for Windows.

Run: .venv\\Scripts\\python.exe minesweeper_helper.py
See README.md for installation, calibration, limitations, and test instructions.

The solver does not inspect game memory, send clicks, trust user flags, or guess.
A guarantee from the solver is conditional on an accurately recognised board.
Qt / MSS are imported only when the GUI starts, so the core can be unit-tested
without a Windows desktop. No screenshots or templates are uploaded anywhere.
"""
from __future__ import annotations

import base64
import math
import os
from pathlib import Path
import time
import traceback
from dataclasses import dataclass, field
from typing import Iterable

try:
    import cv2
    import numpy as np
except ImportError as exc:
    raise SystemExit(
        "A dependency is missing. In the project terminal run:\n"
        r".\.venv\Scripts\python.exe -m pip install -r requirements.txt"
    ) from exc

# Board values. FLAG remains a Boolean unknown for all logical deductions.
COVERED, FLAG, UNREADABLE, GAME_OVER = -1, -2, -3, -4
LABELS = [COVERED, FLAG, 0, 1, 2, 3, 4, 5, 6, 7, 8, GAME_OVER]
SYMBOLS = {COVERED: "C", FLAG: "F", UNREADABLE: "?", GAME_OVER: "X"}
SYMBOLS.update({i: str(i) for i in range(9)})
DESCRIPTIONS = {
    COVERED: "Covered / unopened", FLAG: "Flag", 0: "Revealed empty (zero)",
    GAME_OVER: "Revealed mine / game over",
    **{i: f"Revealed {i}" for i in range(1, 9)},
}
TILE_SIZE = 32
DEFAULT_THRESHOLD = 0.065  # A distance cutoff, NOT a calibrated probability.
MIN_MARGIN = 0.010
MAX_SAMPLES_PER_LABEL = 60


@dataclass
class SolveResult:
    mines: set[int] = field(default_factory=set)
    safe: set[int] = field(default_factory=set)
    limited: bool = False
    error: str = ""


def neighbours(index: int, rows: int, cols: int) -> Iterable[int]:
    """Indices of up to eight neighbours, with no wraparound at board edges."""
    r, c = divmod(index, cols)
    for rr in range(max(0, r - 1), min(rows, r + 2)):
        for cc in range(max(0, c - 1), min(cols, c + 2)):
            if (rr, cc) != (r, c):
                yield rr * cols + cc


class InconsistentBoard(ValueError):
    pass


class SearchLimit(Exception):
    pass


def _put_constraint(
    constraints: dict[frozenset[int], int], cells: frozenset[int], count: int,
) -> bool:
    if not 0 <= count <= len(cells):
        raise InconsistentBoard("A clue requires an impossible number of mines.")
    if not cells:
        return False
    old = constraints.get(cells)
    if old is not None and old != count:
        raise InconsistentBoard("Two clues give contradictory mine counts.")
    if old is None:
        constraints[cells] = count
        return True
    return False


def _enumerate_component(
    variables: set[int], constraints: list[tuple[frozenset[int], int]],
    deadline: float, max_nodes: int = 150_000,
) -> tuple[set[int], set[int]]:
    """Complete Boolean search. A partial search NEVER yields deductions."""
    if time.monotonic() >= deadline:
        raise SearchLimit
    degree = {v: 0 for v in variables}
    for cells, _ in constraints:
        for v in cells:
            degree[v] += 1
    order = sorted(variables, key=lambda v: (-degree[v], v))
    memberships = {v: [] for v in order}
    required = []
    remaining = []
    sums = [0] * len(constraints)
    for j, (cells, count) in enumerate(constraints):
        required.append(count)
        remaining.append(len(cells))
        for v in cells:
            memberships[v].append(j)
    full_mask = (1 << len(order)) - 1
    always_mine = full_mask
    ever_mine = 0
    solutions = 0
    nodes = 0

    def visit(depth: int, mask: int) -> None:
        nonlocal nodes, solutions, always_mine, ever_mine
        nodes += 1
        if nodes > max_nodes or (nodes % 128 == 0 and time.monotonic() >= deadline):
            raise SearchLimit
        if depth == len(order):
            solutions += 1
            always_mine &= mask
            ever_mine |= mask
            return
        involved = memberships[order[depth]]
        for value in (0, 1):
            feasible = True
            for j in involved:
                remaining[j] -= 1
                sums[j] += value
                if sums[j] > required[j] or sums[j] + remaining[j] < required[j]:
                    feasible = False
            if feasible:
                visit(depth + 1, mask | (value << depth))
            for j in involved:
                remaining[j] += 1
                sums[j] -= value

    visit(0, 0)
    if not solutions:
        raise InconsistentBoard("No mine arrangement satisfies the visible clues.")
    return (
        {v for i, v in enumerate(order) if always_mine & (1 << i)},
        {v for i, v in enumerate(order) if not ever_mine & (1 << i)},
    )


def normalise_tile(tile: np.ndarray) -> np.ndarray:
    """Keep colour and bevels; shrink/enlarge a tile to a common 32x32 shape."""
    if tile.ndim != 3 or tile.shape[2] != 3 or min(tile.shape[:2]) < 4:
        raise ValueError("Tile is too small or is not an RGB image.")
    return cv2.resize(tile, (TILE_SIZE, TILE_SIZE), interpolation=cv2.INTER_AREA)


def split_tiles(frame: np.ndarray, rows: int, cols: int) -> list[np.ndarray]:
    height, width = frame.shape[:2]
    if rows < 1 or cols < 1 or height / rows < 8 or width / cols < 8:
        raise ValueError("Selection is too small for that row/column count.")
    ys = np.rint(np.linspace(0, height, rows + 1)).astype(int)
    xs = np.rint(np.linspace(0, width, cols + 1)).astype(int)
    return [frame[ys[r]:ys[r + 1], xs[c]:xs[c + 1]]
            for r in range(rows) for c in range(cols)]


# Classic mode reads colour, stroke layout and raised borders instead of
# requiring an exact pixel-for-pixel example. All images here are RGB, not BGR.
CLASSIC_PALETTE = np.array([
    (0, 0, 247), (0, 119, 0), (236, 0, 0),
    (0, 0, 128), (128, 0, 0), (0, 128, 128),
], dtype=np.float32)
TEMPLATE_SHIFT_PIXELS = 2  # Search +/- 2 pixels AFTER normalising to 32x32.
TEMPLATE_BLUR_SIGMA = 0.65


def _glyph_image(mask: np.ndarray) -> np.ndarray | None:
    """Tight, softly resampled glyph silhouette, independent of its position."""
    y, x = np.where(mask)
    if len(x) < 8:
        return None
    crop = mask[y.min():y.max() + 1, x.min():x.max() + 1].astype(np.float32)
    return cv2.resize(crop, (20, 24), interpolation=cv2.INTER_AREA)


def _seven_templates() -> list[np.ndarray]:
    # A black 7 must pass a SHAPE test. Black pixels alone could be a mine.
    bitmaps = [
        ["1111111", "0000011", "0000110", "0001100", "0011000", "0011000", "0011000"],
        ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
        ["11111", "00011", "00011", "00011", "00011", "00011", "00011"],
    ]
    templates = []
    for rows in bitmaps:
        mask = np.array([[char == "1" for char in row] for row in rows], dtype=np.uint8)
        templates.append(_glyph_image(mask))
    # Ordinary sans-serif variants supplement the pixel-style silhouettes.
    for font in (cv2.FONT_HERSHEY_SIMPLEX, cv2.FONT_HERSHEY_DUPLEX):
        for thickness in (1, 2, 3):
            mask = np.zeros((32, 32), np.uint8)
            cv2.putText(mask, "7", (5, 26), font, .85, 255, thickness, cv2.LINE_AA)
            templates.append(_glyph_image(mask > 90))
    return templates


_SEVENS = _seven_templates()


def _classic_monochrome(centre: np.ndarray, background: float) -> int:
    """Conservative handling of grey 8s, black 7s and possible mine graphics."""
    gray = np.mean(centre, axis=2)
    neutral = np.ptp(centre, axis=2) < 28
    ink = neutral & (gray < background - 38)
    y, x = np.where(ink)
    if len(x) < 25 or y.max() - y.min() + 1 < 12:
        return UNREADABLE
    darkest = float(np.percentile(gray[ink], 20))
    if darkest < background - 110:
        mask = neutral & (gray < background - 90)
        glyph = _glyph_image(mask)
        if glyph is None:
            return UNREADABLE
        # Soft Dice similarity; require a distinctive 7, not a roughly dark blob.
        score = max(float(2 * np.minimum(glyph, ref).sum() /
                          max(1e-6, glyph.sum() + ref.sum())) for ref in _SEVENS)
        if score >= .78:
            return 7
        # Unknown black icons (including most mines) stop hints, never become 0.
        return UNREADABLE
    if not 70 <= darkest + (198 - background) <= 165:
        return UNREADABLE
    # The standard grey 8 must have two enclosed spaces. A bomb with a single
    # white glint, a border fragment, and a question mark do not pass this test.
    mask = (ink.astype(np.uint8) * 255)
    contours, hierarchy = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    if hierarchy is None:
        return UNREADABLE
    holes = [cv2.contourArea(contours[i]) for i, item in enumerate(hierarchy[0])
             if item[3] >= 0 and cv2.contourArea(contours[i]) >= 2.0]
    return 8 if len(holes) == 2 else UNREADABLE


def classify_classic_tile(tile: np.ndarray) -> int:
    """Recognise a classic grey Minesweeper tile without user-labelled examples.

    Uses colour ranges (not exact RGB equality), neutral-background estimation,
    border evidence and simple symbol geometry. Unsupported/ambiguous graphics
    remain UNREADABLE. This is a visual heuristic, not a correctness guarantee.
    """
    return _classify_classic_normalised(normalise_tile(tile))


def _classify_classic_normalised(tile: np.ndarray) -> int:
    im = tile.astype(np.float32)
    gray = np.mean(im, axis=2)
    chroma = np.ptp(im, axis=2)
    neutral = chroma < 22
    centre = im[5:27, 5:27]
    middle_gray = gray[5:27, 5:27]
    bg_pixels = middle_gray[neutral[5:27, 5:27] &
                            (middle_gray > 145) & (middle_gray < 240)]
    if bg_pixels.size < 90:
        return UNREADABLE
    background = float(np.median(bg_pixels))
    bright = neutral & (gray > background + 25)
    shadow = neutral & (gray < background - 24)
    # Search short edge bands, not exact edge coordinates. This absorbs small
    # crop shifts and fractional cell widths produced by display/browser scaling.
    top = float(np.max(np.mean(bright[:6, 7:25], axis=1)))
    left = float(np.max(np.mean(bright[7:25, :6], axis=0)))
    bottom = float(np.max(np.mean(shadow[-6:, 7:25], axis=1)))
    right = float(np.max(np.mean(shadow[7:25, -6:], axis=0)))
    raised = min(top, left) > .62 and min(bottom, right) > .55
    r, g, b = centre[:, :, 0], centre[:, :, 1], centre[:, :, 2]
    colour = np.ptp(centre, axis=2) > 55
    black = (np.ptp(centre, axis=2) < 30) & (middle_gray < background - 100)
    red = (r > g + 60) & (r > b + 60)
    inner = im[7:25, 7:25]
    inner_ink = np.max(np.abs(inner - background), axis=2) > 30
    if raised:
        # A flag has a red cloth ABOVE a dark pole/base, as well as a raised cell.
        # A red number or one of our overlay markers must not count as a flag.
        if int(red.sum()) >= 4 and int(black.sum()) >= 12:
            yr, _ = np.where(red)
            yb, _ = np.where(black)
            if yr.mean() + 2 < yb.mean() and yr.max() < 19 and yb.max() > 12:
                return FLAG
        if float(inner_ink.mean()) < .025 and int(colour.sum()) < 4:
            return COVERED
        return UNREADABLE
    # A partial bevel suggests a misaligned covered cell, not an opened zero.
    if max(top, left) > .62:
        return UNREADABLE
    if float(inner_ink.mean()) < .025 and int(colour.sum()) < 4:
        return 0
    if int(colour.sum()) < 10:
        return _classic_monochrome(centre, background)
    pixels = centre[colour]
    saturation = np.ptp(pixels, axis=1)
    core = pixels[saturation >= np.percentile(saturation, 65)]
    # Core strokes carry the digit colour; antialiased edge pixels should not
    # turn a dark-blue 4 into a blue 1 or a maroon 5 into a red 3.
    rgb = np.median(core, axis=0) + (198 - background)
    scores = np.sqrt(np.mean((CLASSIC_PALETTE - rgb) ** 2, axis=1))
    ranking = np.argsort(scores)
    if (scores[ranking[0]] > 35 or scores[ranking[1]] - scores[ranking[0]] < 18 or
            int(black.sum()) > 8):
        return UNREADABLE
    y, x = np.where(colour)
    width, height = x.max() - x.min() + 1, y.max() - y.min() + 1
    if not (6 <= width <= 22 and 12 <= height <= 22 and 25 <= len(x) <= 370):
        return UNREADABLE
    if len(x) / (width * height) > .94:  # Solid coloured blocks are not digits.
        return UNREADABLE
    return int(ranking[0]) + 1


def _template_feature(tile: np.ndarray) -> np.ndarray:
    # A little smoothing suppresses antialiasing/resampling noise, but retains
    # RGB colour and cell bevels. Do not crop the bevel away: C is not 0.
    return cv2.GaussianBlur(tile.astype(np.float32) / 255.0, (3, 3),
                            TEMPLATE_BLUR_SIGMA)


def _template_distance(sample: np.ndarray, reference: np.ndarray) -> float:
    """Best centre-weighted RMS distance over a small aligned shift search."""
    pad = TEMPLATE_SHIFT_PIXELS
    # Each map covers the SAME offsets. Never minimise the centre and border
    # independently: that could combine two incompatible alignments.
    full_ref = reference[pad:TILE_SIZE - pad, pad:TILE_SIZE - pad]
    full = cv2.matchTemplate(sample, full_ref, cv2.TM_SQDIFF)
    central_ref = reference[8:24, 8:24]
    central_sample = sample[8 - pad:24 + pad, 8 - pad:24 + pad]
    centre = cv2.matchTemplate(central_sample, central_ref, cv2.TM_SQDIFF)
    full = np.sqrt(np.maximum(full, 0) / full_ref.size)
    centre = np.sqrt(np.maximum(centre, 0) / central_ref.size)
    return float(np.min(.45 * full + .55 * centre))


class TemplateBank:
    """Optional classic recogniser plus tolerant, user-labelled fallback examples."""
    def __init__(self, classic_auto: bool = False) -> None:
        self.classic_auto = classic_auto
        self.samples: list[tuple[int, np.ndarray]] = []

    def clone(self) -> TemplateBank:
        bank = TemplateBank(classic_auto=self.classic_auto)
        bank.samples = [(label, image.copy()) for label, image in self.samples]
        return bank

    def add(self, label: int, tile: np.ndarray) -> None:
        if label not in LABELS:
            raise ValueError("Invalid template label.")
        normalised = normalise_tile(tile)
        # An exact copy under a different name is almost certainly mislabelling.
        for existing_label, image in self.samples:
            if np.array_equal(normalised, image):
                if existing_label != label:
                    raise ValueError(
                        f"This exact appearance was already labelled {SYMBOLS[existing_label]}. "
                        "Undo / clear that label before giving it a different name."
                    )
                return
        if sum(x == label for x, _ in self.samples) >= MAX_SAMPLES_PER_LABEL:
            raise ValueError("Too many examples for this label. Clear its examples and retrain.")
        self.samples.append((label, normalised))

    def classify(
        self, tiles: list[np.ndarray], threshold: float = DEFAULT_THRESHOLD,
    ) -> tuple[np.ndarray, np.ndarray]:
        count = len(tiles)
        values = np.full(count, UNREADABLE, dtype=np.int16)
        distances = np.full(count, np.inf, dtype=np.float32)
        # The same digit usually appears dozens of times. Reuse a result only
        # for identical pixels WITHIN this frame, never across changed boards.
        cache: dict[bytes, tuple[int, float]] = {}
        templates = None
        for i, tile in enumerate(tiles):
            normalised = normalise_tile(tile)
            key = normalised.tobytes()
            if key in cache:
                values[i], distances[i] = cache[key]
                continue
            if self.classic_auto:
                label = _classify_classic_normalised(normalised)
                if label != UNREADABLE:
                    # 0 is a sentinel for a built-in result, not a probability.
                    values[i], distances[i] = label, 0.0
                    cache[key] = (label, 0.0)
                    continue
            if self.samples:
                if templates is None:
                    templates = {}
                    for label, image in self.samples:
                        templates.setdefault(label, []).append(_template_feature(image))
                sample = _template_feature(normalised)
                names = list(templates)
                label_scores = [min(_template_distance(sample, ref) for ref in templates[label])
                                for label in names]
                ranking = np.argsort(label_scores)
                best = label_scores[int(ranking[0])]
                second = label_scores[int(ranking[1])] if len(ranking) > 1 else math.inf
                distances[i] = best
                if best <= threshold and second - best >= MIN_MARGIN:
                    values[i] = names[int(ranking[0])]
            cache[key] = (int(values[i]), float(distances[i]))
        return values, distances

    def to_json(self) -> list[dict]:
        records = []
        for label, image in self.samples:
            ok, encoded = cv2.imencode(".png", image)
            if not ok:
                raise ValueError("Could not encode a template.")
            records.append({"label": label, "png": base64.b64encode(encoded).decode("ascii")})
        return records

    @classmethod
    def from_json(cls, records: list) -> TemplateBank:
        if not isinstance(records, list) or len(records) > len(LABELS) * MAX_SAMPLES_PER_LABEL:
            raise ValueError("Invalid / oversized template profile.")
        bank = cls()
        for item in records:
            label = int(item["label"])
            if label not in LABELS or len(item["png"]) > 100_000:
                raise ValueError("Invalid template data.")
            raw = base64.b64decode(item["png"], validate=True)
            image = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
            if image is None or image.shape != (TILE_SIZE, TILE_SIZE, 3):
                raise ValueError("Invalid template dimensions.")
            bank.samples.append((label, image))
        return bank


def analyse_frame(frame: np.ndarray, rows: int, cols: int, bank: TemplateBank,
                  threshold: float) -> tuple[np.ndarray, SolveResult]:
    # The solver imports core definitions from this module.
    from solve_board import solve_board

    values, _ = bank.classify(split_tiles(frame, rows, cols), threshold)
    board = values.reshape(rows, cols)
    return board, solve_board(board)


@dataclass
class Region:
    screen_name: str
    monitor: dict[str, int]
    left: int
    top: int
    width: int
    height: int
    rows: int
    cols: int

    def capture_box(self) -> dict[str, int]:
        return {"left": self.left, "top": self.top,
                "width": self.width, "height": self.height}


def profile_path() -> Path:
    folder = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "MinesweeperHelper"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "profile.json"


def main() -> int:
    try:
        # Load the GUI only after this module's core definitions are available.
        from run_gui import run_gui

        return run_gui()
    except Exception:
        details = traceback.format_exc()
        print(details)
        try:
            log = profile_path().with_name("last_error.txt")
            log.write_text(details, encoding="utf-8")
            print(f"Error details saved locally to: {log}")
        except OSError:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

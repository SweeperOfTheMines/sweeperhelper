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
import ctypes
import json
import math
import os
from pathlib import Path
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
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


def solve_board(
    board: np.ndarray, *, budget_seconds: float = 0.5,
    max_component: int = 22, max_constraints: int = 4500,
) -> SolveResult:
    """Infer certain cells using propagation, subsets, then bounded exact search.

    No total-mine count is assumed. Flags are treated exactly like unopened
    cells, so an incorrect flag cannot become a false premise. Flat indices
    in the returned sets refer to this specific board snapshot, never a cache.
    """
    arr = np.asarray(board)
    if arr.ndim != 2 or not arr.size or arr.size > 6400:
        return SolveResult(error="Expected a nonempty rectangular board (max 6400 cells).")
    if not np.issubdtype(arr.dtype, np.integer):
        return SolveResult(error="Board values must be integers.")
    rows, cols = arr.shape
    flat = [int(x) for x in arr.flat]
    if UNREADABLE in flat:
        return SolveResult(error="Unrecognised tiles: check recognition / grid alignment before solving.")
    if GAME_OVER in flat:
        return SolveResult(error="A revealed mine / game-over tile is visible.")
    if any(x not in (COVERED, FLAG, *range(9)) for x in flat):
        return SolveResult(error="Invalid tile value.")
    unknown = {i for i, v in enumerate(flat) if v in (COVERED, FLAG)}
    constraints: dict[frozenset[int], int] = {}
    result = SolveResult()
    deadline = time.monotonic() + max(0.0, budget_seconds)
    try:
        for i, value in enumerate(flat):
            if 0 <= value <= 8:
                cells = frozenset(j for j in neighbours(i, rows, cols) if j in unknown)
                _put_constraint(constraints, cells, value)

        # Repeat basic rules and the difference rule: A subset of B implies
        # sum(B - A) = sum(B) - sum(A). All added equations are entailed.
        while True:
            reduced: dict[frozenset[int], int] = {}
            for cells, count in constraints.items():
                _put_constraint(
                    reduced, cells - result.mines - result.safe,
                    count - len(cells & result.mines),
                )
            constraints = reduced
            new_mines: set[int] = set()
            new_safe: set[int] = set()
            for cells, count in constraints.items():
                if count == 0:
                    new_safe.update(cells)
                elif count == len(cells):
                    new_mines.update(cells)
            if new_mines & new_safe:
                raise InconsistentBoard("The clues imply both safe and mined for one cell.")
            if new_mines or new_safe:
                result.mines.update(new_mines)
                result.safe.update(new_safe)
                # Reductions must be checked before returning a timed-out result.
                continue
            if time.monotonic() >= deadline:
                result.limited = bool(constraints)
                return result
            added = False
            items = sorted(constraints.items(), key=lambda item: len(item[0]))
            # Only compare pairs that share a cell, instead of all board pairs.
            index: dict[int, set[int]] = {}
            for j, (cells, _) in enumerate(items):
                for cell in cells:
                    index.setdefault(cell, set()).add(j)
            for i, (a, ka) in enumerate(items):
                if time.monotonic() >= deadline:
                    result.limited = True
                    return result
                candidates = index[min(a, key=lambda v: len(index[v]))]
                for j in sorted(candidates):
                    if j <= i:
                        continue
                    b, kb = items[j]
                    if a < b:
                        added |= _put_constraint(constraints, b - a, kb - ka)
                    if len(constraints) >= max_constraints:
                        result.limited = True
                        break
                if result.limited:
                    break
            if not added or result.limited:
                break

        # Residual connected components can be enumerated independently.
        # A single incomplete component contributes NO search-based hints.
        items = list(constraints.items())
        memberships: dict[int, set[int]] = {}
        for j, (cells, _) in enumerate(items):
            for v in cells:
                memberships.setdefault(v, set()).add(j)
        unseen = set(memberships)
        components = []
        while unseen:
            first = min(unseen)
            unseen.remove(first)
            variables = {first}
            equation_ids: set[int] = set()
            stack = [first]
            while stack:
                v = stack.pop()
                for j in memberships[v] - equation_ids:
                    equation_ids.add(j)
                    for w in items[j][0]:
                        if w in unseen:
                            unseen.remove(w)
                            variables.add(w)
                            stack.append(w)
            components.append((variables, [items[j] for j in sorted(equation_ids)]))
        for variables, equations in sorted(components, key=lambda item: len(item[0])):
            if len(variables) > max_component or time.monotonic() >= deadline:
                result.limited = True
                continue
            try:
                mines, safe = _enumerate_component(variables, equations, deadline)
                result.mines.update(mines)
                result.safe.update(safe)
            except SearchLimit:
                result.limited = True
        if result.mines & result.safe:
            raise InconsistentBoard("Conflicting deductions.")
        return result
    except InconsistentBoard as exc:
        # Discard ALL hints on any detected contradiction.
        return SolveResult(error=str(exc))


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


class WindowsAPI:
    """Small, typed Win32 wrapper. Construct only on Windows, after QApplication."""
    def __init__(self) -> None:
        from ctypes import wintypes as w
        self.w = w
        self.u = ctypes.WinDLL("user32", use_last_error=True)
        self.u.GetForegroundWindow.argtypes = []
        self.u.GetForegroundWindow.restype = w.HWND
        self.u.GetAncestor.argtypes = [w.HWND, w.UINT]
        self.u.GetAncestor.restype = w.HWND
        self.u.WindowFromPoint.argtypes = [w.POINT]
        self.u.WindowFromPoint.restype = w.HWND
        self.u.IsWindow.argtypes = [w.HWND]
        self.u.IsWindow.restype = w.BOOL
        self.u.GetAsyncKeyState.argtypes = [ctypes.c_int]
        self.u.GetAsyncKeyState.restype = ctypes.c_short
        self.u.SetWindowDisplayAffinity.argtypes = [w.HWND, w.DWORD]
        self.u.SetWindowDisplayAffinity.restype = w.BOOL
        self.u.SetWindowPos.argtypes = [w.HWND, w.HWND, ctypes.c_int, ctypes.c_int,
                                        ctypes.c_int, ctypes.c_int, w.UINT]
        self.u.SetWindowPos.restype = w.BOOL
        pointer_long = ctypes.c_ssize_t
        self.get_style = getattr(self.u, "GetWindowLongPtrW", self.u.GetWindowLongW)
        self.get_style.argtypes = [w.HWND, ctypes.c_int]
        self.get_style.restype = pointer_long
        self.set_style = getattr(self.u, "SetWindowLongPtrW", self.u.SetWindowLongW)
        self.set_style.argtypes = [w.HWND, ctypes.c_int, pointer_long]
        self.set_style.restype = pointer_long
        self.dwm = ctypes.WinDLL("dwmapi", use_last_error=True)
        self.dwm.DwmFlush.argtypes = []
        self.dwm.DwmFlush.restype = ctypes.c_long

    def flush(self) -> None:
        self.dwm.DwmFlush()

    def key_down(self, virtual_key: int) -> bool:
        return bool(self.u.GetAsyncKeyState(virtual_key) & 0x8000)

    def root_at(self, x: int, y: int) -> int:
        hwnd = self.u.WindowFromPoint(self.w.POINT(x, y))
        return int(self.u.GetAncestor(hwnd, 2) or 0)  # GA_ROOT

    def foreground(self) -> int:
        return int(self.u.GetForegroundWindow() or 0)

    def configure_overlay(self, hwnd: int) -> bool:
        # Qt flags already express this, but native flags reinforce click-through
        # across OTHER processes, not merely other widgets in this application.
        style = self.get_style(hwnd, -20)  # GWL_EXSTYLE
        style |= 0x00080000 | 0x00000020 | 0x08000000 | 0x00000080
        # WS_EX_LAYERED | TRANSPARENT | NOACTIVATE | TOOLWINDOW
        self.set_style(hwnd, -20, style)
        self.u.SetWindowPos(hwnd, self.w.HWND(-1), 0, 0, 0, 0,
                            0x0001 | 0x0002 | 0x0010 | 0x0020)
        # HWND_TOPMOST; NOSIZE | NOMOVE | NOACTIVATE | FRAMECHANGED.
        if sys.getwindowsversion().build < 19041:
            return False
        return bool(self.u.SetWindowDisplayAffinity(hwnd, 0x00000011))


def profile_path() -> Path:
    folder = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "MinesweeperHelper"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "profile.json"


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
                p.setPen(QPen(QColor("#00eeff"), max(2.0, min(cw, ch) * 0.1)))
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
                self.mine_overlay.setChecked(True)
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


def main() -> int:
    try:
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

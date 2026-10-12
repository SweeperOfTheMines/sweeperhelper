from minesweeper_helper import COVERED, FLAG, GAME_OVER, UNREADABLE, InconsistentBoard, SearchLimit, SolveResult, _enumerate_component, _put_constraint, neighbours
import numpy as np
import time


def solve_board(
    board: np.ndarray, *, budget_seconds: float = 0.5,
    max_component: int = 22, max_constraints: int = 2500,
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
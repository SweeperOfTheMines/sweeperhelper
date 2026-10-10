# Minesweeper Helper for Windows

A local, read-only Python helper. Select your board, teach it the tile designs,
and it overlays red mine symbols on **unflagged, covered cells that its logic
proves are mines**. It does not click, place flags, inspect game memory, or make
probabilistic guesses. You continue playing normally.

**Important distinction:** it cannot see what is hidden behind an unopened tile.
It reasons from visible numbers. “Proved” always means **conditional on the board
being recognised correctly**. Image recognition and user calibration can be wrong.
Do not treat a marker as an unconditional guarantee.

## Files

- `minesweeper_helper.py`: the complete application and solver, in one Python file.
- `requirements.txt`: Python libraries to install.
- `START.bat`: launches the already-installed application with a double-click.
- `test_helper.py`: automated tests for the solver and image matching.
- `demo_board.html`: optional offline practice board to try the workflow.
- `TEST_RESULTS.txt`: what was actually tested during preparation.

## 1. Install Python and open the project

Use **standard Python 3.13, 64-bit**. Python 3.12, 64-bit is also a target for
these dependencies, but the commands below explicitly use 3.13. This package is
not intended for Python 3.14+, 32-bit Python, or the free-threaded build.

Install the latest available 3.13.x Windows release using its **Windows installer
(64-bit)**, not the embeddable package. Enable the Python launcher / PATH options
when offered. Keep an existing Python 3.13 installation rather than reinstalling.

Extract the ZIP into an ordinary folder you can edit. Do not run inside the ZIP.
In VS Code, use **File > Open Folder** and select the folder that directly contains
`minesweeper_helper.py` and `requirements.txt`. Install the **Python extension by
Microsoft** from VS Code's Extensions panel when needed.

Open **Terminal > New Terminal**. PowerShell is fine. First check Python:

```powershell
py -3.13 --version
```

The result should begin with `Python 3.13`.

## 2. Install the project libraries

Run these commands **one at a time**, waiting for each to finish:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

The `.venv` folder holds a separate Python environment for this project. These
commands call its Python directly, so you do **not** need to activate the
environment or change PowerShell's execution policy.

In VS Code press **Ctrl+Shift+P**, choose **Python: Select Interpreter**, and select
the interpreter ending in:

```text
.venv\Scripts\python.exe
```

If it is not listed, use **Enter interpreter path** and browse to that file.

## 3. Run the application

From the same VS Code terminal:

```powershell
.\.venv\Scripts\python.exe minesweeper_helper.py
```

The terminal stays occupied while the helper is open. That is normal. After the
first installation, double-clicking `START.bat` is an alternative to opening
VS Code every time. Running Python as administrator should not be necessary.

## 4. Prepare the game

Use a normal rectangular Minesweeper board whose numbered cells count mines in
the eight neighbouring cells. Keep the complete board visible on **one monitor**.
Different monitors can use different Windows scaling settings; the program maps
the selected monitor's physical pixels to the overlay coordinates.

Open a few cells first so there are numbers to read. The helper cannot infer
anything from a completely covered board without clues. Use a windowed game or a
browser tab, and leave the browser zoom unchanged after setup.

Move the helper's control window onto another monitor, or beside the board. It
must not cover any part of the game grid. The preview inside the helper is not
the live game; clicking the preview does not uncover cells.

## 5. Select the board

Choose the correct **Monitor**, then enter the exact **Rows** and **Columns**.
Rows go top-to-bottom; columns go left-to-right. For example, a nine-by-nine game
has 9 rows and 9 columns. Do not include the timer or mine counter.

Click **1. Select board area**. The helper temporarily hides itself and freezes
an image of the selected screen. Drag from the top-left outer edge of the first
cell to the bottom-right outer edge of the final cell.

**Select the ENTIRE grid, not only its currently opened portion or a subsection.**
Leaving out real neighbours could create false deductions at the crop edges.
Include each tile's own border/bevel, but exclude the decorative frame surrounding
the whole board. The green grid should line up with every row and column.

Before accepting, use **arrow keys** to shift the selection by one physical
screen pixel. **Shift + arrow keys** adjusts its width/height by one pixel. Drag
again to replace the rectangle. **Enter** accepts; **Esc** cancels.

The selected region is fixed on the screen. Reselect after moving or resizing
the game window, scrolling the page, changing its zoom, or changing board size.
Restart the helper after changing Windows monitor layout or display scaling.

## 6. Teach the tile designs — initial calibration

Click **2. Teach tiles / refresh snapshot**. This captures a fresh image while
hiding the helper. The teaching dialog contains a **frozen screenshot**, so clicks
in it only label examples. They do not play the game.

Choose a label, then click a tile that looks like that label. You can also press
the label's key first, then click its example:

| Label | Which tile to click |
|---|---|
| `C` | A still-covered/unopened cell. |
| `F` | A cell showing a flag you placed in the game. |
| `0` | A cell that has already been revealed and is empty, with no number. |
| `1`–`8` | A revealed cell displaying that exact number. |
| `X` | A revealed mine / game-over mine tile. This stops hints. |

**Do not confuse `C` and `0`.** A covered cell has an unknown value. A revealed
empty cell asserts that all its neighbours are safe. A wrong `0` label can
therefore create wrong deductions.

Teach a covered tile, a revealed empty tile, and one example of every number
currently on the board. Teach a flag when one is visible. To obtain an example,
you may temporarily flag a covered square, capture/teach it, then remove the flag
if it was only for calibration. Teaching `F` learns the graphic, not that the
square actually contains a mine.

A number not currently visible does not need to be taught yet. When a new number
appears later, the helper may show `?` and withhold the overlay until you teach it.

For checkerboard or alternating-colour designs, teach **both background variants
of each tile type that appears**. Multiple examples per label are supported. Add
examples from different positions if font antialiasing or fractional tile widths
cause differences. A question-mark state used by your game can be taught as `C`:
it is still a covered, logically unknown cell.

The preview labels update as you teach. Turn off **Show recognition labels** to
inspect an obscured digit, then turn it back on to check recognition. `?` means
unrecognised, never “mine”. Use **Undo**, **Clear chosen label**, or **Clear all
examples** to correct mistakes. Save when the labels match the actual board.

Examples are saved locally for reuse at:

```text
%LOCALAPPDATA%\MinesweeperHelper\profile.json
```

The **Open saved-profile folder** button opens that location. Whole screenshots
are not routinely written to disk. The profile contains only the tile examples
you labelled, plus row/column settings. No game data is uploaded.

## 7. Start monitoring and enable hints

Click **3. Start monitoring (F8)**. Move the control window off the board and make
the game or helper the foreground window. The helper first works in preview-only
mode. **Compare its recognised numbers and C/F/0 labels against the real board.**

After verifying the labels, check **4. Enable mine overlay**. Red, spiky mine
symbols are drawn over covered cells that the solver proves contain mines.
The overlay is designed to pass mouse input through to the game. It does not
place game flags by itself.

Right-click the actual game squares to flag them. After recognition catches up,
the red symbol disappears from each flagged square. Uncover more cells normally;
the helper reads the new clues and recomputes its hints.

**Flags are not assumed correct.** The solver still derives mine locations from
the revealed numbers, treating flagged cells as unknowns. A mistaken flag will
not become a premise for further deductions. If a flag is on a cell proved safe,
the status line warns you. An uncertain flag is not automatically declared wrong.

**Also show safe squares** adds green outlines to covered cells proved safe. It
is off by default, and only works while the mine-overlay checkbox is enabled.
The program never clicks those cells for you.

The default scan interval is **300 milliseconds**. Hints require two consecutive
matching recognised boards, so updates have a delay rather than being instant.
They are withheld while the board is changing, unreadable, or contradictory.
Mouse presses and focus changes clear stale hints when detected. This is polling,
not a zero-latency guarantee.

## Controls and everyday use

**F8** pauses or resumes, including while the game has focus. **F9** quits.
The control-window buttons do the same. These keys are polled, not globally
reserved, so another program may also respond to them. No keystrokes are logged.

**Compatibility capture** is on by default: it briefly hides the overlay before
each screenshot, preventing the helper from reading its own mine symbols. Some
flicker is expected. On supported Windows systems, you can turn it off to use
Windows capture exclusion instead. Turn it back on immediately if symbols cause
recognition failures. The option cannot be disabled when native exclusion is
unavailable.

When playing another board at the same position with the same dimensions and
appearance, you can reuse the examples. Pause before resetting a game, then
resume after the new board settles. Reselect when its position or dimensions
change. Clear/retrain examples when switching to a different game theme.

When you restart the helper, examples are restored but the board area and overlay
permission are not. Select the area again and verify the preview. This prevents
old screen coordinates from being used accidentally.

## Troubleshooting

| Symptom | What to do |
|---|---|
| `py` is not recognised, or Python 3.13 is not found | Install standard Python 3.13, including its launcher, then close and reopen VS Code. |
| `requirements.txt` cannot be found | Open the folder directly containing that file; make sure the terminal is in it. |
| `ModuleNotFoundError` | Re-run the install command using `.\.venv\Scripts\python.exe`; make sure VS Code uses that same interpreter. |
| PowerShell says running scripts is disabled | Use the exact commands above. They invoke `python.exe`, not `Activate.ps1`; no policy change is needed. |
| No matching package distribution | Check that you used standard 64-bit Python 3.12/3.13, not 32-bit, 3.14+, or free-threaded Python. |
| Lots of `?` labels | First check full-grid selection, rows/columns and bevel alignment. Then teach missing designs and colour variants. |
| A real `2` is labelled `1`, `C`, or `0` | Disable the overlay immediately. Clear the wrong examples and retrain. Inspect the grid alignment. |
| A previously unseen number stops hints | Refresh the teaching screenshot, label that number, save, start and verify again. |
| No overlay although the preview is correct | Enable the mine-overlay checkbox; check the status. No forced mine may exist. It deliberately does not guess. |
| The helper says to bring the game forward | Click the game and keep the helper control window off the selected grid. |
| Hints shift away from the cells | Reselect the exact grid. Restart after monitor/scaling changes. Keep the board on one monitor. |
| Overlay flickers | Expected in compatibility mode. Native exclusion can reduce it on supported systems; retain compatibility mode if recognition suffers. |
| The overlay blocks clicks or seems stuck | Press F9 to close it. Do not continue playing through a faulty overlay; report the behaviour with your Windows/game details. |
| Capture is blank in an exclusive fullscreen game | Use windowed/borderless mode. Secure/unsupported capture surfaces are outside this helper's scope. |
| Controls do not fit on the screen | Resize the window or scroll its contents; F8/F9 still work. |

The **Match cutoff** is an image-distance threshold, not a probability or an
accuracy percentage. The default is deliberately strict. A higher value may
recognise more tiles but also mistake one tile for another. Prefer more accurate
selection and more examples over loosening the cutoff. Changing the cutoff
pauses monitoring and requires another preview check.

A raised exception during startup may be recorded locally in `last_error.txt`
inside the saved-profile folder. Other failures are shown in the status line or
terminal. Copy the full message when diagnosing a problem.

## Optional practice board

Open `demo_board.html` in a browser. It is an offline 9-by-9 game included only to
practice selection, calibration and flag updates. It makes no network requests.
The initial position is deliberately fixed and partly revealed. Use rows = 9,
columns = 9, and select only its square grid. Use **Reset practice board** to return
to that same position. This is not a demonstration that your separate Minesweeper
game or Windows overlay has been tested.

## Logic, scope and limitations

The solver turns each revealed number into an equation over its covered/flagged
neighbours. It applies zero/all-mine rules and subset-difference deductions,
then exhaustively checks small connected groups of remaining unknown cells.
A cell becomes a search-based hint only if its state is the same in every valid
arrangement **after that component's search completes**.

The default exact-search limit is 22 variables per component, 150,000 search
nodes, and an approximately 0.18-second logic budget per analysed snapshot.
Large or expensive components are skipped; completed proof-based deductions
remain available. It can miss deductions that a more expensive solver or the
total mine count could establish. The status reports a reached search limit.
An unfinished search is never interpreted as evidence that a cell is safe/mined.

This version does not use the game's total-mine counter, estimate probabilities,
automatically detect board dimensions, follow a moving game window, support
nonrectangular/hexagonal boards, or guarantee a win. It needs readable, sufficiently
large, regularly spaced tiles and a stable visual theme. Covering the board with
other windows or notifications can break recognition. Moving the mouse away from
the grid can help when a game changes tile colours on hover.

It supports selecting one board from one of several monitors, not a board split
across multiple monitors. Monitor origin and dimensions are matched explicitly;
a mapping failure stops setup rather than silently assuming all monitors use
100% scaling.

## Run the automated tests

```powershell
.\.venv\Scripts\python.exe -m unittest -v test_helper.py
```

See `TEST_RESULTS.txt` for results from the preparation environment and the precise
untested parts. Passing the core tests does **not** verify Windows display capture,
DPI behaviour, click-through, hotkeys, or your game's tile recognition.

## Primary technical references

The APIs used are documented by their maintainers:

```text
Python Windows releases:
https://www.python.org/downloads/windows/
Python virtual environments:
https://docs.python.org/3/library/venv.html
VS Code Python environments:
https://code.visualstudio.com/docs/python/environments
Qt window flags (including WindowTransparentForInput):
https://doc.qt.io/qt-6/qt.html
Qt high-DPI coordinate model:
https://doc.qt.io/qt-6/highdpi.html
Windows SetWindowDisplayAffinity / WDA_EXCLUDEFROMCAPTURE:
https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setwindowdisplayaffinity
MSS project:
https://github.com/BoboTiG/python-mss
OpenCV Python packages:
https://pypi.org/project/opencv-python-headless/
```

# Minesweeper Helper v2 — automatic classic recognition

A Windows screen-only, click-through Minesweeper assistant. It does not inspect
game memory, send clicks, assume flags are correct, or make probabilistic guesses.
Its deductions depend on correct recognition of the visible board.

## Upgrading an existing installation

1. Press **F9** to close the running helper.
2. Keep a backup of your old `minesweeper_helper.py`, then replace it with the
   `minesweeper_helper.py` from this version. **No new packages are needed.**
3. Run it using the existing environment:

   ```powershell
   .\.venv\Scripts\python.exe minesweeper_helper.py
   ```

4. Leave **Classic grey tiles: recognise automatically (no teaching needed)**
   checked. Select the complete grid, start monitoring, check the preview labels,
   and enable the mine overlay. Teaching is optional, not a required setup step.

For the screenshot supplied with this update, the board is **16 rows by 30
columns**. The timer, mine counter, smiley button and decorative frame must NOT
be inside the selected grid. Keep the entire board on one monitor.

The replacement includes the previously discussed overlay-timer restart. The
scan interval now ranges from 150 to 10,000 ms, with a default of **3,000 ms**.
Set it to your preferred value in the control window. Longer intervals mean
slower responses to board changes. Other local edits you made to the old script
are not automatically merged: keep the backup so you can copy them across.

## What changed

### Automatic recognition for classic grey tiles

The recogniser uses broad colour ranges, the cell's raised border, and simple
symbol geometry rather than exact tile photographs. It distinguishes:

- Raised, empty covered tiles from flat, revealed empty cells.
- Red flags with dark poles/bases from red number clues.
- Blue 1 / dark-blue 4, and red 3 / maroon 5, using core stroke colour rather
  than antialiased edge pixels.
- Standard coloured 2 and 6, plus shape-checked black 7 and grey 8.

It tolerates small alignment errors by examining short border bands rather than
one exact edge pixel. Digit colour is estimated from strongly coloured interior
strokes. Moderate changes in neutral background brightness are compensated.

Black pixels alone are NOT treated as 7. A 7 must match a 7-shaped silhouette;
a grey 8 must have two enclosed spaces. Unsupported symbols, question marks and
most revealed mine graphics remain `?`, stopping hints. A taught mine example can
be labelled `X`, which also stops hints.

Classic mode does not learn from guesses or from previous deductions. There is
no online service, model download, OCR engine or game-memory access.

### More forgiving learned-template fallback

Saved examples from version 1 remain compatible. They are tried when automatic
recognition cannot identify a tile, or when classic mode is switched off.

The fallback matcher now applies slight Gaussian smoothing and searches small
relative translations rather than comparing only identically positioned pixels.
The same offset is used for the centre and border comparison, preserving the
important difference between covered and open-empty cells.

These constants near the top of the script control **learned-template matching**:

```python
DEFAULT_THRESHOLD = 0.065    # Previously 0.025; a distance, NOT a probability.
TEMPLATE_SHIFT_PIXELS = 2   # +/- 2 pixels after normalising a tile to 32 x 32.
TEMPLATE_BLUR_SIGMA = 0.65  # Light smoothing before comparing learned examples.
```

Use **Template tolerance (higher = more forgiving)** to adjust the cutoff at
runtime. It does not change the classic colour/geometry rules. Raising it too far
can accept the wrong number. The minimum separation between competing labels
is retained; ambiguity still produces `?` instead of forcing a match.

A result cache is used only for identical tile pixels within one frame. It is
not carried across changing boards and does not preserve stale deductions.

## First-time installation

Use standard **64-bit CPython 3.13** on Windows 10/11, not a free-threaded build.
Install the Microsoft Python extension in VS Code. Extract this project, then
use **File > Open Folder** to open the folder containing the Python script.

In **Terminal > New Terminal**, run these commands one at a time:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe minesweeper_helper.py
```

No virtual-environment activation or PowerShell execution-policy change is
needed. In VS Code, use **Ctrl+Shift+P > Python: Select Interpreter**, then select
`.venv\Scripts\python.exe`. Subsequent launches can use `START.bat`.

The pinned dependencies are unchanged from version 1. Do not replace your
existing environment just to install this update.

## Selecting and checking the board

Set the monitor, row count and column count. Click **1. Select board area** and
drag around the entire cell grid. The green lines should follow the cell edges.
Arrows move the selection by one screen pixel; Shift+arrows adjust its size.
Enter accepts and Esc cancels.

Small pixel differences are tolerated, but the correct complete grid is still
required. The helper does not automatically determine row/column counts, track
a moving game window, or repair a selection containing only part of the board.
Reselect after moving/resizing the game or changing browser zoom. Restart the
helper after changing Windows monitor layout/scaling.

Keep the helper window and other applications off the game board. The preview
uses these labels:

| Label | Meaning |
| --- | --- |
| C | Covered, unopened cell |
| F | Flag graphic; not assumed to prove a mine |
| 0 | Opened empty cell |
| 1–8 | Revealed numerical clue |
| ? | Unreadable; hints withheld |
| X | Known revealed mine / game-over example; hints withheld |

Click **3. Start monitoring**, verify the labels, then enable the mine overlay.
Two matching recognised reads are required before showing hints. This is not a
replacement for checking the preview: a stable recognition error is possible.

## Gameplay and overlay behaviour

The overlay is intended to be click-through. Right-click the actual game to
flag marked cells, and uncover further squares normally. Recognised flags no
longer receive an extra mine marker. Optional green outlines indicate cells
proved safe; they are off by default. The helper never clicks for you.

**F8** pauses/resumes. **F9** exits. If an overlay interferes with input, press F9.

Compatibility capture briefly hides the overlay before capturing the board so
its own symbols are not read as game graphics. The pre-capture delay remains
40 ms. The next scan countdown restarts after the analysed overlay is displayed,
so processing time does not consume most of the intended visible interval.

On supported systems, unchecking **Compatibility capture** uses Windows capture
exclusion instead, without the intentional pre-capture hide. Restore it if
recognition deteriorates or the overlay is captured. Availability and reliable
behaviour still depend on the real Windows/graphics setup.

Mouse presses, focus changes, unreadable tiles and contradictions can clear
hints before the interval expires. These checks remain in place. A three-second
scan interval does not promise that stale hints will persist through a click.

## Other themes / optional teaching

For a different theme, switch off classic recognition and click **Optional:
teach / refresh snapshot**. Choose a label and click an example in the frozen
screenshot. This does not click the live game. Multiple examples are supported;
small rendering changes should not need a new example each time.

**Never label a covered cell as 0, or an unknown digit as C or 0.** Undo or clear a
label to correct mistakes. Save retains the examples locally under:

```text
%LOCALAPPDATA%\MinesweeperHelper\profile.json
```

The automatic/classic choice is saved too. Existing tile records are not
converted or deleted by switching modes. Clearing saved examples does not remove
the built-in recogniser. A missing or invalid saved profile does not prevent
classic mode from running without examples.

The old `demo_board.html` remains useful for a controlled 9 x 9 test, but its
font-based flag is not the same as the classic bitmap flag in the supplied
screenshot. For that demo, switch off classic mode and teach C, F, 0, 1 and 2
once. The starting position's first unflagged forced mine is row 3, column 3.

## Tests and limitations

Run the full included suite:

```powershell
.\.venv\Scripts\python.exe -m unittest -v test_helper.py test_recognition.py
```

**55 test methods passed** in the preparation environment. The supplied
480-cell screenshot is recognised without any taught examples. Tests include
small selection offsets, selected rescaling factors, blur, noise, brightness
changes, learned-template shifts, uncertain-match rejection and solver logic.

The screenshot was used during development and testing. Modified copies of it
are not an independent dataset. It contains C, F, 0 and digits 1–5; actual game
6/7/8s were unavailable, so those were tested only with synthetic glyphs.

One extra 65%-size area-resampled version leaves one 5 unreadable. This is
reported rather than relaxing the rules until every test image gets a label.
Other fonts, themes, extremely small tiles, significant blur, animations or
major selection errors may need teaching or may remain unreadable.

Windows GUI startup, live capture, mixed-DPI monitors, click-through and capture
exclusion could not be exercised in this Linux environment. The included tests
are not a claim of an end-to-end Windows validation. See **TEST_RESULTS.txt** for
scope and **test_run.txt** for actual output.

## References for the implementation

- OpenCV template matching:
  https://docs.opencv.org/4.13.0/de/da9/tutorial_template_matching.html
- OpenCV smoothing:
  https://docs.opencv.org/4.13.0/d4/d13/tutorial_py_filtering.html
- Qt timer restart behaviour:
  https://doc.qt.io/qtforpython-6/PySide6/QtCore/QTimer.html


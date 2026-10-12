# Minesweeper Helper

**A live, click-through Minesweeper hint overlay for Windows.**

Select your board, check its recognised clues, and see mine markers over covered
squares while you keep playing the actual game. Optional outlines show cells the
solver proves safe. Recognition of supported classic grey tiles is automatic;
other graphics can use saved examples.

**Windows desktop · Standard 64-bit Python 3.13 · Early-stage project**

<!-- BEFORE LAUNCH: replace this comment with a real 20–35 second recording.
Do not insert a screenshot of a plain board and describe it as the working overlay.
For a GIF that you have actually created and added as media/demo.gif, use:
![Live demo: hints update after a flag and a newly uncovered cell](media/demo.gif)
Alternatively upload a small MP4 through GitHub's README editor and insert the
attachment link GitHub returns. Add a static screenshot with alt text as fallback.
-->

## What it does — and what it does not

The helper reads visible board pixels, uses logical constraints to identify forced
mines and safe squares, and draws hints in a separate click-through overlay.
It does not read game memory, play moves for you, or label a probabilistic guess
as certain. Your flags are recognised, but are not assumed to prove a mine.

**Every deduction depends on a correctly recognised, complete board.** Check the
preview before enabling the overlay. An ambiguous board can legitimately have
no hints; bounded search can also miss deductions. Unreadable cells and detected
contradictions stop hints rather than forcing a label.

## Start here

This is a **source distribution**: Python and the dependencies below are required.
VS Code is optional, not a runtime requirement.

Download and extract a tagged source release, or use **Code > Download ZIP** for
the current branch. Open a PowerShell terminal in the folder containing
`minesweeper_helper.py` and `requirements.txt`.

Install standard 64-bit Python 3.13 first, then run these commands one at a time:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe minesweeper_helper.py
```

Keep the environment local; no activation or execution-policy change is necessary.
After initial setup, `START.bat` can launch the application again.

### First hints

Open a game and reveal a few cells. Keep the entire board on one monitor, with
other windows out of its way. In the helper, choose the monitor and exact row and
column counts, then select the **whole cell grid**, excluding counters and frames.

Leave **Classic grey tiles: recognise automatically** enabled for supported classic
graphics. Start monitoring and check the labels in the preview against the game.
Then enable the mine overlay. Play normally; the helper rechecks the board and
updates its hints as you flag and uncover cells.

**F8 pauses/resumes. F9 exits.** Press F9 immediately if the overlay interferes
with mouse input. Reselect the area after moving/resizing the game or changing zoom.

See the [full setup and troubleshooting guide](docs/USER_GUIDE.md) for grid
selection, optional teaching, multi-monitor setup and overlay timing.

## Compatibility and known limits

Designed for Windows desktop use; macOS and Linux desktop overlays are not supported.
Classic recognition is tuned to classic grey, bevelled tiles with coloured digits,
not every Minesweeper theme. The entire grid must be selected accurately, and each
board must fit on one monitor. It does not automatically follow a moving window.

Compatibility capture briefly hides the hints before taking a screenshot. The
alternative capture-exclusion mode depends on the actual Windows/graphics setup.
Mouse presses, focus changes and recognition problems can clear hints early.

The existing 9×9 `demo_board.html` is an **offline practice board**, not a browser
port of this helper. Its different flag graphics need classic mode turned off and
one-time teaching of the visible tile types. See the full guide for details.

See [compatibility evidence](docs/COMPATIBILITY.md) for the distinction between
screenshot tests, synthetic tests and independently confirmed live setups.

## How it works

Screen capture → split the selected grid into cells → recognise tile states →
solve the resulting constraints → display hints without sending game input.

Classic recognition uses colour ranges, border structure and symbol geometry.
Saved-example fallback uses tolerant image matching. The solver combines local
rules, constraint subtraction and bounded exact search. It does not need an AI
model, API key or hosted service at runtime.

This project was developed with AI assistance and iterative testing. Bug reports,
review and independent Windows testing are welcome.

## Privacy and use

The baseline application processes screenshots locally and does not upload them.
The area selector captures the selected monitor to let you choose the board;
normal monitoring analyses the selected board area. Saved tile examples are stored
locally under `%LOCALAPPDATA%\MinesweeperHelper\profile.json`.

Use this as a private practice or programming tool, not to submit assisted results
as unassisted competitive scores. Respect the rules of the game or service you use.

## Tests and contributions

```powershell
.\.venv\Scripts\python.exe -m unittest -v test_helper.py test_recognition.py
```

The original v2 test report covers solver logic, synthetic tiles, and one supplied
classic-board screenshot plus transformations of that same scene. It is not an
independent accuracy benchmark or an end-to-end desktop test. Real game glyphs
6–8 were not present in that screenshot.

The test workflow checks the supplied tests on a Windows runner; real capture,
click-through behaviour and monitor scaling still need human testing.

Found a wrong label, displaced marker or installation problem? Open a GitHub issue
using the bug-report form, with your version, game, scaling settings and a cropped
board image where useful. Reports that a setup works are useful too.
See [CONTRIBUTING.md](CONTRIBUTING.md).

<!-- Preserve/add the existing project's actual licence and acknowledgements here.
Do not add a licence badge or claim a licence that has not been chosen and committed.
-->

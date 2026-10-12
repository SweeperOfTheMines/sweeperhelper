# Contributing

The highest-value contributions right now are independent Windows testing,
recognition bug reports with reproducible cropped boards, and clearer first-run
instructions. Small focused fixes are easier to review than a large rewrite.

## Report a problem

Use the bug-report issue form. Include the release or commit; Windows version;
game/theme; grid dimensions; display resolution and scaling; browser zoom; capture
mode; the steps you took; and what the preview/status line showed. A screenshot of
just the board is often useful. Remove personal information first.

Do not post credentials, whole-desktop captures or unrelated log files. Do not
work around security warnings by disabling your protections.

## Report a working setup

Use the compatibility-report form. State what you actually tested, including
whether hints updated after a flag and a newly revealed cell. A successful import
or passing unit test is not the same as a working desktop overlay.

## Make a change

Discuss a significant new feature in an issue before implementing it. For a small
fix, use a branch in your own fork, make the focused change, add a regression test
where possible, and open a pull request explaining the problem and test evidence.
Do not check in `.venv`, saved profiles, credentials or generated build folders.

From the application folder, run:

```powershell
.\.venv\Scripts\python.exe -m unittest -v test_helper.py test_recognition.py
```

Keep `test_data/classic_screenshot.png` in the test checkout so that the screenshot
regression tests actually run. The workflow explicitly checks that this fixture exists.

## Preserve these behaviours

Wrong flags must not become trusted premises. Ambiguous search results must not
be presented as certain. Unknown recognition must not silently become an empty
cell. Hints must not remain valid just because an older board had the same shape.
Do not remove stale-hint checks merely to improve a recording or benchmark.

AI-assisted changes are welcome when the contributor has reviewed them, can
explain the change and has supplied meaningful test evidence. Do not submit
unreviewed generated patches or fabricate test results.

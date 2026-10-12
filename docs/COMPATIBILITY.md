# Compatibility evidence

Separate automated recognition results from a live desktop test. Record the
release/tag or commit, not just “latest”. Do not add guessed operating-system,
browser or display settings to the table.

## Existing v2 evidence

| Evidence | What was checked | What it does not establish |
| --- | --- | --- |
| One supplied 16×30 classic screenshot | 480 visible tile labels, including flags, covered/empty cells and digits 1–5 | Other games/themes or live screen capture |
| Transformed copies of that same screenshot | Selected position offsets, rescaling, blur, noise and brightness tests | An independent accuracy benchmark |
| Synthetic 6–8 and other symbols | Selected rendered glyphs and rejection checks | Real game digits 6–8 |
| Core solver tests | Deductions, contradictions, flags-as-unknown and reference comparisons | Correctness when screen recognition is wrong |

The earlier v2 preparation environment was Linux with image-library versions
listed in the original test report. A Windows Actions run and live desktop
observations are separate evidence and should be added only when actually obtained.

## Live reports

No independent live setup is asserted by this template. Add your own verified setup
and tester reports below. Preserve limitations, failures and uncertain results.

| Version / commit | Windows version | Game and theme | Monitor resolution / scaling | Browser zoom | Capture mode | Result and evidence |
| --- | --- | --- | --- | --- | --- | --- |

A useful successful run includes first-time installation, correct preview labels,
mouse clicks passing through, a flag removing a marker, a newly revealed clue
updating the hints, and F8/F9 controls. Mixed-DPI behaviour needs an actual
multi-monitor test, not merely a computer with two monitors attached.

Crop personal information out of any shared screenshots.

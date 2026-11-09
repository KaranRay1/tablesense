# Evaluation: run 6649e08f4fb3 vs stock_4385.csv

| Metric | Value |
|---|---|
| labeled_table_hours | 0.016 |
| state_accuracy | 0.9246 |
| true_changes | 6 |
| missed_changes | 3 |
| missed_rate | 0.5 |
| false_transitions | 0 |
| false_transitions_per_table_hour | 0.0 |
| median_delay_s | 1.45 |
| p90_delay_s | 1.55 |
| wrong_yellow_seconds | 0.0 |
| wrong_yellow_episodes | 0 |

| Table | Labeled min | Accuracy | True changes | Missed | False | Median delay s | Wrong-yellow s |
|---|---|---|---|---|---|---|---|
| T1 | 0.2 | 100.0% | 0 | 0 | 0 | - | 0 |
| T2 | 0.2 | 85.2% | 2 | 0 | 0 | 1.5 | 0 |
| T3 | 0.2 | 95.0% | 3 | 3 | 0 | - | 0 |
| T4 | 0.2 | 89.6% | 1 | 0 | 0 | 1.4 | 0 |

Matching window: -0.5s / +3.0s.

## Notes (written by hand)

A/B on the same clip, same detector and timers; the only difference is the tabletop check (`--no-tabletop` for the "off" run: [off](2026-11-09_stock4385_tabletop_off.md)).

| | Off | On |
|---|---|---|
| Overall accuracy | 84.4% | **92.5%** |
| T2 (the fast-clear table) | 53.0% | **85.2%** |
| T2 yellow on a clean table | 7.9 → 12.9 s (timeout) | 7.9 → 8.4 s (`looks_clear`) |
| False transitions | 1 | 0 |

**Caveats, read before quoting these numbers:**
- One 15 s time-lapse with 4 tables. This shows the mechanism works on real pixels; it is not an accuracy figure.
- The references were captured from stretches of **this same clip** labeled green. At a venue they'd come from another day, so expect worse.
- The thresholds (35% pixel change, 0.25 clear) were chosen after looking at this clip's scores. Not tuned on held-out data.
- First version skipped the check whenever any person box touched the tabletop; in this tight layout neighbours' boxes always did, so T2 was almost never read (green only at 9.2 s). Masking out people's pixels instead fixed that.
- **Seen failure:** T3's reference (7 frames, paper and a cup on the table) read a table with plates as "clear". Didn't matter here (T3 never went yellow), but a bad reference shows green on a dirty table. Capture references from long, clean stretches.
- Clips 4672 and 43248 can't be used for this (close-ups, no fixed camera); see docs/datasets.md.

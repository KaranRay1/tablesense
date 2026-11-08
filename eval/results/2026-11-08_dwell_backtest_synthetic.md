# 2026-11-08: dwell prediction backtest (SYNTHETIC)

**Synthetic data**, generated from the same features the model uses (time of day, group size), so this flatters the baseline. It checks that the code works and that conditioning on elapsed time helps. It is not evidence of real performance.

- Data: `tablesense.sim`, 21 days from 2026-10-12, 12 tables. Train on 14 days (759 visits), test on 7 (365).
- Model: median remaining dwell of past visits with the same daypart and group size that lasted at least as long (fallback: daypart, then all).
- Naive: overall median dwell minus time already spent.

| Time already seated | Visits | Model MAE | Naive MAE | Actual inside 25-75% range |
|---|---|---|---|---|
| 10 min | 365 | 16.0 min | 21.8 min | 47% |
| 30 min | 321 | 15.7 min | 21.4 min | 49% |
| 60 min | 122 | 18.9 min | 31.0 min | 46% |

## Takeaways

- Conditioning on time already seated beats the naive guess, most of all for long stays.
- The range is honest (~50% inside a 25-75% band).
- ~16 min error is too coarse for "free at 19:42". The host view shows **which table frees first** and a range, not an exact time.
- Re-run on real data before any claims: `python -m tablesense.analytics.predict --events <real.db> --start <first day>`.

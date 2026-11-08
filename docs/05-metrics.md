# 5. Metrics and evaluation

If it isn't measured on labeled footage, it doesn't count, for sales or for the CV.

## Ground truth

Hand-label footage **per table over time**: `table_id, start_ts, end_ts, true_state`. Label at the level of state segments, not per frame (much faster). A simple labeling tool goes in `tools/`.

Keep separate splits:
- **dev**: footage used to tune thresholds
- **test**: footage never looked at while tuning (different days, ideally including a busy weekend shift)

## Core metrics

| Metric | Definition | Phase 1 target |
|--------|-----------|----------------|
| **State accuracy (time-weighted)** | % of table-seconds where predicted state = true state | ≥ 90% |
| **Transition delay** | Time from the true state change to our detected change (median, p90) | median ≤ 60 s |
| **False transitions / table / hour** | State changes that don't match any true change | ≤ 1 |
| **Missed transitions** | True changes we never detected | ≤ 5% |
| **Wrongly-yellow rate** | Times we said NEEDS_CLEARING while guests were only away | As close to 0 as possible. **This is the costliest error.** |

## Product metrics (later phases)

| Metric | Phase |
|--------|-------|
| Dwell-time error (MAE, minutes) per session | 2 |
| Wait-prediction MAE (minutes) | 3 |
| **False alerts per shift** (shadow mode, judged by a manager) | 4 |
| % of staff with notifications still on after 2 weeks | 4 |
| Uptime: hours run without a manual restart | 2+ |
| Setup time per new venue (zone drawing + tuning) | 2+ |

## System metrics

- fps processed per camera on the edge device
- CPU/GPU usage and temperature
- end-to-end latency, frame to state change

## Reporting

Each evaluation run writes `eval/results/<date>_<venue>_<split>.json` plus a short markdown summary. Never overwrite old results; we want to see progress over time.

The CV bullet this should become: *"Deployed at N venues; X% time-weighted table-state accuracy on held-out footage; Y false alerts per shift."*

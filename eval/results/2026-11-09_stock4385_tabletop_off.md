# Evaluation: run 32d16e6bfafc vs stock_4385.csv

| Metric | Value |
|---|---|
| labeled_table_hours | 0.016 |
| state_accuracy | 0.8442 |
| true_changes | 6 |
| missed_changes | 4 |
| missed_rate | 0.6667 |
| false_transitions | 1 |
| false_transitions_per_table_hour | 64.286 |
| median_delay_s | 1.51 |
| p90_delay_s | 1.56 |
| wrong_yellow_seconds | 0.0 |
| wrong_yellow_episodes | 0 |

| Table | Labeled min | Accuracy | True changes | Missed | False | Median delay s | Wrong-yellow s |
|---|---|---|---|---|---|---|---|
| T1 | 0.2 | 100.0% | 0 | 0 | 0 | - | 0 |
| T2 | 0.2 | 53.0% | 2 | 1 | 1 | 1.6 | 0 |
| T3 | 0.2 | 95.0% | 3 | 3 | 0 | - | 0 |
| T4 | 0.2 | 89.6% | 1 | 0 | 0 | 1.4 | 0 |

Matching window: -0.5s / +3.0s.

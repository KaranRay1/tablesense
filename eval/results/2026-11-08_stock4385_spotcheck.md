# 2026-11-08: first end-to-end run, stock clip 4385 (visual spot-check)

**Not a measured accuracy.** No ground-truth labels yet; states were checked by eye on 10 frames.

- Clip: Mixkit 4385, ceiling view, 1 row of 4 tables, 15 s **time-lapse** (timers scaled down, see `configs/stock_4385.yaml`)
- Model: `yolo11s.pt`, imgsz 640, conf 0.4, ByteTrack, 8 fps processed, 0.5 s smoothing
- Speed: 120 frames in 6.2 s (19.5 fps) on Apple Silicon (MPS), annotation included

## Model comparison (3 frames, people found in the dining row)

| Model | imgsz | t=0 s (truth: 4) | t=7 s | t=14 s | ms/frame |
|-------|-------|------|------|------|------|
| yolo11n | 640 | 3 | 3 | 4 | 16 |
| yolo11n | 1280 | 4 | 2 | 5 | 46 |
| **yolo11s** | **640** | **4** | 2 | 5 | **29** |
| yolo11m | 640 | 4 | 2 | 6 | 67 |

## State results

| Table | Truth (by eye) | System | OK? |
|-------|---------------|--------|-----|
| T1 | empty all clip | EMPTY | yes |
| T2 | guests leave, glasses left | SEATED → AWAY → NEEDS_CLEARING (7.9 s) → EMPTY via timeout (12.9 s) | yes (clearing not observed directly) |
| T3 | occupied all clip | SEATED, 1 false AWAY (2.9–3.8 s) | partly: false transition, but stayed red thanks to the hold |
| T4 | group of ~6 sits at ~9 s | EMPTY → SEATED (9.2 s), counted 3–4 | state yes, count low |

## Learned

1. The AWAY hold works as designed: a detection dropout produced a hidden AWAY, not a wrong yellow.
2. People counts under-count crowded, blurry tables. States are reliable before counts are; don't promise exact group sizes.
3. Zones must cover the seats, not just the table top. With an angled camera, boxes of seated people sit "behind" their table.
4. A false positive (a lamp detected as a person) was harmless because it was outside every zone. Zones double as a filter.

## Next

Label real footage (10+ min, sit → away → leave → clear) and compute the metrics in `docs/05-metrics.md`.

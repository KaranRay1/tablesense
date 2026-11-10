# 2026-11-10: tiled detection vs larger input, 6 stock scenes

YOLO11s, 4 frames/s, M5 Pro (MPS). Cell = mean people found per frame (frame-to-frame change). No ground-truth labels; precision checked by eye on one frame per clip. Clips: [docs/datasets.md](../../docs/datasets.md). Follows [2026-11-09_detector_stock_scenes.md](2026-11-09_detector_stock_scenes.md).

| Setup | Food court (~100 visible) | Turkish cafe | Overhead cafe (truth 2) | Mall | Food market | Mixkit 4385 | ms/frame (median) |
|---|---|---|---|---|---|---|---|
| 640, tracker, conf 0.4 (old default) | 7.6 (13.9%) | 17.1 (4.6%) | 1.7 (20.6%) | 12.8 (10.5%) | 10.9 (7.8%) | 4.2 (15.1%) | 34 |
| 1280, tracker, conf 0.4 | 13.1 (7.5%) | 21.9 (4.5%) | **2.0 (0%)** | 30.7 (5.6%) | 14.2 (10.1%) | 4.2 (18.4%) | 111 |
| 1280, no tracker, conf 0.3 | 18.9 (7.3%) | 25.4 (5.6%) | 2.2 (16.3%) | 37.9 (4.3%) | 17.7 (6.9%) | 5.8 (18.4%) | 109 |
| 2x2 tiles + full frame, 640, conf 0.3 | 21.9 (6.6%) | 24.0 (4.3%) | 2.1 (14.3%) | 40.7 (4.1%) | 18.2 (7.2%) | 5.8 (22.6%) | 206 |
| 3x3 tiles + full frame, 640, conf 0.3 | **45.6 (4.7%)** | 26.2 (3.7%) | 2.1 (16.7%) | **43.8 (3.8%)** | 14.9 (10.4%) | 6.0 (22.5%) | 543 |

## By-eye check (written by hand)

3x3 tiles on one frame each: food court 49 boxes, mall 38, 4385 5, overhead 2. Nearly all are real people, including distant heads that no other setup found; no clear false positives seen. Misses remain (people half-hidden behind others, seated people on low benches). Some boxes cover two people: the merge drops a small box mostly inside a bigger one, which is right for a person cut at a tile edge but wrong for someone standing behind them. That undercounts, it doesn't invent people.

The extra people at conf 0.3 without the tracker are mostly real too (4385: the woman at the back of the bar). The tracker's own start threshold (0.25 in `bytetrack.yaml`) and its need for a few frames of history are part of why tracked runs find fewer.

## What to use (decision)

- **Near cameras:** 640 with the tracker. Nothing to gain (4385 is the same at every setting except recall at the far end of the room).
- **High or wide cameras:** 1280 is the best value: most of the gain for ~3x the cost of 640. Overhead views especially need it (flicker drops from 21% to 0 with the tracker).
- **Far, dense scenes** (food courts, big rooms): 3x3 tiles find up to 3.5x more people, at 543 ms/frame. That's too slow at 3 fps even on this laptop, but table states change over tens of seconds (`t_seat` 45 s), so **1 fps is enough** and tiles=3 at 1 fps costs about the same compute as 640 at 3-4 fps... on this laptop. An edge box will be several times slower; measure before promising it.
- Set per camera in the venue config: `imgsz`, `conf`, `tiles`, `fps`.

Not measured: whether more people found actually changes table colours (it should only matter for tables where everyone is small or half-hidden), and any of this on an edge device.

# 2026-11-09: person detection on 6 stock scenes, 640 vs 1280 px

YOLO11s + ByteTrack (`YoloPersonDetector`), conf 0.4, 4 frames/s, M5 Pro (MPS). No ground-truth labels: counts are compared with each other and with a by-eye count. Clips: [docs/datasets.md](../../docs/datasets.md).

| Clip | People found, 640 px (mean, min–max) | 1280 px | Frame-to-frame change 640 → 1280 |
|---|---|---|---|
| Food court (dense, eye level, ~100 visible) | 7.6 (5–11) | 13.1 (9–17) | 13.9% → 7.5% |
| Turkish cafe (high angle) | 16.7 (5–18) | 21.4 (8–26) | 6.7% → 5.5% |
| Overhead cafe (straight down, 2 people) | 1.7 (0–3) | **2.0 (2–2)** | 22.7% → **0%** |
| Mall food court (far, high) | 12.6 (0–19) | 30.1 (0–36) | 11.6% → 7.0% |
| Food market (first 9 s) | 10.5 (1–13) | 13.6 (3–18) | 11.5% → 12.8% |
| Mixkit 4385 (ceiling, near) | 4.1 (0–8) | 4.1 (0–8) | 16.9% → 18.5% |

Time per frame including decoding: about 75–200 ms at 640, 125–165 ms at 1280 on this laptop.

One food-court frame, by eye ~100 people visible:

| | conf 0.4 | conf 0.25 | conf 0.15 |
|---|---|---|---|
| 640 px | 7 | 13 | 13 |
| 1280 px | 15 | 24 | 24 |

Below 0.25 nothing changes: ByteTrack's own threshold for starting a track (0.25 in ultralytics' default `bytetrack.yaml`) is the real floor.

## What this means (written by hand)

- **640 px undercounts badly whenever people are small**: high or distant cameras (mall: 2.4× more people found at 1280) and straight-down views (overhead cafe: flickers 0–3 at 640, rock steady at 1280). Most real CCTV in cafes is high-mounted and wide, so this is the normal case, not an edge case.
- **Near cameras don't care** (Mixkit 4385: same at both sizes). So input size should be a per-camera setting chosen at setup, not a global default.
- **Dense eye-level crowds are out of reach** for this detector: at best ~24 of ~100 people. For table states that's less bad than it sounds (a table needs only *one* person found to count as occupied), but anything that counts heads (group size, staffing) is unreliable in such scenes. Pitch venues with a high camera angle; say no to a single eye-level camera over a packed room.
- Flicker (frame-to-frame change) is what the smoother and the state machine's timers absorb; at 5–15% it's in the range they're built for. 23% (overhead, 640) is not.
- Next: make `imgsz` (and conf) per-camera in the venue config; try tiled inference (SAHI-style) or a larger model for far cameras, and measure the cost on the edge device, since 1280 is roughly 4× the pixels.

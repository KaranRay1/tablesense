# 2026-11-08: detection speed benchmark

Machine: Apple M5 Pro (15 cores, 24 GB), PyTorch 2.15, ultralytics 8.4. 60 frames from the 3 stock clips, 640 px input, person class only. Raw numbers: `2026-11-08_benchmark.json`.

| Model | Device | Median ms/frame | Cameras at 3 fps* |
|---|---|---|---|
| yolo11n | CPU | 15.6 | 21 |
| yolo11n | Apple GPU (MPS) | 7.2 | 46 |
| yolo11s | CPU | 28.7 | 11 |
| yolo11s | Apple GPU (MPS) | 9.5 | 35 |

\* Detection time only, so an upper bound. Video decoding and the rest of the pipeline add a little.

## What this does and doesn't tell us

- **On a strong laptop, one machine could cover a whole venue** (11+ cameras on CPU with the more accurate `yolo11s`).
- **It does NOT tell us what a cheap edge box can do.** Limiting PyTorch to 2 threads gave the same numbers here, because the M5's few fast cores do the work. Low-power mini PCs (the ~€150-300 kind a cafe would accept) have much slower cores, so expect several times slower. That could mean only 1-3 cameras with `yolo11s`.
- Next: run `tools/benchmark.py` on the candidate edge device(s) before quoting hardware to an owner. Options if too slow: `yolo11n` (≈2× faster, less accurate), lower fps (2 fps is enough for table states), or ONNX/OpenVINO export.

"""Measure person-detection speed, and how many cameras one device could handle.

Usage:
    .venv/bin/python tools/benchmark.py --videos data/raw/stock/*.mp4 \\
        --models models/yolo11n.pt models/yolo11s.pt --devices cpu mps --out eval/results/bench.json

Cameras supported = how many streams at --fps the device keeps up with, using
detection time only (decoding and the rest of the pipeline are small but not
zero, so treat it as an upper bound).
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import cv2

from tablesense.detect.people import YoloPersonDetector


def load_frames(paths: list[str], per_video: int) -> list:
    frames = []
    for p in paths:
        cap = cv2.VideoCapture(p)
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        for i in range(per_video):
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(i * n / per_video))
            ok, f = cap.read()
            if ok:
                frames.append(f)
        cap.release()
    return frames


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--videos", nargs="+", required=True)
    ap.add_argument("--models", nargs="+", default=["models/yolo11n.pt", "models/yolo11s.pt"])
    ap.add_argument("--devices", nargs="+", default=["cpu"])
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--frames", type=int, default=20, help="frames per video")
    ap.add_argument("--fps", type=float, default=3.0, help="processing rate per camera")
    ap.add_argument("--threads", type=int, help="limit CPU threads (rough stand-in for a smaller device)")
    ap.add_argument("--out")
    args = ap.parse_args()

    if args.threads:
        import torch

        torch.set_num_threads(args.threads)
    frames = load_frames(args.videos, args.frames)
    rows = []
    for model in args.models:
        for device in args.devices:
            det = YoloPersonDetector(model, imgsz=args.imgsz, device=device, track=False)
            for f in frames[:3]:
                det.detect(f)  # warm-up
            ms = []
            for f in frames:
                t = time.perf_counter()
                det.detect(f)
                ms.append((time.perf_counter() - t) * 1000)
            med = statistics.median(ms)
            row = {
                "model": Path(model).name,
                "device": device,
                "imgsz": args.imgsz,
                "cpu_threads": args.threads,
                "frames": len(ms),
                "median_ms": round(med, 1),
                "p90_ms": round(sorted(ms)[int(0.9 * (len(ms) - 1))], 1),
                "cameras_at_fps": int(1000 / (med * args.fps)),
            }
            rows.append(row)
            print(row)

    result = {
        "machine": f"{platform.machine()} {platform.platform()}",
        "processor": platform.processor(),
        "fps_per_camera": args.fps,
        "results": rows,
    }
    if args.out:
        Path(args.out).write_text(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

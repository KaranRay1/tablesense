"""Run the pipeline on one camera of a venue config.

Example:
    python -m tablesense.cli --config configs/stock_4385.yaml \\
        --out data/out/4385.mp4 --events data/out/events.db
"""

from __future__ import annotations

import argparse
import time
import uuid
from datetime import datetime
from pathlib import Path

from .config import load_venue_config
from .events.log import EventLog
from .ingest.video import is_live, read_frames
from .pipeline import TablePipeline
from .zones.tables import Anchor


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", required=True, help="venue YAML")
    p.add_argument("--camera", help="camera id (default: first camera)")
    p.add_argument("--source", help="override the camera's source (file or rtsp URL)")
    p.add_argument("--fps", type=float, help="override processing fps")
    p.add_argument("--model", default="yolo11s.pt")
    p.add_argument("--conf", type=float, default=0.4, help="detection confidence threshold")
    p.add_argument("--smooth", type=float, default=5.0, help="count smoothing window, seconds")
    p.add_argument("--imgsz", type=int, default=640)
    p.add_argument("--anchor", choices=[a.value for a in Anchor], default=Anchor.CENTER.value)
    p.add_argument("--max-seconds", type=float)
    p.add_argument("--no-tabletop", action="store_true", help="ignore tabletop references (for A/B runs)")
    p.add_argument("--out", help="write an annotated video here")
    p.add_argument("--events", help="SQLite event log path")
    p.add_argument(
        "--start",
        help="wall-clock time of video second 0, ISO format with timezone, e.g. "
        "2026-11-07T18:00:00+00:00 (default: now). Matters for reports on recorded footage.",
    )
    args = p.parse_args(argv)

    venue = load_venue_config(args.config)
    cam = venue.camera(args.camera) if args.camera else venue.cameras[0]
    source = args.source or cam.source
    fps = args.fps or cam.fps
    tables = venue.tables_for(cam.id)
    if not tables:
        p.error(f"camera {cam.id} has no tables in {args.config}")

    from .detect.people import YoloPersonDetector

    from .detect.tabletop import TabletopParams, monitor_from_config

    detector = YoloPersonDetector(args.model, conf=args.conf, imgsz=args.imgsz)
    tabletop = None if args.no_tabletop else monitor_from_config(tables, TabletopParams(**venue.tabletop))
    if tabletop:
        print(f"tabletop check on: {sorted(tabletop.tops)}")
    pipeline = TablePipeline(
        tables,
        detector,
        venue.timers,
        anchor=Anchor(args.anchor),
        smoothing_window=args.smooth,
        tabletop=tabletop,
    )
    started_at = datetime.fromisoformat(args.start).timestamp() if args.start else time.time()
    run_id = uuid.uuid4().hex[:12]
    log, run = None, None
    if args.events:
        log = EventLog(args.events)
        run = log.start_run(
            run_id,
            venue_id=venue.venue_id,
            camera_id=cam.id,
            source="live" if is_live(source) else "replay",
            source_uri=source,
            started_at=started_at,
            model=args.model,
        )
    writer = None

    n, t0 = 0, time.perf_counter()
    try:
        for frame in read_frames(source, sample_fps=fps, max_seconds=args.max_seconds):
            result = pipeline.process(frame.ts, frame.image)
            n += 1
            for tr in result.transitions:
                print(
                    f"[{frame.ts:7.1f}s] {tr.table_id}: {tr.from_state.value} -> "
                    f"{tr.to_state.value} [{tr.reason}] (happened at {tr.at:.1f}s, n={tr.people_count})"
                )
                if log:
                    log.write(run, tr)
            if args.out:
                import cv2

                from .viz import annotate

                img = annotate(frame.image, tables, result)
                if writer is None:
                    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
                    h, w = img.shape[:2]
                    writer = cv2.VideoWriter(args.out, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
                writer.write(img)
    finally:
        if writer is not None:
            writer.release()
        if log:
            log.close()

    dt = time.perf_counter() - t0
    print(f"processed {n} frames in {dt:.1f}s ({n / dt if dt else 0:.1f} fps), run_id={run_id}")
    print("final states:", {tid: m.state.value for tid, m in pipeline.machines.items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

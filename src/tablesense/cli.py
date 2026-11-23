"""Run the pipeline on one camera of a venue config.

Example:
    python -m tablesense.cli --config configs/stock_4385.yaml \\
        --out data/out/4385.mp4 --events data/out/events.db
"""

from __future__ import annotations

import argparse
import json
import os
import time
import uuid
from datetime import datetime
from pathlib import Path

from .config import load_venue_config
from .events.log import EventLog
from .ingest.video import is_live, probe, read_frames
from .pipeline import TablePipeline
from .zones.tables import Anchor


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", required=True, help="venue YAML")
    p.add_argument("--camera", help="camera id (default: first camera)")
    p.add_argument("--source", help="override the camera's source (file or rtsp URL)")
    p.add_argument("--source-env", help="read the source from this environment variable instead "
                   "(keeps camera passwords out of the process list)")
    p.add_argument("--fps", type=float, help="override processing fps")
    p.add_argument("--model", default="yolo11s.pt")
    p.add_argument("--conf", type=float, help="detection confidence threshold (default: camera's conf)")
    p.add_argument("--smooth", type=float, default=5.0, help="count smoothing window, seconds")
    p.add_argument("--imgsz", type=int, help="detector input size (default: camera's imgsz)")
    p.add_argument("--tiles", type=int, help="n x n tiled detection (default: camera's tiles)")
    p.add_argument("--anchor", choices=[a.value for a in Anchor], default=Anchor.CENTER.value)
    p.add_argument("--max-seconds", type=float)
    p.add_argument("--no-tabletop", action="store_true", help="ignore tabletop references (for A/B runs)")
    p.add_argument("--out", help="write an annotated video here")
    p.add_argument("--events", help="SQLite event log path")
    p.add_argument("--status-file", help="keep a JSON status here (progress, fps, people, table states)")
    p.add_argument("--preview", help="write an annotated JPEG here about once a second, only while "
                   "PREVIEW.on was touched in the last 15 s (live preview in the web app)")
    p.add_argument("--keyframes-dir", help="save an annotated JPEG at every table change, plus keyframes.json")
    p.add_argument(
        "--start",
        help="wall-clock time of video second 0, ISO format with timezone, e.g. "
        "2026-11-07T18:00:00+00:00 (default: now). Matters for reports on recorded footage.",
    )
    args = p.parse_args(argv)

    venue = load_venue_config(args.config)
    cam = venue.camera(args.camera) if args.camera else venue.cameras[0]
    source = os.environ.get(args.source_env, "") if args.source_env else (args.source or cam.source)
    if not source:
        p.error(f"no source: ${args.source_env} is empty" if args.source_env else "no source")
    fps = args.fps or cam.fps
    tables = venue.tables_for(cam.id)
    if not tables:
        p.error(f"camera {cam.id} has no tables in {args.config}")

    from .detect.people import TiledPersonDetector, YoloPersonDetector
    from .detect.tabletop import TabletopParams, monitor_from_config

    tiles = args.tiles or cam.tiles
    conf, imgsz = args.conf or cam.conf, args.imgsz or cam.imgsz
    if tiles > 1:
        detector = TiledPersonDetector(args.model, conf=conf, imgsz=imgsz, tiles=tiles)
    else:
        detector = YoloPersonDetector(args.model, conf=conf, imgsz=imgsz)
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
    live = is_live(source)
    status = Status(args.status_file, camera=cam.id, live=live, fps_target=fps, run_id=run_id)
    keyframes = Keyframes(args.keyframes_dir) if args.keyframes_dir else None
    if not live and args.status_file:
        try:
            status.data["duration"] = probe(source).duration
        except OSError:
            pass

    n, t0 = 0, time.perf_counter()
    last_preview = 0.0
    try:
        for frame in read_frames(source, sample_fps=fps, max_seconds=args.max_seconds):
            result = pipeline.process(frame.ts, frame.image)
            n += 1
            for tr in result.transitions:
                print(
                    f"[{frame.ts:7.1f}s] {tr.table_id}: {tr.from_state.value} -> "
                    f"{tr.to_state.value} [{tr.reason}] (happened at {tr.at:.1f}s, n={tr.people_count})",
                    flush=True,
                )
                if log:
                    log.write(run, tr)
            want_preview = args.preview and time.time() - last_preview >= 1.0 and _flag_fresh(args.preview + ".on")
            if args.out or want_preview or (keyframes and (result.transitions or n == 1)):
                from .viz import annotate

                img = annotate(frame.image, tables, result)
                if keyframes:
                    if n == 1:
                        keyframes.add(img, frame.ts, None)
                    for tr in result.transitions:
                        keyframes.add(img, frame.ts, tr)
                if want_preview:
                    _write_jpeg(args.preview, img)
                    last_preview = time.time()
                if args.out:
                    import cv2

                    if writer is None:
                        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
                        h, w = img.shape[:2]
                        writer = cv2.VideoWriter(args.out, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
                    writer.write(img)
            status.frame(n, frame.ts, time.perf_counter() - t0, result)
        status.finish("lost" if live else "done")
    except OSError as e:
        status.finish("error", str(e).replace(source, "<source>"))
        print(f"error: {status.data['error']}", flush=True)
        return 2
    except KeyboardInterrupt:
        status.finish("stopped")
    finally:
        if writer is not None:
            writer.release()
        if log:
            log.close()
        if args.preview:
            Path(args.preview).unlink(missing_ok=True)

    dt = time.perf_counter() - t0
    print(f"processed {n} frames in {dt:.1f}s ({n / dt if dt else 0:.1f} fps), run_id={run_id}")
    print("final states:", {tid: m.state.value for tid, m in pipeline.machines.items()})
    return 0


def _flag_fresh(path: str, max_age_s: float = 15.0) -> bool:
    try:
        return time.time() - os.path.getmtime(path) < max_age_s
    except OSError:
        return False


def _atomic_write(path: str | Path, data: bytes) -> None:
    tmp = f"{path}.tmp{os.getpid()}"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def _write_jpeg(path: str | Path, img, quality: int = 80) -> None:
    import cv2

    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if ok:
        _atomic_write(path, buf.tobytes())


class Status:
    """A small JSON file the web app reads to show progress and live numbers."""

    def __init__(self, path: str | None, **info) -> None:
        self.path = path
        self.started = time.time()
        self.data = {"state": "starting", "frames": 0, "video_ts": 0.0, "proc_fps": 0.0, "duration": None,
                     "people": 0, "max_people": 0, "counts": {}, "states": {}, "error": None,
                     "started_at": self.started, "updated_at": self.started, **info}
        self._last = 0.0
        self._write()

    def frame(self, n: int, ts: float, elapsed: float, result) -> None:
        people = sum(1 for t in result.table_of_person if t) if result.table_of_person else 0
        self.data.update(state="running", frames=n, video_ts=round(ts, 2), proc_fps=round(n / elapsed, 2) if elapsed else 0.0,
                         people=len(result.people), people_at_tables=people,
                         max_people=max(self.data["max_people"], len(result.people)),
                         counts=dict(result.counts), states={k: v.value for k, v in result.states.items()})
        if time.time() - self._last >= 1.0:
            self._write()

    def finish(self, state: str, error: str | None = None) -> None:
        self.data.update(state=state, error=error)
        self._write()

    def _write(self) -> None:
        self._last = self.data["updated_at"] = time.time()
        if self.path:
            _atomic_write(self.path, json.dumps(self.data).encode())


class Keyframes:
    """An annotated still at every table change: visual proof for a clip check."""

    MAX = 400

    def __init__(self, directory: str) -> None:
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.items: list[dict] = []

    def add(self, img, ts: float, tr) -> None:
        if len(self.items) >= self.MAX:
            return
        i = len(self.items) + 1
        _write_jpeg(self.dir / f"kf-{i:04d}.jpg", img, quality=75)
        self.items.append({"i": i, "ts": round(ts, 2), "table": tr.table_id if tr else None,
                           "from": tr.from_state.value if tr else None, "to": tr.to_state.value if tr else None,
                           "reason": tr.reason if tr else "start", "at": round(tr.at, 2) if tr else round(ts, 2)})
        _atomic_write(self.dir / "keyframes.json", json.dumps(self.items).encode())


if __name__ == "__main__":
    raise SystemExit(main())

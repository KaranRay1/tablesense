"""Read frames from a video file or an RTSP stream at a reduced rate."""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any


@dataclass
class Frame:
    ts: float  # seconds: video time for files, time since start for live streams
    index: int  # frame index in the source
    image: Any  # BGR numpy array


def is_live(source: str) -> bool:
    return source.startswith(("rtsp://", "rtsps://", "http://", "https://"))


def read_frames(
    source: str,
    sample_fps: float | None = None,
    max_seconds: float | None = None,
    reconnect_attempts: int = 5,
    reconnect_wait: float = 2.0,
) -> Iterator[Frame]:
    """Yield frames at roughly `sample_fps` (None = every frame).

    Table states change over seconds, so 2-5 fps is plenty and keeps the edge
    device's load low. Live streams reconnect automatically when they drop.
    """
    import cv2

    live = is_live(source)
    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        raise OSError(f"cannot open video source: {source}")

    native_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    interval = 0.0 if not sample_fps else 1.0 / sample_fps
    start = time.monotonic()
    next_ts = 0.0
    index = -1
    failures = 0

    try:
        while True:
            ok, image = cap.read()
            if not ok:
                if not live or failures >= reconnect_attempts:
                    return
                failures += 1
                cap.release()
                time.sleep(reconnect_wait)
                cap = cv2.VideoCapture(source)
                continue
            failures = 0
            index += 1
            ts = time.monotonic() - start if live else index / native_fps
            if max_seconds is not None and ts > max_seconds:
                return
            if ts + 1e-9 < next_ts:
                continue
            next_ts = ts + interval
            yield Frame(ts=ts, index=index, image=image)
    finally:
        cap.release()

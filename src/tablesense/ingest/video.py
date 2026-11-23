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


@dataclass
class SourceInfo:
    width: int
    height: int
    fps: float
    duration: float | None  # seconds; None for live streams


def probe(source: str, open_timeout_s: float = 10.0) -> SourceInfo:
    """Open a source once and read its size, frame rate and (for files) length."""
    import cv2

    cap = cv2.VideoCapture(source, cv2.CAP_FFMPEG, [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, int(open_timeout_s * 1000)])
    try:
        if not cap.isOpened():
            raise OSError("cannot open video source")
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        n = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        duration = None if is_live(source) or n <= 0 else n / fps
        return SourceInfo(int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)), fps, duration)
    finally:
        cap.release()


def grab_frame(source: str, at_seconds: float = 0.0, open_timeout_s: float = 10.0) -> Any:
    """One BGR frame: `at_seconds` into a file, or the next frame of a stream."""
    import cv2

    cap = cv2.VideoCapture(source, cv2.CAP_FFMPEG, [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, int(open_timeout_s * 1000),
                                                    cv2.CAP_PROP_READ_TIMEOUT_MSEC, int(open_timeout_s * 1000)])
    try:
        if not cap.isOpened():
            raise OSError("cannot open video source")
        if at_seconds and not is_live(source):
            cap.set(cv2.CAP_PROP_POS_MSEC, at_seconds * 1000)
        ok, image = cap.read()
        if not ok and at_seconds and not is_live(source):  # clip shorter than at_seconds
            cap.set(cv2.CAP_PROP_POS_MSEC, 0)
            ok, image = cap.read()
        if not ok:
            raise OSError("opened the source but could not read a frame")
        return image
    finally:
        cap.release()


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

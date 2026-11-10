"""Person detection + tracking.

The pipeline depends only on the `Detector` protocol, so tests can use a fake
and the model can be swapped (fine-tuned weights, ONNX on an edge device)
without touching anything else.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from ..zones.tables import Box


@dataclass(frozen=True)
class Person:
    box: Box
    confidence: float
    track_id: int | None = None


class Detector(Protocol):
    def detect(self, image: Any) -> list[Person]: ...


PERSON_CLASS_ID = 0  # COCO "person"


class YoloPersonDetector:
    """YOLO person detector with ultralytics' built-in ByteTrack.

    Model weights are downloaded by ultralytics on first use if `model` is a
    bare name like "yolo11s.pt".

    Note: ByteTrack's lost-track buffer is counted in frames (30 by default),
    so at 3 fps a track survives ~10 s of occlusion.
    """

    def __init__(
        self,
        model: str = "yolo11s.pt",
        conf: float = 0.4,
        imgsz: int = 640,
        device: str | None = None,
        track: bool = True,
    ) -> None:
        from ultralytics import YOLO

        self.model = YOLO(model)
        self.conf = conf
        self.imgsz = imgsz
        self.device = device
        self.track = track

    def detect(self, image: Any) -> list[Person]:
        kwargs = dict(
            conf=self.conf,
            imgsz=self.imgsz,
            classes=[PERSON_CLASS_ID],
            device=self.device,
            verbose=False,
        )
        if self.track:
            result = self.model.track(image, persist=True, tracker="bytetrack.yaml", **kwargs)[0]
        else:
            result = self.model.predict(image, **kwargs)[0]
        boxes = result.boxes
        if boxes is None or len(boxes) == 0:
            return []
        ids = boxes.id.int().tolist() if boxes.id is not None else [None] * len(boxes)
        return [
            Person(box=tuple(float(v) for v in xyxy), confidence=float(c), track_id=tid)
            for xyxy, c, tid in zip(boxes.xyxy.tolist(), boxes.conf.tolist(), ids)
        ]


# --- tiled detection ---------------------------------------------------------
# Small, distant people (high or wide cameras) get lost when a 1280x720 frame
# is shrunk to the model's input size. Running the model on overlapping tiles
# makes them bigger. A full-frame pass is kept so people larger than a tile
# are still found whole. See eval/results/2026-11-10_tiled_detection.md.


def tile_grid(width: int, height: int, n: int, overlap: float = 0.2) -> list[tuple[int, int, int, int]]:
    """n x n overlapping tiles covering the frame, as (x1, y1, x2, y2)."""
    if n < 1:
        raise ValueError("n must be >= 1")
    if not 0 <= overlap < 1:
        raise ValueError("overlap must be in [0, 1)")
    if n == 1:
        return [(0, 0, width, height)]

    def spans(size: int) -> list[tuple[int, int]]:
        tile = size / (n - (n - 1) * overlap)
        step = tile * (1 - overlap)
        out = []
        for i in range(n):
            a = round(i * step)
            b = size if i == n - 1 else min(size, round(i * step + tile))
            out.append((a, b))
        return out

    return [(x1, y1, x2, y2) for y1, y2 in spans(height) for x1, x2 in spans(width)]


def _area(b: Box) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _inter(a: Box, b: Box) -> float:
    return _area((max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])))


def merge_people(people: list[Person], iou: float = 0.5, ios: float = 0.7) -> list[Person]:
    """Greedy merge of detections from overlapping tiles.

    Drops a box when it overlaps a kept one by IoU > `iou`, or when most of
    the smaller box lies inside the other (intersection over smaller area >
    `ios`): that's a person cut at a tile edge and found again whole. When
    two boxes are duplicates, the larger (most complete) one is kept.
    """
    order = sorted(people, key=lambda p: (_area(p.box), p.confidence), reverse=True)
    kept: list[Person] = []
    for p in order:
        dup = False
        for k in kept:
            inter = _inter(p.box, k.box)
            if inter <= 0:
                continue
            union = _area(p.box) + _area(k.box) - inter
            smaller = min(_area(p.box), _area(k.box)) or 1.0
            if inter / union > iou or inter / smaller > ios:
                dup = True
                break
        if not dup:
            kept.append(p)
    return kept


class TiledPersonDetector:
    """YOLO on an n x n grid of overlapping tiles plus the full frame, merged.

    No tracking (track ids are None): the pipeline only needs counts, and
    ByteTrack can't follow ids across tiles. Costs about n*n + 1 model runs
    per frame, batched into one call.
    """

    def __init__(
        self,
        model: str = "yolo11s.pt",
        conf: float = 0.3,
        imgsz: int = 640,
        tiles: int = 2,
        overlap: float = 0.2,
        device: str | None = None,
    ) -> None:
        from ultralytics import YOLO

        self.model = YOLO(model)
        self.conf = conf
        self.imgsz = imgsz
        self.tiles = tiles
        self.overlap = overlap
        self.device = device

    def detect(self, image: Any) -> list[Person]:
        h, w = image.shape[:2]
        regions = [(0, 0, w, h)] + tile_grid(w, h, self.tiles, self.overlap)
        crops = [image[y1:y2, x1:x2] for x1, y1, x2, y2 in regions]
        results = self.model.predict(
            crops,
            conf=self.conf,
            imgsz=self.imgsz,
            classes=[PERSON_CLASS_ID],
            device=self.device,
            verbose=False,
        )
        found: list[Person] = []
        for (x1, y1, _, _), r in zip(regions, results):
            if r.boxes is None:
                continue
            for xyxy, c in zip(r.boxes.xyxy.tolist(), r.boxes.conf.tolist()):
                bx1, by1, bx2, by2 = xyxy
                found.append(Person((bx1 + x1, by1 + y1, bx2 + x1, by2 + y1), float(c)))
        return merge_people(found)


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

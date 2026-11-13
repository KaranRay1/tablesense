"""Does a tabletop look clear? Compare it with a reference image of the same
table when it was clean and set.

Why: from people counts alone, "a waiter cleared the table" and "a guest came
back for their coat" look identical. The tabletop tells them apart: plates,
glasses and paper show up as a difference from the clean reference.

No model needed: per-pixel difference on blurred grayscale, after matching
overall brightness (lighting drifts during the day). Pixels under detected
people are left out, since arms and bodies would read as clutter; if less
than `min_visible` of the tabletop is left, the reading is None (unknown).

Known limits (measure on real footage): a stale reference (new tablecloth,
moved table, camera knocked) reads as permanently dirty; bright sun patches
can read as clutter; small items (a phone, one glass) may fall under the
threshold.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

Point = tuple[float, float]
Box = tuple[float, float, float, float]


@dataclass(frozen=True)
class TabletopParams:
    pixel_diff: float = 0.35  # relative brightness change that counts as "different"
    clear_below: float = 0.25  # clear if fewer than this fraction of pixels differ
    min_visible: float = 0.5  # fraction of the tabletop that must be unblocked

    def __post_init__(self) -> None:
        if not 0 < self.pixel_diff:
            raise ValueError("pixel_diff must be > 0")
        for name in ("clear_below", "min_visible"):
            if not 0 < getattr(self, name) <= 1:
                raise ValueError(f"{name} must be in (0, 1]")


def polygon_bbox(polygon: Sequence[Point]) -> tuple[int, int, int, int]:
    xs, ys = zip(*polygon)
    return int(min(xs)), int(min(ys)), int(max(xs)) + 1, int(max(ys)) + 1


def prepare(image: Any, polygon: Sequence[Point]) -> Any:
    """Blurred grayscale crop of the polygon's bounding box, as float32."""
    import cv2
    import numpy as np

    x1, y1, x2, y2 = polygon_bbox(polygon)
    crop = image[y1:y2, x1:x2]
    if crop.ndim == 3:
        crop = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    return cv2.GaussianBlur(crop, (5, 5), 0).astype(np.float32)


def polygon_mask(polygon: Sequence[Point]) -> Any:
    import cv2
    import numpy as np

    x1, y1, x2, y2 = polygon_bbox(polygon)
    mask = np.zeros((y2 - y1, x2 - x1), np.uint8)
    pts = np.array([(x - x1, y - y1) for x, y in polygon], np.int32)
    cv2.fillPoly(mask, [pts], 1)
    return mask.astype(bool)


def clutter_score(crop: Any, reference: Any, mask: Any, pixel_diff: float) -> float:
    """Fraction of tabletop pixels that differ from the clean reference."""
    import numpy as np

    if crop.shape != reference.shape:
        raise ValueError(f"crop {crop.shape} and reference {reference.shape} differ in size")
    cur = crop / max(float(np.median(crop[mask])), 1.0)
    ref = reference / max(float(np.median(reference[mask])), 1.0)
    return float((np.abs(cur - ref)[mask] > pixel_diff).mean())


class TabletopMonitor:
    """Per-table clear/cluttered readings for one camera."""

    def __init__(
        self,
        tops: Mapping[str, Sequence[Point]],
        references: Mapping[str, Any],
        params: TabletopParams | None = None,
    ) -> None:
        missing = set(tops) ^ set(references)
        if missing:
            raise ValueError(f"tables need both a top polygon and a reference: {sorted(missing)}")
        self.params = params or TabletopParams()
        self.tops = {tid: tuple(p) for tid, p in tops.items()}
        self.bboxes = {tid: polygon_bbox(p) for tid, p in self.tops.items()}
        self.masks = {tid: polygon_mask(p) for tid, p in self.tops.items()}
        self.refs = dict(references)
        for tid, ref in self.refs.items():
            x1, y1, x2, y2 = self.bboxes[tid]
            if ref.shape[:2] != (y2 - y1, x2 - x1):
                raise ValueError(
                    f"table {tid}: reference is {ref.shape[1]}x{ref.shape[0]}, "
                    f"top polygon box is {x2 - x1}x{y2 - y1}; recapture it"
                )
        self.last_scores: dict[str, float | None] = {tid: None for tid in self.tops}

    def visible_mask(self, tid: str, person_boxes: Sequence[Box]) -> Any:
        """The tabletop mask minus the pixels under any person's box."""
        x1, y1, _, _ = self.bboxes[tid]
        mask = self.masks[tid].copy()
        h, w = mask.shape
        for bx1, by1, bx2, by2 in person_boxes:
            c1, r1 = max(int(bx1) - x1, 0), max(int(by1) - y1, 0)
            c2, r2 = min(int(bx2) - x1 + 1, w), min(int(by2) - y1 + 1, h)
            if c1 < c2 and r1 < r2:
                mask[r1:r2, c1:c2] = False
        return mask

    def check(self, image: Any, person_boxes: Sequence[Box]) -> dict[str, bool | None]:
        out: dict[str, bool | None] = {}
        for tid, poly in self.tops.items():
            mask = self.visible_mask(tid, person_boxes)
            if mask.sum() < self.params.min_visible * self.masks[tid].sum():
                self.last_scores[tid] = None
                out[tid] = None
                continue
            score = clutter_score(prepare(image, poly), self.refs[tid], mask, self.params.pixel_diff)
            self.last_scores[tid] = score
            out[tid] = score < self.params.clear_below
        return out


def load_reference(path: str) -> Any:
    import cv2
    import numpy as np

    img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise OSError(f"cannot read tabletop reference {path}; capture it with tools/capture_reference.py")
    return img.astype(np.float32)


def monitor_from_config(tables: Sequence[Any], params: TabletopParams | None = None) -> TabletopMonitor | None:
    """Build a monitor for the tables that have `top` and `reference` set (None if none do)."""
    with_top = [t for t in tables if t.top and t.reference]
    if not with_top:
        return None
    return TabletopMonitor(
        {t.id: t.top for t in with_top},
        {t.id: load_reference(t.reference) for t in with_top},
        params,
    )

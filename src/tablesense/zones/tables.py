"""Map detected people to table zones. Pure geometry, no CV dependency."""

from __future__ import annotations

from enum import Enum
from typing import Iterable, Sequence

from ..config import Point, TableConfig

Box = tuple[float, float, float, float]  # x1, y1, x2, y2 in pixels


class Anchor(str, Enum):
    """Which point of a person's box decides which table they're at.

    CENTER suits ceiling cameras looking down. BOTTOM_CENTER (roughly where
    they sit or stand) suits angled cameras, where the box center of a
    seated person can fall on the table behind them.
    """

    CENTER = "center"
    BOTTOM_CENTER = "bottom_center"


def anchor_point(box: Box, anchor: Anchor = Anchor.CENTER) -> Point:
    x1, y1, x2, y2 = box
    cx = (x1 + x2) / 2
    if anchor is Anchor.BOTTOM_CENTER:
        return cx, y2
    return cx, (y1 + y2) / 2


def point_in_polygon(x: float, y: float, polygon: Sequence[Point]) -> bool:
    """Ray casting. Points exactly on an edge may go either way."""
    inside = False
    n = len(polygon)
    for i in range(n):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            x_cross = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < x_cross:
                inside = not inside
    return inside


def _centroid(polygon: Sequence[Point]) -> Point:
    xs, ys = zip(*polygon)
    return sum(xs) / len(xs), sum(ys) / len(ys)


def assign_people(
    boxes: Iterable[Box],
    tables: Sequence[TableConfig],
    anchor: Anchor = Anchor.CENTER,
) -> list[str | None]:
    """For each box, the id of the table it belongs to (or None).

    A person is counted at one table at most. If zones overlap, the table
    whose centroid is nearest wins.
    """
    centroids = {t.id: _centroid(t.polygon) for t in tables}
    out: list[str | None] = []
    for box in boxes:
        x, y = anchor_point(box, anchor)
        hits = [t.id for t in tables if point_in_polygon(x, y, t.polygon)]
        if not hits:
            out.append(None)
        else:
            out.append(
                min(hits, key=lambda tid: (centroids[tid][0] - x) ** 2 + (centroids[tid][1] - y) ** 2)
            )
    return out


def count_people(
    boxes: Iterable[Box],
    tables: Sequence[TableConfig],
    anchor: Anchor = Anchor.CENTER,
) -> dict[str, int]:
    counts = {t.id: 0 for t in tables}
    for tid in assign_people(boxes, tables, anchor):
        if tid is not None:
            counts[tid] += 1
    return counts

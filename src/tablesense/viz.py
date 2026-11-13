"""Draw table zones, states and people on a frame (for debugging and demos)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .config import TableConfig
from .pipeline import FrameResult
from .state import COLOR_OF, Color

BGR = {
    Color.GREEN: (80, 200, 60),
    Color.RED: (50, 50, 230),
    Color.YELLOW: (0, 210, 240),
}
PERSON_IN_ZONE = (255, 200, 0)
PERSON_OUTSIDE = (160, 160, 160)


def annotate(image: Any, tables: Sequence[TableConfig], result: FrameResult) -> Any:
    import cv2
    import numpy as np

    out = image.copy()
    overlay = image.copy()
    for t in tables:
        color = BGR[COLOR_OF[result.states[t.id]]]
        pts = np.array(t.polygon, dtype=np.int32)
        cv2.fillPoly(overlay, [pts], color)
    out = cv2.addWeighted(overlay, 0.3, out, 0.7, 0)

    for t in tables:
        state = result.states[t.id]
        color = BGR[COLOR_OF[state]]
        pts = np.array(t.polygon, dtype=np.int32)
        cv2.polylines(out, [pts], True, color, 2)
        x, y = pts.min(axis=0)
        label = f"{t.id} {state.value} n={result.counts[t.id]}"
        top = result.top_clear.get(t.id, "off")
        if top != "off":
            label += " top=" + {True: "clear", False: "dirty", None: "?"}[top]
        if t.top:
            cv2.polylines(out, [np.array(t.top, dtype=np.int32)], True, (255, 255, 255), 1)
        _label(out, label, (int(x), max(14, int(y) - 6)), color)

    for person, tid in zip(result.people, result.table_of_person):
        x1, y1, x2, y2 = (int(v) for v in person.box)
        color = PERSON_IN_ZONE if tid else PERSON_OUTSIDE
        cv2.rectangle(out, (x1, y1), (x2, y2), color, 1)
        tag = f"#{person.track_id}" if person.track_id is not None else ""
        if tag:
            _label(out, tag, (x1, max(12, y1 - 3)), color, scale=0.4)

    _label(out, f"t={result.ts:6.1f}s  people={len(result.people)}", (8, 20), (255, 255, 255))
    return out


def _label(img: Any, text: str, org: tuple[int, int], color, scale: float = 0.5) -> None:
    import cv2

    (w, h), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 1)
    x, y = org
    cv2.rectangle(img, (x - 2, y - h - 3), (x + w + 2, y + 3), (0, 0, 0), -1)
    cv2.putText(img, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale, color, 1, cv2.LINE_AA)

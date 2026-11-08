"""Smoke test for the real YOLO detector. Skipped unless the CV extras, the
weights in models/ and the stock clip are all present (they're not in git)."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CLIP = ROOT / "data/raw/stock/mixkit-4385-720.mp4"
WEIGHTS = ROOT / "models/yolo11s.pt"


@pytest.mark.skipif(not (CLIP.exists() and WEIGHTS.exists()), reason="clip or weights missing")
def test_yolo_finds_seated_people_in_the_dining_row():
    cv2 = pytest.importorskip("cv2")
    pytest.importorskip("ultralytics")
    from tablesense.detect.people import YoloPersonDetector

    cap = cv2.VideoCapture(str(CLIP))
    ok, frame = cap.read()
    cap.release()
    assert ok

    people = YoloPersonDetector(str(WEIGHTS), track=False).detect(frame)
    in_row = [p for p in people if 600 <= (p.box[0] + p.box[2]) / 2 <= 1060]
    # 4 people are seated at T2 and T3 in the first frame.
    assert len(in_row) >= 3
    assert all(0.0 < p.confidence <= 1.0 for p in people)

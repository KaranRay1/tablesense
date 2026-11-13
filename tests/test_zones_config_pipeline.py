"""Zones, config, event log and the full pipeline with a fake detector (no CV needed)."""

from pathlib import Path

import pytest

from tablesense.config import TableConfig, parse_venue_config
from tablesense.detect.people import Person
from tablesense.events.log import EventLog
from tablesense.pipeline import TablePipeline
from tablesense.state import State, Timers
from tablesense.zones.tables import (
    Anchor,
    anchor_point,
    assign_people,
    count_people,
    point_in_polygon,
)

ROOT = Path(__file__).resolve().parents[1]
SQUARE = ((0, 0), (100, 0), (100, 100), (0, 100))
T1 = TableConfig("T1", "cam", SQUARE)
T2 = TableConfig("T2", "cam", ((200, 0), (300, 0), (300, 100), (200, 100)))


def box_at(x, y, half=10):
    return (x - half, y - half, x + half, y + half)


# --- geometry ----------------------------------------------------------------


def test_point_in_polygon():
    assert point_in_polygon(50, 50, SQUARE)
    assert not point_in_polygon(150, 50, SQUARE)
    assert not point_in_polygon(-1, 50, SQUARE)
    tri = ((0, 0), (100, 0), (0, 100))
    assert point_in_polygon(10, 10, tri)
    assert not point_in_polygon(90, 90, tri)


def test_anchor_points():
    box = (10, 20, 30, 60)
    assert anchor_point(box, Anchor.CENTER) == (20, 40)
    assert anchor_point(box, Anchor.BOTTOM_CENTER) == (20, 60)


def test_count_people_per_table():
    boxes = [box_at(50, 50), box_at(60, 40), box_at(250, 50), box_at(150, 50)]
    assert count_people(boxes, [T1, T2]) == {"T1": 2, "T2": 1}


def test_person_in_overlapping_zones_counted_once_at_nearest_table():
    a = TableConfig("A", "cam", ((0, 0), (100, 0), (100, 100), (0, 100)))
    b = TableConfig("B", "cam", ((80, 0), (180, 0), (180, 100), (80, 100)))
    # Centroids: A at x=50, B at x=130. Overlap is x in [80, 100].
    assert assign_people([box_at(85, 50)], [a, b]) == ["A"]
    assert assign_people([box_at(95, 50)], [a, b]) == ["B"]


# --- config ------------------------------------------------------------------


def test_example_config_parses():
    yaml = pytest.importorskip("yaml")
    raw = yaml.safe_load((ROOT / "configs" / "example_venue.yaml").read_text())
    venue = parse_venue_config(raw)
    assert venue.venue_id == "example_cafe"
    assert [t.id for t in venue.tables_for("cam_floor_1")] == ["T1", "T2"]
    assert venue.timers.t_away == 240


@pytest.mark.parametrize(
    "mutate, msg",
    [
        (lambda r: r["tables"][0].update(camera="nope"), "unknown camera"),
        (lambda r: r["tables"][0].update(polygon=[[0, 0], [1, 1]]), "at least 3"),
        (lambda r: r["tables"].append(dict(r["tables"][0])), "duplicate table"),
        (lambda r: r.pop("venue_id"), "missing key"),
    ],
)
def test_bad_config_rejected(mutate, msg):
    raw = {
        "venue_id": "v",
        "cameras": [{"id": "cam", "source": "x.mp4"}],
        "tables": [{"id": "T1", "camera": "cam", "polygon": [[0, 0], [1, 0], [1, 1]]}],
    }
    mutate(raw)
    with pytest.raises(ValueError, match=msg):
        parse_venue_config(raw)


# --- pipeline with a scripted fake detector ----------------------------------


class ScriptedDetector:
    """Returns people according to a function of time; ignores the image."""

    def __init__(self, script):
        self.script = script
        self.ts = 0.0

    def detect(self, image):
        return [Person(box=b, confidence=0.9, track_id=i) for i, b in enumerate(self.script(self.ts))]


def run(pipeline, detector, seconds):
    transitions = []
    for ts in range(seconds):
        detector.ts = ts
        transitions += pipeline.process(ts, image=None).transitions
    return transitions


def test_pipeline_full_cycle_at_one_table_while_other_stays_empty():
    def script(t):
        if t < 600:
            return [box_at(40, 40), box_at(60, 60), box_at(150, 50)]  # 2 at T1, 1 in the aisle
        if 900 <= t < 930:
            return [box_at(50, 50)]  # waiter clears T1
        return []

    det = ScriptedDetector(script)
    pipe = TablePipeline([T1, T2], det, Timers())
    trs = run(pipe, det, 1000)
    assert [(t.table_id, t.to_state) for t in trs] == [
        ("T1", State.SEATED),
        ("T1", State.AWAY),
        ("T1", State.NEEDS_CLEARING),
        ("T1", State.EMPTY),
    ]
    assert trs[0].at == 0
    assert trs[2].at == pytest.approx(600, abs=3)  # left time, after smoothing lag
    assert pipe.machines["T2"].state is State.EMPTY


def test_pipeline_smooths_missed_detections():
    det = ScriptedDetector(lambda t: [] if t % 6 == 0 else [box_at(50, 50)])
    pipe = TablePipeline([T1], det, Timers())
    trs = run(pipe, det, 900)
    assert [t.to_state for t in trs] == [State.SEATED]


def test_pipeline_requires_tables():
    with pytest.raises(ValueError):
        TablePipeline([], ScriptedDetector(lambda t: []))


# --- event log ---------------------------------------------------------------


def test_event_log_roundtrip():
    det = ScriptedDetector(lambda t: [box_at(50, 50)] if t < 100 else [])
    pipe = TablePipeline([T1], det, Timers())
    log = EventLog(":memory:")
    r = log.start_run(
        "r1", venue_id="v", camera_id="cam", source="replay",
        source_uri="rtsp://user:secret@10.0.0.5/s1", started_at=1_000_000.0,
    )
    log.write_many(r, run(pipe, det, 400))
    events = log.events("r1")
    assert [e.to_state for e in events] == [State.SEATED, State.AWAY, State.NEEDS_CLEARING]
    assert [e.reason for e in events] == ["seated", "absent", "hold_expired"]
    assert events[0].at == 1_000_000.0  # stored as wall-clock epoch
    assert log.events("other") == []
    assert log.run().source_uri == "rtsp://10.0.0.5/s1"  # credentials never stored
    log.close()

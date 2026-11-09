"""Tabletop check: the monitor itself, and how the state machine uses it."""

import numpy as np
import pytest

from tablesense.config import TableConfig, parse_venue_config
from tablesense.detect.people import Person
from tablesense.detect.tabletop import TabletopMonitor, TabletopParams, clutter_score, polygon_mask, prepare
from tablesense.pipeline import TablePipeline
from tablesense.state import State, TableStateMachine, Timers


def feed(machine, segments, start=0):
    """Segments of (duration_s, people, top_clear), one update per second."""
    trs, ts = [], start
    for duration, count, top in segments:
        for _ in range(duration):
            tr = machine.update(ts, count, top)
            if tr:
                trs.append(tr)
            ts += 1
    return trs, ts


# --- state machine -----------------------------------------------------------


def test_fast_clear_during_hold_goes_green_not_yellow():
    """Guests leave; a waiter clears within the hold (seen as 'returned');
    then nobody. Without the tabletop: yellow on a clean table, green only
    after 20 min. With it: green when the hold ends."""
    m = TableStateMachine("T1")
    scene = [(100, 2, False), (30, 0, False), (40, 1, None), (300, 0, True)]
    trs, _ = feed(m, scene)
    assert [t.reason for t in trs] == ["seated", "absent", "returned", "absent", "cleared_in_hold"]
    assert m.state is State.EMPTY
    # The hold still ran in full from when the waiter left.
    assert trs[-1].detected_at - trs[-2].at == pytest.approx(240)

    m = TableStateMachine("T1")
    trs, _ = feed(m, [(a, b, None) for a, b, _ in scene])
    assert m.state is State.NEEDS_CLEARING  # old behaviour, unchanged without the signal


def test_hold_is_not_cut_short_by_a_clear_table():
    """Guests' plates cleared, they step out for a smoke: still red for the whole hold."""
    m = TableStateMachine("T1")
    trs, ts = feed(m, [(100, 2, False), (200, 0, True)])
    assert m.state is State.AWAY
    trs, _ = feed(m, [(30, 2, None)], start=ts)  # they come back
    assert m.state is State.SEATED


def test_dirty_table_still_goes_yellow():
    m = TableStateMachine("T1")
    feed(m, [(100, 2, False), (300, 0, False)])
    assert m.state is State.NEEDS_CLEARING


def test_yellow_turns_green_when_top_seen_clear():
    """A clear the people count missed (too short, or the waiter wasn't detected)."""
    m = TableStateMachine("T1", Timers(t_top_clear=15))
    trs, ts = feed(m, [(100, 2, False), (300, 0, False), (5, 0, None)])
    assert m.state is State.NEEDS_CLEARING
    trs, _ = feed(m, [(20, 0, True)], start=ts)
    assert [t.reason for t in trs] == ["looks_clear"]
    assert trs[0].at == ts and trs[0].detected_at == ts + 15


def test_one_clear_reading_is_not_enough():
    m = TableStateMachine("T1")
    feed(m, [(100, 2, False), (300, 0, False), (5, 0, True), (1, 0, False), (5, 0, True)])
    assert m.state is State.NEEDS_CLEARING


def test_unknown_readings_keep_the_last_one():
    m = TableStateMachine("T1", Timers(t_top_clear=10))
    trs, _ = feed(m, [(100, 2, False), (300, 0, False), (3, 0, True), (20, 0, None)])
    assert trs[-1].reason == "looks_clear"


# --- monitor -----------------------------------------------------------------

TOP = ((10, 10), (60, 10), (60, 40), (10, 40))


def frame(clutter=False, brightness=60):
    img = np.full((100, 100, 3), brightness, np.uint8)
    if clutter:
        img[15:35, 15:45] = 230  # a white plate
    return img


def test_clutter_score_ignores_overall_lighting():
    ref = prepare(frame(), TOP)
    mask = polygon_mask(TOP)
    assert clutter_score(prepare(frame(brightness=90), TOP), ref, mask, 0.35) < 0.01
    assert clutter_score(prepare(frame(clutter=True), TOP), ref, mask, 0.35) > 0.3


def test_monitor_reads_clear_dirty_and_blocked():
    mon = TabletopMonitor({"T1": TOP}, {"T1": prepare(frame(), TOP)})
    assert mon.check(frame(), []) == {"T1": True}
    assert mon.check(frame(clutter=True), []) == {"T1": False}
    assert mon.check(frame(), [(5, 5, 50, 45)]) == {"T1": None}  # someone leaning over most of it
    assert mon.check(frame(), [(70, 50, 90, 90)]) == {"T1": True}  # someone nearby, not over it
    # A neighbour's box over a corner of the plate area: the rest is still read.
    assert mon.check(frame(clutter=True), [(50, 30, 90, 90)]) == {"T1": False}
    assert mon.check(frame(), [(50, 30, 90, 90)]) == {"T1": True}


def test_monitor_rejects_wrong_size_reference():
    with pytest.raises(ValueError, match="recapture"):
        TabletopMonitor({"T1": TOP}, {"T1": np.zeros((5, 5), np.float32)})


def test_params_validated():
    with pytest.raises(ValueError):
        TabletopParams(clear_below=1.5)


class Scripted:
    def __init__(self, script):
        self.script = iter(script)

    def detect(self, image):
        return [Person(b, 0.9) for b in next(self.script)]


def test_pipeline_passes_tabletop_readings():
    table = TableConfig("T1", "cam", ((0, 0), (100, 0), (100, 100), (0, 100)), top=TOP)
    mon = TabletopMonitor({"T1": TOP}, {"T1": prepare(frame(), TOP)})
    pipe = TablePipeline([table], Scripted([[], []]), tabletop=mon)
    assert pipe.process(0, frame()).top_clear == {"T1": True}
    assert pipe.process(1, frame(clutter=True)).top_clear == {"T1": False}


# --- config ------------------------------------------------------------------


def base_config(**table):
    return {
        "venue_id": "v",
        "cameras": [{"id": "cam", "source": "x.mp4"}],
        "tables": [{"id": "T1", "camera": "cam", "polygon": [[0, 0], [1, 0], [1, 1]], **table}],
    }


def test_config_top_and_reference():
    v = parse_venue_config({**base_config(top=[[0, 0], [1, 0], [1, 1]], reference="r.png"), "tabletop": {"clear_below": 0.2}})
    assert v.tables[0].top == ((0, 0), (1, 0), (1, 1)) and v.tables[0].reference == "r.png"
    assert v.tabletop == {"clear_below": 0.2}
    assert parse_venue_config(base_config()).tables[0].top is None
    with pytest.raises(ValueError, match="without a top"):
        parse_venue_config(base_config(reference="r.png"))


def test_camera_imgsz_and_conf():
    raw = base_config()
    assert parse_venue_config(raw).cameras[0].imgsz == 640
    raw["cameras"][0].update(imgsz=1280, conf=0.25)
    cam = parse_venue_config(raw).cameras[0]
    assert (cam.imgsz, cam.conf) == (1280, 0.25)
    raw["cameras"][0]["imgsz"] = 1000
    with pytest.raises(ValueError, match="multiple of 32"):
        parse_venue_config(raw)

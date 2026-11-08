"""Evaluation metrics, checked against hand-computed numbers."""

import json

import pytest

from tablesense.evaluation import (
    Segment,
    evaluate,
    load_labels,
    main,
    predicted_segments,
    segments_from_marks,
)
from tablesense.events.log import EventLog
from tablesense.state import Color, State, Transition

G, R, Y = Color.GREEN, Color.RED, Color.YELLOW
S = State


def seg(tid, a, b, c):
    return Segment(tid, a, b, c)


def test_predicted_segments_merge_seated_and_away_into_red():
    preds = predicted_segments(
        [("T1", 145, S.SEATED), ("T1", 300, S.AWAY), ("T1", 310, S.SEATED),
         ("T1", 430, S.AWAY), ("T1", 460, S.NEEDS_CLEARING), ("T1", 520, S.EMPTY)],
        ["T1", "T2"],
        end=600,
    )
    assert preds == [
        seg("T1", 0, 145, G), seg("T1", 145, 460, R), seg("T1", 460, 520, Y), seg("T1", 520, 600, G),
        seg("T2", 0, 600, G),  # table with no events stays green
    ]


def test_full_cycle_accuracy_and_delays():
    labels = [seg("T1", 0, 100, G), seg("T1", 100, 400, R), seg("T1", 400, 500, Y), seg("T1", 500, 600, G)]
    preds = [seg("T1", 0, 145, G), seg("T1", 145, 460, R), seg("T1", 460, 520, Y), seg("T1", 520, 600, G)]
    r = evaluate(labels, preds)
    t = r.tables[0]
    # correct: 100 (green) + 255 (red 145-400) + 40 (yellow 460-500) + 80 (green 520-600)
    assert t.correct_seconds == pytest.approx(475)
    assert t.accuracy == pytest.approx(475 / 600)
    assert (t.true_changes, t.matched, t.missed, t.false_transitions) == (3, 3, 0, 0)
    assert t.delays == [45, 60, 20]
    assert t.confusion["red->green"] == pytest.approx(45)
    s = r.summary()
    assert s["median_delay_s"] == 45
    assert s["wrong_yellow_seconds"] == 0


def test_wrong_yellow_and_false_transitions():
    # Guests stay the whole time; the system wrongly goes yellow for 100 s.
    labels = [seg("T1", 60, 1000, R)]
    preds = [seg("T1", 0, 45, G), seg("T1", 45, 500, R), seg("T1", 500, 600, Y), seg("T1", 600, 1000, R)]
    t = evaluate(labels, preds).tables[0]
    assert t.accuracy == pytest.approx(840 / 940)
    assert t.wrong_yellow_seconds == pytest.approx(100)
    assert t.wrong_yellow_episodes == 1
    assert (t.true_changes, t.missed, t.false_transitions) == (0, 0, 2)


def test_missed_change():
    labels = [seg("T1", 0, 100, G), seg("T1", 100, 200, R)]
    t = evaluate(labels, [seg("T1", 0, 200, G)]).tables[0]
    assert (t.true_changes, t.matched, t.missed) == (1, 0, 1)
    assert t.accuracy == pytest.approx(0.5)


def test_change_outside_matching_window_is_missed_and_false():
    labels = [seg("T1", 0, 100, G), seg("T1", 100, 1000, R)]
    preds = [seg("T1", 0, 400, G), seg("T1", 400, 1000, R)]  # 300 s late
    t = evaluate(labels, preds, max_delay=180).tables[0]
    assert (t.missed, t.false_transitions) == (1, 1)


def test_unlabeled_gaps_are_ignored():
    labels = [seg("T1", 0, 100, G), seg("T1", 200, 300, R)]  # 100-200 unlabeled
    preds = [seg("T1", 0, 150, G), seg("T1", 150, 300, R)]
    t = evaluate(labels, preds).tables[0]
    assert t.labeled_seconds == 200
    assert t.accuracy == 1.0
    assert (t.true_changes, t.false_transitions) == (0, 0)  # change at 150 is in the gap


def test_segments_from_marks():
    marks = [("T1", 0, G), ("T1", 50, R), ("T1", 80, R), ("T1", 120, Y), ("T2", 30, R)]
    assert segments_from_marks(marks, end=200) == [
        seg("T1", 0, 50, G), seg("T1", 50, 120, R), seg("T1", 120, 200, Y), seg("T2", 30, 200, R),
    ]
    assert segments_from_marks([("T2", 30, R)], end=200, start_color=G) == [
        seg("T2", 0, 30, G), seg("T2", 30, 200, R),
    ]


def test_load_labels_validates(tmp_path):
    good = tmp_path / "l.csv"
    good.write_text("table_id,start,end,state\nT1,0,10,green\nT1,10,20,Seated\n")
    assert load_labels(good) == [seg("T1", 0, 10, G), seg("T1", 10, 20, R)]
    bad = tmp_path / "bad.csv"
    bad.write_text("table_id,start,end,state\nT1,0,10,green\nT1,5,20,red\n")
    with pytest.raises(ValueError, match="overlap"):
        load_labels(bad)
    bad.write_text("table_id,start,end,state\nT1,0,10,purple\n")
    with pytest.raises(ValueError, match="unknown"):
        load_labels(bad)


def test_cli_end_to_end(tmp_path, capsys):
    labels = tmp_path / "labels.csv"
    labels.write_text("table_id,start,end,state\nT1,0,100,green\nT1,100,400,red\n")
    db = tmp_path / "events.db"
    log = EventLog(db)
    run = log.start_run("r1", venue_id="v", camera_id="c", source="replay", started_at=1_700_000_000)
    log.write(run, Transition("T1", S.EMPTY, S.SEATED, at=100, detected_at=145, people_count=2, reason="seated"))
    log.close()

    out = tmp_path / "res" / "eval"
    assert main(["--labels", str(labels), "--events", str(db), "--out", str(out)]) == 0
    data = json.loads((tmp_path / "res" / "eval.json").read_text())
    assert data["summary"]["state_accuracy"] == pytest.approx(355 / 400, abs=1e-4)
    assert data["summary"]["median_delay_s"] == 45
    assert "Evaluation: run r1" in capsys.readouterr().out

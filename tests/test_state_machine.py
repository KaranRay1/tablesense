"""Tests for the table state machine. Default timers, one observation per second.

Each test is a scene from a real service, written as segments of
(duration_seconds, people_count).
"""

from pathlib import Path

import pytest

from tablesense.state import Color, CountSmoother, State, TableStateMachine, Timers

ROOT = Path(__file__).resolve().parents[1]


def feed(machine, segments, start=0, step=1):
    """Feed (duration, count) segments. Returns (transitions, next_ts)."""
    transitions, ts = [], start
    for duration, count in segments:
        for _ in range(int(duration / step)):
            tr = machine.update(ts, count)
            if tr:
                transitions.append(tr)
            ts += step
    return transitions, ts


def states(transitions):
    return [t.to_state for t in transitions]


def seated_then_left(m):
    """Guests sit 0-99 s, then leave for good. Table ends NEEDS_CLEARING."""
    trs, ts = feed(m, [(100, 2), (300, 0)])
    assert states(trs) == [State.SEATED, State.AWAY, State.NEEDS_CLEARING]
    return trs, ts


# --- EMPTY -> SEATED -------------------------------------------------------


def test_passer_by_does_not_seat():
    m = TableStateMachine("T1")
    trs, _ = feed(m, [(10, 1), (60, 0)])
    assert trs == []
    assert m.state is State.EMPTY


def test_guests_seat_after_t_seat_and_time_is_backdated():
    m = TableStateMachine("T1")
    trs, _ = feed(m, [(60, 2)])
    assert states(trs) == [State.SEATED]
    assert trs[0].at == 0  # when they actually sat down
    assert trs[0].detected_at == 45
    assert trs[0].delay == 45
    assert m.color is Color.RED


# --- SEATED -> AWAY -> back ------------------------------------------------


def test_short_occlusion_while_seated_changes_nothing():
    m = TableStateMachine("T1")
    trs, _ = feed(m, [(60, 2), (10, 0), (60, 2)])
    assert states(trs) == [State.SEATED]


def test_toilet_break_returns_to_seated_and_never_goes_yellow():
    m = TableStateMachine("T1")
    trs, _ = feed(m, [(100, 2), (120, 0), (30, 1)])
    assert states(trs) == [State.SEATED, State.AWAY, State.SEATED]
    away = trs[1]
    assert away.at == 100 and away.detected_at == 120
    assert trs[2].at == 220  # when they came back


def test_away_is_shown_as_red():
    m = TableStateMachine("T1")
    feed(m, [(100, 2), (30, 0)])
    assert m.state is State.AWAY
    assert m.color is Color.RED


# --- AWAY -> NEEDS_CLEARING ------------------------------------------------


def test_guests_leave_goes_yellow_after_hold_with_left_time_backdated():
    m = TableStateMachine("T1")
    trs, _ = seated_then_left(m)
    yellow = trs[2]
    assert yellow.at == 100  # left_at = when they disappeared, not when hold ended
    assert yellow.detected_at == 340  # 100 + t_away (240)
    assert m.color is Color.YELLOW


def test_blip_during_away_does_not_restart_hold():
    """A waiter passing for 3 s mustn't push yellow back by 4 minutes."""
    m = TableStateMachine("T1")
    trs, _ = feed(m, [(100, 2), (100, 0), (3, 1), (200, 0)])
    assert states(trs) == [State.SEATED, State.AWAY, State.NEEDS_CLEARING]
    assert trs[2].at == 100
    assert trs[2].detected_at == 340


# --- NEEDS_CLEARING -> EMPTY / SEATED --------------------------------------


def test_clearing_visit_turns_table_green():
    m = TableStateMachine("T1")
    _, ts = seated_then_left(m)  # ts == 400
    trs, _ = feed(m, [(30, 1), (5, 0)], start=ts)
    assert states(trs) == [State.EMPTY]
    assert trs[0].detected_at == 430
    assert m.color is Color.GREEN


def test_visit_too_short_does_not_count_as_clearing():
    m = TableStateMachine("T1")
    _, ts = seated_then_left(m)
    trs, _ = feed(m, [(5, 1), (30, 0)], start=ts)
    assert trs == []
    assert m.state is State.NEEDS_CLEARING


def test_new_guests_sit_at_dirty_table():
    m = TableStateMachine("T1")
    _, ts = seated_then_left(m)
    trs, _ = feed(m, [(120, 3)], start=ts)
    assert states(trs) == [State.SEATED]
    assert trs[0].at == ts
    assert trs[0].detected_at == ts + 90


def test_assume_cleared_after_timeout():
    m = TableStateMachine("T1")
    _, ts = seated_then_left(m)  # yellow detected at 340
    trs, _ = feed(m, [(1200, 0)], start=ts)
    assert states(trs) == [State.EMPTY]
    assert trs[0].detected_at == 340 + 1200


# --- robustness -------------------------------------------------------------


def test_stream_gap_does_not_count_as_presence():
    m = TableStateMachine("T1")
    assert m.update(0, 1) is None
    assert m.update(50, 1) is None  # 50 s gap: we don't know they were there
    trs, _ = feed(m, [(60, 1)], start=51)
    assert states(trs) == [State.SEATED]
    assert trs[0].at == 50


def test_timestamps_going_backwards_raise():
    m = TableStateMachine("T1")
    m.update(10, 0)
    with pytest.raises(ValueError):
        m.update(5, 0)


def test_negative_count_raises():
    with pytest.raises(ValueError):
        TableStateMachine("T1").update(0, -1)


def test_noisy_detections_with_smoother_give_one_clean_session():
    """30 min meal where the detector misses the guests 1 frame in 7."""
    m, smoother = TableStateMachine("T1"), CountSmoother(window=5)
    transitions = []
    for ts in range(1800):
        raw = 0 if ts % 7 == 0 else 2
        tr = m.update(ts, smoother.update(ts, raw))
        if tr:
            transitions.append(tr)
    assert states(transitions) == [State.SEATED]


def test_full_service_cycle():
    m = TableStateMachine("T1")
    trs, _ = feed(
        m,
        [
            (5, 1),  # someone walks past
            (20, 0),
            (1500, 3),  # 25 min meal
            (90, 0),  # whole group steps out (smoke break)
            (600, 3),  # back for dessert
            (300, 0),  # leave
            (40, 1),  # waiter clears
            (60, 0),
        ],
    )
    assert states(trs) == [
        State.SEATED,
        State.AWAY,
        State.SEATED,
        State.AWAY,
        State.NEEDS_CLEARING,
        State.EMPTY,
    ]
    assert [t.reason for t in trs] == [
        "seated", "absent", "returned", "absent", "hold_expired", "clearing_visit",
    ]


def test_reasons_distinguish_timeout_and_reseating():
    m = TableStateMachine("T1")
    _, ts = seated_then_left(m)
    trs, _ = feed(m, [(1200, 0)], start=ts)
    assert trs[0].reason == "assumed_clear"

    m = TableStateMachine("T1")
    _, ts = seated_then_left(m)
    trs, _ = feed(m, [(120, 3)], start=ts)
    assert trs[0].reason == "reseated_dirty"


# --- manual override & config ----------------------------------------------


def test_manual_override():
    m = TableStateMachine("T1")
    seated_then_left(m)
    tr = m.force(State.EMPTY, ts=500)
    assert tr.manual and tr.to_state is State.EMPTY
    assert m.force(State.EMPTY, ts=501) is None


def test_timers_from_example_config():
    yaml = pytest.importorskip("yaml")
    cfg = yaml.safe_load((ROOT / "configs" / "example_venue.yaml").read_text())
    timers = Timers.from_dict(cfg["timers"])
    assert timers.t_away == 240


def test_timers_reject_bad_values():
    with pytest.raises(ValueError):
        Timers.from_dict({"t_awayy": 10})
    with pytest.raises(ValueError):
        Timers(t_seat=-1)
    with pytest.raises(ValueError):
        Timers(clearing_visit_min=100, clearing_visit_max=90)


# --- smoother ---------------------------------------------------------------


def test_smoother_ignores_single_dropout_and_single_false_positive():
    s = CountSmoother(window=5)
    out = [s.update(t, c) for t, c in enumerate([2, 2, 0, 2, 2, 2])]
    assert out[2:] == [2, 2, 2, 2]
    s = CountSmoother(window=5)
    out = [s.update(t, c) for t, c in enumerate([0, 0, 0, 1, 0, 0])]
    assert max(out) == 0

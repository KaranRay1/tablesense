"""Sessions and weekly stats, with hand-checked numbers."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from tablesense.analytics.sessions import Session, build_sessions
from tablesense.analytics.stats import compute_week, daypart
from tablesense.events.log import Event
from tablesense.state import State

S = State
DUB = ZoneInfo("Europe/Dublin")
MON = datetime(2026, 11, 2, tzinfo=DUB)  # a Monday, GMT (UTC+0)
T0 = MON.timestamp()
H = 3600


def ev(tid, at, frm, to, reason, n=2, detected=None, manual=False):
    return Event("r", "v", tid, at, at if detected is None else detected, frm, to, n, reason, manual)


def visit(tid, seat, leave, clear, reason="clearing_visit", n=2):
    """Events for one full visit, times in seconds after Monday 00:00."""
    return [
        ev(tid, T0 + seat, S.EMPTY, S.SEATED, "seated", n, detected=T0 + seat + 45),
        ev(tid, T0 + leave, S.SEATED, S.AWAY, "absent", 0, detected=T0 + leave + 20),
        ev(tid, T0 + leave, S.AWAY, S.NEEDS_CLEARING, "hold_expired", 0, detected=T0 + leave + 240),
        ev(tid, T0 + clear, S.NEEDS_CLEARING, S.EMPTY, reason, 0),
    ]


# --- sessions ----------------------------------------------------------------


def test_simple_visit():
    [s] = build_sessions(visit("T1", 12 * H, 13 * H, 13 * H + 600))
    assert s.dwell == H
    assert s.dirty == 600
    assert s.group_size == 2
    assert s.cleared_by == "clearing_visit"


def test_away_and_return_stays_one_visit():
    evs = [
        ev("T1", T0, S.EMPTY, S.SEATED, "seated"),
        ev("T1", T0 + 600, S.SEATED, S.AWAY, "absent"),
        ev("T1", T0 + 700, S.AWAY, S.SEATED, "returned"),
        ev("T1", T0 + 1800, S.SEATED, S.AWAY, "absent"),
        ev("T1", T0 + 1800, S.AWAY, S.NEEDS_CLEARING, "hold_expired", detected=T0 + 2040),
        ev("T1", T0 + 2100, S.NEEDS_CLEARING, S.EMPTY, "clearing_visit"),
    ]
    [s] = build_sessions(evs)
    assert (s.dwell, s.dirty) == (1800, 300)


def test_assumed_clear_has_no_dirty_time():
    [s] = build_sessions(visit("T1", 0, H, H + 1200, reason="assumed_clear"))
    assert s.dwell == H
    assert s.dirty is None
    assert s.cleared_by == "assumed_clear"


def test_reseated_at_dirty_table_starts_new_visit():
    evs = visit("T1", 0, H, 0)[:3] + [ev("T1", T0 + H + 300, S.NEEDS_CLEARING, S.SEATED, "reseated_dirty", 4)]
    a, b = build_sessions(evs)
    assert a.cleared_by == "reseated_dirty" and a.dirty is None
    assert b.seated_at == T0 + H + 300 and b.group_size == 4 and b.left_at is None


def test_open_visit_at_end_of_data():
    [s] = build_sessions([ev("T1", T0, S.EMPTY, S.SEATED, "seated")])
    assert s.left_at is None and s.dwell is None


def test_tables_are_independent():
    evs = visit("T1", 0, H, H + 60) + visit("T2", 600, 2 * H, 2 * H + 60)
    assert [s.table_id for s in build_sessions(evs)] == ["T1", "T2"]


# --- weekly stats ------------------------------------------------------------


def week(sessions, n_tables=2, start=MON, days=7, **kw):
    end = datetime.fromtimestamp(start.timestamp() + days * 86400, DUB)
    return compute_week(sessions, start, end, n_tables=n_tables, **kw)


def test_occupancy_splits_across_hours():
    # One table occupied Monday 12:30-14:00.
    s = Session("T1", T0 + 12.5 * H, 2, left_at=T0 + 14 * H, cleared_at=T0 + 14 * H + 300,
                cleared_by="clearing_visit")
    st = week([s])
    assert st.occupancy[0][12] == 0.5
    assert st.occupancy[0][13] == 1.0
    assert st.occupancy[0][14] == 0.0
    assert st.occupancy[1][12] == 0.0  # Tuesday
    assert st.visits == 1 and st.visits_by_day[0] == 1
    assert st.dirty["median_min"] == 5.0


def test_occupancy_averages_over_multiple_weeks():
    # Two Mondays: occupied 13:00-14:00 on the first only -> average 0.5.
    s = Session("T1", T0 + 13 * H, 2, left_at=T0 + 14 * H)
    st = week([s], days=14)
    assert st.occupancy[0][13] == 0.5


def test_staffing_rounds_up_by_tables_per_waiter():
    # 6 tables occupied all Friday 19:00-20:00, 5 tables per waiter -> 2 waiters.
    fri19 = T0 + 4 * 86400 + 19 * H
    ss = [Session(f"T{i}", fri19, 2, left_at=fri19 + H) for i in range(6)]
    st = week(ss, n_tables=10, tables_per_waiter=5)
    assert st.occupancy[4][19] == 6.0
    assert st.staffing[4][19] == 2
    assert st.staffing[4][18] == 0
    assert st.peak_hours[0] == ("Fri", 19, 6.0)


def test_dwell_by_daypart_and_group():
    ss = [
        Session("T1", T0 + 12 * H, 2, left_at=T0 + 12 * H + 40 * 60),
        Session("T2", T0 + 13 * H, 2, left_at=T0 + 13 * H + 60 * 60),
        Session("T1", T0 + 19 * H, 5, left_at=T0 + 19 * H + 90 * 60),
    ]
    st = week(ss)
    assert st.dwell_by_daypart["Lunch"] == {"n": 2, "median_min": 50.0, "p90_min": 60.0}
    assert st.dwell_by_daypart["Dinner"]["median_min"] == 90.0
    assert st.dwell_by_group["5+"]["n"] == 1
    assert st.turns_per_table_per_day == round(3 / 2 / 7, 2)


def test_dst_week_counts_repeated_hour_correctly():
    # Week containing Sun 2026-10-25, when 01:00-02:00 happens twice in Dublin.
    start = datetime(2026, 10, 19, tzinfo=DUB)
    t = start.timestamp()
    s = Session("T1", t, 1, left_at=t + 7 * 86400 + H)  # occupied the whole (169 h) week
    st = week([s], n_tables=1, start=start, days=7 + 1 / 24)
    assert st.occupancy[6][1] == 1.0  # two 01:00 hours, each fully occupied
    assert all(v == 1.0 for row in st.occupancy for v in row)


def test_rejects_naive_datetimes():
    with pytest.raises(ValueError):
        compute_week([], datetime(2026, 11, 2), datetime(2026, 11, 9), n_tables=1)


def test_daypart():
    assert [daypart(h) for h in (8, 12, 16, 20, 2)] == ["Breakfast", "Lunch", "Afternoon", "Dinner", "Late"]

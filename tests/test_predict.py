"""Dwell prediction baseline and wait estimates."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from tablesense.analytics.predict import (
    DwellModel,
    TableNow,
    backtest,
    free_estimates,
    wait_for_party,
)
from tablesense.analytics.sessions import Session
from tablesense.state import State

DUB = ZoneInfo("Europe/Dublin")
MON = datetime(2026, 11, 2, tzinfo=DUB).timestamp()
H, M = 3600, 60


def visit(hour, dwell_min, group=2, day=0, dirty_min=None):
    seat = MON + day * 86400 + hour * H
    left = seat + dwell_min * M
    if dirty_min is None:
        return Session("T", seat, group, left_at=left)
    return Session("T", seat, group, left_at=left, cleared_at=left + dirty_min * M, cleared_by="clearing_visit")


def test_remaining_is_conditional_on_time_already_spent():
    hist = [visit(19, d) for d in (30, 60, 90)]
    m = DwellModel(min_n=1).fit(hist)
    seat = MON + 7 * 86400 + 19 * H
    assert m.remaining(seat, 2, seat).seconds == 60 * M  # median of 30/60/90
    # Still here after 70 min: only the 90-min visit is comparable -> 20 min left.
    p = m.remaining(seat, 2, seat + 70 * M)
    assert p.seconds == 20 * M and p.n == 1 and p.basis == "Dinner/2"


def test_falls_back_from_group_to_daypart_to_all_to_default():
    hist = [visit(19, 80, group=2)] * 3 + [visit(12, 40, group=2)] * 3
    m = DwellModel(min_n=3).fit(hist)
    seat = MON + 7 * 86400 + 19 * H
    assert m.remaining(seat, 2, seat).basis == "Dinner/2"
    assert m.remaining(seat, 6, seat).basis == "Dinner"  # no 5+ history at dinner
    assert m.remaining(seat, 2, seat + 60 * M).basis == "Dinner/2"
    assert m.remaining(seat, 2, seat + 85 * M).basis == "default"  # longer than every past visit


def test_clear_time_uses_measured_dirty_history():
    m = DwellModel(min_n=2).fit([visit(12, 40, dirty_min=d) for d in (4, 6, 8)])
    assert m.clear_time(MON, MON).seconds == 6 * M
    assert DwellModel(min_n=2).clear_time(MON, MON).basis == "default"


def test_free_estimates_and_queue():
    m = DwellModel(min_n=1).fit([visit(19, 60, dirty_min=5)] * 5)
    now = MON + 7 * 86400 + 19 * H + 30 * M
    tables = [
        TableNow("T1", 2, State.SEATED, since=now - 30 * M, group_size=2),  # ~30 + 5 min
        TableNow("T2", 4, State.NEEDS_CLEARING, since=now - 1 * M),  # ~4 min
        TableNow("T3", 2, State.EMPTY, since=now),  # free now
        TableNow("T4", 6, State.AWAY, since=now - 50 * M, group_size=2),  # ~10 + 5 min
    ]
    est = free_estimates(m, tables, now)
    assert [e.table_id for e in est] == ["T3", "T2", "T4", "T1"]
    assert est[1].seconds == pytest.approx(4 * M)
    assert est[3].seconds == pytest.approx(35 * M)

    assert wait_for_party(est, 2).table_id == "T3"
    assert wait_for_party(est, 4).table_id == "T2"
    assert wait_for_party(est, 4, queue_position=2).table_id == "T4"
    assert wait_for_party(est, 8) is None  # no table that big


def test_backtest_beats_naive_when_dwell_depends_on_time_of_day():
    train = [visit(12, 30 + i % 10, day=i // 7) for i in range(70)]
    train += [visit(19, 90 + i % 10, day=i // 7) for i in range(70)]
    test = [visit(12, 35, day=14), visit(19, 95, day=14)]
    res = backtest(train, test, checkpoints_min=(10,))
    assert res["10min"]["n"] == 2
    assert res["10min"]["mae_min"] < res["10min"]["naive_mae_min"]

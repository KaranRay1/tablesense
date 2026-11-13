"""Business days, the synthetic generator, and the HTML report."""

import itertools
from datetime import datetime
from zoneinfo import ZoneInfo

from tablesense.analytics.report import main as report_main
from tablesense.analytics.report import render_html
from tablesense.analytics.sessions import Session, build_sessions
from tablesense.analytics.stats import business_day, compute_week
from tablesense.events.log import EventLog
from tablesense.sim import main as sim_main
from tablesense.sim import simulate

DUB = ZoneInfo("Europe/Dublin")
MON = datetime(2026, 11, 2, tzinfo=DUB)
H = 3600


def test_after_midnight_counts_as_previous_night():
    assert business_day(datetime(2026, 11, 7, 1, 0, tzinfo=DUB)) == 4  # Sat 01:00 -> Fri
    assert business_day(datetime(2026, 11, 7, 6, 0, tzinfo=DUB)) == 5  # Sat 06:00 -> Sat
    assert business_day(datetime(2026, 11, 2, 2, 0, tzinfo=DUB)) == 6  # Mon 02:00 -> Sun

    # Friday 23:00 - Saturday 01:00: all of it is Friday night.
    fri23 = MON.timestamp() + 4 * 86400 + 23 * H
    st = compute_week([Session("T1", fri23, 2, left_at=fri23 + 2 * H)], MON,
                      datetime(2026, 11, 9, tzinfo=DUB), n_tables=1)
    assert st.occupancy[4][23] == 1.0 and st.occupancy[4][0] == 1.0
    assert st.occupancy[5][0] == 0.0
    assert st.visits_by_day[4] == 1


def test_simulation_is_reproducible_and_consistent():
    a, stats = simulate(MON, days=2, seed=3)
    b, _ = simulate(MON, days=2, seed=3)
    assert a == b
    assert stats["seated"] + stats["turned_away"] == stats["groups"]
    sessions = build_sessions(_as_events(a))
    assert len(sessions) == stats["seated"]
    assert all(s.dwell and s.dwell > 0 for s in sessions)
    # No table hosts two visits at once.
    by_table = {}
    for s in sessions:
        by_table.setdefault(s.table_id, []).append(s)
    for ss in by_table.values():
        ss.sort(key=lambda s: s.seated_at)
        assert all(x.cleared_at <= y.seated_at for x, y in itertools.pairwise(ss))


def _as_events(transitions):
    log = EventLog(":memory:")
    run = log.start_run("r", venue_id="v", camera_id="c", source="synthetic", started_at=MON.timestamp())
    log.write_many(run, transitions)
    return log.events()


def test_report_end_to_end_marks_synthetic(tmp_path):
    db, out = tmp_path / "sim.db", tmp_path / "report.html"
    assert sim_main(["--events", str(db), "--start", "2026-11-02", "--days", "7"]) == 0
    assert report_main(["--events", str(db), "--start", "2026-11-02", "--tables", "12", "--out", str(out)]) == 0
    page = out.read_text()
    assert "Synthetic data." in page
    assert "Suggested floor staff" in page
    assert page.count("<tr><th scope='row'>Sat</th>") == 2  # both grids


def test_report_handles_empty_week_and_escapes_names():
    st = compute_week([], MON, datetime(2026, 11, 9, tzinfo=DUB), n_tables=5)
    page = render_html(st, "<Bob's & Co>", synthetic=False)
    assert "&lt;Bob&#x27;s &amp; Co&gt;" in page
    assert "Synthetic data." not in page

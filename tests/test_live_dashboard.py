"""Floor status, shadow alerts, and the dashboard HTTP API."""

import json
import threading
import urllib.request
from http.server import HTTPServer

import pytest

from tablesense.dashboard.server import Clock, Dashboard, make_handler
from tablesense.events.log import EventLog
from tablesense.live import AlertEngine, AlertRules, TableStatus, floor_status
from tablesense.state import State, Transition

S = State
T0 = 1_793_000_000.0  # some fixed epoch
M = 60


def tr(tid, frm, to, at, det, reason, n=0):
    return Transition(tid, frm, to, at - T0, det - T0, n, False, reason)


@pytest.fixture
def log():
    log = EventLog(":memory:")
    run = log.start_run("r", venue_id="v", camera_id="c", source="replay", started_at=T0)
    log.write_many(run, [
        tr("T1", S.EMPTY, S.SEATED, T0, T0 + 45, "seated", 3),
        tr("T1", S.SEATED, S.AWAY, T0 + 30 * M, T0 + 30 * M + 20, "absent"),
        tr("T1", S.AWAY, S.NEEDS_CLEARING, T0 + 30 * M, T0 + 34 * M, "hold_expired"),
        tr("T1", S.NEEDS_CLEARING, S.EMPTY, T0 + 40 * M, T0 + 40 * M, "clearing_visit"),
        tr("T2", S.EMPTY, S.SEATED, T0 + 5 * M, T0 + 5 * M + 45, "seated", 2),
    ])
    yield log
    log.close()


def test_floor_status_over_time(log):
    ev = log.events()
    seats = {"T1": 4, "T2": 2, "T3": 2}
    at = lambda now: {t.table_id: (t.state, t.since, t.group_size) for t in floor_status(ev, seats, now)}

    s = at(T0 + 10 * M)
    assert s["T1"] == (S.SEATED, T0, 3)
    assert s["T2"] == (S.SEATED, T0 + 5 * M, 2)
    assert s["T3"][0] is S.EMPTY  # never seen

    assert at(T0 + 32 * M)["T1"][0] is S.AWAY  # shown red to staff
    assert at(T0 + 33 * M)["T1"][:2] == (S.AWAY, T0)  # yellow not known yet (detected at 34 min)
    assert at(T0 + 35 * M)["T1"][:2] == (S.NEEDS_CLEARING, T0 + 30 * M)
    assert at(T0 + 41 * M)["T1"][0] is S.EMPTY


def test_alerts_fire_once_resolve_and_take_verdicts(log):
    eng = AlertEngine(log, "v", AlertRules(needs_clearing_after=3 * M))
    dirty = [TableStatus("T1", 4, S.NEEDS_CLEARING, since=T0, group_size=0)]
    clean = [TableStatus("T1", 4, S.EMPTY, since=T0 + 10 * M, group_size=0)]

    assert eng.update(dirty, T0 + 2 * M) == []  # not long enough
    [aid] = eng.update(dirty, T0 + 4 * M)
    assert eng.update(dirty, T0 + 5 * M) == []  # no duplicate while open
    eng.update(clean, T0 + 10 * M)
    [a] = eng.recent()
    assert a["resolved_at"] == T0 + 10 * M
    assert "needs clearing" in a["message"]

    assert eng.set_verdict(aid, "wrong", T0 + 11 * M)
    assert eng.shadow_summary() == {"alerts": 1, "judged": 1, "wrong": 1, "wrong_pct": 100}
    with pytest.raises(ValueError):
        eng.set_verdict(aid, "maybe", T0)
    assert eng.recent(now=T0) == []  # nothing fired before T0


def test_unserved_rule_is_off_by_default_and_respects_staff_contact(log):
    seated = [TableStatus("T2", 2, S.SEATED, since=T0, group_size=2)]
    assert AlertEngine(log, "v").update(seated, T0 + 30 * M) == []

    eng = AlertEngine(log, "v2", AlertRules(unserved_after=5 * M))
    assert eng.update(seated, T0 + 6 * M, staff_contact={"T2"}) == []
    assert len(eng.update(seated, T0 + 6 * M)) == 1


def test_dashboard_http_api(log):
    clock = Clock(start=T0 + 36 * M, speed=1)  # T1 is yellow, T2 seated
    dash = Dashboard(log, "v", {"T1": 4, "T2": 2, "T3": 2}, "Europe/Dublin", clock,
                     AlertRules(needs_clearing_after=3 * M))
    server = HTTPServer(("127.0.0.1", 0), make_handler(dash))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        page = urllib.request.urlopen(base + "/").read().decode()
        assert "<title>TableSense</title>" in page

        st = json.loads(urllib.request.urlopen(base + "/api/status").read())
        by_id = {t["id"]: t for t in st["tables"]}
        assert by_id["T1"]["label"] == "Needs clearing"
        assert by_id["T2"]["label"] == "Seated" and by_id["T2"]["group_size"] == 2
        assert by_id["T3"]["label"] == "Free" and by_id["T3"]["free_in_min"] is None
        assert st["counts"] == {"Free": 1, "Seated": 1, "Needs clearing": 1}
        assert st["mode"] == "demo"
        [alert] = st["alerts"]

        req = urllib.request.Request(
            f"{base}/api/alerts/{alert['id']}/verdict",
            data=json.dumps({"verdict": "correct"}).encode(),
            headers={"Content-Type": "application/json", "X-TableSense": "1"},
            method="POST",
        )
        assert json.loads(urllib.request.urlopen(req).read()) == {"ok": True}
        st = json.loads(urllib.request.urlopen(base + "/api/status").read())
        assert st["alerts"][0]["verdict"] == "correct"

        bad = urllib.request.Request(f"{base}/api/alerts/1/verdict", data=b'{"verdict":"meh"}', method="POST",
                                     headers={"X-TableSense": "1"})
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(bad)
        assert e.value.code == 400
    finally:
        server.shutdown()


def test_dashboard_insights_table_and_system_api(log):
    clock = Clock(start=T0 + 36 * M, speed=1)
    dash = Dashboard(log, "v", {"T1": 4, "T2": 2, "T3": 2}, "Europe/Dublin", clock,
                     AlertRules(needs_clearing_after=3 * M))
    server = HTTPServer(("127.0.0.1", 0), make_handler(dash))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    get = lambda path: json.loads(urllib.request.urlopen(base + path).read())
    try:
        wk = get("/api/week")
        assert wk["available"] and wk["start"] in wk["available"]
        assert wk["n_tables"] == 3 and len(wk["occupancy_pct"]) == 7
        assert wk["partial"]  # the clock is inside the only week with data
        assert wk["visits"] == 2  # T1 (finished) and T2 (still seated)

        h = get("/api/tables/T1")
        assert h["seats"] == 4
        assert [e["to"] for e in h["events"]] == ["NEEDS_CLEARING", "AWAY", "SEATED"]  # newest first, none after the clock
        assert h["events"][0]["reason"] == "Guests left"

        sysinfo = get("/api/system")
        assert sysinfo["tables"] == 3 and sysinfo["seats"] == 8
        assert sysinfo["data"]["events"] == 5
        assert sysinfo["alerts"]["shadow_mode"] is True

        report = urllib.request.urlopen(f"{base}/report?start={wk['start']}").read().decode()
        assert "<html" in report.lower()

        for bad in ("/api/week?start=not-a-date", "/api/tables/T9"):
            with pytest.raises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(base + bad)
            assert e.value.code in (400, 404)
    finally:
        server.shutdown()


def test_dashboard_reports_areas(log):
    clock = Clock(start=T0 + 36 * M, speed=1)  # T1 yellow, T2 seated, T3 free
    areas = [{"id": "ground", "name": "Ground floor", "step_free": True},
             {"id": "first", "name": "First floor", "step_free": False}]
    dash = Dashboard(log, "v", {"T1": 4, "T2": 2, "T3": 2}, "Europe/Dublin", clock,
                     area_of={"T1": "ground", "T2": "first", "T3": "first"}, areas=areas)
    st = dash.status()
    assert {t["id"]: t["area"] for t in st["tables"]} == {"T1": "ground", "T2": "first", "T3": "first"}
    ground, first = st["areas"]
    assert ground["name"] == "Ground floor" and ground["tables"] == 1 and ground["seats"] == 4
    assert ground["counts"] == {"Free": 0, "Seated": 0, "Needs clearing": 1}
    assert first["step_free"] is False and first["counts"] == {"Free": 1, "Seated": 1, "Needs clearing": 0}
    assert st["counts"] == {"Free": 1, "Seated": 1, "Needs clearing": 1}  # venue totals unchanged
    assert dash.table_history("T2")["area_name"] == "First floor"
    assert [(a["id"], a["tables"]) for a in dash.system()["areas"]] == [("ground", 1), ("first", 2)]


def test_dashboard_defaults_to_one_area_and_rejects_unknown_ones(log):
    clock = Clock(start=T0 + 36 * M, speed=1)
    dash = Dashboard(log, "v", {"T1": 4, "T2": 2}, "Europe/Dublin", clock)
    st = dash.status()
    assert [a["id"] for a in st["areas"]] == ["main"] and {t["area"] for t in st["tables"]} == {"main"}
    with pytest.raises(ValueError, match="unknown areas"):
        Dashboard(log, "v", {"T1": 4}, "Europe/Dublin", clock, area_of={"T1": "roof"},
                  areas=[{"id": "main", "name": "Main", "step_free": True}])


def test_dashboard_takes_areas_from_the_venue_config(log):
    yaml = pytest.importorskip("yaml")
    from pathlib import Path

    from tablesense.config import parse_venue_config

    raw = yaml.safe_load((Path(__file__).parents[1] / "configs" / "demo_two_floors.yaml").read_text())
    venue = parse_venue_config(raw)
    seats = {t.id: t.seats for t in venue.tables}
    dash = Dashboard(log, "v", seats, "Europe/Dublin", Clock(start=T0 + 36 * M), venue=venue)
    st = dash.status()
    assert [a["name"] for a in st["areas"]] == ["Ground floor", "First floor"]
    assert next(t for t in st["tables"] if t["id"] == "T12")["area"] == "first"
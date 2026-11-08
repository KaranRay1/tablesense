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
        assert "<title>Floor Dashboard</title>" in page

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
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        assert json.loads(urllib.request.urlopen(req).read()) == {"ok": True}
        st = json.loads(urllib.request.urlopen(base + "/api/status").read())
        assert st["alerts"][0]["verdict"] == "correct"

        bad = urllib.request.Request(f"{base}/api/alerts/1/verdict", data=b'{"verdict":"meh"}', method="POST")
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(bad)
        assert e.value.code == 400
    finally:
        server.shutdown()

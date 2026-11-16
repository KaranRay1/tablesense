"""Sign-in: PIN/password checks, signed cookies, rate limits, and who may open what."""

import base64
import json
import threading
import urllib.error
import urllib.request
from http.server import HTTPServer

import pytest

from tablesense.dashboard.auth import Auth, from_env, read_cookie
from tablesense.dashboard.server import Clock, Dashboard, main, make_handler
from tablesense.events.log import EventLog
from tablesense.live import AlertRules
from tablesense.state import State, Transition

PIN, PW = "4821", "correct-horse-9"


class FakeClock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def test_settings_are_validated():
    with pytest.raises(ValueError, match="4-8 digits"):
        Auth(staff_pin="12a4")
    with pytest.raises(ValueError, match="10 characters"):
        Auth(owner_password="short")
    with pytest.raises(ValueError, match="32 characters"):
        from_env({"TABLESENSE_SESSION_SECRET": "too-short"})
    assert not from_env({}).enabled
    assert from_env({"TABLESENSE_OWNER_PASSWORD": PW}).enabled


def test_pin_gives_staff_password_gives_owner():
    a = Auth(PIN, PW)
    assert a.login("ip", PIN) == "staff"
    assert a.login("ip", PW) == "owner"
    assert a.login("ip", "0000") is None


def test_cookie_round_trip_expiry_and_tampering():
    clock = FakeClock()
    a = Auth(PIN, PW, clock=clock)
    tok, ttl = a.issue("staff")
    assert a.verify(tok).role == "staff"
    payload, sig = tok.split(".")
    forged = base64.urlsafe_b64encode(base64.urlsafe_b64decode(payload + "==").replace(b"staff", b"owner"))
    assert a.verify(forged.decode().rstrip("=") + "." + sig) is None
    assert a.verify("garbage") is None and a.verify(None) is None
    clock.t += ttl + 1
    assert a.verify(tok) is None


def test_changing_the_password_signs_everyone_out():
    secret = b"x" * 32
    tok, _ = Auth(PIN, PW, secret=secret).issue("owner")
    assert Auth(PIN, PW, secret=secret).verify(tok)  # same settings: still valid (e.g. after restart)
    assert Auth(PIN, PW + "!", secret=secret).verify(tok) is None


def test_wrong_guesses_lock_the_device_then_back_off():
    clock = FakeClock()
    a = Auth(PIN, PW, clock=clock, max_failures=3, lockout_s=60)
    for _ in range(3):
        assert a.retry_after("dev") == 0
        a.login("dev", "1111")
    assert 59 <= a.retry_after("dev") <= 60
    assert a.retry_after("other") == 0  # other devices unaffected
    clock.t += 61
    for _ in range(3):
        a.login("dev", "1111")
    assert a.retry_after("dev") > 100  # second lockout is longer


def test_global_limit_stops_guessing_from_many_devices():
    a = Auth(PIN, PW, clock=FakeClock(), global_max_failures=5, max_failures=100)
    for i in range(5):
        a.login(f"dev{i}", "1111")
    assert a.retry_after("new-device") > 0


def test_read_cookie():
    assert read_cookie("a=1; ts_session=abc.def; b=2") == "abc.def"
    assert read_cookie(None) is None


# --- over HTTP --------------------------------------------------------------------


class Client:
    def __init__(self, base):
        self.base, self.cookie = base, None

    def req(self, path, body=None, headers=None):
        h = {"X-TableSense": "1", **(headers or {})}
        if self.cookie:
            h["Cookie"] = self.cookie
        r = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(),
                                   headers=h, method="GET" if body is None else "POST")
        try:
            with urllib.request.urlopen(r) as resp:
                if c := resp.headers.get("Set-Cookie"):
                    self.cookie = c.split(";")[0]
                return resp.status, json.loads(resp.read() or b"null") if "json" in resp.headers["Content-Type"] \
                    else resp.read()
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"null")


T0 = 1_793_000_000.0


@pytest.fixture
def log():
    log = EventLog(":memory:")
    run = log.start_run("r", venue_id="v", camera_id="c", source="replay", started_at=T0)
    log.write_many(run, [Transition("T1", State.EMPTY, State.SEATED, 0, 45, 2, False, "seated")])
    yield log
    log.close()


@pytest.fixture
def server(log):
    dash = Dashboard(log, "v", {"T1": 4, "T2": 2, "T3": 2}, "Europe/Dublin", Clock(start=T0 + 36 * 60, speed=1),
                     AlertRules())
    srv = HTTPServer(("127.0.0.1", 0), make_handler(dash, Auth(PIN, PW)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_signed_out_sees_only_the_page(server):
    c = Client(server)
    assert c.req("/")[0] == 200
    assert c.req("/healthz") == (200, {"ok": True})
    assert c.req("/api/me") == (401, {"role": None, "login": True})
    assert c.req("/api/status")[0] == 401
    assert c.req("/api/tables/T1/served", {})[0] == 401


def test_staff_gets_floor_not_insights(server):
    c = Client(server)
    assert c.req("/api/login", {"secret": PIN}) == (200, {"role": "staff", "login": True})
    assert "HttpOnly" in urllib.request.urlopen(urllib.request.Request(
        server + "/api/login", data=json.dumps({"secret": PIN}).encode(),
        headers={"X-TableSense": "1"}, method="POST")).headers["Set-Cookie"]
    assert c.req("/api/me")[1]["role"] == "staff"
    assert c.req("/api/status")[0] == 200
    assert c.req("/api/tables/T1")[0] in (200, 404)
    assert c.req("/api/week")[0] == 403
    assert c.req("/api/system")[0] == 403
    assert c.req("/report")[0] == 403
    c.req("/api/logout", {})
    assert c.req("/api/status")[0] == 401


def test_owner_gets_everything(server):
    c = Client(server)
    assert c.req("/api/login", {"secret": PW})[1]["role"] == "owner"
    assert c.req("/api/system")[0] == 200
    assert c.req("/api/week")[0] in (200, 400)


def test_wrong_pin_then_lockout(server):
    c = Client(server)
    for _ in range(5):
        assert c.req("/api/login", {"secret": "0000"})[0] == 401
    code, body = c.req("/api/login", {"secret": PIN})
    assert code == 429 and body["retry_after"] > 0


def test_posts_from_other_sites_are_refused(server):
    c = Client(server)
    c.req("/api/login", {"secret": PW})
    assert c.req("/api/tables/T1/served", {}, headers={"X-TableSense": "0"})[0] == 403
    assert c.req("/api/tables/T1/served", {}, headers={"Origin": "http://evil.example"})[0] == 403


def test_security_headers(server):
    with urllib.request.urlopen(server + "/") as r:
        assert r.headers["X-Frame-Options"] == "DENY"
        assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]


def test_refuses_network_without_login(tmp_path, monkeypatch, capsys):
    for k in ("TABLESENSE_OWNER_PASSWORD", "TABLESENSE_STAFF_PIN"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(SystemExit):
        main(["--events", str(tmp_path / "e.db"), "--venue-id", "v", "--host", "0.0.0.0",
              "--env-file", str(tmp_path / "none.env")])
    assert "TABLESENSE_OWNER_PASSWORD" in capsys.readouterr().err


def test_pin_without_owner_password_is_refused(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TABLESENSE_STAFF_PIN", PIN)
    monkeypatch.delenv("TABLESENSE_OWNER_PASSWORD", raising=False)
    with pytest.raises(SystemExit):
        main(["--events", str(tmp_path / "e.db"), "--venue-id", "v", "--env-file", str(tmp_path / "none.env")])
    assert "set both" in capsys.readouterr().err

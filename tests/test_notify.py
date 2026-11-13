"""Notifications: channels (with fakes, nothing is really sent), Served, delivery."""

import json
import os
import urllib.parse

import pytest

from tablesense.dashboard.server import Clock, Dashboard, main
from tablesense.events.log import EventLog
from tablesense.live import AlertRules
from tablesense.notify import Alert, ConsoleNotifier, EmailNotifier, SmsNotifier, from_env, load_dotenv
from tablesense.state import State, Transition

S = State
T0 = 1_793_000_000.0
M = 60
ALERT = Alert(7, "T4", "unserved", "Table T4: guests seated 5 min and not marked served. Please check on them.")


# --- channels ------------------------------------------------------------------


class FakeSMTP:
    sent = []

    def __init__(self, host, port, timeout):
        self.host, self.port = host, port
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, context):
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append(("login", user))

    def send_message(self, msg):
        FakeSMTP.sent.append((self, msg))


def test_email_uses_tls_login_and_short_subject():
    FakeSMTP.sent = []
    n = EmailNotifier("smtp.example.com", 587, "bot@x.ie", "app-pass", "bot@x.ie", ["a@x.ie", "b@x.ie"],
                      smtp_factory=FakeSMTP)
    n.send(ALERT)
    [(smtp, msg)] = FakeSMTP.sent
    assert smtp.calls == ["starttls", ("login", "bot@x.ie")]
    assert msg["Subject"] == "Table T4: Check on guests"
    assert msg["To"] == "a@x.ie, b@x.ie"
    assert "Please check on them" in msg.get_content()


class FakeResp:
    status = 201

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return b"{}"


def test_sms_posts_one_message_per_number_to_twilio():
    reqs = []
    n = SmsNotifier("AC123", "tok", "+15550001", ["+353800000001", "+353800000002"],
                    urlopen=lambda req, timeout: reqs.append(req) or FakeResp())
    n.send(ALERT)
    assert len(reqs) == 2
    assert reqs[0].full_url == "https://api.twilio.com/2010-04-01/Accounts/AC123/Messages.json"
    form = urllib.parse.parse_qs(reqs[0].data.decode())
    assert form["To"] == ["+353800000001"] and form["From"] == ["+15550001"]
    assert form["Body"][0].startswith("Table T4: Check on guests.")
    assert reqs[0].get_header("Authorization").startswith("Basic ")


def test_from_env_reports_missing_settings_without_secrets():
    with pytest.raises(ValueError, match="TABLESENSE_SMTP_PASSWORD"):
        from_env(["email"], env={"TABLESENSE_SMTP_HOST": "h", "TABLESENSE_SMTP_USER": "u",
                                 "TABLESENSE_EMAIL_TO": "a@x.ie"})
    [n] = from_env(["email"], env={"TABLESENSE_SMTP_HOST": "h", "TABLESENSE_SMTP_USER": "u@x.ie",
                                   "TABLESENSE_SMTP_PASSWORD": "p", "TABLESENSE_EMAIL_TO": "a@x.ie, b@x.ie"})
    assert n.recipients == ["a@x.ie", "b@x.ie"] and n.sender == "u@x.ie" and n.port == 587
    with pytest.raises(ValueError, match="unknown"):
        from_env(["pigeon"], env={})


def test_load_dotenv_does_not_override(tmp_path, monkeypatch):
    f = tmp_path / ".env"
    f.write_text("# comment\nTS_A=1\nTS_B='two'\n\nTS_C=three\n")
    monkeypatch.setenv("TS_C", "already")
    monkeypatch.delenv("TS_A", raising=False)
    monkeypatch.delenv("TS_B", raising=False)
    assert load_dotenv(f) == {"TS_A": "1", "TS_B": "two"}
    assert os.environ["TS_C"] == "already"


# --- dashboard: served, delivery, limits -------------------------------------------


def make_dash(notifiers, now_offset=8 * M, start_offset=0, **kw):
    """Server starts at T0 + start_offset; the clock is then moved to T0 + now_offset."""
    log = EventLog(":memory:")
    run = log.start_run("r", venue_id="v", camera_id="c", source="replay", started_at=T0)
    log.write(run, Transition("T4", S.EMPTY, S.SEATED, 0, 45, 2, False, "seated"))
    t = [T0 + start_offset]
    clock = Clock()
    clock.now = lambda: t[0]
    rules = AlertRules(unserved_after=5 * M)
    dash = Dashboard(log, "v", {"T4": 4, "T5": 2}, "Europe/Dublin", clock, rules, notifiers, **kw)
    t[0] = T0 + now_offset
    return dash


class Boom:
    name = "sms"

    def send(self, alert):
        raise OSError("network down")


def test_unserved_alert_is_sent_and_served_resolves_it():
    lines = []
    dash = make_dash([ConsoleNotifier(lines.append)])
    [aid] = dash.tick()
    assert dash.deliver_pending() == 1
    assert lines == ["[ALERT] Table T4: Check on guests | Table T4: guests seated 8 min and not marked served. "
                     "Please check on them."]
    st = dash.status()
    [a] = st["alerts"]
    assert a["delivered"] == 1 and a["delivery"] == "console: sent" and a["resolved_at"] is None

    assert dash.served("T4")
    assert not dash.served("T5")  # not seated
    st = dash.status()
    assert st["alerts"][0]["resolved_at"] is not None
    assert {t["id"]: t["served"] for t in st["tables"]} == {"T4": True, "T5": False}
    assert st["first_contact"] == {"visits": 1, "median_min": 8.0}
    assert dash.tick() == []  # no new alert for a served table


def test_failed_channel_is_recorded_and_others_still_send():
    lines = []
    dash = make_dash([Boom(), ConsoleNotifier(lines.append)])
    dash.tick()
    dash.deliver_pending()
    [a] = dash.status()["alerts"]
    assert a["delivered"] == 1
    assert a["delivery"] == "sms: failed (OSError: network down); console: sent"
    assert len(lines) == 1


def test_hourly_limit():
    lines = []
    dash = make_dash([ConsoleNotifier(lines.append)], max_notifications_per_hour=0)
    dash.tick()
    dash.deliver_pending()
    [a] = dash.status()["alerts"]
    assert lines == [] and a["delivered"] == 0 and a["delivery"] == "skipped: hourly limit reached"


def test_no_alert_before_five_minutes_and_shadow_mode_sends_nothing():
    dash = make_dash([], now_offset=4 * M)
    assert dash.tick() == []
    dash = make_dash([], now_offset=6 * M)
    assert len(dash.tick()) == 1
    assert dash.deliver_pending() == 0  # nothing queued without notifiers


def test_demo_mode_refuses_real_channels(tmp_path):
    with pytest.raises(SystemExit):
        main(["--events", str(tmp_path / "x.db"), "--demo-start", "2026-10-31T19:30", "--notify", "email"])


def test_restart_mid_service_does_not_alert_for_old_visits():
    # Server starts 80 min into T4's visit: that visit is skipped.
    assert make_dash([], start_offset=80 * M, now_offset=81 * M).tick() == []
    # Started 3 min into the visit: still within the 5-min window, so it alerts.
    assert len(make_dash([], start_offset=3 * M, now_offset=6 * M).tick()) == 1


def test_test_command_without_env_file(tmp_path, capsys):
    from tablesense.notify import main as notify_main

    assert notify_main(["--channel", "email", "--env-file", str(tmp_path / "missing.env")]) == 1
    assert "cp .env.example .env" in capsys.readouterr().out
    assert notify_main(["--channel", "console", "--env-file", str(tmp_path / "missing.env")]) == 0
    assert "[ALERT] Table TEST" in capsys.readouterr().out


# --- SMS errors and number checks ---------------------------------------------------


def twilio_http_error(code, message, status=400):
    import io
    import urllib.error

    body = io.BytesIO(json.dumps({"code": code, "message": message}).encode())
    return urllib.error.HTTPError("https://api.twilio.com", status, "BAD REQUEST", {}, body)


def test_sms_surfaces_twilio_reason_and_keeps_sending_to_others():
    from tablesense.notify import TwilioError

    sent = []

    def urlopen(req, timeout):
        to = urllib.parse.parse_qs(req.data.decode())["To"][0]
        if to.endswith("01"):
            raise twilio_http_error(21608, "The number is unverified. Trial accounts cannot send messages to unverified numbers")
        sent.append(to)
        return FakeResp()

    n = SmsNotifier("AC1", "tok", "+15550001", ["+353870000001", "+353870000002"], urlopen=urlopen)
    with pytest.raises(TwilioError) as e:
        n.send(ALERT)
    assert e.value.code == 21608
    assert "unverified" in str(e.value)
    assert "+3538" in str(e.value) and "0000001" not in str(e.value)  # number masked
    assert sent == ["+353870000002"]  # the other waiter still got it


def test_sms_rejects_local_format_numbers_without_echoing_them():
    with pytest.raises(ValueError, match=r"\+3538XXXXXXXX") as e:
        SmsNotifier("AC1", "tok", "+15550001", ["0871234567"])
    assert "0871234567" not in str(e.value)
    with pytest.raises(ValueError, match="TWILIO_FROM"):
        SmsNotifier("AC1", "tok", "not a number!", ["+353871234567"])
    SmsNotifier("AC1", "tok", "TableSense", ["+353871234567"])  # alphanumeric sender is allowed


def test_every_common_twilio_error_has_a_hint():
    from tablesense.notify import TWILIO_HINTS

    for code in (20003, 21211, 21408, 21608, 21610):
        assert TWILIO_HINTS[code]


# --- free push (ntfy) ----------------------------------------------------------------


def test_push_posts_title_priority_and_message_to_topic():
    from tablesense.notify import NtfyNotifier

    reqs = []
    n = NtfyNotifier("tablesense-abcdefghijklmnop", urlopen=lambda req, timeout: reqs.append(req) or FakeResp())
    n.send(ALERT)
    [r] = reqs
    assert r.full_url == "https://ntfy.sh/tablesense-abcdefghijklmnop"
    assert r.get_header("Title") == "Table T4: Check on guests"
    assert r.get_header("Priority") == "high"
    assert r.data.decode() == ALERT.message
    assert r.get_header("Authorization") is None


def test_push_rejects_short_guessable_topics_and_uses_own_server():
    from tablesense.notify import NtfyNotifier, from_env, new_topic

    with pytest.raises(ValueError, match="long and random"):
        NtfyNotifier("waiters")
    t = new_topic()
    assert t.startswith("tablesense-") and len(t) >= 30 and t != new_topic()
    [n] = from_env(["push"], env={"TABLESENSE_NTFY_TOPIC": t, "TABLESENSE_NTFY_SERVER": "http://10.0.0.5:8080/",
                                  "TABLESENSE_NTFY_TOKEN": "tk_x"})
    assert (n.server, n.token) == ("http://10.0.0.5:8080", "tk_x")
    with pytest.raises(ValueError, match="TABLESENSE_NTFY_TOPIC"):
        from_env(["push"], env={})


def test_push_http_error_is_readable():
    import urllib.error

    from tablesense.notify import NtfyNotifier

    def urlopen(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {}, None)

    with pytest.raises(OSError, match="ntfy returned 429"):
        NtfyNotifier("tablesense-abcdefghijklmnop", urlopen=urlopen).send(ALERT)


# --- texts from the owner's number via the Mac Messages app ---------------------

from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402

from tablesense.notify import Alert as _Alert, MessagesNotifier, from_env as _from_env  # noqa: E402


def _fake_run(rc=0, stderr=""):
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        return SimpleNamespace(returncode=rc, stderr=stderr)

    return run, calls


def test_messages_passes_text_and_numbers_as_arguments():
    run, calls = _fake_run()
    n = MessagesNotifier(["+353871234567", "+353861234567"], run=run, platform="darwin")
    n.send(_Alert(1, "T4", "unserved", 'Seated 5 min "quote" & stuff'))
    argv = calls[0]
    assert argv[0] == "osascript" and argv[3] == "SMS"
    assert argv[4].startswith("Table T4") and '"quote"' in argv[4]
    assert argv[5:] == ["+353871234567", "+353861234567"]
    assert '"quote"' not in argv[2]  # never pasted into the script


def test_messages_explains_missing_sms_forwarding():
    run, _ = _fake_run(1, "execution error: Can't get account (-1728)")
    n = MessagesNotifier(["+353871234567"], run=run, platform="darwin")
    with pytest.raises(OSError, match="Text Message Forwarding"):
        n.send(_Alert(1, "T1", "test", "hi"))


def test_messages_rejects_bad_numbers_and_non_mac():
    with pytest.raises(ValueError, match="international"):
        MessagesNotifier(["0871234567"], platform="darwin")
    with pytest.raises(ValueError, match="Mac"):
        MessagesNotifier(["+353871234567"], platform="linux")


def test_from_env_builds_messages_channel(monkeypatch):
    monkeypatch.setattr("sys.platform", "darwin")
    import tablesense.notify as nt
    monkeypatch.setattr(nt.MessagesNotifier.__init__, "__defaults__",
                        ("SMS", nt.subprocess.run, "darwin"))
    [n] = _from_env(["messages"], {"TABLESENSE_SMS_TO": "+353871234567", "TABLESENSE_MESSAGES_SERVICE": "iMessage"})
    assert n.name == "messages" and n.service == "iMessage"

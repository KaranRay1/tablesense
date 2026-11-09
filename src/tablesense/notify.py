"""Send alerts to staff phones: push (ntfy, free), email (SMTP), SMS (Twilio), or the console.

Configured from environment variables, usually a local `.env` file that is
git-ignored (see `.env.example`). Credentials are never stored in the repo,
the event log, or the alert messages.

Messages contain only a table id and minutes: no images, no personal data.
"""

from __future__ import annotations

import base64
import json
import os
import re
import smtplib
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Callable, Mapping, Protocol


def ssl_context() -> ssl.SSLContext:
    """Verified TLS that works even where Python can't find the system
    certificates (e.g. the python.org installer on macOS): use certifi's
    bundle when it's installed."""
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _urlopen(req: urllib.request.Request, timeout: float):
    return urllib.request.urlopen(req, timeout=timeout, context=ssl_context())


@dataclass(frozen=True)
class Alert:
    id: int
    table_id: str
    kind: str  # 'unserved' | 'needs_clearing'
    message: str


class Notifier(Protocol):
    name: str

    def send(self, alert: Alert) -> None:
        """Deliver one alert. Raises on failure."""


def subject_for(alert: Alert) -> str:
    what = {"unserved": "Check on guests", "needs_clearing": "Needs clearing"}.get(alert.kind, alert.kind)
    return f"Table {alert.table_id}: {what}"


class NtfyNotifier:
    """Free push notifications through ntfy (https://ntfy.sh), an open-source app.

    Each phone installs the ntfy app and subscribes to the topic. On the public
    ntfy.sh server anyone who knows the topic name can read it, so the topic
    must be long and random (like a password). Messages only ever contain a
    table id and minutes. For production, run ntfy on the venue's edge box
    (TABLESENSE_NTFY_SERVER) so alerts never leave the building.
    """

    name = "push"
    PRIORITY = {"unserved": "high", "needs_clearing": "default", "test": "high"}
    TAGS = {"unserved": "bell", "needs_clearing": "broom", "test": "white_check_mark"}

    def __init__(
        self,
        topic: str,
        server: str = "https://ntfy.sh",
        token: str | None = None,
        urlopen: Callable = _urlopen,
        timeout: float = 15,
    ) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", topic):
            raise ValueError(
                "TABLESENSE_NTFY_TOPIC must be 16-64 letters, digits, - or _ (long and random, "
                "because anyone who knows it can read the alerts)"
            )
        if not server.startswith(("https://", "http://")):
            raise ValueError("TABLESENSE_NTFY_SERVER must start with https:// (or http:// on a local network)")
        self.topic, self.server, self.token = topic, server.rstrip("/"), token
        self.urlopen, self.timeout = urlopen, timeout

    def send(self, alert: Alert) -> None:
        headers = {
            "Title": subject_for(alert),
            "Priority": self.PRIORITY.get(alert.kind, "default"),
            "Tags": self.TAGS.get(alert.kind, "bell"),
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(
            f"{self.server}/{self.topic}", data=alert.message.encode(), headers=headers, method="POST"
        )
        try:
            with self.urlopen(req, timeout=self.timeout) as resp:
                resp.read()
        except urllib.error.HTTPError as e:
            raise OSError(f"ntfy returned {e.code} {e.reason}") from None


def new_topic() -> str:
    """A random, unguessable topic name."""
    import secrets

    return "tablesense-" + secrets.token_urlsafe(18).replace("_", "x").replace("-", "y")


class ConsoleNotifier:
    """Prints alerts. For demos and testing without sending anything."""

    name = "console"

    def __init__(self, write: Callable[[str], None] | None = None) -> None:
        self.write = write or (lambda line: print(line, flush=True))

    def send(self, alert: Alert) -> None:
        self.write(f"[ALERT] {subject_for(alert)} | {alert.message}")


class EmailNotifier:
    name = "email"

    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        sender: str,
        recipients: list[str],
        smtp_factory: Callable[..., smtplib.SMTP] = smtplib.SMTP,
        timeout: float = 15,
    ) -> None:
        if not recipients:
            raise ValueError("email notifier needs at least one recipient")
        self.host, self.port, self.user, self.password = host, port, user, password
        self.sender, self.recipients = sender, recipients
        self.smtp_factory, self.timeout = smtp_factory, timeout

    def send(self, alert: Alert) -> None:
        msg = EmailMessage()
        msg["Subject"] = subject_for(alert)
        msg["From"] = self.sender
        msg["To"] = ", ".join(self.recipients)
        msg.set_content(f"{alert.message}\n\n-- TableSense (automatic alert, no reply needed)")
        with self.smtp_factory(self.host, self.port, timeout=self.timeout) as smtp:
            smtp.starttls(context=ssl_context())
            smtp.login(self.user, self.password)
            smtp.send_message(msg)


class TwilioError(OSError):
    def __init__(self, code: int | None, message: str) -> None:
        super().__init__(f"Twilio error {code}: {message}" if code else message)
        self.code = code


# The Twilio errors people hit first, in plain English.
TWILIO_HINTS = {
    20003: "Wrong Account SID or Auth Token. Copy both again from the Twilio console home page.",
    21211: "The 'To' number isn't valid. Use international format, e.g. +3538XXXXXXXX (drop the leading 0).",
    21408: "Texting this country isn't switched on. In the Twilio console: Messaging > Settings > "
    "Geo permissions, tick Ireland (or the right country), and save.",
    21606: "TWILIO_FROM isn't an SMS-capable number on your account. Use the number Twilio gave you.",
    21659: "TWILIO_FROM isn't a Twilio number on your account. Use the number Twilio gave you.",
    21608: "Trial accounts can only text verified numbers. Verify yours in the console: "
    "Phone Numbers > Manage > Verified Caller IDs.",
    21610: "This number replied STOP to your Twilio number. Text START to it to opt back in.",
    21614: "The 'To' number can't receive SMS (e.g. a landline).",
}

E164 = re.compile(r"^\+[1-9]\d{6,14}$")
ALPHA_SENDER = re.compile(r"^(?=.*[A-Za-z])[A-Za-z0-9 ]{1,11}$")


def mask(number: str) -> str:
    """+353871234567 -> +3538••••••67 (for error messages and logs)."""
    return number if len(number) < 8 else number[:5] + "•" * (len(number) - 7) + number[-2:]


class SmsNotifier:
    """SMS via Twilio's REST API. Needs a Twilio account and a sending number."""

    name = "sms"

    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        sender: str,
        recipients: list[str],
        urlopen: Callable = _urlopen,
        timeout: float = 15,
    ) -> None:
        if not recipients:
            raise ValueError("sms notifier needs at least one recipient")
        bad = [mask(r) for r in recipients if not E164.match(r)]
        if bad:
            raise ValueError(
                f"TABLESENSE_SMS_TO: not in international format: {', '.join(bad)}. "
                "Irish mobiles look like +3538XXXXXXXX (country code, then the number without its leading 0)."
            )
        if not (E164.match(sender) or ALPHA_SENDER.match(sender)):
            raise ValueError("TWILIO_FROM must be your Twilio number in international format, e.g. +1XXXXXXXXXX")
        self.sid, self.token, self.sender, self.recipients = account_sid, auth_token, sender, recipients
        self.urlopen, self.timeout = urlopen, timeout

    def send(self, alert: Alert) -> None:
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self.sid}/Messages.json"
        auth = base64.b64encode(f"{self.sid}:{self.token}".encode()).decode()
        body = f"{subject_for(alert)}. {alert.message}"
        errors: list[TwilioError] = []
        for to in self.recipients:  # one failing number mustn't stop the others
            req = urllib.request.Request(
                url,
                data=urllib.parse.urlencode({"From": self.sender, "To": to, "Body": body}).encode(),
                headers={"Authorization": f"Basic {auth}"},
                method="POST",
            )
            try:
                with self.urlopen(req, timeout=self.timeout) as resp:
                    resp.read()
            except urllib.error.HTTPError as e:
                try:
                    info = json.loads(e.read() or b"{}")
                except ValueError:
                    info = {}
                errors.append(TwilioError(info.get("code"), f"{mask(to)}: {info.get('message') or e.reason}"))
        if errors:
            if len(errors) == 1:
                raise errors[0]
            raise TwilioError(errors[0].code, "; ".join(str(e) for e in errors))


# --- configuration --------------------------------------------------------------


def load_dotenv(path: str | Path = ".env") -> dict[str, str]:
    """Read KEY=VALUE lines. Doesn't override variables already set."""
    p = Path(path)
    loaded = {}
    if not p.exists():
        return loaded
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v
            loaded[k] = v
    return loaded


def _require(env: Mapping[str, str], keys: list[str], channel: str) -> None:
    missing = [k for k in keys if not env.get(k)]
    if missing:
        raise ValueError(f"{channel} notifications need these settings in .env: {', '.join(missing)}")


def _split(v: str) -> list[str]:
    return [x.strip() for x in v.split(",") if x.strip()]


def from_env(channels: list[str], env: Mapping[str, str] | None = None) -> list[Notifier]:
    env = os.environ if env is None else env
    out: list[Notifier] = []
    for ch in channels:
        if ch == "console":
            out.append(ConsoleNotifier())
        elif ch == "push":
            _require(env, ["TABLESENSE_NTFY_TOPIC"], "Push")
            out.append(NtfyNotifier(
                env["TABLESENSE_NTFY_TOPIC"],
                server=env.get("TABLESENSE_NTFY_SERVER") or "https://ntfy.sh",
                token=env.get("TABLESENSE_NTFY_TOKEN") or None,
            ))
        elif ch == "email":
            keys = ["TABLESENSE_SMTP_HOST", "TABLESENSE_SMTP_USER", "TABLESENSE_SMTP_PASSWORD", "TABLESENSE_EMAIL_TO"]
            _require(env, keys, "Email")
            out.append(EmailNotifier(
                host=env["TABLESENSE_SMTP_HOST"],
                port=int(env.get("TABLESENSE_SMTP_PORT", "587")),
                user=env["TABLESENSE_SMTP_USER"],
                password=env["TABLESENSE_SMTP_PASSWORD"],
                sender=env.get("TABLESENSE_EMAIL_FROM") or env["TABLESENSE_SMTP_USER"],
                recipients=_split(env["TABLESENSE_EMAIL_TO"]),
            ))
        elif ch == "sms":
            keys = ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM", "TABLESENSE_SMS_TO"]
            _require(env, keys, "SMS")
            out.append(SmsNotifier(
                env["TWILIO_ACCOUNT_SID"], env["TWILIO_AUTH_TOKEN"], env["TWILIO_FROM"],
                _split(env["TABLESENSE_SMS_TO"]),
            ))
        else:
            raise ValueError(f"unknown notification channel {ch!r} (use push, email, sms, console)")
    return out


# --- send a test alert -----------------------------------------------------------

HINTS = {
    "SMTPAuthenticationError": "Login refused. For Gmail, use a 16-character app password "
    "(not your normal password) and check TABLESENSE_SMTP_USER is the full address.",
    "SMTPRecipientsRefused": "The recipient address was refused. Check TABLESENSE_EMAIL_TO.",
    "gaierror": "Can't find the mail server. Check TABLESENSE_SMTP_HOST and your internet connection.",
    "TimeoutError": "The mail server didn't answer. Check TABLESENSE_SMTP_PORT (587 for Gmail).",
    "ConnectionRefusedError": "Connection refused. Check TABLESENSE_SMTP_HOST and TABLESENSE_SMTP_PORT.",
}


def main(argv: list[str] | None = None) -> int:
    """python -m tablesense.notify --channel email   (sends ONE test message)"""
    import argparse

    p = argparse.ArgumentParser(description="Send one test alert to check your .env settings.")
    p.add_argument("--channel", nargs="+", default=["email"], choices=["push", "console", "email", "sms"])
    p.add_argument("--env-file", default=".env")
    args = p.parse_args(argv)

    if not Path(args.env_file).exists() and set(args.channel) - {"console"}:
        print(f"No {args.env_file} found. Run: cp .env.example .env  and fill it in.")
        return 1
    load_dotenv(args.env_file)
    try:
        notifiers = from_env(args.channel)
    except ValueError as e:
        print(e)
        return 1

    test = Alert(0, "TEST", "test", "This is a test alert from TableSense. If you got this, alerts work.")
    failed = 0
    for n in notifiers:
        to = getattr(n, "recipients", None)
        try:
            n.send(test)
            shown = [mask(r) if n.name == "sms" else r for r in to] if to else []
            print(f"{n.name}: sent{' to ' + ', '.join(shown) if shown else ''}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            name = type(e).__name__
            print(f"{n.name}: FAILED ({name}: {str(e)[:200]})")
            hint = TWILIO_HINTS.get(getattr(e, "code", None)) or HINTS.get(name)
            if hint:
                print(f"  hint: {hint}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

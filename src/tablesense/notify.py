"""Send alerts to staff phones: email (SMTP), SMS (Twilio), or the console.

Configured from environment variables, usually a local `.env` file that is
git-ignored (see `.env.example`). Credentials are never stored in the repo,
the event log, or the alert messages.

Messages contain only a table id and minutes: no images, no personal data.
"""

from __future__ import annotations

import base64
import json
import os
import smtplib
import ssl
import urllib.parse
import urllib.request
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Callable, Mapping, Protocol


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
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(self.user, self.password)
            smtp.send_message(msg)


class SmsNotifier:
    """SMS via Twilio's REST API. Needs a Twilio account and a sending number."""

    name = "sms"

    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        sender: str,
        recipients: list[str],
        urlopen: Callable = urllib.request.urlopen,
        timeout: float = 15,
    ) -> None:
        if not recipients:
            raise ValueError("sms notifier needs at least one recipient")
        self.sid, self.token, self.sender, self.recipients = account_sid, auth_token, sender, recipients
        self.urlopen, self.timeout = urlopen, timeout

    def send(self, alert: Alert) -> None:
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self.sid}/Messages.json"
        auth = base64.b64encode(f"{self.sid}:{self.token}".encode()).decode()
        body = f"{subject_for(alert)}. {alert.message}"
        for to in self.recipients:
            req = urllib.request.Request(
                url,
                data=urllib.parse.urlencode({"From": self.sender, "To": to, "Body": body}).encode(),
                headers={"Authorization": f"Basic {auth}"},
                method="POST",
            )
            with self.urlopen(req, timeout=self.timeout) as resp:
                if resp.status >= 300:
                    raise OSError(f"Twilio returned {resp.status}: {json.loads(resp.read() or b'{}')}")


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
            raise ValueError(f"unknown notification channel {ch!r} (use console, email, sms)")
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
    p.add_argument("--channel", nargs="+", default=["email"], choices=["console", "email", "sms"])
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
            print(f"{n.name}: sent{f' to {chr(44).join(to)}' if to else ''}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            name = type(e).__name__
            print(f"{n.name}: FAILED ({name}: {str(e)[:200]})")
            if name in HINTS:
                print(f"  hint: {HINTS[name]}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

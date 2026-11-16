"""Sign-in for the web app: a short PIN for floor staff, a password for the owner.

Roles
    staff  Floor and Alerts: see tables, tap "Mark served", judge alerts.
    owner  Everything, including Insights, System and the printable report.

Settings come from the environment (usually the git-ignored .env):
    TABLESENSE_STAFF_PIN        4-8 digits, shared by the floor team
    TABLESENSE_OWNER_PASSWORD   at least 10 characters
    TABLESENSE_SESSION_SECRET   optional; keeps people signed in across restarts

Sessions are signed cookies (HMAC-SHA256), HttpOnly and SameSite=Strict.
Changing the PIN or password signs everyone out, because the secret they are
signed with includes both. Wrong guesses are rate-limited per device and
overall, so a 4-digit PIN can't be brute-forced on the venue network.

Standard library only.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

ROLES = ("staff", "owner")
STAFF_HOURS = 12  # one long shift
OWNER_HOURS = 8
COOKIE = "ts_session"


@dataclass(frozen=True)
class Session:
    role: str
    expires: float


@dataclass
class _Failures:
    count: int = 0
    locked_until: float = 0.0


@dataclass
class Auth:
    """Checks secrets, issues and verifies session cookies, rate-limits guessing."""

    staff_pin: str | None = None
    owner_password: str | None = None
    secret: bytes = field(default_factory=lambda: secrets.token_bytes(32))
    clock: Callable[[], float] = time.time
    max_failures: int = 5  # per device, before a lockout
    lockout_s: float = 300  # first lockout; doubles each time after
    global_max_failures: int = 30  # across all devices, per window
    global_window_s: float = 600

    def __post_init__(self) -> None:
        if self.staff_pin is not None and not (self.staff_pin.isdigit() and 4 <= len(self.staff_pin) <= 8):
            raise ValueError("TABLESENSE_STAFF_PIN must be 4-8 digits")
        if self.owner_password is not None and len(self.owner_password) < 10:
            raise ValueError("TABLESENSE_OWNER_PASSWORD must be at least 10 characters")
        if self.staff_pin and self.owner_password and self.staff_pin == self.owner_password:
            raise ValueError("the staff PIN and owner password must differ")
        # Sign with a key derived from the secrets too: changing either signs everyone out.
        material = b"\0".join([self.secret, (self.staff_pin or "").encode(), (self.owner_password or "").encode()])
        self._key = hashlib.sha256(material).digest()
        self._lock = threading.Lock()
        self._fails: dict[str, _Failures] = {}
        self._lockouts: dict[str, int] = {}
        self._recent: list[float] = []

    @property
    def enabled(self) -> bool:
        return bool(self.owner_password)

    # --- sign-in ----------------------------------------------------------------

    def retry_after(self, client: str) -> float:
        """Seconds this device must wait before trying again (0 if it may try now)."""
        now = self.clock()
        with self._lock:
            f = self._fails.get(client)
            wait = max(0.0, f.locked_until - now) if f else 0.0
            self._recent = [t for t in self._recent if now - t < self.global_window_s]
            if len(self._recent) >= self.global_max_failures:
                wait = max(wait, self._recent[0] + self.global_window_s - now)
            return wait

    def login(self, client: str, given: str) -> str | None:
        """The role `given` unlocks, or None. Callers must check retry_after() first."""
        given = (given or "").strip()
        role = None
        # Compare against both every time, in constant time, so timing reveals nothing.
        if self.owner_password and hmac.compare_digest(given.encode(), self.owner_password.encode()):
            role = "owner"
        if self.staff_pin and hmac.compare_digest(given.encode(), self.staff_pin.encode()):
            role = role or "staff"
        now = self.clock()
        with self._lock:
            if role:
                self._fails.pop(client, None)
                return role
            f = self._fails.setdefault(client, _Failures())
            f.count += 1
            self._recent.append(now)
            if f.count >= self.max_failures:
                n = self._lockouts.get(client, 0)
                f.locked_until = now + self.lockout_s * (2**n)
                f.count = 0
                self._lockouts[client] = n + 1
        return None

    # --- session cookies --------------------------------------------------------

    def issue(self, role: str) -> tuple[str, int]:
        """A signed token for `role` and its lifetime in seconds."""
        if role not in ROLES:
            raise ValueError(f"unknown role {role!r}")
        ttl = (OWNER_HOURS if role == "owner" else STAFF_HOURS) * 3600
        payload = f"{role}|{int(self.clock()) + ttl}|{secrets.token_hex(8)}"
        sig = hmac.new(self._key, payload.encode(), hashlib.sha256).digest()
        token = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=") + "." + \
            base64.urlsafe_b64encode(sig).decode().rstrip("=")
        return token, ttl

    def verify(self, token: str | None) -> Session | None:
        if not token or "." not in token:
            return None
        try:
            p64, s64 = token.split(".", 1)
            payload = base64.urlsafe_b64decode(p64 + "=" * (-len(p64) % 4))
            sig = base64.urlsafe_b64decode(s64 + "=" * (-len(s64) % 4))
        except (ValueError, TypeError):
            return None
        if not hmac.compare_digest(sig, hmac.new(self._key, payload, hashlib.sha256).digest()):
            return None
        try:
            role, expires, _ = payload.decode().split("|")
            exp = float(expires)
        except ValueError:
            return None
        if role not in ROLES or exp <= self.clock():
            return None
        return Session(role, exp)


def cookie_header(token: str, max_age: int) -> str:
    # No `Secure`: the app is served over plain http on the venue network. Use a
    # TLS reverse proxy if it's ever reachable from anywhere else (it shouldn't be).
    return f"{COOKIE}={token}; Max-Age={max_age}; Path=/; HttpOnly; SameSite=Strict"


def clear_cookie_header() -> str:
    return f"{COOKIE}=; Max-Age=0; Path=/; HttpOnly; SameSite=Strict"


def read_cookie(header: str | None) -> str | None:
    for part in (header or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == COOKIE:
            return v
    return None


def from_env(env: Mapping[str, str]) -> Auth:
    secret = env.get("TABLESENSE_SESSION_SECRET")
    if secret is not None and secret and len(secret) < 32:
        raise ValueError("TABLESENSE_SESSION_SECRET must be at least 32 characters (or leave it out)")
    return Auth(
        staff_pin=env.get("TABLESENSE_STAFF_PIN") or None,
        owner_password=env.get("TABLESENSE_OWNER_PASSWORD") or None,
        **({"secret": secret.encode()} if secret else {}),
    )

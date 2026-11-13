"""TableSense web app: live floor, alerts, weekly insights and system status.

Runs on the venue's edge box; open it on the host-stand tablet or a laptop on
the venue's network. One self-contained page (no internet needed).

    # live, reading the event log the pipeline writes, emailing alerts to staff:
    python -m tablesense.dashboard.server --events data/out/events.db --config configs/my_venue.yaml \\
        --notify email --unserved-min 5

    # demo on synthetic history, clock starting Saturday 18:30 and running 30x
    # (demo mode only allows --notify console):
    python -m tablesense.dashboard.server --events data/out/sim3w.db \\
        --demo-start 2026-10-31T18:30 --speed 30 --notify console

Alerts are checked in the background every few seconds, whether or not the
page is open. New alerts go to every --notify channel. Without --notify they
stay in shadow mode (recorded, never sent).

Standard library only (runs on a small edge box). Binds to 127.0.0.1 by
default. There's no login, so only expose it on the venue's own network
(--host 0.0.0.0), never the internet.
"""

from __future__ import annotations

import argparse
import json
import queue
import re
import sqlite3
import statistics
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from ..analytics.predict import DwellModel
from ..analytics.sessions import build_sessions
from ..analytics.stats import DAYS, HOUR_ORDER, compute_week
from ..events.log import EventLog
from ..live import AlertEngine, AlertRules, TableStatus, estimates_for, floor_status
from ..notify import Alert, Notifier
from ..state import State

if TYPE_CHECKING:
    from ..config import VenueConfig

LABEL = {
    State.EMPTY: "Free",
    State.SEATED: "Seated",
    State.AWAY: "Seated",  # the hidden hold looks like seated to staff
    State.NEEDS_CLEARING: "Needs clearing",
}
HISTORY_WINDOW = 36 * 3600  # events needed to know the current floor
MODEL_REFIT_EVERY = 15 * 60
WEEK_CACHE_SECONDS = 60  # real seconds; weekly numbers don't change minute to minute
REASON_TEXT = {
    "seated": "Guests sat down",
    "absent": "Guests out of view (hold started)",
    "returned": "Guests back",
    "hold_expired": "Guests left",
    "reseated_dirty": "New guests at an uncleared table",
    "clearing_visit": "Cleared (staff visit seen)",
    "assumed_clear": "Assumed cleared (timeout)",
    "cleared_in_hold": "Cleared during the hold (tabletop clear)",
    "looks_clear": "Cleared (tabletop seen clear)",
    "manual": "Changed by staff",
}


@dataclass
class Clock:
    """Real time, or a demo clock that starts at `start` and runs `speed`x."""

    start: float | None = None
    speed: float = 1.0
    _t0: float = field(default_factory=time.time)

    def now(self) -> float:
        if self.start is None:
            return time.time()
        return self.start + (time.time() - self._t0) * self.speed

    @property
    def demo(self) -> bool:
        return self.start is not None


class Dashboard:
    def __init__(
        self,
        log: EventLog,
        venue_id: str,
        seats: dict[str, int],
        tz: str,
        clock: Clock,
        rules: AlertRules | None = None,
        notifiers: Sequence[Notifier] = (),
        max_notifications_per_hour: int = 40,
        venue: VenueConfig | None = None,
        tables_per_waiter: float = 5.0,
    ) -> None:
        self.log = log
        self.venue_id = venue_id
        self.seats = seats
        self.zone = ZoneInfo(tz)
        self.clock = clock
        # Demo clocks can rewind on restart, so demo alerts live in memory only.
        mem = sqlite3.connect(":memory:", check_same_thread=False) if clock.demo else None
        self.alerts = AlertEngine(log, venue_id, rules, conn=mem)
        self.notifiers = list(notifiers)
        self.max_per_hour = max_notifications_per_hour
        self._sent_times: deque[float] = deque()  # real wall-clock send times, for the rate limit
        self._outbox: queue.Queue[int] = queue.Queue()
        self.lock = threading.RLock()  # one SQLite connection, several threads
        # Visits already seated longer than the threshold when we start were
        # missed while we were down: don't alert for all of them at once.
        r = self.alerts.rules
        self.unserved_not_before = None if r.unserved_after is None else clock.now() - r.unserved_after
        self.model: DwellModel | None = None
        self.model_fitted_at = float("-inf")
        runs = log.runs()
        self.synthetic = any(r.source == "synthetic" for r in runs if r.venue_id == venue_id)
        self.venue = venue
        self.tables_per_waiter = tables_per_waiter
        self._week_cache: dict[tuple[str, int], tuple[float, dict]] = {}

    # --- core ---------------------------------------------------------------

    def _model(self, now: float) -> DwellModel:
        if self.model is None or abs(now - self.model_fitted_at) > MODEL_REFIT_EVERY:
            history = [e for e in self.log.events(venue_id=self.venue_id, until=now) if e.detected_at <= now]
            self.model = DwellModel(str(self.zone)).fit(build_sessions(history))
            self.model_fitted_at = now
        return self.model

    def _floor(self, now: float) -> list[TableStatus]:
        events = self.log.events(venue_id=self.venue_id, since=now - HISTORY_WINDOW, until=now + 1)
        return floor_status(events, self.seats, now)

    def tick(self) -> list[int]:
        """Check alert rules now. New alerts are queued for delivery."""
        with self.lock:
            now = self.clock.now()
            floor = self._floor(now)
            new = self.alerts.update(
                floor, now,
                staff_contact=self.alerts.served_tables(floor, now),
                unserved_not_before=self.unserved_not_before,
            )
        if self.notifiers:
            for aid in new:
                self._outbox.put(aid)
        return new

    def served(self, table_id: str) -> bool:
        """A staff member marked a seated table as served."""
        with self.lock:
            now = self.clock.now()
            t = next((t for t in self._floor(now) if t.table_id == table_id), None)
            if t is None or t.state not in (State.SEATED, State.AWAY):
                return False
            self.alerts.record_contact(table_id, now, seated_at=t.since)
        self.tick()  # resolves an open 'unserved' alert right away
        return True

    # --- delivery -----------------------------------------------------------

    def deliver_pending(self, block: bool = False) -> int:
        """Send queued alerts. Returns how many were processed."""
        n = 0
        while True:
            try:
                aid = self._outbox.get(block=block, timeout=1 if block else None)
            except queue.Empty:
                return n
            block = False
            n += 1
            with self.lock:
                a = self.alerts.get(aid)
            if a is None:
                continue
            now = time.time()
            while self._sent_times and now - self._sent_times[0] > 3600:
                self._sent_times.popleft()
            if len(self._sent_times) >= self.max_per_hour:
                with self.lock:
                    self.alerts.mark_delivered(aid, "skipped: hourly limit reached", ok=False)
                continue
            results = []
            for nt in self.notifiers:
                try:
                    nt.send(Alert(a["id"], a["table_id"], a["kind"], a["message"]))
                    results.append((nt.name, True, ""))
                except Exception as e:  # noqa: BLE001 - a failed channel mustn't stop the others
                    results.append((nt.name, False, f"{type(e).__name__}: {e}"[:200]))
            self._sent_times.append(now)
            ok = any(r[1] for r in results)
            note = "; ".join(f"{name}: sent" if good else f"{name}: failed ({err})" for name, good, err in results)
            with self.lock:
                self.alerts.mark_delivered(aid, note, ok)

    def start_background(self, interval: float = 5.0) -> None:
        def check_loop() -> None:
            while True:
                try:
                    self.tick()
                except Exception as e:  # noqa: BLE001 - keep checking even if one tick fails
                    print(f"alert check failed: {e}")
                time.sleep(interval)

        def send_loop() -> None:
            while True:
                self.deliver_pending(block=True)

        threading.Thread(target=check_loop, daemon=True, name="alert-check").start()
        if self.notifiers:
            threading.Thread(target=send_loop, daemon=True, name="alert-send").start()

    # --- API payloads -------------------------------------------------------

    def status(self) -> dict:
        self.tick()
        with self.lock:
            now = self.clock.now()
            floor = self._floor(now)
            est = estimates_for(self._model(now), floor, now)
            served = self.alerts.served_tables(floor, now)
            alerts = self.alerts.recent(now=now)
            shadow = self.alerts.shadow_summary()
            contact = self.alerts.first_contact_minutes()

        tables = []
        for t in floor:
            e = est[t.table_id]
            tables.append({
                "id": t.table_id,
                "seats": t.seats,
                "state": t.state.value,
                "color": t.color,
                "label": LABEL[t.state],
                "minutes": round((now - t.since) / 60),
                "group_size": t.group_size,
                "served": t.table_id in served,
                "free_in_min": None if t.state is State.EMPTY else round(e.seconds / 60),
                "free_low_min": round(e.low / 60),
                "free_high_min": round(e.high / 60),
                "basis": e.basis,
            })
        counts = {lab: sum(1 for t in tables if t["label"] == lab) for lab in ("Free", "Seated", "Needs clearing")}
        local = datetime.fromtimestamp(now, self.zone)
        r = self.alerts.rules
        return {
            "venue": self.venue_id,
            "now": now,
            "now_local": local.strftime("%a %H:%M"),
            "mode": "demo" if self.clock.demo else "live",
            "speed": self.clock.speed,
            "synthetic": self.synthetic,
            "notify": [n.name for n in self.notifiers],
            "unserved_after_min": None if r.unserved_after is None else round(r.unserved_after / 60),
            "tables": tables,
            "counts": counts,
            "alerts": alerts,
            "shadow": shadow,
            "first_contact": {
                "visits": len(contact),
                "median_min": round(statistics.median(contact), 1) if contact else None,
            },
        }

    # --- weekly insights ------------------------------------------------------

    def _monday(self, ts: float) -> date:
        local = datetime.fromtimestamp(ts, self.zone)
        if local.hour < 6:  # trading days run 06:00-06:00
            local -= timedelta(days=1)
        d = local.date()
        return d - timedelta(days=d.weekday())

    def available_weeks(self, now: float) -> list[str]:
        """Mondays of weeks that have any data, oldest first, up to now."""
        with self.lock:
            row = self.log.conn.execute(
                "SELECT MIN(at) FROM table_events WHERE venue_id = ?", (self.venue_id,)
            ).fetchone()
        if not row or row[0] is None:
            return []
        first, last = self._monday(row[0]), self._monday(now)
        out = []
        while first <= last:
            out.append(first.isoformat())
            first += timedelta(days=7)
        return out

    def week(self, start: str | None = None, days: int = 7) -> dict:
        """Owner numbers for one trading week (default: the last complete one)."""
        now = self.clock.now()
        weeks = self.available_weeks(now)
        current = self._monday(now).isoformat()
        if start is None:
            done = [w for w in weeks if w < current]
            start = done[-1] if done else current
        d0 = date.fromisoformat(start)  # ValueError -> 400
        if not 1 <= days <= 31:
            raise ValueError("days must be 1-31")
        key = (d0.isoformat(), days)
        hit = self._week_cache.get(key)
        if hit and time.time() - hit[0] < WEEK_CACHE_SECONDS:
            return hit[1]

        t0 = datetime(d0.year, d0.month, d0.day, 6, tzinfo=self.zone)
        t1 = t0 + timedelta(days=days)
        end = min(t1.timestamp(), now)
        with self.lock:
            # A visit can start before the period: look back a day for its events.
            events = [e for e in self.log.events(venue_id=self.venue_id, since=t0.timestamp() - 86400, until=end)
                      if e.detected_at <= now]
        st = compute_week(build_sessions(events), t0, t1, len(self.seats), str(self.zone), self.tables_per_waiter)
        used = [i for i, h in enumerate(HOUR_ORDER) if any(st.occupancy[d][h] > 0 for d in range(7))]
        hours = list(HOUR_ORDER[min(used): max(used) + 1]) if used else list(range(8, 22))
        out = {
            "start": d0.isoformat(),
            "days": days,
            "partial": end < t1.timestamp(),
            "label": f"{t0:%a %d %b} – {(t1 - timedelta(days=1)):%a %d %b %Y}",
            "available": weeks,
            "synthetic": self.synthetic,
            "n_tables": st.n_tables,
            "tables_per_waiter": st.tables_per_waiter,
            "days_of_week": list(DAYS),
            "hours": hours,
            "occupancy_pct": [[round(v, 1) for v in row] for row in st.avg_occupancy_pct],
            "staffing": st.staffing,
            "visits": st.visits,
            "visits_by_day": st.visits_by_day,
            "turns_per_table_per_day": st.turns_per_table_per_day,
            "peak_hours": [{"day": d, "hour": h, "tables": round(o, 1)} for d, h, o in st.peak_hours],
            "dwell_by_daypart": st.dwell_by_daypart,
            "dwell_by_group": st.dwell_by_group,
            "dirty": st.dirty,
        }
        self._week_cache[key] = (time.time(), out)
        return out

    def report_html(self, start: str | None = None) -> str:
        """The printable owner report for a week (same as analytics.report)."""
        from ..analytics.report import render_html

        w = self.week(start)
        d0 = date.fromisoformat(w["start"])
        t0 = datetime(d0.year, d0.month, d0.day, 6, tzinfo=self.zone)
        with self.lock:
            events = [e for e in self.log.events(venue_id=self.venue_id, since=t0.timestamp() - 86400,
                                                 until=min((t0 + timedelta(days=7)).timestamp(), self.clock.now()))]
        st = compute_week(build_sessions(events), t0, t0 + timedelta(days=7), len(self.seats),
                          str(self.zone), self.tables_per_waiter)
        return render_html(st, self.venue_id, self.synthetic)

    # --- one table --------------------------------------------------------------

    def table_history(self, table_id: str) -> dict | None:
        """Today's state changes for one table (trading day, 06:00 onwards)."""
        if table_id not in self.seats:
            return None
        now = self.clock.now()
        local = datetime.fromtimestamp(now, self.zone)
        day = local - timedelta(days=1) if local.hour < 6 else local
        t0 = day.replace(hour=6, minute=0, second=0, microsecond=0).timestamp()
        with self.lock:
            events = [e for e in self.log.events(venue_id=self.venue_id, since=t0, until=now + 1)
                      if e.table_id == table_id and e.detected_at <= now]
        sessions = [s for s in build_sessions(events) if s.seated_at >= t0]
        dwell = [s.dwell for s in sessions if s.dwell is not None]
        return {
            "id": table_id,
            "seats": self.seats[table_id],
            "visits_today": len(sessions),
            "median_dwell_min": round(statistics.median(dwell) / 60) if dwell else None,
            "events": [
                {
                    "at": e.at,
                    "at_local": datetime.fromtimestamp(e.at, self.zone).strftime("%H:%M"),
                    "to": e.to_state.value,
                    "label": LABEL[e.to_state],
                    "reason": REASON_TEXT.get(e.reason, e.reason),
                    "people": e.people_count,
                    "delay_s": round(e.detected_at - e.at),
                }
                for e in reversed(events)
            ][:40],
        }

    # --- system -------------------------------------------------------------------

    def system(self) -> dict:
        now = self.clock.now()
        with self.lock:
            runs = [r for r in self.log.runs() if r.venue_id == self.venue_id]
            n_events = self.log.conn.execute(
                "SELECT COUNT(*), MIN(at), MAX(at) FROM table_events WHERE venue_id = ?", (self.venue_id,)
            ).fetchone()
        fmt = lambda ts: None if ts is None else datetime.fromtimestamp(ts, self.zone).strftime("%d %b %Y %H:%M")
        r = self.alerts.rules
        cameras, tables = [], []
        if self.venue:
            cameras = [{"id": c.id, "fps": c.fps, "imgsz": c.imgsz, "conf": c.conf, "tiles": c.tiles,
                        "source": "live stream" if c.source.startswith(("rtsp", "http")) else "recorded file"}
                       for c in self.venue.cameras]
            tables = [{"id": t.id, "camera": t.camera, "seats": t.seats, "tabletop_check": bool(t.top and t.reference)}
                      for t in self.venue.tables]
            timers = {k: getattr(self.venue.timers, k) for k in (
                "t_seat", "t_gone", "t_return", "t_away", "t_clear_assume", "t_top_clear")}
        else:
            timers = None
        return {
            "venue": self.venue_id,
            "mode": "demo" if self.clock.demo else "live",
            "speed": self.clock.speed,
            "synthetic": self.synthetic,
            "timezone": str(self.zone),
            "now_local": fmt(now),
            "tables": len(self.seats),
            "seats": sum(self.seats.values()),
            "data": {"events": n_events[0], "first": fmt(n_events[1]), "last": fmt(n_events[2])},
            "runs": [{"source": x.source, "camera": x.camera_id, "model": x.model, "started": fmt(x.started_at)}
                     for x in runs[-8:]][::-1],
            "runs_total": len(runs),
            "alerts": {
                "channels": [n.name for n in self.notifiers],
                "shadow_mode": not self.notifiers,
                "clear_after_min": None if r.needs_clearing_after is None else round(r.needs_clearing_after / 60, 1),
                "unserved_after_min": None if r.unserved_after is None else round(r.unserved_after / 60, 1),
                "max_per_hour": self.max_per_hour,
            },
            "cameras": cameras,
            "table_config": tables,
            "timers": timers,
        }

    def verdict(self, alert_id: int, verdict: str) -> bool:
        with self.lock:
            return self.alerts.set_verdict(alert_id, verdict, self.clock.now())


def make_handler(dash: Dashboard) -> Callable[..., BaseHTTPRequestHandler]:
    page = resources.files("tablesense.dashboard").joinpath("index.html").read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj) -> None:
            self._send(code, json.dumps(obj).encode(), "application/json")

        def _body(self) -> dict:
            length = min(int(self.headers.get("Content-Length", 0)), 1024)
            return json.loads(self.rfile.read(length) or b"{}")

        def do_GET(self) -> None:
            url = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            try:
                if url.path in ("/", "/index.html"):
                    self._send(200, page, "text/html; charset=utf-8")
                elif url.path == "/api/status":
                    self._json(200, dash.status())
                elif url.path == "/api/week":
                    self._json(200, dash.week(q.get("start"), int(q.get("days", 7))))
                elif url.path == "/api/system":
                    self._json(200, dash.system())
                elif m := re.fullmatch(r"/api/tables/([A-Za-z0-9_-]{1,32})", url.path):
                    h = dash.table_history(m.group(1))
                    self._json(200, h) if h else self._json(404, {"error": "no such table"})
                elif url.path == "/report":
                    self._send(200, dash.report_html(q.get("start")).encode(), "text/html; charset=utf-8")
                else:
                    self._json(404, {"error": "not found"})
            except ValueError as e:
                self._json(400, {"error": str(e)})

        def do_POST(self) -> None:
            try:
                if m := re.fullmatch(r"/api/alerts/(\d+)/verdict", self.path):
                    ok = dash.verdict(int(m.group(1)), self._body().get("verdict"))
                    return self._json(200 if ok else 404, {"ok": ok})
                if m := re.fullmatch(r"/api/tables/([A-Za-z0-9_-]{1,32})/served", self.path):
                    ok = dash.served(m.group(1))
                    return self._json(200 if ok else 409, {"ok": ok})
            except (ValueError, AttributeError) as e:
                return self._json(400, {"error": str(e)})
            self._json(404, {"error": "not found"})

        def log_message(self, *args) -> None:  # keep the console quiet
            pass

    return Handler


def main(argv: list[str] | None = None) -> int:
    from ..notify import from_env, load_dotenv

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--events", required=True)
    p.add_argument("--config", help="venue config (table ids and seats)")
    p.add_argument("--venue-id", help="default: the config's, or the only venue in the log")
    p.add_argument("--tz", default=None, help="default: config timezone or Europe/Dublin")
    p.add_argument("--demo-start", help="start a demo clock at this local time (ISO)")
    p.add_argument("--speed", type=float, default=1.0, help="demo clock speed")
    p.add_argument("--clear-alert-min", type=float, default=3.0, help="alert when yellow this long")
    p.add_argument("--unserved-min", type=float, help="alert when seated this long and not marked served (off if unset)")
    p.add_argument("--notify", nargs="*", default=[], choices=["push", "messages", "console", "email", "sms"],
                   help="where to send alerts (settings come from .env)")
    p.add_argument("--max-per-hour", type=int, default=40, help="cap on notifications sent per hour")
    p.add_argument("--tables-per-waiter", type=float, default=5.0, help="for the staffing suggestion")
    p.add_argument("--env-file", default=".env")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    args = p.parse_args(argv)

    if args.demo_start and set(args.notify) - {"console"}:
        p.error("demo mode only allows --notify console (a fast demo clock would flood real phones)")
    load_dotenv(args.env_file)
    try:
        notifiers = from_env(args.notify)
    except ValueError as e:
        p.error(str(e))

    log = EventLog(args.events)
    venue = None
    if args.config:
        from ..config import load_venue_config

        venue = load_venue_config(args.config)
        venue_id = args.venue_id or venue.venue_id
        seats = {t.id: t.seats or 4 for t in venue.tables}
        tz = args.tz or venue.timezone
    else:
        from ..sim import DEFAULT_TABLE_SEATS

        venues = sorted({r.venue_id for r in log.runs()})
        venue_id = args.venue_id or (venues[0] if len(venues) == 1 else p.error("pass --venue-id or --config"))
        tables = sorted({e.table_id for e in log.events(venue_id=venue_id)}, key=lambda t: (len(t), t))
        seats = {t: (DEFAULT_TABLE_SEATS[i] if i < len(DEFAULT_TABLE_SEATS) else 4) for i, t in enumerate(tables)}
        tz = args.tz or "Europe/Dublin"

    start = None
    if args.demo_start:
        start = datetime.fromisoformat(args.demo_start).replace(tzinfo=ZoneInfo(tz)).timestamp()
    rules = AlertRules(
        needs_clearing_after=args.clear_alert_min * 60,
        unserved_after=None if args.unserved_min is None else args.unserved_min * 60,
    )
    dash = Dashboard(log, venue_id, seats, tz, Clock(start, args.speed), rules, notifiers, args.max_per_hour,
                     venue=venue, tables_per_waiter=args.tables_per_waiter)
    dash.start_background(interval=max(0.5, 5.0 / args.speed) if start else 5.0)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(dash))
    sends = ", ".join(n.name for n in notifiers) or "nowhere (shadow mode)"
    print(f"dashboard for {venue_id} ({len(seats)} tables) on http://{args.host}:{server.server_port}"
          f"{'  [DEMO clock ' + str(args.speed) + 'x]' if start else ''}; alerts go to {sends}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

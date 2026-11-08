"""Live floor dashboard for the host stand (a tablet on the venue's network).

    # live, reading the event log the pipeline writes:
    python -m tablesense.dashboard.server --events data/out/events.db --config configs/my_venue.yaml

    # demo on synthetic history, clock starting Saturday 18:30 and running 30x:
    python -m tablesense.dashboard.server --events data/out/sim3w.db \\
        --demo-start 2026-10-31T18:30 --speed 30

Standard library only (runs on a small edge box). Binds to 127.0.0.1 by
default. There's no login, so only expose it on the venue's own network
(--host 0.0.0.0), never the internet.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib import resources
from typing import Callable
from zoneinfo import ZoneInfo

from ..analytics.predict import DwellModel
from ..analytics.sessions import build_sessions
from ..events.log import EventLog
from ..live import AlertEngine, AlertRules, estimates_for, floor_status
from ..state import State

LABEL = {
    State.EMPTY: "Free",
    State.SEATED: "Seated",
    State.AWAY: "Seated",  # the hidden hold looks like seated to staff
    State.NEEDS_CLEARING: "Needs clearing",
}
HISTORY_WINDOW = 36 * 3600  # events needed to know the current floor
MODEL_REFIT_EVERY = 15 * 60


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
    ) -> None:
        self.log = log
        self.venue_id = venue_id
        self.seats = seats
        self.zone = ZoneInfo(tz)
        self.clock = clock
        # Demo clocks can rewind on restart, so demo alerts live in memory only.
        self.alerts = AlertEngine(log, venue_id, rules, conn=sqlite3.connect(":memory:", check_same_thread=False) if clock.demo else None)
        self.model: DwellModel | None = None
        self.model_fitted_at = float("-inf")
        runs = log.runs()
        self.synthetic = any(r.source == "synthetic" for r in runs if r.venue_id == venue_id)

    def _model(self, now: float) -> DwellModel:
        if self.model is None or abs(now - self.model_fitted_at) > MODEL_REFIT_EVERY:
            history = [e for e in self.log.events(venue_id=self.venue_id, until=now) if e.detected_at <= now]
            self.model = DwellModel(str(self.zone)).fit(build_sessions(history))
            self.model_fitted_at = now
        return self.model

    def status(self) -> dict:
        now = self.clock.now()
        events = self.log.events(venue_id=self.venue_id, since=now - HISTORY_WINDOW, until=now + 1)
        floor = floor_status(events, self.seats, now)
        est = estimates_for(self._model(now), floor, now)
        self.alerts.update(floor, now)

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
                "free_in_min": None if t.state is State.EMPTY else round(e.seconds / 60),
                "free_low_min": round(e.low / 60),
                "free_high_min": round(e.high / 60),
                "basis": e.basis,
            })
        counts = {lab: sum(1 for t in tables if t["label"] == lab) for lab in ("Free", "Seated", "Needs clearing")}
        local = datetime.fromtimestamp(now, self.zone)
        return {
            "venue": self.venue_id,
            "now": now,
            "now_local": local.strftime("%a %H:%M"),
            "mode": "demo" if self.clock.demo else "live",
            "speed": self.clock.speed,
            "synthetic": self.synthetic,
            "tables": tables,
            "counts": counts,
            "alerts": self.alerts.recent(now=now),
            "shadow": self.alerts.shadow_summary(),
        }

    def verdict(self, alert_id: int, verdict: str) -> bool:
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

        def do_GET(self) -> None:  # noqa: N802
            if self.path in ("/", "/index.html"):
                self._send(200, page, "text/html; charset=utf-8")
            elif self.path == "/api/status":
                self._json(200, dash.status())
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            m = re.fullmatch(r"/api/alerts/(\d+)/verdict", self.path)
            if not m:
                return self._json(404, {"error": "not found"})
            try:
                length = min(int(self.headers.get("Content-Length", 0)), 1024)
                verdict = json.loads(self.rfile.read(length) or b"{}").get("verdict")
                ok = dash.verdict(int(m.group(1)), verdict)
            except (ValueError, AttributeError) as e:
                return self._json(400, {"error": str(e)})
            self._json(200 if ok else 404, {"ok": ok})

        def log_message(self, *args) -> None:  # keep the console quiet
            pass

    return Handler


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--events", required=True)
    p.add_argument("--config", help="venue config (table ids and seats)")
    p.add_argument("--venue-id", help="default: the config's, or the only venue in the log")
    p.add_argument("--tz", default=None, help="default: config timezone or Europe/Dublin")
    p.add_argument("--demo-start", help="start a demo clock at this local time (ISO)")
    p.add_argument("--speed", type=float, default=1.0, help="demo clock speed")
    p.add_argument("--clear-alert-min", type=float, default=3.0)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    args = p.parse_args(argv)

    log = EventLog(args.events)
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
    dash = Dashboard(log, venue_id, seats, tz, Clock(start, args.speed),
                     AlertRules(needs_clearing_after=args.clear_alert_min * 60))
    server = HTTPServer((args.host, args.port), make_handler(dash))
    print(f"dashboard for {venue_id} ({len(seats)} tables) on http://{args.host}:{server.server_port}"
          f"{'  [DEMO clock ' + str(args.speed) + 'x]' if start else ''}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

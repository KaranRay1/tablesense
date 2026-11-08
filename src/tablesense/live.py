"""Live floor status and shadow-mode alerts, derived from the event log.

A table's current state is its latest event, so the same code serves live
runs, replays of recorded footage, and the synthetic demo.

Alerts run in SHADOW MODE: they're recorded with a verdict slot, never pushed
to phones. A manager marks each one right or wrong. That gives the
"false alerts per shift" number needed before going live (docs/02-roadmap.md,
Phase 4).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Sequence

from .analytics.predict import DwellModel, FreeEstimate, TableNow, free_estimates
from .analytics.sessions import build_sessions
from .events.log import Event, EventLog
from .state import COLOR_OF, State

ALERTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts (
    id           INTEGER PRIMARY KEY,
    venue_id     TEXT NOT NULL,
    table_id     TEXT NOT NULL,
    kind         TEXT NOT NULL,      -- 'needs_clearing' | 'unserved'
    fired_at     REAL NOT NULL,      -- epoch (venue clock)
    resolved_at  REAL,               -- when the condition ended
    message      TEXT NOT NULL,
    delivered    INTEGER NOT NULL DEFAULT 0,   -- 0 in shadow mode
    verdict      TEXT,               -- NULL | 'correct' | 'wrong'
    verdict_at   REAL
);
CREATE INDEX IF NOT EXISTS idx_alerts_open ON alerts (venue_id, table_id, kind, resolved_at);
"""


@dataclass(frozen=True)
class TableStatus:
    table_id: str
    seats: int
    state: State
    since: float  # seated_at (SEATED/AWAY), left_at (NEEDS_CLEARING), or last change (EMPTY)
    group_size: int

    @property
    def color(self) -> str:
        return COLOR_OF[self.state].value


def floor_status(events: Sequence[Event], seats: dict[str, int], now: float) -> list[TableStatus]:
    """State of every table at `now`, using only events the system knew by then."""
    known = [e for e in events if e.detected_at <= now]
    sessions = build_sessions(known)
    last_session = {}
    for s in sessions:
        last_session[s.table_id] = s
    last_event: dict[str, Event] = {}
    for e in known:
        last_event[e.table_id] = e

    out = []
    for tid, n_seats in seats.items():
        e = last_event.get(tid)
        if e is None:
            out.append(TableStatus(tid, n_seats, State.EMPTY, now, 0))
            continue
        s = last_session.get(tid)
        if e.to_state in (State.SEATED, State.AWAY) and s is not None:
            out.append(TableStatus(tid, n_seats, e.to_state, s.seated_at, s.group_size))
        elif e.to_state is State.NEEDS_CLEARING:
            out.append(TableStatus(tid, n_seats, e.to_state, e.at, 0))
        else:
            out.append(TableStatus(tid, n_seats, e.to_state, e.at, 0))
    return out


def estimates_for(model: DwellModel, status: Sequence[TableStatus], now: float) -> dict[str, FreeEstimate]:
    tables = [TableNow(t.table_id, t.seats, t.state, t.since, t.group_size) for t in status]
    return {e.table_id: e for e in free_estimates(model, tables, now)}


@dataclass(frozen=True)
class AlertRules:
    needs_clearing_after: float = 3 * 60  # yellow this long -> alert
    # Seated this long with no staff contact -> alert. Disabled (None) until
    # staff detection exists: without it, every seated table would alert.
    unserved_after: float | None = None


class AlertEngine:
    def __init__(
        self,
        log: EventLog,
        venue_id: str,
        rules: AlertRules | None = None,
        conn: sqlite3.Connection | None = None,
    ) -> None:
        # `conn` overrides where alerts are stored (e.g. in memory for demos).
        self.conn: sqlite3.Connection = conn or log.conn
        self.conn.executescript(ALERTS_SCHEMA)
        self.venue_id = venue_id
        self.rules = rules or AlertRules()

    def _open(self) -> dict[tuple[str, str], int]:
        rows = self.conn.execute(
            "SELECT id, table_id, kind FROM alerts WHERE venue_id = ? AND resolved_at IS NULL",
            (self.venue_id,),
        ).fetchall()
        return {(t, k): i for i, t, k in rows}

    def update(self, status: Sequence[TableStatus], now: float, staff_contact: set[str] | None = None) -> list[int]:
        """Fire new alerts and resolve finished ones. Returns ids of new alerts."""
        r = self.rules
        should: dict[tuple[str, str], str] = {}
        for t in status:
            mins = (now - t.since) / 60
            if t.state is State.NEEDS_CLEARING and now - t.since >= r.needs_clearing_after:
                should[(t.table_id, "needs_clearing")] = (
                    f"Table {t.table_id} needs clearing: guests left {mins:.0f} min ago"
                )
            if (
                r.unserved_after is not None
                and t.state is State.SEATED
                and now - t.since >= r.unserved_after
                and t.table_id not in (staff_contact or set())
            ):
                should[(t.table_id, "unserved")] = f"Table {t.table_id}: seated {mins:.0f} min, no staff contact seen"

        open_ = self._open()
        new_ids = []
        for key, msg in should.items():
            if key not in open_:
                cur = self.conn.execute(
                    "INSERT INTO alerts (venue_id, table_id, kind, fired_at, message) VALUES (?, ?, ?, ?, ?)",
                    (self.venue_id, key[0], key[1], now, msg),
                )
                new_ids.append(cur.lastrowid)
        for key, aid in open_.items():
            if key not in should:
                self.conn.execute("UPDATE alerts SET resolved_at = ? WHERE id = ?", (now, aid))
        self.conn.commit()
        return new_ids

    def set_verdict(self, alert_id: int, verdict: str, now: float) -> bool:
        if verdict not in ("correct", "wrong"):
            raise ValueError("verdict must be 'correct' or 'wrong'")
        cur = self.conn.execute(
            "UPDATE alerts SET verdict = ?, verdict_at = ? WHERE id = ? AND venue_id = ?",
            (verdict, now, alert_id, self.venue_id),
        )
        self.conn.commit()
        return cur.rowcount == 1

    def recent(self, limit: int = 30, now: float | None = None) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, table_id, kind, fired_at, resolved_at, message, verdict FROM alerts"
            " WHERE venue_id = ? AND fired_at <= ? ORDER BY fired_at DESC, id DESC LIMIT ?",
            (self.venue_id, float("inf") if now is None else now, limit),
        ).fetchall()
        keys = ("id", "table_id", "kind", "fired_at", "resolved_at", "message", "verdict")
        return [dict(zip(keys, r)) for r in rows]

    def shadow_summary(self) -> dict:
        total, judged, wrong = self.conn.execute(
            "SELECT COUNT(*), COUNT(verdict), SUM(verdict = 'wrong') FROM alerts WHERE venue_id = ?",
            (self.venue_id,),
        ).fetchone()
        return {
            "alerts": total,
            "judged": judged,
            "wrong": wrong or 0,
            "wrong_pct": round(100 * (wrong or 0) / judged) if judged else None,
        }

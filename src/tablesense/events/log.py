"""State-change log in SQLite. The core data product: reports are built from it.

Times are stored as Unix epoch seconds (UTC). Each processing run records its
start time, so the time within the video is `at - runs.started_at`. That is
what evaluation against labels uses.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ..state import State, Transition

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id      TEXT PRIMARY KEY,
    venue_id    TEXT NOT NULL,
    camera_id   TEXT NOT NULL,
    source      TEXT NOT NULL,   -- 'live' | 'replay' | 'synthetic'
    source_uri  TEXT,            -- file path; never store camera credentials here
    started_at  REAL NOT NULL,   -- epoch seconds of video time 0
    model       TEXT,
    created_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS table_events (
    id            INTEGER PRIMARY KEY,
    run_id        TEXT NOT NULL REFERENCES runs(run_id),
    venue_id      TEXT NOT NULL,
    table_id      TEXT NOT NULL,
    at            REAL NOT NULL,   -- epoch seconds: when it happened
    detected_at   REAL NOT NULL,   -- epoch seconds: when the system was sure
    from_state    TEXT NOT NULL,
    to_state      TEXT NOT NULL,
    people_count  INTEGER NOT NULL,
    reason        TEXT NOT NULL,
    manual        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_events_table ON table_events (venue_id, table_id, at);
CREATE INDEX IF NOT EXISTS idx_events_run ON table_events (run_id, detected_at);
"""


@dataclass(frozen=True)
class Event:
    run_id: str
    venue_id: str
    table_id: str
    at: float
    detected_at: float
    from_state: State
    to_state: State
    people_count: int
    reason: str
    manual: bool = False


@dataclass(frozen=True)
class Run:
    run_id: str
    venue_id: str
    camera_id: str
    source: str
    source_uri: str | None
    started_at: float
    model: str | None


def strip_credentials(uri: str) -> str:
    """rtsp://user:pass@host/x -> rtsp://host/x"""
    if "://" in uri and "@" in uri.split("://", 1)[1].split("/", 1)[0]:
        scheme, rest = uri.split("://", 1)
        return f"{scheme}://{rest.split('@', 1)[1]}"
    return uri


class EventLog:
    def __init__(self, path: str | Path) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.executescript(SCHEMA)

    # --- writing -------------------------------------------------------------

    def start_run(
        self,
        run_id: str,
        *,
        venue_id: str,
        camera_id: str,
        source: str,
        started_at: float,
        source_uri: str | None = None,
        model: str | None = None,
    ) -> Run:
        run = Run(
            run_id, venue_id, camera_id, source,
            strip_credentials(source_uri) if source_uri else None, started_at, model,
        )
        self.conn.execute(
            "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (*_run_tuple(run), time.time()),
        )
        self.conn.commit()
        return run

    def write(self, run: Run, tr: Transition) -> None:
        """Write a transition whose times are seconds since the run started."""
        self.write_many(run, [tr])

    def write_many(self, run: Run, transitions: Iterable[Transition]) -> None:
        self.conn.executemany(
            "INSERT INTO table_events (run_id, venue_id, table_id, at, detected_at,"
            " from_state, to_state, people_count, reason, manual)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    run.run_id,
                    run.venue_id,
                    tr.table_id,
                    run.started_at + tr.at,
                    run.started_at + tr.detected_at,
                    tr.from_state.value,
                    tr.to_state.value,
                    tr.people_count,
                    tr.reason,
                    int(tr.manual),
                )
                for tr in transitions
            ],
        )
        self.conn.commit()

    # --- reading -------------------------------------------------------------

    def runs(self) -> list[Run]:
        rows = self.conn.execute(
            "SELECT run_id, venue_id, camera_id, source, source_uri, started_at, model"
            " FROM runs ORDER BY created_at"
        ).fetchall()
        return [Run(*r) for r in rows]

    def run(self, run_id: str | None = None) -> Run:
        """A run by id, or the most recent one."""
        runs = self.runs()
        if not runs:
            raise LookupError("event log has no runs")
        if run_id is None:
            return runs[-1]
        for r in runs:
            if r.run_id == run_id:
                return r
        raise LookupError(f"no run {run_id!r}")

    def events(
        self,
        run_id: str | None = None,
        venue_id: str | None = None,
        since: float | None = None,
        until: float | None = None,
    ) -> list[Event]:
        q = (
            "SELECT run_id, venue_id, table_id, at, detected_at, from_state, to_state,"
            " people_count, reason, manual FROM table_events WHERE 1=1"
        )
        args: list = []
        for col, op, val in (
            ("run_id", "=", run_id),
            ("venue_id", "=", venue_id),
            ("at", ">=", since),
            ("at", "<", until),
        ):
            if val is not None:
                q += f" AND {col} {op} ?"
                args.append(val)
        rows = self.conn.execute(q + " ORDER BY detected_at, id", args).fetchall()
        return [
            Event(r[0], r[1], r[2], r[3], r[4], State(r[5]), State(r[6]), r[7], r[8], bool(r[9]))
            for r in rows
        ]

    def close(self) -> None:
        self.conn.close()


def _run_tuple(r: Run) -> tuple:
    return (r.run_id, r.venue_id, r.camera_id, r.source, r.source_uri, r.started_at, r.model)

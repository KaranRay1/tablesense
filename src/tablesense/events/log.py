"""State-change log in SQLite. The core data product: reports are built from it."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ..state import Transition

SCHEMA = """
CREATE TABLE IF NOT EXISTS table_events (
    id            INTEGER PRIMARY KEY,
    venue_id      TEXT NOT NULL,
    camera_id     TEXT NOT NULL,
    table_id      TEXT NOT NULL,
    at            REAL NOT NULL,   -- when it happened (seconds; see run_start)
    detected_at   REAL NOT NULL,   -- when the system was sure
    from_state    TEXT NOT NULL,
    to_state      TEXT NOT NULL,
    people_count  INTEGER NOT NULL,
    source        TEXT NOT NULL,   -- 'live' | 'replay' | 'manual_override'
    run_id        TEXT NOT NULL    -- one per processing run
);
CREATE INDEX IF NOT EXISTS idx_events_table ON table_events (venue_id, table_id, at);
"""


class EventLog:
    def __init__(self, path: str | Path) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.executescript(SCHEMA)

    def write(
        self, tr: Transition, *, venue_id: str, camera_id: str, source: str, run_id: str
    ) -> None:
        self.conn.execute(
            "INSERT INTO table_events (venue_id, camera_id, table_id, at, detected_at,"
            " from_state, to_state, people_count, source, run_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                venue_id,
                camera_id,
                tr.table_id,
                tr.at,
                tr.detected_at,
                tr.from_state.value,
                tr.to_state.value,
                tr.people_count,
                "manual_override" if tr.manual else source,
                run_id,
            ),
        )
        self.conn.commit()

    def rows(self, run_id: str | None = None) -> list[tuple]:
        q = "SELECT table_id, at, detected_at, from_state, to_state, people_count FROM table_events"
        args: tuple = ()
        if run_id is not None:
            q += " WHERE run_id = ?"
            args = (run_id,)
        return self.conn.execute(q + " ORDER BY detected_at, table_id", args).fetchall()

    def close(self) -> None:
        self.conn.close()

"""Turn table events into visits: seated -> left -> cleared.

A visit ("session") starts when guests sit down and ends when the table is
next usable. AWAY periods (toilet, smoke break) stay inside the visit.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

from ..events.log import Event
from ..state import State


@dataclass(frozen=True)
class Session:
    table_id: str
    seated_at: float  # epoch seconds
    group_size: int  # people counted when seating was confirmed (often an undercount)
    left_at: float | None = None  # None: still seated when the data ends
    cleared_at: float | None = None
    # How the table became usable again:
    #   'clearing_visit'  a clear was observed: dirty time is measured
    #   'assumed_clear'   timeout: real clear time unknown
    #   'reseated_dirty'  new guests sat at an uncleared table
    #   'manual'          staff override
    #   None              still dirty or still seated when the data ends
    cleared_by: str | None = None

    @property
    def dwell(self) -> float | None:
        return None if self.left_at is None else self.left_at - self.seated_at

    @property
    def dirty(self) -> float | None:
        """Seconds the table sat uncleared. Only when a clear was observed."""
        if self.left_at is None or self.cleared_at is None or self.cleared_by != "clearing_visit":
            return None
        return self.cleared_at - self.left_at


def build_sessions(events: Iterable[Event]) -> list[Session]:
    by_table: dict[str, list[Event]] = defaultdict(list)
    for e in events:
        by_table[e.table_id].append(e)

    out: list[Session] = []
    for tid, evs in by_table.items():
        evs.sort(key=lambda e: (e.detected_at, e.at))
        cur: dict | None = None  # the open visit

        def close(**kw) -> None:
            nonlocal cur
            if cur is not None:
                cur.update(kw)
                out.append(Session(**cur))
            cur = None

        for e in evs:
            to = e.to_state
            if to is State.SEATED and e.from_state in (State.EMPTY, State.NEEDS_CLEARING):
                if e.from_state is State.NEEDS_CLEARING:
                    close(cleared_at=e.at, cleared_by="reseated_dirty")
                elif cur is not None:  # SEATED with an open visit: data gap, close what we have
                    close()
                cur = {"table_id": tid, "seated_at": e.at, "group_size": e.people_count}
            elif to is State.NEEDS_CLEARING and cur is not None:
                cur["left_at"] = e.at
            elif to is State.EMPTY and cur is not None:
                if "left_at" not in cur:  # e.g. manual override straight to EMPTY
                    cur["left_at"] = e.at
                close(cleared_at=e.at, cleared_by="manual" if e.manual else e.reason)
            # SEATED <-> AWAY stays inside the visit.
        if cur is not None:
            close()

    return sorted(out, key=lambda s: (s.seated_at, s.table_id))

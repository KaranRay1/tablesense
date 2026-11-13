"""Per-table state machine. Pure logic, no CV.

Input: a stream of (timestamp, people_count) for one table, ideally smoothed
(see smoothing.py), plus optionally whether the tabletop looks clear (see
detect/tabletop.py). Output: Transition records when the table's state changes.

Spec: docs/04-state-machine.md
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields
from enum import Enum
from typing import Any


class State(str, Enum):
    EMPTY = "EMPTY"
    SEATED = "SEATED"
    AWAY = "AWAY"  # hidden hold: guests not visible but may come back
    NEEDS_CLEARING = "NEEDS_CLEARING"


class Color(str, Enum):
    GREEN = "green"
    RED = "red"
    YELLOW = "yellow"


COLOR_OF = {
    State.EMPTY: Color.GREEN,
    State.SEATED: Color.RED,
    State.AWAY: Color.RED,  # never shown as its own color
    State.NEEDS_CLEARING: Color.YELLOW,
}


@dataclass(frozen=True)
class Timers:
    """Thresholds in seconds. Tune per venue from real footage."""

    t_seat: float = 45  # EMPTY -> SEATED: presence needed (filters passers-by)
    t_gone: float = 20  # SEATED -> AWAY: absence needed (filters occlusion)
    t_return: float = 5  # AWAY -> SEATED: presence needed to count as "back"
    t_away: float = 240  # AWAY -> NEEDS_CLEARING: how long the hold lasts
    clearing_visit_min: float = 10  # shortest presence that counts as clearing
    # In NEEDS_CLEARING, presence shorter than this that then ends is a
    # clearing visit; presence lasting this long means new guests sat down.
    clearing_visit_max: float = 90
    t_clear_assume: float = 1200  # NEEDS_CLEARING -> EMPTY if nobody clears it
    max_gap: float = 10  # a larger gap between updates resets the timers
    # How long the tabletop must look clear (only used when a tabletop check
    # is configured) before an AWAY/NEEDS_CLEARING table may turn green.
    t_top_clear: float = 15

    def __post_init__(self) -> None:
        for f in fields(self):
            if getattr(self, f.name) < 0:
                raise ValueError(f"{f.name} must be >= 0")
        if self.clearing_visit_min >= self.clearing_visit_max:
            raise ValueError("clearing_visit_min must be < clearing_visit_max")

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> Timers:
        known = {f.name for f in fields(cls)}
        unknown = set(d) - known
        if unknown:
            raise ValueError(f"unknown timer keys: {sorted(unknown)}")
        return cls(**{k: float(v) for k, v in d.items()})


@dataclass(frozen=True)
class Transition:
    table_id: str
    from_state: State
    to_state: State
    at: float  # when the change really happened (e.g. when guests left)
    detected_at: float  # when the machine was sure; at <= detected_at
    people_count: int
    manual: bool = False
    reason: str = ""  # why it changed: see REASONS

    @property
    def delay(self) -> float:
        return self.detected_at - self.at


REASONS = (
    "seated",  # EMPTY -> SEATED
    "absent",  # SEATED -> AWAY
    "returned",  # AWAY -> SEATED
    "hold_expired",  # AWAY -> NEEDS_CLEARING
    "reseated_dirty",  # NEEDS_CLEARING -> SEATED (new guests at an uncleared table)
    "clearing_visit",  # NEEDS_CLEARING -> EMPTY, a clear was observed
    "assumed_clear",  # NEEDS_CLEARING -> EMPTY by timeout, real clear time unknown
    "cleared_in_hold",  # AWAY -> EMPTY: hold ended and the tabletop looks clear
    "looks_clear",  # NEEDS_CLEARING -> EMPTY: tabletop seen clear with nobody there
    "manual",  # override
)


class TableStateMachine:
    def __init__(
        self,
        table_id: str,
        timers: Timers | None = None,
        initial: State = State.EMPTY,
    ) -> None:
        self.table_id = table_id
        self.timers = timers or Timers()
        self.state = initial
        self._last_ts: float | None = None
        self._present = False
        self._run_start: float | None = None  # start of current present/absent run
        self._away_since: float | None = None  # when guests disappeared
        self._state_since: float | None = None
        self._top_clear_since: float | None = None  # tabletop clear continuously since

    @property
    def color(self) -> Color:
        return COLOR_OF[self.state]

    def update(self, ts: float, people_count: int, top_clear: bool | None = None) -> Transition | None:
        """Feed one observation. Returns a Transition if the state changed.

        `top_clear`: does the tabletop match its clean reference? None means
        unknown (no check configured, or the view is blocked) and keeps the
        previous reading. Without it the machine behaves exactly as before.
        """
        if people_count < 0:
            raise ValueError("people_count must be >= 0")
        if self._last_ts is not None and ts < self._last_ts:
            raise ValueError(f"timestamps went backwards: {ts} < {self._last_ts}")

        present = people_count > 0
        gap = self._last_ts is not None and ts - self._last_ts > self.timers.max_gap
        prev_present, prev_run_start = self._present, self._run_start

        # A long gap (stream dropped) means we don't know what happened, so
        # restart the current run instead of assuming it lasted the whole gap.
        if self._run_start is None or present != self._present or gap:
            self._run_start = ts
        self._present = present
        self._last_ts = ts
        if gap or top_clear is False:
            self._top_clear_since = None
        elif top_clear and self._top_clear_since is None:
            self._top_clear_since = ts
        if self._state_since is None:
            self._state_since = ts

        run = ts - self._run_start
        t = self.timers
        s = self.state
        top_ok = self._top_clear_since is not None and ts - self._top_clear_since >= t.t_top_clear

        if s is State.EMPTY:
            if present and run >= t.t_seat:
                return self._go(State.SEATED, self._run_start, ts, people_count, "seated")

        elif s is State.SEATED:
            if not present and run >= t.t_gone:
                self._away_since = self._run_start
                return self._go(State.AWAY, self._run_start, ts, people_count, "absent")

        elif s is State.AWAY:
            # Hold is measured from when guests left; short blips (a waiter
            # passing, a detection glitch) don't restart it.
            if present and run >= t.t_return:
                self._away_since = None
                return self._go(State.SEATED, self._run_start, ts, people_count, "returned")
            if not present and ts - self._away_since >= t.t_away:
                left_at = self._away_since
                self._away_since = None
                if top_ok:
                    # Cleared during the hold (often by a waiter the people
                    # count mistook for guests coming back). The hold still
                    # runs in full, so guests on a break don't lose the table.
                    return self._go(State.EMPTY, left_at, ts, people_count, "cleared_in_hold")
                return self._go(State.NEEDS_CLEARING, left_at, ts, people_count, "hold_expired")

        elif s is State.NEEDS_CLEARING:
            if present and run >= t.clearing_visit_max:
                return self._go(State.SEATED, self._run_start, ts, people_count, "reseated_dirty")
            ended_visit = (
                prev_present
                and not present
                and not gap
                and prev_run_start is not None
                and t.clearing_visit_min <= ts - prev_run_start < t.clearing_visit_max
            )
            if ended_visit:
                return self._go(State.EMPTY, ts, ts, people_count, "clearing_visit")
            if not present and top_ok:
                cleared_at = max(self._top_clear_since, self._state_since)
                return self._go(State.EMPTY, cleared_at, ts, people_count, "looks_clear")
            if not present and ts - self._state_since >= t.t_clear_assume:
                return self._go(State.EMPTY, ts, ts, people_count, "assumed_clear")

        return None

    def force(self, state: State, ts: float) -> Transition | None:
        """Manual override (e.g. owner taps a table). Also a free label."""
        if state is self.state:
            return None
        self._away_since = ts if state is State.AWAY else None
        self._last_ts = ts if self._last_ts is None else max(ts, self._last_ts)
        return self._go(state, ts, ts, 0, "manual", manual=True)

    def _go(
        self,
        to: State,
        at: float,
        detected_at: float,
        count: int,
        reason: str,
        manual: bool = False,
    ) -> Transition:
        tr = Transition(self.table_id, self.state, to, at, detected_at, count, manual, reason)
        self.state = to
        self._state_since = detected_at
        return tr

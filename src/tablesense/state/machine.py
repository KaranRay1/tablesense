"""Per-table state machine. Pure logic, no CV.

Input: a stream of (timestamp, people_count) for one table, ideally smoothed
(see smoothing.py). Output: Transition records when the table's state changes.

Spec: docs/04-state-machine.md
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from enum import Enum
from typing import Any, Mapping


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

    def __post_init__(self) -> None:
        for f in fields(self):
            if getattr(self, f.name) < 0:
                raise ValueError(f"{f.name} must be >= 0")
        if self.clearing_visit_min >= self.clearing_visit_max:
            raise ValueError("clearing_visit_min must be < clearing_visit_max")

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "Timers":
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

    @property
    def delay(self) -> float:
        return self.detected_at - self.at


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

    @property
    def color(self) -> Color:
        return COLOR_OF[self.state]

    def update(self, ts: float, people_count: int) -> Transition | None:
        """Feed one observation. Returns a Transition if the state changed."""
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
        if self._state_since is None:
            self._state_since = ts

        run = ts - self._run_start
        t = self.timers
        s = self.state

        if s is State.EMPTY:
            if present and run >= t.t_seat:
                return self._go(State.SEATED, self._run_start, ts, people_count)

        elif s is State.SEATED:
            if not present and run >= t.t_gone:
                self._away_since = self._run_start
                return self._go(State.AWAY, self._run_start, ts, people_count)

        elif s is State.AWAY:
            # Hold is measured from when guests left; short blips (a waiter
            # passing, a detection glitch) don't restart it.
            if present and run >= t.t_return:
                self._away_since = None
                return self._go(State.SEATED, self._run_start, ts, people_count)
            if not present and ts - self._away_since >= t.t_away:
                left_at = self._away_since
                self._away_since = None
                return self._go(State.NEEDS_CLEARING, left_at, ts, people_count)

        elif s is State.NEEDS_CLEARING:
            if present and run >= t.clearing_visit_max:
                return self._go(State.SEATED, self._run_start, ts, people_count)
            ended_visit = (
                prev_present
                and not present
                and not gap
                and prev_run_start is not None
                and t.clearing_visit_min <= ts - prev_run_start < t.clearing_visit_max
            )
            if ended_visit:
                return self._go(State.EMPTY, ts, ts, people_count)
            if not present and ts - self._state_since >= t.t_clear_assume:
                return self._go(State.EMPTY, ts, ts, people_count)

        return None

    def force(self, state: State, ts: float) -> Transition | None:
        """Manual override (e.g. owner taps a table). Also a free label."""
        if state is self.state:
            return None
        self._away_since = ts if state is State.AWAY else None
        self._last_ts = ts if self._last_ts is None else max(ts, self._last_ts)
        return self._go(state, ts, ts, 0, manual=True)

    def _go(
        self, to: State, at: float, detected_at: float, count: int, manual: bool = False
    ) -> Transition:
        tr = Transition(self.table_id, self.state, to, at, detected_at, count, manual)
        self.state = to
        self._state_since = detected_at
        return tr

"""Frame -> people -> per-table counts -> smoothed counts -> table states."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from .config import TableConfig
from .detect.people import Detector, Person
from .state import CountSmoother, State, TableStateMachine, Timers, Transition
from .zones.tables import Anchor, assign_people


@dataclass
class FrameResult:
    ts: float
    people: list[Person]
    table_of_person: list[str | None]  # parallel to `people`
    raw_counts: dict[str, int]
    counts: dict[str, int]  # smoothed
    states: dict[str, State]
    transitions: list[Transition]


class TablePipeline:
    def __init__(
        self,
        tables: Sequence[TableConfig],
        detector: Detector,
        timers: Timers | None = None,
        anchor: Anchor = Anchor.CENTER,
        smoothing_window: float = 5.0,
    ) -> None:
        if not tables:
            raise ValueError("pipeline needs at least one table")
        self.tables = tuple(tables)
        self.detector = detector
        self.anchor = anchor
        self.machines = {t.id: TableStateMachine(t.id, timers) for t in self.tables}
        self.smoothers = {t.id: CountSmoother(smoothing_window) for t in self.tables}

    def process(self, ts: float, image: Any) -> FrameResult:
        people = self.detector.detect(image)
        table_of = assign_people([p.box for p in people], self.tables, self.anchor)
        raw = {t.id: 0 for t in self.tables}
        for tid in table_of:
            if tid is not None:
                raw[tid] += 1

        counts, transitions = {}, []
        for tid, machine in self.machines.items():
            counts[tid] = self.smoothers[tid].update(ts, raw[tid])
            tr = machine.update(ts, counts[tid])
            if tr is not None:
                transitions.append(tr)

        return FrameResult(
            ts=ts,
            people=people,
            table_of_person=table_of,
            raw_counts=raw,
            counts=counts,
            states={tid: m.state for tid, m in self.machines.items()},
            transitions=transitions,
        )

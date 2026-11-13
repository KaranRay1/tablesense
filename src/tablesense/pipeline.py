"""Frame -> people -> per-table counts -> smoothed counts -> table states."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .config import TableConfig
from .detect.people import Detector, Person
from .state import CountSmoother, State, TableStateMachine, Timers, Transition
from .zones.tables import Anchor, assign_people

if TYPE_CHECKING:
    from .detect.tabletop import TabletopMonitor


@dataclass
class FrameResult:
    ts: float
    people: list[Person]
    table_of_person: list[str | None]  # parallel to `people`
    raw_counts: dict[str, int]
    counts: dict[str, int]  # smoothed
    states: dict[str, State]
    transitions: list[Transition]
    top_clear: dict[str, bool | None] = field(default_factory=dict)  # tabletop readings, if configured


class TablePipeline:
    def __init__(
        self,
        tables: Sequence[TableConfig],
        detector: Detector,
        timers: Timers | None = None,
        anchor: Anchor = Anchor.CENTER,
        smoothing_window: float = 5.0,
        tabletop: TabletopMonitor | None = None,
    ) -> None:
        if not tables:
            raise ValueError("pipeline needs at least one table")
        self.tables = tuple(tables)
        self.detector = detector
        self.anchor = anchor
        self.machines = {t.id: TableStateMachine(t.id, timers) for t in self.tables}
        self.smoothers = {t.id: CountSmoother(smoothing_window) for t in self.tables}
        self.tabletop = tabletop

    def process(self, ts: float, image: Any) -> FrameResult:
        people = self.detector.detect(image)
        table_of = assign_people([p.box for p in people], self.tables, self.anchor)
        raw = {t.id: 0 for t in self.tables}
        for tid in table_of:
            if tid is not None:
                raw[tid] += 1

        top_clear: dict[str, bool | None] = {}
        if self.tabletop is not None:
            top_clear = self.tabletop.check(image, [p.box for p in people])

        counts, transitions = {}, []
        for tid, machine in self.machines.items():
            counts[tid] = self.smoothers[tid].update(ts, raw[tid])
            tr = machine.update(ts, counts[tid], top_clear.get(tid))
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
            top_clear=top_clear,
        )

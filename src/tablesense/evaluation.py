"""Score the system against hand labels. Metrics: docs/05-metrics.md.

Labels are a CSV of colour segments per table, in seconds from video start:

    table_id,start,end,state
    T1,0,312.5,green
    T1,312.5,1840,red
    T1,1840,2210,yellow

`state` is what a person watching would want staff to see: green (empty and
clean), red (guests here, including guests briefly away), yellow (guests gone,
needs clearing). Time not covered by any label is ignored, so you can label
only the parts you're sure about.

Predictions are compared by **colour as shown at the time** (`detected_at`),
because that's what staff would have seen. SEATED and AWAY are both red.
"""

from __future__ import annotations

import csv
import itertools
import json
import statistics
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .state import COLOR_OF, Color, State

LABEL_ALIASES = {
    "green": Color.GREEN, "empty": Color.GREEN, "g": Color.GREEN,
    "red": Color.RED, "seated": Color.RED, "occupied": Color.RED, "away": Color.RED, "r": Color.RED,
    "yellow": Color.YELLOW, "needs_clearing": Color.YELLOW, "dirty": Color.YELLOW, "y": Color.YELLOW,
}


@dataclass(frozen=True)
class Segment:
    table_id: str
    start: float
    end: float
    color: Color


@dataclass(frozen=True)
class Change:
    table_id: str
    t: float
    from_color: Color
    to_color: Color


# --- labels ------------------------------------------------------------------


def parse_color(value: str) -> Color:
    try:
        return LABEL_ALIASES[value.strip().lower()]
    except KeyError:
        raise ValueError(f"unknown label state {value!r}; use green/red/yellow") from None


def validate_segments(segments: Sequence[Segment]) -> None:
    by_table: dict[str, list[Segment]] = defaultdict(list)
    for s in segments:
        if s.end <= s.start:
            raise ValueError(f"{s.table_id}: segment end {s.end} <= start {s.start}")
        by_table[s.table_id].append(s)
    for tid, segs in by_table.items():
        segs.sort(key=lambda s: s.start)
        for a, b in itertools.pairwise(segs):
            if b.start < a.end - 1e-6:
                raise ValueError(f"{tid}: labels overlap at {b.start}s")


def load_labels(path: str | Path) -> list[Segment]:
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise ValueError(f"no labels in {path}")
    missing = {"table_id", "start", "end", "state"} - set(rows[0])
    if missing:
        raise ValueError(f"labels CSV missing columns: {sorted(missing)}")
    segs = [
        Segment(r["table_id"].strip(), float(r["start"]), float(r["end"]), parse_color(r["state"]))
        for r in rows
    ]
    validate_segments(segs)
    return segs


def segments_from_marks(
    marks: Iterable[tuple[str, float, Color]], end: float, start_color: Color | None = None
) -> list[Segment]:
    """Turn 'table X became COLOR at t' marks (from the labeling tool) into segments.

    Each table's labeled time starts at its first mark unless `start_color` is
    given, in which case it starts at 0 in that colour.
    """
    by_table: dict[str, list[tuple[float, Color]]] = defaultdict(list)
    for tid, t, c in marks:
        by_table[tid].append((t, c))
    out: list[Segment] = []
    for tid, ms in by_table.items():
        ms.sort(key=lambda m: m[0])
        if start_color is not None and ms[0][0] > 0:
            ms.insert(0, (0.0, start_color))
        for (t, c), nxt in zip(ms, ms[1:] + [(end, None)]):
            if nxt[0] > t:
                out.append(Segment(tid, t, nxt[0], c))
    return merge_segments(out)


def merge_segments(segments: Iterable[Segment]) -> list[Segment]:
    """Join touching segments of the same table and colour."""
    out: list[Segment] = []
    for s in sorted(segments, key=lambda s: (s.table_id, s.start)):
        last = out[-1] if out else None
        if last and last.table_id == s.table_id and last.color is s.color and abs(last.end - s.start) < 1e-6:
            out[-1] = Segment(s.table_id, last.start, s.end, s.color)
        else:
            out.append(s)
    return out


# --- predictions -------------------------------------------------------------


def predicted_segments(
    transitions: Iterable[tuple[str, float, State]],
    table_ids: Iterable[str],
    end: float,
    initial: State = State.EMPTY,
) -> list[Segment]:
    """(table_id, time shown, new state) -> colour segments from 0 to `end`."""
    by_table: dict[str, list[tuple[float, State]]] = {tid: [] for tid in table_ids}
    for tid, t, st in transitions:
        by_table.setdefault(tid, []).append((t, st))
    out: list[Segment] = []
    for tid, trs in by_table.items():
        trs.sort(key=lambda x: x[0])
        t0, color = 0.0, COLOR_OF[initial]
        for t, st in trs:
            if t > t0:
                out.append(Segment(tid, t0, min(t, end), color))
            t0, color = max(t0, t), COLOR_OF[st]
            if t0 >= end:
                break
        if t0 < end:
            out.append(Segment(tid, t0, end, color))
    return merge_segments(s for s in out if s.end > s.start)


def changes(segments: Sequence[Segment]) -> list[Change]:
    """Colour changes between touching segments (gaps break the chain)."""
    out = []
    segs = sorted(segments, key=lambda s: (s.table_id, s.start))
    for a, b in itertools.pairwise(segs):
        if a.table_id == b.table_id and abs(a.end - b.start) < 1e-6 and a.color is not b.color:
            out.append(Change(a.table_id, b.start, a.color, b.color))
    return out


# --- metrics -----------------------------------------------------------------


@dataclass
class TableResult:
    table_id: str
    labeled_seconds: float = 0.0
    correct_seconds: float = 0.0
    confusion: dict[str, float] = field(default_factory=dict)  # "true->pred": seconds
    true_changes: int = 0
    matched: int = 0
    missed: int = 0
    false_transitions: int = 0
    delays: list[float] = field(default_factory=list)
    wrong_yellow_seconds: float = 0.0  # shown yellow while guests were there
    wrong_yellow_episodes: int = 0

    @property
    def accuracy(self) -> float | None:
        return self.correct_seconds / self.labeled_seconds if self.labeled_seconds else None


@dataclass
class EvalResult:
    tables: list[TableResult]
    tolerance_before: float
    max_delay: float

    @property
    def labeled_seconds(self) -> float:
        return sum(t.labeled_seconds for t in self.tables)

    def summary(self) -> dict:
        secs = self.labeled_seconds
        delays = [d for t in self.tables for d in t.delays]
        true_changes = sum(t.true_changes for t in self.tables)
        false_tr = sum(t.false_transitions for t in self.tables)
        hours = secs / 3600
        return {
            "labeled_table_hours": round(hours, 3),
            "state_accuracy": round(sum(t.correct_seconds for t in self.tables) / secs, 4) if secs else None,
            "true_changes": true_changes,
            "missed_changes": sum(t.missed for t in self.tables),
            "missed_rate": round(sum(t.missed for t in self.tables) / true_changes, 4) if true_changes else None,
            "false_transitions": false_tr,
            "false_transitions_per_table_hour": round(false_tr / hours, 3) if hours else None,
            "median_delay_s": round(statistics.median(delays), 2) if delays else None,
            "p90_delay_s": round(_percentile(delays, 90), 2) if delays else None,
            "wrong_yellow_seconds": round(sum(t.wrong_yellow_seconds for t in self.tables), 1),
            "wrong_yellow_episodes": sum(t.wrong_yellow_episodes for t in self.tables),
        }

    def to_dict(self) -> dict:
        tables = []
        for t in self.tables:
            d = asdict(t)
            d["accuracy"] = None if t.accuracy is None else round(t.accuracy, 4)
            tables.append(d)
        return {
            "summary": self.summary(),
            "params": {"tolerance_before_s": self.tolerance_before, "max_delay_s": self.max_delay},
            "tables": tables,
        }


def evaluate(
    labels: Sequence[Segment],
    predictions: Sequence[Segment],
    tolerance_before: float = 5.0,
    max_delay: float = 180.0,
) -> EvalResult:
    """Compare predicted colour segments to labels.

    A true colour change is *matched* by the first unused predicted change to
    the same colour within [t - tolerance_before, t + max_delay]. Predicted
    changes inside labeled time that match nothing are false transitions.
    """
    validate_segments(labels)
    results: dict[str, TableResult] = {}
    preds_by_table: dict[str, list[Segment]] = defaultdict(list)
    for p in predictions:
        preds_by_table[p.table_id].append(p)

    for lab in labels:
        r = results.setdefault(lab.table_id, TableResult(lab.table_id))
        r.labeled_seconds += lab.end - lab.start
        for p in preds_by_table[lab.table_id]:
            lo, hi = max(lab.start, p.start), min(lab.end, p.end)
            if hi <= lo:
                continue
            key = f"{lab.color.value}->{p.color.value}"
            r.confusion[key] = r.confusion.get(key, 0.0) + (hi - lo)
            if p.color is lab.color:
                r.correct_seconds += hi - lo
            elif lab.color is Color.RED and p.color is Color.YELLOW:
                r.wrong_yellow_seconds += hi - lo

    # Wrong-yellow episodes: predicted yellow segments that overlap true red.
    for tid, r in results.items():
        reds = [s for s in labels if s.table_id == tid and s.color is Color.RED]
        for p in preds_by_table[tid]:
            if p.color is Color.YELLOW and any(min(p.end, s.end) > max(p.start, s.start) for s in reds):
                r.wrong_yellow_episodes += 1

    true_changes = changes(labels)
    pred_changes = changes(predictions)
    for tid, r in results.items():
        lab_segs = [s for s in labels if s.table_id == tid]
        tc = [c for c in true_changes if c.table_id == tid]
        pc = [c for c in pred_changes if c.table_id == tid and _inside(c.t, lab_segs)]
        used = [False] * len(pc)
        r.true_changes = len(tc)
        for c in sorted(tc, key=lambda c: c.t):
            for i, p in enumerate(pc):
                if not used[i] and p.to_color is c.to_color and c.t - tolerance_before <= p.t <= c.t + max_delay:
                    used[i] = True
                    r.matched += 1
                    r.delays.append(round(p.t - c.t, 3))
                    break
            else:
                r.missed += 1
        r.false_transitions = used.count(False)

    return EvalResult(sorted(results.values(), key=lambda r: r.table_id), tolerance_before, max_delay)


def _inside(t: float, segs: Sequence[Segment]) -> bool:
    """Strictly after a segment starts: a change at the very first labeled
    moment can't be judged (we don't know the colour just before it)."""
    return any(s.start < t <= s.end for s in segs)


def _percentile(xs: Sequence[float], q: float) -> float:
    s = sorted(xs)
    k = (len(s) - 1) * q / 100
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


# --- reporting ---------------------------------------------------------------


def to_markdown(result: EvalResult, title: str) -> str:
    s = result.summary()
    lines = [
        f"# {title}",
        "",
        "| Metric | Value |",
        "|---|---|",
        *(f"| {k} | {v} |" for k, v in s.items()),
        "",
        "| Table | Labeled min | Accuracy | True changes | Missed | False | Median delay s | Wrong-yellow s |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for t in result.tables:
        acc = "-" if t.accuracy is None else f"{t.accuracy:.1%}"
        med = f"{statistics.median(t.delays):.1f}" if t.delays else "-"
        lines.append(
            f"| {t.table_id} | {t.labeled_seconds / 60:.1f} | {acc} | {t.true_changes} | {t.missed}"
            f" | {t.false_transitions} | {med} | {t.wrong_yellow_seconds:.0f} |"
        )
    lines += ["", f"Matching window: -{result.tolerance_before}s / +{result.max_delay}s.", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import argparse

    from .events.log import EventLog

    p = argparse.ArgumentParser(description="Score a processing run against hand labels.")
    p.add_argument("--labels", required=True, help="labels CSV (table_id,start,end,state)")
    p.add_argument("--events", required=True, help="SQLite event log from tablesense.cli")
    p.add_argument("--run-id", help="run to score (default: latest)")
    p.add_argument("--out", help="write <out>.json and <out>.md")
    p.add_argument("--tolerance-before", type=float, default=5.0)
    p.add_argument("--max-delay", type=float, default=180.0)
    args = p.parse_args(argv)

    labels = load_labels(args.labels)
    log = EventLog(args.events)
    run = log.run(args.run_id)
    events = log.events(run.run_id)
    log.close()

    end = max(s.end for s in labels)
    preds = predicted_segments(
        ((e.table_id, e.detected_at - run.started_at, e.to_state) for e in events),
        {s.table_id for s in labels},
        end,
    )
    result = evaluate(labels, preds, args.tolerance_before, args.max_delay)
    md = to_markdown(result, f"Evaluation: run {run.run_id} vs {Path(args.labels).name}")
    print(md)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(f"{args.out}.json").write_text(json.dumps(result.to_dict(), indent=2))
        Path(f"{args.out}.md").write_text(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

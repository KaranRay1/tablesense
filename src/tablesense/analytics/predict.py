"""When will a table be free? A deliberately simple baseline.

Remaining time for a seated group = the median of (dwell - elapsed) over past
visits that were similar (same time of day and group size) AND had already
lasted at least as long as this one. Conditioning on "still here after X
minutes" matters: a group that has been here 60 min is not "almost done" just
because the average visit is 45 min.

Only replace this with ML if a backtest on real data shows it's not good
enough (docs/02-roadmap.md, Phase 3).

    python -m tablesense.analytics.predict --events data/out/sim3w.db --train-days 14 --start 2026-11-02
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from ..state import State
from .sessions import Session
from .stats import daypart

DEFAULT_CLEAR_S = 6 * 60  # used until there's measured dirty-time history


def group_bucket(n: int) -> str:
    return "1" if n <= 1 else "2" if n == 2 else "3-4" if n <= 4 else "5+"


@dataclass(frozen=True)
class Prediction:
    seconds: float  # median estimate
    low: float  # 25th percentile
    high: float  # 75th percentile
    n: int  # past visits the estimate is based on
    basis: str  # which history was used, e.g. "Dinner/3-4", "Dinner", "all", "default"


def _quantiles(xs: Sequence[float]) -> tuple[float, float, float]:
    s = sorted(xs)
    if len(s) == 1:
        return s[0], s[0], s[0]
    q = statistics.quantiles(s, n=4, method="inclusive")
    return q[0], q[1], q[2]


class DwellModel:
    def __init__(self, tz: str = "Europe/Dublin", min_n: int = 15) -> None:
        self.zone = ZoneInfo(tz)
        self.min_n = min_n
        self.by_key: dict[tuple[str, str], list[float]] = defaultdict(list)
        self.by_part: dict[str, list[float]] = defaultdict(list)
        self.all: list[float] = []
        self.dirty: list[float] = []

    def fit(self, sessions: Sequence[Session]) -> DwellModel:
        for s in sessions:
            if s.dwell is None or s.dwell <= 0:
                continue
            part = daypart(datetime.fromtimestamp(s.seated_at, self.zone).hour)
            self.by_key[(part, group_bucket(s.group_size))].append(s.dwell)
            self.by_part[part].append(s.dwell)
            self.all.append(s.dwell)
            if s.dirty is not None:
                self.dirty.append(s.dirty)
        return self

    def remaining(self, seated_at: float, group_size: int, now: float) -> Prediction:
        """Seconds until a seated group leaves."""
        elapsed = max(0.0, now - seated_at)
        part = daypart(datetime.fromtimestamp(seated_at, self.zone).hour)
        for basis, pool in (
            (f"{part}/{group_bucket(group_size)}", self.by_key.get((part, group_bucket(group_size)), [])),
            (part, self.by_part.get(part, [])),
            ("all", self.all),
        ):
            left = [d - elapsed for d in pool if d > elapsed]
            if len(left) >= self.min_n:
                lo, med, hi = _quantiles(left)
                return Prediction(med, lo, hi, len(left), basis)
        # Longer than almost every past visit: they're probably leaving soon.
        return Prediction(5 * 60, 0, 15 * 60, 0, "default")

    def clear_time(self, dirty_since: float, now: float) -> Prediction:
        """Seconds until a dirty table is cleared."""
        elapsed = max(0.0, now - dirty_since)
        left = [d - elapsed for d in self.dirty if d > elapsed]
        if len(left) >= self.min_n:
            lo, med, hi = _quantiles(left)
            return Prediction(med, lo, hi, len(left), "dirty")
        return Prediction(max(60.0, DEFAULT_CLEAR_S - elapsed), 0, DEFAULT_CLEAR_S, 0, "default")


@dataclass(frozen=True)
class TableNow:
    """What the live system knows about a table right now."""

    table_id: str
    seats: int
    state: State
    since: float  # epoch: seated_at for SEATED/AWAY, left_at for NEEDS_CLEARING
    group_size: int = 0


@dataclass(frozen=True)
class FreeEstimate:
    table_id: str
    seats: int
    seconds: float
    low: float
    high: float
    basis: str


def free_estimates(model: DwellModel, tables: Sequence[TableNow], now: float) -> list[FreeEstimate]:
    """Every table's estimated time until it's ready for new guests, soonest first."""
    out = []
    for t in tables:
        if t.state is State.EMPTY:
            out.append(FreeEstimate(t.table_id, t.seats, 0, 0, 0, "free"))
        elif t.state is State.NEEDS_CLEARING:
            c = model.clear_time(t.since, now)
            out.append(FreeEstimate(t.table_id, t.seats, c.seconds, c.low, c.high, "clearing"))
        else:
            r = model.remaining(t.since, t.group_size, now)
            # After they leave the table still needs clearing; the 4-min hold is part of that history.
            c = model.clear_time(now, now)
            out.append(
                FreeEstimate(t.table_id, t.seats, r.seconds + c.seconds, r.low + c.low, r.high + c.high, r.basis)
            )
    return sorted(out, key=lambda e: (e.seconds, e.seats))


def wait_for_party(
    estimates: Sequence[FreeEstimate], party_size: int, queue_position: int = 1
) -> FreeEstimate | None:
    """Estimated wait for the Nth party in the queue needing `party_size` seats.

    Simplification: parties ahead are assumed to need a similar table, so the
    Nth party gets the Nth table that fits. Good enough to say "about 15 min".
    """
    fitting = [e for e in estimates if e.seats >= party_size]
    if len(fitting) < queue_position:
        return None
    return fitting[queue_position - 1]


# --- backtest ------------------------------------------------------------------


def backtest(
    train: Sequence[Session],
    test: Sequence[Session],
    checkpoints_min: Sequence[int] = (10, 30, 60),
    tz: str = "Europe/Dublin",
) -> dict:
    """MAE in minutes of predicted remaining dwell, vs a naive 'overall median dwell minus elapsed'."""
    model = DwellModel(tz).fit(train)
    naive_med = statistics.median(model.all) if model.all else 0.0
    rows: dict[int, dict[str, list[float]]] = {c: {"model": [], "naive": [], "covered": []} for c in checkpoints_min}
    for s in test:
        if s.dwell is None:
            continue
        for c in checkpoints_min:
            elapsed = c * 60
            if s.dwell <= elapsed:
                continue
            actual = s.dwell - elapsed
            p = model.remaining(s.seated_at, s.group_size, s.seated_at + elapsed)
            rows[c]["model"].append(abs(p.seconds - actual) / 60)
            rows[c]["naive"].append(abs(max(0.0, naive_med - elapsed) - actual) / 60)
            rows[c]["covered"].append(1.0 if p.low <= actual <= p.high else 0.0)
    out = {}
    for c, r in rows.items():
        n = len(r["model"])
        out[f"{c}min"] = {
            "n": n,
            "mae_min": round(statistics.mean(r["model"]), 1) if n else None,
            "naive_mae_min": round(statistics.mean(r["naive"]), 1) if n else None,
            "in_range_pct": round(100 * statistics.mean(r["covered"])) if n else None,
        }
    return out


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json

    from ..events.log import EventLog
    from .sessions import build_sessions

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--events", required=True)
    p.add_argument("--start", required=True, help="first day of data, YYYY-MM-DD (local)")
    p.add_argument("--train-days", type=int, default=14)
    p.add_argument("--tz", default="Europe/Dublin")
    p.add_argument("--out", help="write results JSON here")
    args = p.parse_args(argv)

    zone = ZoneInfo(args.tz)
    split = datetime.fromisoformat(args.start).replace(tzinfo=zone).timestamp() + args.train_days * 86400
    log = EventLog(args.events)
    runs = {r.run_id: r for r in log.runs()}
    events = log.events()
    log.close()
    synthetic = any(runs[e.run_id].source == "synthetic" for e in events if e.run_id in runs)
    sessions = build_sessions(events)
    train = [s for s in sessions if s.seated_at < split]
    test = [s for s in sessions if s.seated_at >= split]
    res = {"synthetic": synthetic, "train_visits": len(train), "test_visits": len(test),
           "by_elapsed": backtest(train, test, tz=args.tz)}
    print(json.dumps(res, indent=2))
    if args.out:
        with open(args.out, "w") as f:
            json.dump(res, f, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

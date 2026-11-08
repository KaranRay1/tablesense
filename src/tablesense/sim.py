"""Generate a SYNTHETIC week of table events, for demoing and testing reports.

Synthetic data is never evidence. Every run is stored with source='synthetic',
and the report marks it as such. The numbers below are plausible guesses for an
independent cafe-restaurant, not measurements.

    python -m tablesense.sim --events data/out/sim.db --start 2026-11-02 --days 7
"""

from __future__ import annotations

import argparse
import math
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .events.log import EventLog
from .state import State, Transition

S = State

# Opening hours per weekday (Mon..Sun), local time.
OPEN_HOURS = [(8, 22)] * 4 + [(8, 23), (9, 23), (9, 17)]
# Arriving groups per hour by local hour (weekday baseline).
ARRIVALS = {
    8: 2, 9: 3, 10: 2.5, 11: 3, 12: 7, 13: 7, 14: 4, 15: 2.5, 16: 2.5,
    17: 3.5, 18: 6, 19: 8, 20: 6, 21: 3, 22: 1.5,
}
DAY_FACTOR = [0.8, 0.85, 0.9, 1.0, 1.35, 1.5, 1.1]
GROUP_SIZES = [(1, 0.2), (2, 0.45), (3, 0.13), (4, 0.12), (5, 0.05), (6, 0.05)]
DWELL_MEDIAN_MIN = {"Breakfast": 30, "Lunch": 45, "Afternoon": 35, "Dinner": 75, "Late": 50}
DEFAULT_TABLE_SEATS = [2, 2, 2, 2, 4, 4, 4, 4, 4, 6, 6, 8]
HOLD = 240  # matches the default AWAY hold: earliest the system can show yellow
DELAY_SEAT, DELAY_AWAY = 45, 20  # detection delays with default timers


@dataclass
class Table:
    id: str
    seats: int
    free_at: float = 0.0  # epoch: when it's clean and usable again


def _daypart(hour: int) -> str:
    return "Breakfast" if hour < 11 else "Lunch" if hour < 15 else "Afternoon" if hour < 18 else "Dinner"


def _poisson_times(rng: random.Random, start: float, rate_per_hour: float) -> list[float]:
    out, t = [], start
    if rate_per_hour <= 0:
        return out
    while True:
        t += rng.expovariate(rate_per_hour / 3600)
        if t >= start + 3600:
            return out
        out.append(t)


def simulate(
    start: datetime,
    days: int = 7,
    table_seats: list[int] | None = None,
    seed: int = 7,
    busy: float = 1.0,
) -> tuple[list[Transition], dict]:
    """Return transitions (seconds relative to `start`) and summary counts."""
    rng = random.Random(seed)
    tz = start.tzinfo
    t0 = start.timestamp()
    tables = [Table(f"T{i + 1}", s) for i, s in enumerate(table_seats or DEFAULT_TABLE_SEATS)]
    out: list[Transition] = []
    stats = {"groups": 0, "seated": 0, "turned_away": 0}

    def tr(tid, frm, to, at, det, n, reason):
        out.append(Transition(tid, frm, to, at - t0, det - t0, n, False, reason))

    for d in range(days):
        day = (start + timedelta(days=d)).astimezone(tz)
        o, c = OPEN_HOURS[day.weekday()]
        for hour in range(o, c):
            h_start = day.replace(hour=hour, minute=0, second=0, microsecond=0).timestamp()
            rate = ARRIVALS.get(hour, 1) * DAY_FACTOR[day.weekday()] * busy
            for arrive in _poisson_times(rng, h_start, rate):
                stats["groups"] += 1
                size = rng.choices([g for g, _ in GROUP_SIZES], [w for _, w in GROUP_SIZES])[0]
                free = [t for t in tables if t.free_at <= arrive and t.seats >= size]
                if not free:
                    stats["turned_away"] += 1
                    continue
                table = min(free, key=lambda t: t.seats)  # smallest table that fits
                stats["seated"] += 1

                median = DWELL_MEDIAN_MIN[_daypart(hour)] * 60 * (1 + 0.1 * (size - 2))
                dwell = rng.lognormvariate(math.log(median), 0.35)
                seat, leave = arrive, arrive + dwell
                seen = min(size, rng.choice([size, size, size - 1, size - 2])) if size > 2 else size
                tr(table.id, S.EMPTY, S.SEATED, seat, seat + DELAY_SEAT, max(1, seen), "seated")

                if rng.random() < 0.2 and dwell > 20 * 60:  # someone (or everyone) steps out
                    a = seat + rng.uniform(0.3, 0.7) * dwell
                    back = a + rng.uniform(60, 200)  # shorter than the hold
                    tr(table.id, S.SEATED, S.AWAY, a, a + DELAY_AWAY, 0, "absent")
                    tr(table.id, S.AWAY, S.SEATED, back, back + 5, size, "returned")

                tr(table.id, S.SEATED, S.AWAY, leave, leave + DELAY_AWAY, 0, "absent")
                tr(table.id, S.AWAY, S.NEEDS_CLEARING, leave, leave + HOLD, 0, "hold_expired")
                if rng.random() < 0.1:  # nobody seen clearing: timeout after 20 min
                    clear, reason = leave + HOLD + 1200, "assumed_clear"
                else:
                    busy_now = rate >= 6
                    clear = leave + HOLD + rng.expovariate(1 / (240 if busy_now else 600))
                    reason = "clearing_visit"
                tr(table.id, S.NEEDS_CLEARING, S.EMPTY, clear, clear, 0, reason)
                table.free_at = clear

    out.sort(key=lambda t: t.detected_at)
    return out, stats


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--events", required=True, help="SQLite event log to write")
    p.add_argument("--start", default="2026-11-02", help="first day, YYYY-MM-DD (local)")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--tz", default="Europe/Dublin")
    p.add_argument("--venue-id", default="synthetic_cafe")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--busy", type=float, default=1.0, help="scale arrival rates")
    args = p.parse_args(argv)

    start = datetime.fromisoformat(args.start).replace(tzinfo=ZoneInfo(args.tz))
    transitions, stats = simulate(start, args.days, seed=args.seed, busy=args.busy)
    log = EventLog(args.events)
    run = log.start_run(
        f"sim-{args.seed}-{start:%Y%m%d}",
        venue_id=args.venue_id,
        camera_id="synthetic",
        source="synthetic",
        started_at=start.timestamp(),
    )
    log.write_many(run, transitions)
    log.close()
    print(f"SYNTHETIC: {len(transitions)} events, {stats} -> {args.events} (run {run.run_id})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

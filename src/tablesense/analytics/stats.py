"""Owner-facing numbers from visits: occupancy by hour, dwell, dirty time, staffing.

Pure Python on purpose: runs on a small edge device without pandas.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from .sessions import Session

DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
DAY_START_HOUR = 6  # hours before this belong to the previous night
HOUR_ORDER = tuple(range(DAY_START_HOUR, 24)) + tuple(range(DAY_START_HOUR))
DAYPARTS = (("Breakfast", 6, 11), ("Lunch", 11, 15), ("Afternoon", 15, 18), ("Dinner", 18, 24))


def business_day(local: datetime) -> int:
    """Weekday of the trading day: Saturday 01:00 still counts as Friday night."""
    return (local.weekday() - (1 if local.hour < DAY_START_HOUR else 0)) % 7


def daypart(hour: int) -> str:
    for name, lo, hi in DAYPARTS:
        if lo <= hour < hi:
            return name
    return "Late"


@dataclass
class WeekStats:
    period_start: datetime
    period_end: datetime
    n_tables: int
    tables_per_waiter: float
    # [business day][hour] -> average number of occupied tables during that hour.
    # Business days run 06:00-06:00, so after-midnight hours sit with the night before.
    occupancy: list[list[float]] = field(default_factory=lambda: [[0.0] * 24 for _ in DAYS])
    # [weekday][hour] -> suggested waiters (0 = closed / no guests)
    staffing: list[list[int]] = field(default_factory=lambda: [[0] * 24 for _ in DAYS])
    visits: int = 0
    visits_by_day: list[int] = field(default_factory=lambda: [0] * 7)
    dwell_by_daypart: dict[str, dict] = field(default_factory=dict)
    dwell_by_group: dict[str, dict] = field(default_factory=dict)
    dirty: dict = field(default_factory=dict)
    turns_per_table_per_day: float = 0.0
    peak_hours: list[tuple[str, int, float]] = field(default_factory=list)  # (day, hour, occupied)

    @property
    def avg_occupancy_pct(self) -> list[list[float]]:
        return [[100 * v / self.n_tables for v in row] for row in self.occupancy]


def _summary(xs: Sequence[float]) -> dict:
    if not xs:
        return {"n": 0, "median_min": None, "p90_min": None}
    s = sorted(xs)
    p90 = s[min(len(s) - 1, math.ceil(0.9 * len(s)) - 1)]
    return {"n": len(s), "median_min": round(statistics.median(s) / 60, 1), "p90_min": round(p90 / 60, 1)}


def compute_week(
    sessions: Sequence[Session],
    start: datetime,
    end: datetime,
    n_tables: int,
    tz: str = "Europe/Dublin",
    tables_per_waiter: float = 5.0,
) -> WeekStats:
    """Stats for sessions seated in [start, end). `start`/`end` must be tz-aware."""
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("start and end must be timezone-aware")
    if n_tables <= 0 or tables_per_waiter <= 0:
        raise ValueError("n_tables and tables_per_waiter must be > 0")
    zone = ZoneInfo(tz)
    t0, t1 = start.timestamp(), end.timestamp()
    st = WeekStats(start.astimezone(zone), end.astimezone(zone), n_tables, tables_per_waiter)
    in_period = [s for s in sessions if t0 <= s.seated_at < t1]
    st.visits = len(in_period)

    # Occupied table-seconds per local (weekday, hour), split across hour boundaries.
    occupied = [[0.0] * 24 for _ in DAYS]
    for s in in_period:
        a = s.seated_at
        b = min(s.left_at if s.left_at is not None else t1, t1)
        st.visits_by_day[business_day(datetime.fromtimestamp(a, zone))] += 1
        while a < b:
            local = datetime.fromtimestamp(a, zone)
            # Step in absolute time (DST-safe). Assumes whole-hour UTC offsets, true for Ireland/UK/EU.
            next_hour = (a // 3600 + 1) * 3600
            chunk = min(b, next_hour) - a
            occupied[business_day(local)][local.hour] += chunk
            a += chunk

    # How many of each (weekday, hour) are in the period, to average over weeks.
    hour_count = [[0] * 24 for _ in DAYS]
    h = (t0 // 3600) * 3600
    while h < t1:
        if h >= t0:
            local = datetime.fromtimestamp(h, zone)
            hour_count[business_day(local)][local.hour] += 1
        h += 3600

    for d in range(7):
        for hr in range(24):
            if hour_count[d][hr]:
                occ = occupied[d][hr] / 3600 / hour_count[d][hr]
                st.occupancy[d][hr] = round(occ, 2)
                st.staffing[d][hr] = math.ceil(occ / tables_per_waiter - 1e-9) if occ >= 0.25 else 0

    flat = [(DAYS[d], hr, st.occupancy[d][hr]) for d in range(7) for hr in range(24)]
    st.peak_hours = sorted(flat, key=lambda x: -x[2])[:5]

    dwell_dp: dict[str, list[float]] = defaultdict(list)
    dwell_grp: dict[str, list[float]] = defaultdict(list)
    for s in in_period:
        if s.dwell is None:
            continue
        dwell_dp[daypart(datetime.fromtimestamp(s.seated_at, zone).hour)].append(s.dwell)
        g = "1" if s.group_size <= 1 else "2" if s.group_size == 2 else "3-4" if s.group_size <= 4 else "5+"
        dwell_grp[g].append(s.dwell)
    order = [d[0] for d in DAYPARTS] + ["Late"]
    st.dwell_by_daypart = {k: _summary(dwell_dp[k]) for k in order if dwell_dp.get(k)}
    st.dwell_by_group = {k: _summary(dwell_grp[k]) for k in ("1", "2", "3-4", "5+") if dwell_grp.get(k)}

    measured = [s.dirty for s in in_period if s.dirty is not None]
    st.dirty = {
        **_summary(measured),
        "assumed_clear": sum(1 for s in in_period if s.cleared_by == "assumed_clear"),
        "reseated_dirty": sum(1 for s in in_period if s.cleared_by == "reseated_dirty"),
        "total_dirty_hours": round(sum(measured) / 3600, 1),
    }

    days = (t1 - t0) / 86400
    st.turns_per_table_per_day = round(st.visits / n_tables / days, 2) if days > 0 else 0.0
    return st

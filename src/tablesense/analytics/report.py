"""Weekly owner report as one self-contained HTML file (no external assets).

    python -m tablesense.analytics.report --events data/out/sim.db \\
        --start 2026-11-02 --tables 12 --out data/out/report.html
"""

from __future__ import annotations

import argparse
import html
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from ..events.log import EventLog
from .sessions import build_sessions
from .stats import DAYS, HOUR_ORDER, WeekStats, compute_week

# Sequential blue ramp (light -> dark); index 0 = no guests.
RAMP = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]


def _cell_color(pct: float) -> tuple[str, str]:
    """(background, text) for an occupancy percentage."""
    if pct <= 0:
        return "var(--surface-2)", "var(--text-muted)"
    i = min(len(RAMP) - 1, int(pct / 100 * len(RAMP)))
    return RAMP[i], "#0b0b0b" if i <= 2 else "#ffffff"


def _open_hours(st: WeekStats) -> list[int]:
    """Hours with any guests, in trading-day order (06 ... 23, 00 ... 05)."""
    used = [i for i, h in enumerate(HOUR_ORDER) if any(st.occupancy[d][h] > 0 for d in range(7))]
    return list(HOUR_ORDER[min(used): max(used) + 1]) if used else list(range(8, 22))


def _fmt_min(v) -> str:
    return "–" if v is None else f"{v:.0f} min"


def render_html(st: WeekStats, venue: str, synthetic: bool) -> str:
    e = html.escape
    hours = _open_hours(st)
    pct = st.avg_occupancy_pct
    period = f"{st.period_start:%a %d %b %Y} – {(st.period_end - timedelta(seconds=1)):%a %d %b %Y}"

    busiest = st.peak_hours[0] if st.peak_hours and st.peak_hours[0][2] > 0 else None
    all_dwell = [v for v in st.dwell_by_daypart.values() if v["n"]]
    kpis = [
        ("Visits", f"{st.visits}", f"{st.turns_per_table_per_day} per table per day"),
        (
            "Busiest hour",
            f"{busiest[0]} {busiest[1]:02d}:00" if busiest else "–",
            f"{busiest[2]:.1f} of {st.n_tables} tables in use" if busiest else "",
        ),
        (
            "Table sat dirty",
            _fmt_min(st.dirty.get("median_min")),
            f"median after guests left · {st.dirty.get('total_dirty_hours', 0)} table-hours this week",
        ),
        (
            "Longest stays",
            max(all_dwell, key=lambda v: v["median_min"])["median_min"].__format__(".0f") + " min"
            if all_dwell else "–",
            "median, "
            + (max(st.dwell_by_daypart, key=lambda k: st.dwell_by_daypart[k]["median_min"]) if all_dwell else ""),
        ),
    ]

    def grid(values, fmt, colored: bool, title: str, unit: str) -> str:
        head = "".join(f"<th scope='col'>{h:02d}</th>" for h in hours)
        rows = []
        for d, day in enumerate(DAYS):
            cells = []
            for h in hours:
                v = values[d][h]
                tip = e(f"{day} {h:02d}:00 · {fmt(v)} {unit}".strip())
                if colored:
                    bg, fg = _cell_color(pct[d][h])
                    tip = e(f"{day} {h:02d}:00 · {st.occupancy[d][h]:.1f} of {st.n_tables} tables ({pct[d][h]:.0f}%)")
                    cells.append(f"<td style='background:{bg};color:{fg}' title='{tip}'>{fmt(v) if v else ''}</td>")
                else:
                    cls = " class='hot'" if v >= 3 else ""
                    cells.append(f"<td{cls} title='{tip}'>{fmt(v) if v else ''}</td>")
            rows.append(f"<tr><th scope='row'>{day}</th>{''.join(cells)}</tr>")
        return (
            f"<div class='grid-wrap'><table class='grid' aria-label='{e(title)}'>"
            f"<thead><tr><th></th>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
        )

    occ_grid = grid(pct, lambda v: f"{v:.0f}", True, "Tables in use by hour, percent", "%")
    staff_grid = grid(st.staffing, lambda v: f"{v}", False, "Suggested floor staff by hour", "staff")
    legend = "".join(
        f"<span class='sw' style='background:{c}'></span>" for c in RAMP
    )

    def dwell_rows(d: dict) -> str:
        return "".join(
            f"<tr><th scope='row'>{e(k)}</th><td>{v['n']}</td><td>{_fmt_min(v['median_min'])}</td>"
            f"<td>{_fmt_min(v['p90_min'])}</td></tr>"
            for k, v in d.items()
        )

    banner = (
        "<div class='banner' role='note'><strong>Synthetic data.</strong> These numbers come from a "
        "simulated week, not a real venue. They show what the report looks like, nothing more.</div>"
        if synthetic else ""
    )
    kpi_html = "".join(
        f"<div class='kpi'><div class='kpi-label'>{e(a)}</div><div class='kpi-value'>{e(b)}</div>"
        f"<div class='kpi-sub'>{e(c)}</div></div>"
        for a, b, c in kpis
    )
    d = st.dirty
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Weekly Table Report</title>
<style>
:root {{
  color-scheme: light;
  --surface-1: #fcfcfb; --surface-2: #f0efec; --border: #e1e0db;
  --text-primary: #0b0b0b; --text-secondary: #52514e; --text-muted: #8a897f;
  --accent: #2a78d6; --warn-bg: #fff4d6; --warn-text: #6b4a00; --hot: #fde7dd;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    color-scheme: dark;
    --surface-1: #1a1a19; --surface-2: #262624; --border: #383835;
    --text-primary: #f0efec; --text-secondary: #c3c2b7; --text-muted: #8a897f;
    --accent: #3987e5; --warn-bg: #3a2e0c; --warn-text: #f3d48a; --hot: #4a2a1d;
  }}
}}
:root[data-theme="dark"] {{
  color-scheme: dark;
  --surface-1: #1a1a19; --surface-2: #262624; --border: #383835;
  --text-primary: #f0efec; --text-secondary: #c3c2b7; --text-muted: #8a897f;
  --accent: #3987e5; --warn-bg: #3a2e0c; --warn-text: #f3d48a; --hot: #4a2a1d;
}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--surface-1); color: var(--text-primary);
  font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }}
main {{ max-width: 1040px; margin: 0 auto; padding: 32px 16px 64px; }}
h1 {{ font-size: 26px; margin: 0 0 4px; }}
h2 {{ font-size: 18px; margin: 40px 0 4px; }}
.sub, .note {{ color: var(--text-secondary); margin: 0 0 12px; }}
.note {{ font-size: 13px; }}
.banner {{ background: var(--warn-bg); color: var(--warn-text); padding: 10px 14px;
  border-radius: 8px; margin: 16px 0; }}
.kpis {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; margin-top: 20px; }}
.kpi {{ background: var(--surface-2); border-radius: 10px; padding: 14px 16px; }}
.kpi-label {{ color: var(--text-secondary); font-size: 13px; }}
.kpi-value {{ font-size: 26px; font-weight: 650; margin: 2px 0; font-variant-numeric: tabular-nums; }}
.kpi-sub {{ color: var(--text-muted); font-size: 12px; }}
.grid-wrap {{ overflow-x: auto; }}
table.grid {{ border-collapse: separate; border-spacing: 2px; font-variant-numeric: tabular-nums; }}
.grid th {{ font-weight: 500; color: var(--text-secondary); font-size: 12px; padding: 2px 6px; }}
.grid td {{ width: 40px; min-width: 34px; height: 30px; text-align: center; font-size: 12px;
  border-radius: 4px; background: var(--surface-2); }}
.grid td.hot {{ background: var(--hot); font-weight: 650; }}
.legend {{ display: flex; align-items: center; gap: 6px; color: var(--text-secondary); font-size: 12px; margin-top: 8px; }}
.sw {{ width: 22px; height: 12px; border-radius: 3px; display: inline-block; }}
.two {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 24px; }}
table.simple {{ border-collapse: collapse; width: 100%; font-variant-numeric: tabular-nums; }}
.simple th, .simple td {{ text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--border); }}
.simple thead th {{ color: var(--text-secondary); font-weight: 500; font-size: 13px; }}
ul {{ padding-left: 20px; color: var(--text-secondary); }}
</style></head>
<body><main>
<h1>Weekly table report</h1>
<p class="sub">{e(venue)} · {e(period)} · {st.n_tables} tables</p>
{banner}
<div class="kpis">{kpi_html}</div>

<h2>When you're busy</h2>
<p class="sub">Share of tables with guests, averaged over each hour. Hover a cell for the table count.
Hours after midnight count with the night before.</p>
{occ_grid}
<div class="legend">0%{legend}100%</div>

<h2>Suggested floor staff</h2>
<p class="sub">Waiters needed for the guests seated, at {st.tables_per_waiter:g} occupied tables per waiter.
Hours needing 3 or more are highlighted. Doesn't include kitchen, bar or a minimum opening crew.</p>
{staff_grid}

<h2>How long guests stay</h2>
<div class="two">
<table class="simple"><thead><tr><th>Time of day</th><th>Visits</th><th>Median</th><th>Longest 10% from</th></tr></thead>
<tbody>{dwell_rows(st.dwell_by_daypart)}</tbody></table>
<table class="simple"><thead><tr><th>People seen</th><th>Visits</th><th>Median</th><th>Longest 10% from</th></tr></thead>
<tbody>{dwell_rows(st.dwell_by_group)}</tbody></table>
</div>
<p class="note">Group size is what the camera counted when guests sat down. It tends to undercount large groups.</p>

<h2>Tables waiting to be cleared</h2>
<p class="sub">From when the last guest left until someone cleared the table.</p>
<table class="simple" style="max-width:520px"><tbody>
<tr><th scope="row">Median</th><td>{_fmt_min(d.get('median_min'))}</td></tr>
<tr><th scope="row">Slowest 10% took at least</th><td>{_fmt_min(d.get('p90_min'))}</td></tr>
<tr><th scope="row">Total table-hours sitting dirty</th><td>{d.get('total_dirty_hours', 0)}</td></tr>
<tr><th scope="row">Clears not seen by the camera</th><td>{d.get('assumed_clear', 0)} (left out of the times above)</td></tr>
<tr><th scope="row">Guests seated at an uncleared table</th><td>{d.get('reseated_dirty', 0)}</td></tr>
</tbody></table>
<p class="note">The system waits 4 minutes after guests leave before calling a table dirty (they may be in the
bathroom), so very fast clears inside that window aren't measured precisely yet.</p>

<h2>How this was measured</h2>
<ul>
<li>Table states come from the venue's existing cameras, processed on-site. No video leaves the building and no one is identified.</li>
<li>Times are local ({e(str(st.period_start.tzinfo))}). Each hour is averaged over the days in the period.</li>
<li>Accuracy of the underlying table states is measured separately against hand-labeled footage.</li>
</ul>
</main></body></html>
"""


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--events", required=True)
    p.add_argument("--start", required=True, help="first day, YYYY-MM-DD (local)")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--tz", default="Europe/Dublin")
    p.add_argument("--tables", type=int, required=True, help="number of tables in the venue")
    p.add_argument("--tables-per-waiter", type=float, default=5.0)
    p.add_argument("--venue-id", help="only this venue's events")
    p.add_argument("--venue-name", help="name shown on the report")
    p.add_argument("--out", required=True)
    args = p.parse_args(argv)

    zone = ZoneInfo(args.tz)
    start = datetime.fromisoformat(args.start).replace(tzinfo=zone)
    end = (start + timedelta(days=args.days)).replace(tzinfo=zone)

    log = EventLog(args.events)
    events = log.events(venue_id=args.venue_id)
    runs = {r.run_id: r for r in log.runs()}
    log.close()
    used_runs = {e.run_id for e in events}
    synthetic = any(runs[r].source == "synthetic" for r in used_runs if r in runs)
    venue_ids = sorted({e.venue_id for e in events})

    st = compute_week(build_sessions(events), start, end, args.tables, args.tz, args.tables_per_waiter)
    name = args.venue_name or ", ".join(venue_ids) or "Unknown venue"
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(render_html(st, name, synthetic))
    print(f"{'SYNTHETIC ' if synthetic else ''}report: {st.visits} visits -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

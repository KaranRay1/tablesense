"""Build a self-contained web demo of the dashboard: one HTML file, no server.

Runs the real Dashboard code on synthetic history, records what every API
call returns over a Saturday dinner service, and packs that (gzipped) into a
copy of the web app whose fetch() replays it. The page plays the service at
20x, loops at the end, and keeps "Mark served" and Right/Wrong taps in the
viewer's own browser. Synthetic data only: never point this at a real log.

    python tools/build_web_demo.py --out /path/to/tablesense-demo.html
"""

from __future__ import annotations

import argparse
import base64
import gzip
import json
import random
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime
from importlib import resources
from pathlib import Path
from zoneinfo import ZoneInfo

from tablesense.dashboard.server import Clock, Dashboard
from tablesense.events.log import EventLog
from tablesense.live import AlertRules
from tablesense.notify import ConsoleNotifier
from tablesense.sim import DEFAULT_TABLE_SEATS
from tablesense.sim import main as sim_main
from tablesense.state import State

TZ = "Europe/Dublin"
VENUE = "synthetic_cafe"


@dataclass
class FixedClock(Clock):
    """A clock the recorder moves by hand."""

    t: float = 0.0

    def now(self) -> float:
        return self.t


def serve_delay(table_id: str, since: int) -> float:
    """Seconds until simulated staff reach this visit (same answer every call)."""
    r = random.Random(f"{table_id}-{since}")
    return r.uniform(6 * 60, 12 * 60) if r.random() < 0.25 else r.uniform(90, 270)


def record(start_local: str, hours: float, step_s: int, history_every_s: int, warmup_s: int = 3600) -> dict:
    with tempfile.TemporaryDirectory() as d:
        db = str(Path(d) / "demo.db")
        sim_main(["--events", db, "--start", "2026-10-12", "--days", "21", "--tz", TZ, "--venue-id", VENUE])
        log = EventLog(db)
        tables = sorted({e.table_id for e in log.events(venue_id=VENUE)}, key=lambda t: (len(t), t))
        seats = {t: DEFAULT_TABLE_SEATS[i] if i < len(DEFAULT_TABLE_SEATS) else 4 for i, t in enumerate(tables)}
        t0 = datetime.fromisoformat(start_local).replace(tzinfo=ZoneInfo(TZ)).timestamp()
        # Start the app an hour early and record from t0, so the replay opens
        # mid-service with alerts already working (the app never alerts on
        # visits that began before it started).
        clock = FixedClock(start=t0 - warmup_s, speed=20.0, t=t0 - warmup_s)
        rules = AlertRules(needs_clearing_after=3 * 60, unserved_after=5 * 60)
        dash = Dashboard(log, VENUE, seats, TZ, clock, rules, [ConsoleNotifier(lambda _: None)], 40)

        status, history, served = [], {}, set()
        t, end = t0 - warmup_s, t0 + hours * 3600
        while t <= end:
            clock.t = t
            # Simulated staff: most tables get "Mark served" within a few
            # minutes; about 1 in 4 is reached late, so its alert fires first.
            for ts in dash._floor(t):
                key = (ts.table_id, int(ts.since))
                if ts.state in (State.SEATED, State.AWAY) and key not in served and t - ts.since >= serve_delay(*key):
                    dash.served(ts.table_id)
                    served.add(key)
            dash.tick()
            dash.deliver_pending()
            if t < t0:
                t += step_s
                continue
            if t == t0:
                weeks = {"default": dash.week(None)}
                for w in weeks["default"]["available"]:
                    weeks[w] = dash.week(w)
                system = dash.system()
            status.append(dash.status())
            if int(t - t0) % history_every_s == 0:
                history[str(int(t - t0))] = {tid: dash.table_history(tid) for tid in seats}
            t += step_s
        log.close()
    return {"t0": t0, "step": step_s, "history_every": history_every_s, "speed": 20,
            "status": status, "history": history, "weeks": weeks, "system": system}


SHIM = r"""<script>
/* Demo backend: replays recorded API answers. Taps stay in this browser. */
(() => {
const RAW = "__DATA__";
let D = null;
const ready = (async () => {
  const bytes = Uint8Array.from(atob(RAW), c => c.charCodeAt(0));
  const text = await new Response(new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"))).text();
  D = JSON.parse(text);
})();
const began = Date.now();
let loop = -1, served = new Map(), resolved = new Map(), verdicts = new Map();
function frame() {
  const span = D.status.length * D.step;
  const demoSec = ((Date.now() - began) / 1000) * D.speed;
  const n = Math.floor(demoSec / span);
  if (n !== loop) { loop = n; served = new Map(); resolved = new Map(); verdicts = new Map(); }
  const i = Math.min(D.status.length - 1, Math.floor((demoSec % span) / D.step));
  return { i, offset: (demoSec % span) };
}
const visitKey = (st, t) => Math.round((st.now - t.minutes * 60) / 120);
function status() {
  const { i } = frame();
  const st = structuredClone(D.status[i]);
  for (const t of st.tables) {
    const k = served.get(t.id);
    if (k != null && (t.state === "SEATED" || t.state === "AWAY") && Math.abs(k - visitKey(st, t)) <= 1) t.served = true;
  }
  for (const a of st.alerts) {
    if (verdicts.has(a.id)) a.verdict = verdicts.get(a.id);
    if (!a.resolved_at && resolved.has(a.id)) { a.resolved_at = resolved.get(a.id); a.delivery = a.delivery || "console: sent"; }
  }
  // Count this viewer's Right/Wrong taps in the summary.
  const judged = st.alerts.filter(a => a.verdict).length, wrong = st.alerts.filter(a => a.verdict === "wrong").length;
  st.shadow = { ...st.shadow, judged, wrong, wrong_pct: judged ? Math.round(100 * wrong / judged) : null };
  st.demo_web = true;
  return st;
}
function json(body, code = 200) {
  return new Response(JSON.stringify(body), { status: code, headers: { "Content-Type": "application/json" } });
}
const realFetch = window.fetch.bind(window);
window.fetch = async (input, opts = {}) => {
  const url = typeof input === "string" ? input : input.url;
  if (!url.startsWith("/api/")) return realFetch(input, opts);
  await ready;
  const [path, query] = url.split("?");
  const q = new URLSearchParams(query || "");
  const post = (opts.method || "GET").toUpperCase() === "POST";
  let m;
  if (path === "/api/me") return json({ role: "owner", login: false });
  if (path === "/api/status") return json(status());
  if (path === "/api/system") return json(D.system);
  if (path === "/api/week") return json(D.weeks[q.get("start") || "default"] || D.weeks.default);
  if (post && (m = path.match(/^\/api\/tables\/([\w-]+)\/served$/))) {
    const st = status(), t = st.tables.find(x => x.id === m[1]);
    if (!t || !(t.state === "SEATED" || t.state === "AWAY") || t.served) return json({ ok: false }, 409);
    served.set(t.id, visitKey(st, t));
    for (const a of st.alerts) if (a.table_id === t.id && a.kind === "unserved" && !a.resolved_at) resolved.set(a.id, st.now);
    return json({ ok: true });
  }
  if (post && (m = path.match(/^\/api\/alerts\/(\d+)\/verdict$/))) {
    const body = JSON.parse(opts.body || "{}");
    if (!["correct", "wrong"].includes(body.verdict)) return json({ error: "verdict must be correct or wrong" }, 400);
    verdicts.set(Number(m[1]), body.verdict);
    return json({ ok: true });
  }
  if (!post && (m = path.match(/^\/api\/tables\/([\w-]+)$/))) {
    const { offset } = frame();
    const keys = Object.keys(D.history).map(Number).filter(k => k <= offset);
    const h = D.history[String(keys.length ? Math.max(...keys) : 0)][m[1]];
    return h ? json(h) : json({ error: "no such table" }, 404);
  }
  if (path === "/api/login" || path === "/api/logout") return json({ role: "owner", login: false });
  return json({ error: "not found" }, 404);
};
})();
</script>
"""

BANNER = """<div class="demo-note" role="note">Demo with synthetic data, replayed at 20&times; speed.
Your taps stay in this browser.</div>
"""

BANNER_CSS = """
.demo-note { padding: 8px max(16px, env(safe-area-inset-right, 0px)) 8px max(16px, env(safe-area-inset-left, 0px));
  background: var(--accent-2); color: var(--text); font-size: 13px; text-align: center; border-bottom: 1px solid var(--border); }
.demo-note a { color: var(--accent); font-weight: 600; }
#wk-print { display: none !important; }
"""


def build_page(data: dict) -> str:
    page = resources.files("tablesense.dashboard").joinpath("index.html").read_text()
    # The artifact host adds its own doctype/html/head/body; keep the title and styles.
    page = re.sub(r"<!doctype html>\s*<html[^>]*>\s*<head>\s*", "", page, flags=re.IGNORECASE)
    page = re.sub(r'<meta charset="utf-8">\s*<meta name="viewport"[^>]*>\s*', "", page)
    page = page.replace("<title>TableSense</title>", "<title>TableSense Live Demo</title>", 1)
    page = page.replace("</style>", BANNER_CSS + "</style>", 1)
    page = re.sub(r"</head>\s*<body>\s*", "", page)
    page = re.sub(r"\s*</body>\s*</html>\s*$", "\n", page)
    page = page.replace('<div class="app">', BANNER + '<div class="app">', 1)
    blob = base64.b64encode(gzip.compress(json.dumps(data, separators=(",", ":")).encode(), 9)).decode()
    shim = SHIM.replace("__DATA__", blob)
    i = page.index("<script>")  # the app's own script: the shim must load first
    return page[:i] + shim + page[i:]


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", required=True)
    p.add_argument("--start", default="2026-10-31T18:30", help="local time the replay starts")
    p.add_argument("--hours", type=float, default=3.0)
    p.add_argument("--step", type=int, default=30, help="seconds of demo time between recordings")
    args = p.parse_args()
    data = record(args.start, args.hours, args.step, history_every_s=600)
    html = build_page(data)
    Path(args.out).write_text(html)
    print(f"wrote {args.out}: {len(html) / 1e6:.2f} MB, {len(data['status'])} frames")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

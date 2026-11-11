# 2. Roadmap

Each phase has an **exit gate**. We don't start the next phase until the gate is met. That's the difference between a product and a demo.

---

## Phase 0: Validate and get footage (weeks 1–2)

**Goal:** prove there's a problem worth paying for, and get real footage.

- [ ] Interview 5 owners or managers using [the script](07-customer-discovery.md)
- [x] Research competitors: who already does CV for restaurants, what they charge, why they have or haven't taken off ([0003](decisions/0003-competitors.md))
- [ ] Check the camera situation at each venue: brand, IP or analog, RTSP access, angle, resolution, fps
- [ ] Sign a written data agreement with 1–2 pilot venues ([privacy doc](06-privacy-gdpr.md))
- [ ] Record 3+ days of footage (including a busy weekend shift) from at least one venue

**Exit gate:** ≥1 venue with signed agreement and ≥10 hours of usable footage, *and* ≥3 of 5 owners say they'd pay for at least one feature.

**If no venue access:** use public CCTV-style datasets and our own recorded test scene (a table at home or college), but treat everything built on them as provisional.

---

## Phase 1: Table state engine (weeks 2–5)

**Goal:** turn video into a reliable log of table states.

- [x] Video ingest from a file and from RTSP
- [x] Person detection (YOLO, small model) + tracking (ByteTrack)
- [x] Zone tool: draw table polygons on a camera frame, saved per venue
- [x] State machine with temporal smoothing and the AWAY hold ([spec](04-state-machine.md)), unit-tested without any CV
- [x] Event log: every state change with timestamp, table id, confidence
- [x] Labeling tool (`tools/label_video.py`)
- [ ] Hand-label 2+ hours of footage (table state over time) ← **blocked on footage**
- [x] Evaluation script ([metrics](05-metrics.md))

**Exit gate:** on held-out labeled footage, ≥90% per-table state accuracy (time-weighted), median transition delay ≤60 s, ≤1 false transition per table per hour. Numbers recorded in `eval/results/`.

---

## Phase 2: Weekly report (weeks 5–8)

**Goal:** the first thing an owner would pay for.

- [x] Analytics: occupancy by hour, dwell time by table and time of day, dirty-table time, table turns
- [x] Weekly report (web page) with a staffing suggestion per hour. Built and demoed on synthetic data only
- [ ] Run on the edge device at a pilot venue for 1–2 weeks, unattended
- [ ] Show the owner the report. Ask: "Would you pay for this? How much? What's missing?"
- [ ] Research spike: staff vs. customer (uniform color, zone-based heuristics, or staff wearing a marker). Just measure what's possible.

**Exit gate:** a pilot owner says the report changed a decision (a shift, a rota) *or* agrees to a paid price. The system ran for 7 days without manual restarts.

---

## Phase 3: Dwell-time prediction and wait estimate (weeks 8–10)

- [x] Baseline: median remaining dwell time given time seated, group size (people count), and hour (`analytics/predict.py`)
- [x] Measure error (MAE in minutes) on held-out days. Backtest tool built; synthetic result only ([results](../eval/results/2026-11-08_dwell_backtest_synthetic.md))
- [ ] Only try an ML model if the baseline is clearly not good enough
- [x] Host view: "Table 4 likely free in ~10 min", shown with a range, not a single exact number (dashboard waitlist panel)

**Exit gate:** prediction MAE is good enough that the host would use it (agree a target with the pilot venue, e.g. ≤8 min).

---

## Phase 4: Live dashboard and alerts (week 10+)

- [x] Live floor view on a tablet at the host stand (`tablesense.dashboard.server`), now part of a 4-screen web app: Floor, Alerts, Insights (weekly report in-app), System
- [x] Alert: "Table 3 needs clearing"
- [x] Alert: "Table 7 seated 5 min, no staff contact". Staff tap **Served** to cancel it (stand-in for staff detection). Sent as free push pop-ups (ntfy, tested on a phone); email and SMS also built
- [ ] Replace the Served tap with automatic staff detection (research spike, Phase 2)
- [x] Shadow mode first: log alerts without sending them, and have a manager mark which ones were right (✓/✗ on the dashboard)
- [ ] Turn on real alerts only when shadow-mode false alerts are below target

**Exit gate:** <2 false alerts per shift in shadow mode, and staff keep notifications on after 2 weeks.

---

## Throughout

- Write down every important choice in `docs/decisions/` (one short file each)
- Keep accuracy numbers up to date. They are both the sales pitch and the CV bullet.
- GDPR from day one: no video leaves the venue, and no faces are stored.

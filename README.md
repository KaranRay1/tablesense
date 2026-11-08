# TableSense (working title)

Computer-vision table monitoring for cafes and restaurants, using the security cameras they already have.

- **Green**: table empty and ready
- **Red**: guests seated (a timer starts for "no service yet")
- **Yellow**: guests left, table needs clearing

On top of the table states: a wait-time estimate for the host and a weekly peak-hours and staffing report for the owner.

## Status

**Phase 1, waiting on real footage.** Built and tested (71 tests):

- State machine, and the detection pipeline (YOLO + ByteTrack + table zones), end to end on stock footage ([first spot-check](eval/results/2026-11-08_stock4385_spotcheck.md))
- Labeling tool + evaluation script (accuracy, delays, false transitions, wrongly-yellow time)
- Visits, analytics, and the weekly owner report, demoed on a **synthetic** week
- Dwell prediction ("which table frees first"), and a live floor dashboard with shadow-mode alerts

There's no measured accuracy yet: that needs labeled footage. See [docs/02-roadmap.md](docs/02-roadmap.md).

## Quick start

```bash
python3.13 -m venv .venv
.venv/bin/pip install -e ".[cv,analytics,dev]"
.venv/bin/python -m pytest -q
```

Run on a video (put clips in `data/raw/`, which git ignores):

```bash
.venv/bin/python -m tablesense.cli --config configs/stock_4385.yaml \
    --model models/yolo11s.pt --smooth 0.5 \
    --out data/out/annotated.mp4 --events data/out/events.db
```

Draw table zones for a new clip (opens a window, prints YAML for the config):

```bash
.venv/bin/python tools/draw_zones.py data/raw/own/clip.mp4 --camera cam_floor_1
```

Label a clip, then score a run against the labels:

```bash
.venv/bin/python tools/label_video.py data/raw/own/clip.mp4 --config configs/my_venue.yaml --out eval/labels/clip.csv
.venv/bin/python -m tablesense.evaluation --labels eval/labels/clip.csv --events data/out/events.db --out eval/results/<date>_clip
```

Weekly report (demo on a synthetic week):

```bash
.venv/bin/python -m tablesense.sim --events data/out/sim.db --start 2026-11-02
.venv/bin/python -m tablesense.analytics.report --events data/out/sim.db --start 2026-11-02 --tables 12 --out data/out/report.html
```

Live floor dashboard for the host stand (here as a demo on 3 synthetic weeks, Saturday evening at 60x speed):

```bash
.venv/bin/python -m tablesense.sim --events data/out/sim3w.db --start 2026-10-12 --days 21
.venv/bin/python -m tablesense.dashboard.server --events data/out/sim3w.db --demo-start 2026-10-31T19:30 --speed 60
```

Then open http://127.0.0.1:8765. Alerts run in shadow mode: they're recorded and judged, never sent.

Model weights go in `models/` (git-ignored). Ultralytics downloads `yolo11s.pt` automatically the first time if you pass the bare name.

## Goal

A product that owners pay for and staff trust. It should also work as a CV portfolio project, but only because it has measured results from real deployments, not because it exists.

## Docs (read in order)

| # | Doc | What it answers |
|---|-----|-----------------|
| 1 | [Product brief](docs/01-product-brief.md) | What problem, for whom, why not just POS data, what's out of scope |
| 2 | [Roadmap](docs/02-roadmap.md) | Phases, what each one delivers, and the criteria for moving to the next |
| 3 | [Architecture](docs/03-architecture.md) | Edge device, pipeline, what leaves the venue (no video) |
| 4 | [Table state machine](docs/04-state-machine.md) | Exact rules for green/red/yellow, including the hidden AWAY hold |
| 5 | [Metrics and evaluation](docs/05-metrics.md) | How we measure accuracy and false alerts on labeled footage |
| 6 | [Privacy and GDPR](docs/06-privacy-gdpr.md) | DPIA, signage, retention, data agreement with pilot venues |
| 7 | [Customer discovery](docs/07-customer-discovery.md) | Interview script, pilot offer, what we need to learn |
| - | [Decision log](docs/decisions/) | Why we chose what we chose |
| - | [Risks](docs/08-risks.md) | What could kill this and how we'll find out early |

## Repo layout

```
docs/            planning, specs, decisions
src/tablesense/  the Python package (edge pipeline)
  ingest/        read RTSP streams / recorded video files
  detect/        person detection (YOLO) + tracking (ByteTrack)
  zones/         table polygons per venue, point-in-zone logic
  state/         per-table state machine (pure logic, no CV)
  events/        state-change log (the core data product)
  analytics/     visits, dwell/dirty time, occupancy, staffing, weekly HTML report
  evaluation.py  score runs against hand labels
  sim.py         synthetic week generator (demo/testing only)
configs/         per-venue config (camera URLs, table zones, timers)
tools/           labeling and zone-drawing helpers
eval/labels/     hand labels (CSV, tracked: they're the evidence)
eval/results/    evaluation results, never overwritten
tests/           unit tests (state machine first)
data/            local only, git-ignored; footage never committed
```

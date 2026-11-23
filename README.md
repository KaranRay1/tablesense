# TableSense (working title)
 
[![CI](https://github.com/KaranRay1/tablesense/actions/workflows/ci.yml/badge.svg)](https://github.com/KaranRay1/tablesense/actions/workflows/ci.yml)
 
Computer-vision table monitoring for cafes and restaurants, using the security cameras they already have.
 
- **Green**: table empty and ready
- **Red**: guests seated (a timer starts for "no service yet")
- **Yellow**: guests left, table needs clearing
On top of the table states: a wait-time estimate for the host and a weekly peak-hours and staffing report for the owner.
 
## Status
 
**Phase 1, waiting on real footage.** Built and tested (158 tests, run on every push by GitHub CI):
 
- State machine, and the detection pipeline (YOLO + ByteTrack + table zones), end to end on stock footage ([first spot-check](eval/results/2026-11-08_stock4385_spotcheck.md))
- Labeling tool + evaluation script (accuracy, delays, false transitions, wrongly-yellow time)
- Visits, analytics, and the weekly owner report, demoed on a **synthetic** week
- Dwell prediction ("which table frees first")
- A web app for the venue: **Floor** (live tables, wait estimate, Served taps, per-table timeline), **Alerts** (log with right/wrong judging), **Insights** (weekly heatmaps, staffing, dwell, printable report) and **System** (setup, data, privacy)
- Staff alerts as free push pop-ups (ntfy, tested on a phone), email or SMS, with a **Served** button that cancels the 5-minute alert
- **Cameras and Video check** in the web app: connect live CCTV (RTSP) and draw tables in the browser, or upload a clip and get a per-table timeline, visits, stays and a still at every change
- **Areas** for venues with more than one floor or room (upstairs, terrace): the Floor screen groups tables by area, and the host's wait estimate can be limited to one area or to step-free tables ([decision](docs/decisions/0004-areas-for-multi-floor-venues.md))
- Tabletop check: tells "waiter cleared it" from "guests came back" by comparing the table surface with a clean reference. On the stock clip, the fast-clear table went from 53% to 85% ([result](eval/results/2026-11-09_stock4385_tabletop_on.md))
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
 
Optional tabletop check per table: add a `top:` polygon (table surface only) and capture a reference from a stretch where the table is clean and empty:
 
```bash
.venv/bin/python tools/capture_reference.py --config configs/my_venue.yaml --table T2 --from 0 --to 60 --out data/refs/my_venue/T2.png
```
 
then add `reference: data/refs/my_venue/T2.png` to that table. `--no-tabletop` on the CLI turns it off for A/B runs.
 
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
 
The web app (here as a demo on 3 synthetic weeks, Saturday evening at 20x speed):
 
```bash
.venv/bin/python -m tablesense.sim --events data/out/sim3w.db --start 2026-10-12 --days 21
.venv/bin/python -m tablesense.dashboard.server --events data/out/sim3w.db --demo-start 2026-10-31T19:30 --speed 20 --unserved-min 5
```
 
To try areas (floors), add the demo two-floor layout of the same synthetic cafe: `--config configs/demo_two_floors.yaml`.
 
Then open http://127.0.0.1:8765. Six screens:
 
- **Floor** (host stand tablet): every table's colour, minutes seated, "free in ~N min" with a range, **Mark served**, a wait estimate per party size, and the next tables to free up. Tap a table for today's timeline. With areas configured: a switch per floor or room, tables grouped by area, and a wait estimate for one area or step-free tables only.
- **Alerts**: everything staff were told, delivery status, and **Right / Wrong** buttons. The share of wrong alerts is the number to get down before sending alerts for real.
- **Insights** (owner): any week's visits, busiest hours, occupancy and suggested-staff heatmaps, dwell by time of day and group size, and a printable report.
- **Cameras** (owner): connect the venue's CCTV from the browser. Paste the camera's RTSP address, **Test connection** to get a picture, click round each table to outline it, then **Save and start**. Each camera runs as its own background process on the box, restarts by itself if it crashes or the stream drops, and comes back after a reboot. The card shows frames per second, people in view and each table's state; **Show preview** shows the live picture with outlines and people boxes, only while you're looking.
- **Video check** (owner): upload a clip (up to 2 GB), outline the tables (or copy them from a camera), and **Analyse**. You get a colour timeline per table, time free / seated / needs clearing, visits and median stay, every change with its reason, and an annotated still at each change. The video is deleted once analysed unless you keep it, and every check is removed after 7 days.
- **System**: venue, alert rules, event log, cameras and timers (start with `--config` to see those), privacy notes.
Cameras and Video check need the vision install (`pip install -e ".[cv]"`, or the `app` Docker image) and `--config` (a new, empty file is fine: the app fills it in and keeps the previous version as `.bak`). Camera passwords stay in that git-ignored file (mode 600) and reach the camera process through an environment variable; the browser, logs and event log only ever see the address without them.

Light, dark or automatic theme; works on a phone. Single self-contained page, no internet needed. Alerts run in shadow mode unless you pass `--notify`.
 
### Sign-in
 
On `127.0.0.1` (this computer only) there's no sign-in. To open the app to tablets and phones on the venue network (`--host 0.0.0.0`), set both of these in `.env`, or it refuses to start:
 
- `TABLESENSE_STAFF_PIN` (4–8 digits): floor staff get **Floor** and **Alerts**, can tap **Mark served** and judge alerts.
- `TABLESENSE_OWNER_PASSWORD` (10+ characters): the owner also gets **Insights**, **Cameras**, **Video check**, **System** and the printable report.
Sessions last one shift (12 h staff, 8 h owner) in an HttpOnly, SameSite=Strict cookie. Five wrong tries lock that device for 5 minutes (doubling after), and there's a limit across all devices too, so a 4-digit PIN can't be guessed. Changing the PIN or password signs everyone out. Set `TABLESENSE_SESSION_SECRET` (32+ random characters) to stay signed in across restarts. Plain http is fine on the venue's own network; never expose the app to the internet.
 
### Install on a venue box (Docker)
 
```bash
cp .env.example .env                              # set the PIN, owner password and alert settings
# put the detector weights at ./models/yolo11s.pt (see Quick start)
sudo chown -R 10001 data configs                  # Linux: the app runs as uid 10001
docker compose up -d                              # web app on http://<box-ip>:8765
```

Then sign in as the owner, open **Cameras** and add each camera. The app restarts by itself after a crash or reboot, has a health check (`/healthz`), and keeps the event log, uploaded checks and `configs/venue.yaml` on the box (`./data`, `./configs`). It's the `app` image: the web app plus OpenCV and CPU-only PyTorch, which stays small enough for an edge box. For a report-only box without cameras, the `dashboard` image (about 210 MB) is enough. To try it with synthetic data instead: `docker compose --profile demo up demo` (needs the PIN and password in `.env` too).
 
### Alerts to staff phones (free push, email, or SMS)
 
Two alerts: **"Check on guests"** when a table has been seated 5 min and nobody has tapped **Served** on its card, and **"Needs clearing"** when a table has sat dirty for 3 min.
 
1. `cp .env.example .env` and set `TABLESENSE_NTFY_TOPIC` to a long random name (treat it like a password). `.env` is git-ignored.
2. Each waiter installs the free **ntfy** app and subscribes to that topic. Test it: `.venv/bin/python -m tablesense.notify --channel push`.
3. Run against the live event log:
```bash
.venv/bin/python -m tablesense.dashboard.server --events data/out/events.db --config configs/my_venue.yaml --notify push --unserved-min 5
```
 
- **Push (recommended):** free, no account, no per-message cost. Tested on a phone.
- Email: fill in the SMTP settings in `.env` and add `--notify email`. For Gmail, use an *app password* (Google Account → Security → 2-Step Verification → App passwords).
- Texts from your own number (free, Mac only): put staff numbers in `TABLESENSE_SMS_TO`, turn on Text Message Forwarding for the Mac on your iPhone, and add `--notify messages`. Fine for a pilot run from a Mac; a Linux edge box can't do it.
- SMS: fill in the Twilio settings (paid per message after the trial) and add `--notify sms`. Built and tested against a fake Twilio only.
- Each alert's card shows how it was delivered (or why it failed). Sending is capped at 40 per hour (`--max-per-hour`).
- After a restart, tables already seated longer than 5 min don't alert (avoids a burst of messages mid-service).
- Demo mode only allows `--notify console`, so a fast demo clock can't flood real phones.
- Every **Served** tap is stored, which gives *time to first contact*, a number POS can't provide.
Detector settings are per camera (`imgsz`, `conf`, `tiles`, `fps` in the venue config): 640 px for near cameras, 1280 for high or wide ones, `tiles: 3` at `fps: 1` for far, crowded rooms. Measured in [eval/results/2026-11-10_tiled_detection.md](eval/results/2026-11-10_tiled_detection.md).
 
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
| 9 | [Tech stack](docs/09-tech-stack.md) | Every library and tool used, and why |
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
 
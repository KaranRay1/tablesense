# 9. Tech stack

What TableSense is built with, and why. Versions are the ones tested on the development Mac (November 2026); `pyproject.toml` is the source of truth for what gets installed.

## At a glance

```
IP camera ──RTSP──▶ OpenCV ──▶ YOLO11s + ByteTrack ──▶ table zones ──▶ state machine ──▶ SQLite event log
                                (people, not faces)     (polygons)      (green/red/yellow)        │
                                                                                                  ▼
              phone alerts (ntfy / email / SMS / Messages) ◀── dashboard server ──▶ web app (tablet, phone)
```

Everything runs on one box at the venue. Frames are discarded after inference; only table states and times are stored.

## Language and core

| Part | Tech | Why |
| --- | --- | --- |
| Language | Python 3.11+ (developed on 3.13, CI also runs 3.11) | Best computer-vision ecosystem; one language end to end |
| Config | YAML via PyYAML 6 | Venue, cameras, table polygons and timers are hand-editable |
| Storage | SQLite (Python standard library) | One file per venue, no database server, survives reboots |
| Packaging | setuptools, `pip install -e ".[cv,analytics,dev]"` | Extras keep the dashboard install small (no OpenCV/torch) |

## Computer vision (`src/tablesense/detect`, `ingest`, `zones`, `state`)

| Part | Tech | Why |
| --- | --- | --- |
| Video in | OpenCV 5 (`opencv-python`), RTSP or file | Reads any IP camera stream; samples 1–5 fps, as table states change over seconds |
| People detection | Ultralytics YOLO11s (8.4.x), person class only | Accurate small model that runs on a CPU; can be swapped for fine-tuned or ONNX weights |
| Tracking | ByteTrack (built into Ultralytics), `lap` for matching | Keeps a person's temporary id across frames, so one person leaving a frame doesn't flip the table |
| Far, crowded rooms | Tiled detection (overlapping tiles + full frame, merged) | Small, far-away people are lost when a wide frame is shrunk to model size |
| Deep learning runtime | PyTorch 2.x (CPU build in Docker) | Required by Ultralytics; CPU build avoids gigabytes of unused CUDA |
| Table zones | Pixel polygons per table, drawn with `tools/draw_zones.py` | Counts people per table without any per-venue training |
| Tabletop check | OpenCV image difference against a clean reference photo (NumPy) | Tells "waiter cleared it" from "guests came back" |
| Table logic | Custom state machine + count smoothing (`state/machine.py`, `smoothing.py`) | Timers (seat, gone, away, clearing) turn noisy counts into reliable green/red/yellow |

Privacy by design: no face recognition, no re-identification across days, ids reset, no video stored in normal running. See [06-privacy-gdpr.md](06-privacy-gdpr.md).

## Analytics (`src/tablesense/analytics`)

| Part | Tech | Why |
| --- | --- | --- |
| Visits and stats | Python standard library (`statistics`, `zoneinfo`) | Visits, dwell times, peak hours in the venue's time zone |
| Wait / "free in N min" | Baseline from past visits of the same group size and time of day that lasted at least as long (`predict.py`), with a backtest | Simple, explainable, gives a range not a false-precise number; ML only if the backtest says it's needed |
| Weekly report | HTML report; pandas for the offline analysis CLI | Printable owner report, no BI tool needed |
| Evaluation | `tablesense.evaluation` against hand labels from `tools/label_video.py` | Time-weighted accuracy, delays, false and wrongly-yellow transitions |

## Dashboard and web app (`src/tablesense/dashboard`)

| Part | Tech | Why |
| --- | --- | --- |
| Server | Python standard library `ThreadingHTTPServer`, JSON API | No framework to install or patch on a venue box |
| Front end | One HTML file, plain JavaScript and CSS, no build step | Loads fast on an old tablet; works offline on the local network |
| Screens | Floor, Alerts, Insights, System (hash routes); in-place DOM updates | Live view without flicker or a JS framework |
| Sign-in | Staff PIN and owner password, HMAC-signed session cookies (HttpOnly, SameSite=Strict) | Staff see the floor; only the owner sees reports |
| Security | Login rate limits with lockout, CSRF header + Origin check, CSP and security headers, refuses network access without a password | Safe to put on a venue's Wi-Fi |

## Cameras and video checks (`src/tablesense/dashboard/vision.py`)

| Part | Tech | Why |
| --- | --- | --- |
| Camera processes | One `python -m tablesense.cli` child process per live camera, supervised by the web app (restart with back-off, auto-start after reboot) | A frozen stream or a crash in OpenCV/YOLO can't take the web app down |
| Camera setup | Browser outline editor (SVG over a test frame), saved to the venue YAML with validation and a `.bak` | No desktop tool or YAML editing needed at the venue |
| Live numbers | JSON status file per camera (fps, people, table states), annotated JPEG preview only while watched | Simple, crash-proof hand-off between processes |
| Clip checks | Streamed upload to disk, a queue of one analysis at a time, results from the event log (timelines, visits, stays) plus annotated stills at every change | Try the system on any footage in minutes; uses the same pipeline as live cameras |
| Secrets | Camera address passed by environment variable, stripped everywhere it could be shown | Passwords never in `ps`, logs or the browser |

## Alerts (`src/tablesense/notify.py`)

| Channel | Tech | Notes |
| --- | --- | --- |
| Push (default) | [ntfy](https://ntfy.sh) over HTTPS | Free phone pop-ups with a **Served** button; can be self-hosted on the box |
| Email | SMTP (`smtplib`, TLS) | e.g. Gmail with an app password |
| SMS | Twilio REST API (`urllib`) | Paid per message |
| iMessage / SMS from a Mac | Apple Messages via `osascript` (AppleScript) | Uses the Mac owner's own number; needs Text Message Forwarding |

Secrets (PIN, password, session secret, ntfy topic, SMTP and Twilio keys) live only in a git-ignored `.env`. Alerts contain a table number and minutes, nothing else.

## Running it

| Part | Tech | Why |
| --- | --- | --- |
| Containers | Docker, multi-stage `Dockerfile` (`dashboard` ~210 MB for reports only; `app` ~2.2 GB with cameras and video checks) | Same install on any mini PC; runs as a non-root user |
| Orchestration | Docker Compose with profiles (`camera`, `demo`), `restart: unless-stopped` | One command to start; restarts after a crash or reboot |
| Health | `/healthz` endpoint + Docker `HEALTHCHECK`; clean shutdown on SIGTERM | Shows up as healthy/unhealthy; stops in under a second |
| Target hardware | Mini PC (x86, CPU only) or a Mac for pilots; Jetson later | YOLO11s runs at about 5–6 fps on CPU, enough for table states |

## Quality

| Part | Tech |
| --- | --- |
| Tests | pytest (158 tests): state machine, zones, analytics, dashboard API, sign-in, alerts, cameras and clip checks |
| Lint | Ruff 0.17.4 |
| CI | GitHub Actions: lint, tests on Python 3.11 and 3.13, Docker build + smoke test (`.github/workflows/ci.yml`) |
| Tools | `draw_zones.py`, `capture_reference.py`, `label_video.py`, `benchmark.py`, `build_web_demo.py` |

## Demo

The public demo is one self-contained HTML page built by `tools/build_web_demo.py`. It runs the real dashboard code over three weeks of synthetic data, records the API answers, and replays them in the browser at 20x speed (gzip + base64 data, a `fetch` shim, `DecompressionStream`). It is hosted as a Claude artifact, needs no server, and never touches real venue data.

## What is not used, on purpose

- **No cloud video processing**: everything runs on site ([decision 0001](decisions/0001-edge-processing-no-video-upload.md)).
- **No face recognition or person re-identification**, ever.
- **No web framework or JS build chain**: fewer moving parts to maintain on a box in a cafe.
- **No per-staff tracking**: Served taps are stored per table, not per person.

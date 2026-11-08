# 3. Architecture

## Principle: video stays in the venue

All inference runs **on-site** on a small edge device. Only table states, counts and timestamps leave the building. This keeps GDPR risk low and cloud costs near zero, and it's a selling point ("we never see your footage").

```
 Venue (on-site)                                              Cloud (later)
┌──────────────────────────────────────────────────────┐    ┌──────────────────┐
│ IP cameras ──RTSP──▶ Edge device (mini PC / Jetson)  │    │ API + database   │
│                      ┌───────────────────────────┐   │    │ Weekly reports   │
│                      │ ingest  (frames @ 2–5 fps)│   │    │ Dashboard        │
│                      │ detect  (YOLO, person)    │   │    │ Alerts (Phase 4) │
│                      │ track   (ByteTrack)       │   │    └────────▲─────────┘
│                      │ zones   (table polygons)  │   │             │
│                      │ state   (state machine)   │   │  only JSON  │
│                      │ events  (SQLite log)      │───┼─────────────┘
│                      └───────────────────────────┘   │  table states, counts,
│ Frames are discarded after processing.               │  timestamps. No images.
└──────────────────────────────────────────────────────┘
```

Phases 1–2 can run entirely offline on recorded footage on a laptop. The cloud part isn't needed until a venue wants a remote dashboard.

## Pipeline stages

| Stage | Input → Output | Starting choice | Notes |
|-------|----------------|-----------------|-------|
| **ingest** | RTSP / file → frames | OpenCV / PyAV | Sample 2–5 fps. Table states change over seconds, not milliseconds. Reconnect automatically when a stream drops. |
| **detect** | frame → person boxes | YOLO (small/nano), person class | Test on real ceiling angles. May need fine-tuning on venue footage. |
| **track** | boxes → tracks with IDs | ByteTrack | Gives us "same person still here" and helps with occlusion. |
| **zones** | tracks → people per table | Polygon per table; test a person's *lower-body point* (not the box center) against the polygon | Each venue's zones are configured once with a drawing tool. |
| **state** | people per table over time → state | Pure-Python state machine ([spec](04-state-machine.md)) | No CV dependency, fully unit-testable. |
| **events** | state changes → log | SQLite | The core data product. Everything else is built from this log. |
| **analytics** | event log → reports | pandas | Dwell time, occupancy by hour, dirty time, turns. |

## Data model (event log)

```sql
-- one row per state change
table_events(
  id            INTEGER PRIMARY KEY,
  venue_id      TEXT,
  table_id      TEXT,
  ts            TIMESTAMP,       -- UTC
  from_state    TEXT,            -- EMPTY | SEATED | AWAY | NEEDS_CLEARING
  to_state      TEXT,
  people_count  INTEGER,         -- smoothed count at transition
  confidence    REAL,
  source        TEXT             -- 'live' | 'replay' | 'manual_override'
)

-- one row per completed visit (derived from events)
table_sessions(
  venue_id, table_id,
  seated_at, left_at, cleared_at,
  max_people, dwell_seconds, dirty_seconds
)
```

## Per-venue config

One YAML file per venue in `configs/` (see `configs/example_venue.yaml`): cameras, table polygons per camera, timer thresholds. Thresholds are per venue because a cafe and a restaurant have very different dwell times.

## Tech stack (initial)

- Python 3.11+, `ultralytics` (YOLO + its built-in ByteTrack), OpenCV. Zone geometry is our own pure-Python code.
- Default model: `yolo11s` at 640 px (best accuracy/speed trade-off in the [first spot-check](../eval/results/2026-11-08_stock4385_spotcheck.md))
- SQLite for the event log, pandas for analytics
- pytest
- Later: FastAPI + Postgres for the cloud side, a simple web dashboard

## Hardware question (to answer in Phase 0)

"No new hardware" isn't fully true. We need:
1. **Access to the camera streams.** Many DVR/NVR systems are locked down or cloud-only. Check this at every venue.
2. **An edge device.** Measured on an M5 Pro laptop: `yolo11s` at 28.7 ms/frame on CPU, i.e. up to ~11 cameras at 3 fps ([benchmark](../eval/results/2026-11-08_benchmark.md)). A cheap mini PC will be several times slower. Run `tools/benchmark.py` on the candidate device before quoting hardware.

The honest pitch is "no new cameras", not "no new hardware".

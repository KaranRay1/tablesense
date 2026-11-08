# TableSense (working title)

Computer-vision table monitoring for cafes and restaurants, using the security cameras they already have.

- **Green**: table empty and ready
- **Red**: guests seated (a timer starts for "no service yet")
- **Yellow**: guests left, table needs clearing

On top of the table states: a wait-time estimate for the host and a weekly peak-hours and staffing report for the owner.

## Status

**Phase 0: planning and validation.** No code ships until there is real footage from a real venue. See [docs/02-roadmap.md](docs/02-roadmap.md).

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
  analytics/     dwell time, peak hours, weekly report
configs/         per-venue config (camera URLs, table zones, timers)
tools/           labeling and zone-drawing helpers
eval/            evaluation scripts + results on labeled footage
tests/           unit tests (state machine first)
data/            local only, git-ignored; footage never committed
```

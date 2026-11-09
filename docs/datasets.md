# Datasets and footage

Footage is never committed to git (`data/` is git-ignored). This file records what we have, where it came from, and what it's allowed to be used for.

## Stock clips (`data/raw/stock/`)

Source: [Mixkit](https://mixkit.co), Mixkit Stock Video Free License (commercial and personal use; don't redistribute the clips as-is). Downloaded 2026-11-08, 720p.

| File | Title | What it's good for | Limits |
|------|-------|--------------------|--------|
| `mixkit-4385-720.mp4` | [Customers in a minimalist style restaurant](https://mixkit.co/free-stock-video/customers-in-a-minimalist-style-restaurant-4385/) | Ceiling angle, many tables, waiters moving: closest to CCTV. Test detection and zones. | 15 s time-lapse, so timers don't apply |
| `mixkit-4672-720.mp4` | [Waiter serving meat stew in a restaurant](https://mixkit.co/free-stock-video/waiter-serving-meat-stew-in-a-restaurant-4672/) | ~~Staff member at a seated table~~ **Not usable** (checked 2026-11-09) | Handheld close-up of food being plated: no fixed camera, no table layout, no state changes |
| `mixkit-43248-720.mp4` | [Friends sharing time together in the cafeteria](https://mixkit.co/free-stock-video/friends-sharing-time-together-in-the-cafeteria-43248/) | ~~Seated people, eye-level~~ **Not usable** (checked 2026-11-09) | Close-up of two people chatting: no table changes state; only useful as a detector smoke test |

**What stock clips can't do:** test the state machine's timing. That needs long footage (30+ min) with full sit → away → leave → clear cycles.

## Own test clips (`data/raw/own/`)

Not yet recorded. Plan: phone mounted high, 10–15 min per clip, covering sit down, a guest stepping away and returning, everyone leaving, and someone clearing the table.

## Pilot venue footage (`data/raw/venues/<venue_id>/`)

None yet. Requires a signed data agreement first ([06-privacy-gdpr.md](06-privacy-gdpr.md)).

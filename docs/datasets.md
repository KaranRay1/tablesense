# Datasets and footage

Footage is never committed to git (`data/` is git-ignored). This file records what we have, where it came from, and what it's allowed to be used for.

## Stock clips (`data/raw/stock/`)

Source: [Mixkit](https://mixkit.co), Mixkit Stock Video Free License (commercial and personal use; don't redistribute the clips as-is). Downloaded 2026-11-08, 720p.

| File | Title | What it's good for | Limits |
|------|-------|--------------------|--------|
| `mixkit-4385-720.mp4` | [Customers in a minimalist style restaurant](https://mixkit.co/free-stock-video/customers-in-a-minimalist-style-restaurant-4385/) | Ceiling angle, many tables, waiters moving: closest to CCTV. Test detection and zones. | 15 s time-lapse, so timers don't apply |
| `mixkit-4672-720.mp4` | [Waiter serving meat stew in a restaurant](https://mixkit.co/free-stock-video/waiter-serving-meat-stew-in-a-restaurant-4672/) | ~~Staff member at a seated table~~ **Not usable** (checked 2026-11-09) | Handheld close-up of food being plated: no fixed camera, no table layout, no state changes |
| `mixkit-43248-720.mp4` | [Friends sharing time together in the cafeteria](https://mixkit.co/free-stock-video/friends-sharing-time-together-in-the-cafeteria-43248/) | ~~Seated people, eye-level~~ **Not usable** (checked 2026-11-09) | Close-up of two people chatting: no table changes state; only useful as a detector smoke test |

### Pexels clips (downloaded 2026-11-09, 720p)

Source: [Pexels](https://www.pexels.com), Pexels License (free, commercial use allowed; don't sell or redistribute the clips as-is). Camera motion measured by matching background features against the first frame.

| File | Page | Length | Camera | Good for |
|------|------|--------|--------|----------|
| `pexels-34697769-food-court-720.mp4` | [Bustling indoor food court](https://www.pexels.com/video/bustling-indoor-food-court-with-diverse-crowd-34697769/) | 28 s | Still (0.3 px) | Dense crowd at eye level: worst case for detection |
| `pexels-34569882-turkish-cafe-720.mp4` | [Turkish cafe from above](https://www.pexels.com/video/lively-turkish-cafe-scene-from-above-34569882/) | 11 s | Slow drift (34 px) | Closest to CCTV: high angle, ~10 small tables |
| `pexels-28575211-overhead-cafe-720.mp4` | [Outdoor cafe seating, overhead](https://www.pexels.com/video/aerial-view-of-outdoor-cafe-seating-on-cobblestone-28575211/) | 9 s | Slight sway (8 px) | Straight-down view; 2 empty set tables + 1 occupied (tabletop check) |
| `pexels-17540559-mall-food-court-720.mp4` | [Westfield food court](https://www.pexels.com/video/westfield-shepherd-s-bush-hammersmith-london-17540559/) | 18 s | Slow drift (15 px) | Small, distant people from high above |
| `pexels-34675213-food-market-720.mp4` | [Indoor food market](https://www.pexels.com/video/vibrant-indoor-food-market-with-diverse-crowd-34675213/) | 24 s | Still for ~9 s, then pans | First 9 s only: passers-by vs seated |
| `pexels-3135924-coffee-shop-720.mp4` | [Coffee shop full of customers](https://www.pexels.com/video/a-coffee-shop-restaurant-full-of-customers-3135924/) | 10 s | **Pans the whole time** (837 px) | Not usable; kept only as a "moving camera" negative example |

All are under 30 s: good for detection and zones in new layouts, useless for state-machine timing. Detection results: [eval/results/2026-11-09_detector_stock_scenes.md](../eval/results/2026-11-09_detector_stock_scenes.md).

**What stock clips can't do:** test the state machine's timing. That needs long footage (30+ min) with full sit → away → leave → clear cycles.

## Own test clips (`data/raw/own/`)

Not yet recorded. Plan: phone mounted high, 10–15 min per clip, covering sit down, a guest stepping away and returning, everyone leaving, and someone clearing the table.

## Pilot venue footage (`data/raw/venues/<venue_id>/`)

None yet. Requires a signed data agreement first ([06-privacy-gdpr.md](06-privacy-gdpr.md)).

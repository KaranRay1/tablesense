# 4. Table state machine

This is the core of the product. It's pure logic: it takes "how many people are in this table's zone right now" and outputs a state. It has no CV dependency, so it can be built and tested before we have any footage.

## States

| Internal state | Color shown | Meaning |
|----------------|-------------|---------|
| `EMPTY` | 🟢 Green | No one here, table is clear |
| `SEATED` | 🔴 Red | Guests are at the table |
| `AWAY` | 🔴 Red (hidden hold) | Guests not visible, but may be coming back (toilet, phone call, smoking) |
| `NEEDS_CLEARING` | 🟡 Yellow | Guests have left, table needs clearing |

`AWAY` is never shown as its own color. To staff it looks red. It exists to avoid the worst failure: **a waiter clearing a table while the guests are still eating.**

## Transitions

```
            people present ≥ T_seat
   EMPTY ──────────────────────────────▶ SEATED
     ▲                                     │  ▲
     │                 no people ≥ T_gone  │  │ people back
     │                                     ▼  │
     │                                    AWAY
     │                                     │
     │                    hold ≥ T_away    │
     │                                     ▼
     └──────────────────────────── NEEDS_CLEARING
        cleared (staff visit, or            │
        timeout T_clear_assume)             │ people present ≥ T_seat
                                            ▼
                                          SEATED  (new guests sat at a dirty table)
```

| From | To | Condition | Starting value (tune with data) |
|------|----|-----------|---------------------------------|
| EMPTY | SEATED | ≥1 person in zone for `T_seat` continuously (filters people walking past) | 45 s |
| SEATED | AWAY | 0 people in zone for `T_gone` (filters short occlusions) | 20 s |
| AWAY | SEATED | ≥1 person back in zone for `T_return` | 5 s |
| AWAY | NEEDS_CLEARING | still 0 people after `T_away` | 4 min |
| NEEDS_CLEARING | EMPTY | person in zone for between `clearing_visit_min` and `clearing_visit_max`, then leaves (a "clearing visit"), *or* `T_clear_assume` passes with nobody there | visit 10–90 s; assume after 20 min |
| NEEDS_CLEARING | SEATED | ≥1 person in zone for `clearing_visit_max` | 90 s |
| AWAY | EMPTY | hold ends **and** the tabletop has looked clear for `t_top_clear` (reason `cleared_in_hold`) | 15 s |
| NEEDS_CLEARING | EMPTY | nobody there and the tabletop has looked clear for `t_top_clear` (reason `looks_clear`) | 15 s |

The last two rows only apply when the table has a **tabletop check** configured (below). Without one the machine behaves exactly as the rows above.

## Tabletop check (optional second signal)

People counts can't tell "a waiter cleared the table" from "a guest came back for their coat". The tabletop can: [`detect/tabletop.py`](../src/tablesense/detect/tabletop.py) compares the table surface (`top:` polygon in the config) with a reference image of it clean and set, captured with `tools/capture_reference.py` (median of many frames, so passers-by drop out).

- Score = fraction of tabletop pixels whose brightness differs from the reference by more than 35%, after matching overall brightness. Clear if under 0.25.
- Pixels under detected people are left out. If less than half the tabletop is visible, the reading is "unknown" and the previous reading stands.
- It never cuts the AWAY hold short: guests whose plates were cleared before a smoke break still keep a red table for the full hold. It only changes where the hold leads (green instead of yellow) and lets a yellow table go green as soon as it's seen clear.
- The risky failure is the reverse: a **bad or stale reference** (captured with items on the table, a new tablecloth, the camera knocked) can read a dirty table as clear, which shows **green on a dirty table**. On the stock clip, T3's reference (7 frames with paper on the table) read plates as clear. Capture references from a long stretch of clean tables, and recapture when the layout changes.

Result on the stock clip: T2 53% → 85%, overall 84% → 92% ([eval/results/2026-11-09_stock4385_tabletop_on.md](../eval/results/2026-11-09_stock4385_tabletop_on.md)). One 15 s time-lapse: proof the mechanism works, not an accuracy number.

Implementation: [`src/tablesense/state/machine.py`](../src/tablesense/state/machine.py), tests in [`tests/test_state_machine.py`](../tests/test_state_machine.py).

**Timing rules:**
- Every transition has two times: `at` (when it really happened) and `detected_at` (when the machine was sure). Guests who sat down at 19:00:00 are recorded as seated at 19:00:00, even though the machine only knows at 19:00:45. Their difference is the *transition delay* metric.
- When a table moves from AWAY to NEEDS_CLEARING, `left_at` is **when the people disappeared**, not when the hold ended. Otherwise every dwell time would be inflated by `T_away`.
- The AWAY hold is measured from when guests left. Short presence blips (a waiter passing, under `T_return`) don't restart it.
- **Why NEEDS_CLEARING uses 90 s, not `T_seat`, to detect new guests:** a waiter clearing a table can take up to ~90 s. With 45 s, a slow clear would look like new guests sitting down. The cost is that guests seated at a dirty table are detected 45 s later. That case is rare.
- If updates stop for more than `max_gap` (10 s, e.g. a dropped stream), timers restart instead of assuming the person was there the whole time.

## Smoothing

Raw per-frame counts flicker (occlusion, missed detections). Before the state machine:
- Smooth the per-table count with a rolling window (e.g. the median over the last 5 s).
- Use "present" / "absent" with **hysteresis**: it takes several consecutive frames to switch.

## Known hard cases (we'll measure these, not guess)

| Case | Current rule's behavior | Fix later if it fails often |
|------|------------------------|-----------------------------|
| Waiter standing at an empty table | Short visit, under `T_seat`, so no change | Staff detection |
| Waiter's visit counted as "clearing" when it wasn't | False NEEDS_CLEARING → EMPTY | Object detection (plates/glasses on the table) |
| Guests away > `T_away` (long smoke break) | Wrongly goes yellow | Longer hold if bags/coats are detected |
| **Waiter clears within `T_away` of guests leaving** (common when busy) | Without the tabletop check: waiter's presence reads as guests "returned" (red), then yellow on a clean table, then green only at `T_clear_assume` (~24 min wrong). **With the tabletop check:** green when the hold ends (`cleared_in_hold`), or as soon as the clean top is seen (`looks_clear`). | Done for the count-only case (tabletop check). Still to measure on real footage: how often references go stale, and sun/lighting false "dirty". Staff detection would also shorten the red time during the hold. |
| Table occluded by a pillar or another guest | Count drops, may go AWAY | Second camera angle, tracker memory |
| Guests at adjacent tables pushed together | Counted in both zones | Merge-table config |
| Kids under the table, low camera angles | Missed people | Fine-tune the detector on venue footage |

## Open questions for pilots

- What's the real distribution of toilet/smoke-break durations? (Sets `T_away`.)
- Do staff clear in one visit or several?
- Should owners be able to tap a table to correct its state? (A manual override also gives us free labels.)

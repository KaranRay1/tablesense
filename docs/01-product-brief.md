# 1. Product brief

## Problem

In busy cafes and restaurants, three things cost money and nobody measures them:

1. **Guests sit unserved.** Nobody notices a newly seated table for several minutes. Some guests leave, and most are annoyed before the order is taken.
2. **Tables sit dirty.** A table the guests have left stays uncleared while people wait at the door.
3. **Staffing is guesswork.** Owners schedule shifts from gut feeling, not from when the venue is actually full.

## Users

| User | What they want | What they'll reject |
|------|----------------|---------------------|
| **Owner / manager** (the buyer) | Fewer walkouts, faster turns, correct staffing, a clear weekly report | Expensive hardware, long setup, legal risk |
| **Waiters / floor staff** | To know which table needs them *now* | Wrong alerts, nagging, feeling surveilled |
| **Host** | A credible wait time for the queue | Wrong predictions told to guests |

The owner pays. Staff decide whether it gets used. A product the owner buys and the staff mute has failed.

## Why not just POS data? (the hard question)

POS systems (Toast, Square, Lightspeed and others) already know when a bill is opened and closed. So our answer has to be about **what POS cannot see**:

| Signal | POS | Cameras |
|--------|-----|---------|
| Time from sitting down to first staff contact | No (bill opens at order, not at sitting down) | Yes |
| Time a table sat dirty after guests left | No | Yes |
| Real occupancy, including people waiting or not ordering | No | Yes |
| Walk-ins who left without being seated | No | Possibly (later) |
| When the bill was paid | **Yes** | Weak |

**Hypothesis to validate:** owners care enough about *time to first contact* and *dirty-table time* to pay for them. If interviews show they don't, the product changes or stops. See [07-customer-discovery.md](07-customer-discovery.md).

A future option is to combine both: POS for "bill closed" and cameras for everything POS can't see.

## Value proposition (draft, to be tested)

> "Using the cameras you already have, see how long guests wait before anyone serves them, how long tables sit dirty, and when you're really busy, so you can staff each shift correctly."

## Features, in the order we'll build them

| Feature | Real-time accuracy needed? | Staff buy-in needed? | Build order |
|---------|---------------------------|---------------------|-------------|
| Table state log (green/red/yellow + timestamps) | Medium | No | 1 |
| Weekly peak-hours and staffing report | Low | No | 2 |
| Dwell-time stats and wait-time estimate | Medium | Host only | 3 |
| Live dashboard (tablet at host stand) | High | Some | 4 |
| Push alerts to waiters (unserved / dirty) | **Very high** | **Yes** | 5, only after the measured false-alert rate is low |

The report is the first thing we sell. Alerts are the most impressive feature and the hardest, so they come last.

## Out of scope (for now)

- Recognizing faces or identifying any individual. Never.
- Storing or uploading video to the cloud.
- Detecting dirty plates or objects (later, only if the timer rule fails often).
- Telling staff from customers (needed for "time to first contact"; research spike in Phase 2, not a dependency for the report).
- Reservations, waitlist SMS, payments. Existing tools already do these.
- Multi-location chains. Start with single independent venues.

## Target first customer

An independent cafe or casual restaurant in Dublin with 10–30 tables, IP security cameras with a view of the floor, and an owner we can reach directly.

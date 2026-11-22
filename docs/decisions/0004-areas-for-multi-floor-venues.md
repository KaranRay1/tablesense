# 0004: Areas for venues with more than one floor or room

**Date:** 2026-11-21
**Status:** Accepted

## Context
Many venues aren't one room: an upstairs, a back room, a terrace. Detection doesn't care (each camera runs on its own), but everything built on the event log treated the venue as one floor. Staff work per floor, a host has to know *where* a table is free, and guests who can't use stairs need a wait for step-free tables only.

## Decision
Add optional **areas** to the venue config. A camera belongs to an area and its tables inherit it; a table can override it (e.g. a terrace table seen through a ground-floor window). Each area has a name and a `step_free` flag. Configs without areas behave exactly as before: one area, "Main floor".

First version (this PR):
- `/api/status` reports each table's area and per-area counts
- Floor screen: an area switch, and tables grouped under area headings in "All areas"
- Wait estimate: limited to anywhere, step-free areas, or one area
- System screen: areas and each table's area

## Not yet (follow-up work)
- Weekly insights and staffing **per area** (one waiter can't cover two floors, so staffing must be computed per area and then added up)
- Alerts routed per area (e.g. one ntfy topic per floor)
- A table seen by two cameras: for now each table has one camera; merging two views is a research problem

## Consequences
- Old configs and event logs need no changes; the event log doesn't store areas, they come from the config
- Moving a table to another area changes how its history is grouped, which is what an owner would expect

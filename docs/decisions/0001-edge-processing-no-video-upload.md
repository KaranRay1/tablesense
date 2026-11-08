# 0001: Process video on-site, never upload it

**Date:** 2026-11-08
**Status:** Accepted

## Context
Analyzing guests and staff from security cameras is a new purpose under GDPR. Streaming video from many venues to the cloud is also expensive.

## Decision
Run all inference on an edge device at the venue. Only table states, counts and timestamps leave the building. Frames are discarded after processing.

## Consequences
- Lower legal risk and a clear privacy pitch
- Needs an edge device per venue (cost, setup, remote updates)
- Debugging is harder without stored video. Recording for labeling happens only during pilots, under a written agreement.

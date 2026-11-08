# 0002: Build the weekly report before live alerts

**Date:** 2026-11-08
**Status:** Accepted

## Context
Live alerts need very high accuracy, telling staff from customers, and staff buy-in. A few wrong alerts and staff mute the app. The weekly report tolerates small errors and needs no staff buy-in.

## Decision
Ship in this order: state log → weekly report → dwell prediction → live dashboard → alerts. Alerts run in shadow mode first and go live only when the false-alert rate is below target.

## Consequences
- Something sellable sooner
- Less impressive as an early demo
- Real data (dwell times, away durations) is collected before we tune alert thresholds

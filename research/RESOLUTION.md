# Research execution and barrier resolution

The registered Trendline research protocol uses **15m as the execution timeframe** and **5m high/low sub-bars for barrier resolution**.

- Strategy signals are generated from the closed 15m bar and submitted to the existing simulator.
- The existing simulator fills a signal at the next 15m bar open.
- When an active position's 15m bar touches both stop and target, the simulator uses `execution="intrabar"` with `intrabar_tf="5m"`.
- The 5m resolver checks sub-bar high/low in chronological order and reports unresolved 5m cases as `fallback` to the simulator's pessimistic stop-first rule.
- Structural Trendline `BREAK` remains the Trendline detector's registered structural definition; barrier resolution does not redefine the detector.

Every research result records the effective resolution fields:

```text
barrier_resolution
execution_mode
intrabar_tf
ambiguous_bars
resolved_by_subbars
fallback_to_pessimistic
resolution_breakdown
```

This makes the run auditable and prevents a 15m pessimistic run from being mistaken for a 5m research run.

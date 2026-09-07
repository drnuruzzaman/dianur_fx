Run a single-cell verification before the full matrix:

```bash
python -m research.run_tl --symbol EURUSD --strategy bounce --start 2021-01-01 --barrier-resolution 5m_high_low
```

The CLI now prints and the CSV/HTML report records the effective barrier resolution, execution mode, intrabar timeframe, and ambiguity counters. `5m_high_low` must resolve to `execution_mode=intrabar` and `intrabar_tf=5m`.

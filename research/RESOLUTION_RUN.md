Before the full matrix, run one cell:

```bash
python -m research.run_tl --symbol EURUSD --strategy bounce --start 2021-01-01 --barrier-resolution 5m_high_low
```

The command prints and the CSV/HTML report records `barrier_resolution`, `execution_mode`, `intrabar_tf`, `ambiguous_bars`, `resolved_by_subbars`, `fallback_to_pessimistic`, and `resolution_breakdown`.

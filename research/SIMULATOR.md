# Trendline simulator

The research runner now delegates to the existing `sim.core.Simulator`, the existing MTF Trendline feature builder, and the existing `tl_bounce` / `tl_breakout` strategies. It does not replace the simulator's execution or cost model.

## Install

```bash
pip install -r requirements.txt
```

## One strategy

```bash
python -m research.run_tl --symbol EURUSD --strategy bounce --start 2021-01-01
```

Aliases:

- `bounce` -> existing `tl_bounce`
- `breakout` -> existing `tl_breakout` with `retest_bars=0`
- `breakout_retest` -> existing `tl_breakout` with the registered retest window

## All three hypotheses

```bash
python -m research.run_tl --all --start 2021-01-01
```

## Confluence A/B

```bash
python -m research.run_tl --symbol EURUSD --strategy bounce --ab --start 2021-01-01
```

This compares the repository's existing confluence filter `off` vs `require`. It is an implementation A/B, not the research placebo control.

## Output

Each cell writes a directory under `research_runs/sim_*` containing:

- `trades.csv`
- `equity.csv`
- `by_year.csv`
- `signals.csv` when signals were generated
- `trendlines.csv`
- `metrics.json`
- `config.json`

The matrix also writes:

- `research_runs/simulator_summary.csv`
- `research_runs/simulator_report.html`

The report is an execution report, not a final research verdict. The authoritative Trendline research protocol still requires the separate structural scope gate, matched/time-shift controls, neighbourhood, and sealed walk-forward stages before a strategy is called robust.

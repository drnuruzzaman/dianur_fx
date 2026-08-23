# Automated Trendline Research Pipeline

This package is the orchestration layer for the registered Trendline research
protocol. It intentionally separates:

- correctness gates: pytest/causality/data/execution invariants;
- structural scope gates: diagnostic evidence and versioned scope;
- economic gates: R-conversion and friction;
- fact events: TOUCH/BREAK/RETEST/INVALIDATED;
- strategy hypotheses: Bounce/Breakout/Breakout+Retest;
- paired controls: matched placebo and time-shift control;
- robustness: neighbourhood and sealed walk-forward OOS;
- reporting: CSV/JSON/HTML artifacts.

## Non-negotiable timing contract

Every pattern instance must preserve:

`formed_i <= detected_i <= trigger_i < execution_i`

`invalid_i` is pre-trigger invalidation. `formed_i` is where the geometry sits;
`detected_i` is the first bar at which the complete structure is knowable;
`trigger_i` is the first legal strategy activation; `execution_i` is the actual
simulator fill bar.

## Fact-event contract

The fact event store contains only parameter-free structural facts:

`TOUCH`, `BREAK`, `RETEST`, `INVALIDATED`.

Strategy opinions such as Bounce or Breakout must never be written as fact event
types.

## Important control warning

The existing `sim.tl.diagnostics` module documents a known defect in its old
parallel-line placebo: the placebo approach dynamics are not matched and the
arm can collapse into a coin flip. Do **not** use that old placebo as the
research verdict. The new research protocol requires a matched control or a
same-geometry time-shift control whose construction is explicitly persisted.

## Artifacts

Each authoritative run should produce a directory containing at least:

- `manifest.json`
- `config.json`
- `diagnostics.csv`
- `scope.json`
- `economic_gate.csv`
- `events.csv` or `events.parquet`
- `funnel.csv`
- `strategy_results.csv`
- `placebo_results.csv`
- `time_shift_results.csv`
- `neighbourhood.csv`
- `walk_forward.csv`
- `verdict.json`
- `report.html`

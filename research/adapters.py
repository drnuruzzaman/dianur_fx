"""Adapters from the research orchestrator to DiaNurFx's existing simulator.

This module reuses the repository's existing data loader, feature builder,
Strategy classes, Simulator, and metrics. Research-specific execution settings
are translated explicitly into the existing Simulator Config so the run cannot
silently fall back to 15m pessimistic resolution when the registered protocol
requires 5m high/low sub-bars.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import json

import pandas as pd

from sim.core import Config, Simulator
from sim.fx import FX
from sim.instruments import account_currency, data_hash, load, spec
from sim.metrics import by_year, summarise
from sim.strategies import FEATURE_STRATEGIES
from sim.tl import build

EXEC_TF = "15m"
CONTEXT = ("1h", "4h", "1d")


@dataclass(frozen=True)
class SimulationCell:
    symbol: str
    strategy: str
    start: str
    end: str | None = None
    risk_pct: float = 0.5
    equity: float = 25_000.0
    carry_free: bool = False
    confluence_mode: str = "off"
    retest_bars: int = 0
    retest_atr: float = 0.35
    barrier_resolution: str = "5m_high_low"


def canonical_symbol(symbol: str) -> str:
    """Accept EURUSD or EURUSD.a, matching the repository's broker naming."""
    symbol = symbol.strip()
    return symbol if symbol.endswith(".a") else symbol + ".a"


def load_feature_bundle(symbol: str, start: str, end: str | None = None):
    symbol = canonical_symbol(symbol)
    bars = {tf: load(symbol, tf, start, end) for tf in (EXEC_TF, *CONTEXT)}
    features, states, lines = build(bars, EXEC_TF, CONTEXT)
    return symbol, bars[EXEC_TF], features, states, lines


def _execution_settings(barrier_resolution: str) -> tuple[str, str | None]:
    """Translate the registered research barrier model into Simulator Config."""
    if barrier_resolution == "5m_high_low":
        # Signals/entries remain on 15m; only ambiguous 15m SL/TP bars are
        # resolved from 5m sub-bars using high/low, through the simulator's
        # validated INTRABAR resolver.
        return "intrabar", "5m"
    if barrier_resolution == "15m_high_low":
        # Native 15m H/L leaves the Simulator responsible for ambiguity.
        # Pessimistic is the existing deterministic fallback.
        return "pessimistic", None
    raise ValueError(
        f"unsupported barrier_resolution={barrier_resolution!r}; "
        "expected '5m_high_low' or '15m_high_low'"
    )


def build_config(cell: SimulationCell) -> Config:
    execution, intrabar_tf = _execution_settings(cell.barrier_resolution)
    return Config(
        risk_pct=cell.risk_pct,
        start_equity=cell.equity,
        flat_by_hour=21 if cell.carry_free else None,
        apply_swap=not cell.carry_free,
        execution=execution,
        intrabar_tf=intrabar_tf,
    )


def make_strategy(cell: SimulationCell, features: pd.DataFrame):
    cls = FEATURE_STRATEGIES[cell.strategy]
    kwargs: dict[str, Any] = {"confluence_mode": cell.confluence_mode}
    if cell.strategy == "tl_breakout":
        kwargs.update(retest_bars=cell.retest_bars, retest_atr=cell.retest_atr)
    return cls(features, **kwargs)


def run_cell(root: Path, cell: SimulationCell) -> dict[str, Any]:
    symbol, bars, features, _states, lines = load_feature_bundle(
        cell.symbol, cell.start, cell.end
    )
    strategy = make_strategy(cell, features)
    fx = FX.build(account_currency())
    cfg = build_config(cell)
    sim = Simulator(spec(symbol, EXEC_TF), fx=fx, config=cfg)
    result = sim.run(bars, strategy, symbol, EXEC_TF)
    metrics = summarise(result, bars)
    metrics["data_hash"] = data_hash(symbol, EXEC_TF)
    metrics["symbol"] = symbol
    metrics["strategy"] = cell.strategy
    metrics["timeframe"] = EXEC_TF
    metrics["confluence_mode"] = cell.confluence_mode
    metrics["barrier_resolution"] = cell.barrier_resolution
    metrics["execution_mode"] = cfg.execution
    metrics["intrabar_tf"] = cfg.intrabar_tf
    metrics["ambiguous_bars"] = int(sim.ambiguous)
    metrics["resolved_by_subbars"] = int(sim.resolved_by.get("subbar", 0))
    metrics["fallback_to_pessimistic"] = int(
        sum(v for k, v in sim.resolved_by.items() if "fallback" in str(k).lower())
    )
    metrics["resolution_breakdown"] = dict(sim.resolved_by)

    sig = pd.DataFrame(strategy.signal_log)
    if len(sig):
        metrics["signals_seen"] = int(len(sig))
        metrics["signals_taken"] = int(sig["taken"].sum()) if "taken" in sig else None
        metrics["mean_confluence"] = round(float(sig["confluence"].mean()), 3) if "confluence" in sig else None

    run_id = (
        f"sim_{symbol.replace('.', '')}_{cell.strategy}_{cell.confluence_mode}"
        f"_{pd.Timestamp.utcnow().strftime('%Y%m%d%H%M%S')}"
    )
    out = root / "research_runs" / run_id
    out.mkdir(parents=True, exist_ok=True)
    result.trades.to_csv(out / "trades.csv", index=False)
    result.equity.to_csv(out / "equity.csv")
    by_year(result).to_csv(out / "by_year.csv", index=False)
    if len(sig):
        sig.to_csv(out / "signals.csv", index=False)
    lines.to_csv(out / "trendlines.csv", index=False)
    (out / "metrics.json").write_text(json.dumps(metrics, indent=2, default=str), encoding="utf-8")
    (out / "config.json").write_text(
        json.dumps({
            "run_id": run_id,
            "cell": cell.__dict__,
            "symbol": symbol,
            "execution_tf": EXEC_TF,
            "context": list(CONTEXT),
            "strategy_params": strategy.params(),
            "simulator_config": cfg.__dict__,
            "research_barrier_resolution": cell.barrier_resolution,
            "effective_execution_mode": cfg.execution,
            "effective_intrabar_tf": cfg.intrabar_tf,
            "resolution_breakdown": sim.resolved_by,
            "ambiguous_bars": sim.ambiguous,
            "data_hash": metrics["data_hash"],
        }, indent=2, default=str),
        encoding="utf-8",
    )
    return {**metrics, "run_id": run_id, "output_dir": str(out)}


def run_matrix(root: Path, cells: list[SimulationCell]) -> pd.DataFrame:
    rows = []
    for cell in cells:
        rows.append(run_cell(root, cell))
    df = pd.DataFrame(rows)
    out = root / "research_runs" / "simulator_summary.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    return df

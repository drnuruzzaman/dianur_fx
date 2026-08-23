"""Runnable Trendline simulator entry point.

Examples:
    python -m research.run_tl --symbol EURUSD --strategy bounce --start 2021-01-01
    python -m research.run_tl --all --start 2021-01-01
    python -m research.run_tl --symbol XAUUSD --strategy breakout_retest --retest-bars 12
    python -m research.run_tl --symbol EURUSD --strategy bounce --ab

This command deliberately uses the repository's existing Simulator and
Trendline strategies. It is the executable simulation layer of the larger
research protocol; it does not silently substitute its own entry or cost rules.
"""
from __future__ import annotations

import argparse
from html import escape
import json
from pathlib import Path

import pandas as pd

from .adapters import SimulationCell, run_cell
from .tl_pipeline import Pipeline, persist_json


STRATEGY_ALIASES = {
    "bounce": "tl_bounce",
    "breakout": "tl_breakout",
    "breakout_retest": "tl_breakout",
    "tl_bounce": "tl_bounce",
    "tl_breakout": "tl_breakout",
}


def load_config(root: Path, path: Path):
    try:
        import yaml
    except ImportError as exc:
        raise SystemExit("Install PyYAML: pip install pyyaml") from exc
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def make_report(root: Path, rows: list[dict], *, title: str = "DiaNurFx Trendline Simulator") -> Path:
    out = root / "research_runs" / "simulator_report.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows)
    cols = [c for c in [
        "symbol", "strategy", "confluence_mode", "trades", "avg_R",
        "profit_factor", "win_rate_pct", "max_drawdown_pct", "return_pct",
        "sharpe_daily_annualised", "signals_seen", "signals_taken", "run_id"
    ] if c in df.columns]
    table = df[cols].to_html(index=False, border=0, classes="results") if cols else "<p>No results.</p>"
    payload = df.to_dict(orient="records")
    json_text = escape(json.dumps(payload, indent=2, default=str))
    html = f"""<!doctype html>
<html><head><meta charset='utf-8'><title>{escape(title)}</title>
<style>
body{{font-family:system-ui,-apple-system,sans-serif;max-width:1400px;margin:32px auto;padding:0 20px}}
h1{{margin-bottom:4px}} .muted{{color:#666}} table{{width:100%;border-collapse:collapse}}
th,td{{padding:7px 9px;border-bottom:1px solid #ddd;text-align:right}}
th:first-child,td:first-child{{text-align:left}} .results{{font-size:14px}}
pre{{background:#f6f6f6;padding:16px;overflow:auto;max-height:500px}}
</style></head><body>
<h1>{escape(title)}</h1>
<p class='muted'>15M execution; 1H / 4H / D1 context. Uses the repository's existing Simulator.</p>
<h2>Results</h2>{table}
<h2>Machine-readable result</h2><pre>{json_text}</pre>
</body></html>"""
    out.write_text(html, encoding="utf-8")
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Run DiaNurFx Trendline strategies through the existing simulator")
    p.add_argument("--config", default="configs/tl_research.yaml")
    p.add_argument("--root", default=".")
    p.add_argument("--symbol", default="EURUSD")
    p.add_argument("--strategy", default="bounce", choices=sorted(STRATEGY_ALIASES))
    p.add_argument("--all", action="store_true", help="run all registered trendline strategy hypotheses")
    p.add_argument("--ab", action="store_true", help="run confluence off and required side-by-side")
    p.add_argument("--start", default=None)
    p.add_argument("--end", default=None)
    p.add_argument("--risk-pct", type=float, default=0.5)
    p.add_argument("--equity", type=float, default=25_000.0)
    p.add_argument("--carry-free", action="store_true")
    p.add_argument("--retest-bars", type=int, default=None)
    p.add_argument("--retest-atr", type=float, default=None)
    p.add_argument("--contract-test", action="store_true")
    args = p.parse_args(argv)

    root = Path(args.root).resolve()
    config_path = root / args.config
    if not config_path.exists():
        raise SystemExit(f"config not found: {config_path}")
    config = load_config(root, config_path)

    pipeline = Pipeline(root, config)
    pipeline.persist_manifest()
    if args.contract_test:
        payload = {
            "run_id": pipeline.result.run_id,
            "config_hash": pipeline.result.run_id,
            "status": "CONTRACT_READY",
            "message": "Research contract is installed; simulator adapter is available.",
        }
        persist_json(payload, pipeline.run_dir / "contract.json")
        print(json.dumps(payload, indent=2))
        return 0

    start = args.start or "2021-01-01"
    configured_symbols = [str(x) for x in config.get("instruments", [args.symbol])]
    symbols = configured_symbols if args.all else [args.symbol]
    if args.all and not symbols:
        raise SystemExit("no instruments configured")

    if args.all:
        hypotheses = ["bounce", "breakout", "breakout_retest"]
    else:
        hypotheses = [args.strategy]

    default_retest_bars = int(config.get("strategies", {}).get("breakout_retest", {}).get("retest_max_bars", 12))
    default_retest_atr = float(config.get("strategies", {}).get("breakout_retest", {}).get("retest_distance_atr", 0.40))

    rows: list[dict] = []
    for symbol in symbols:
        for hypothesis in hypotheses:
            modes = ["off", "require"] if args.ab else ["off"]
            for mode in modes:
                strategy_name = STRATEGY_ALIASES[hypothesis]
                is_retest = hypothesis == "breakout_retest"
                cell = SimulationCell(
                    symbol=symbol,
                    strategy=strategy_name,
                    start=start,
                    end=args.end,
                    risk_pct=args.risk_pct,
                    equity=args.equity,
                    carry_free=args.carry_free,
                    confluence_mode=mode,
                    retest_bars=(args.retest_bars if args.retest_bars is not None else (default_retest_bars if is_retest else 0)),
                    retest_atr=(args.retest_atr if args.retest_atr is not None else default_retest_atr),
                )
                print(f"Running {symbol} / {hypothesis} / confluence={mode} ...")
                try:
                    result = run_cell(root, cell)
                except Exception as exc:  # CLI should point to the failed cell clearly.
                    raise SystemExit(f"FAILED {symbol}/{hypothesis}/{mode}: {exc}") from exc
                rows.append(result)
                print(
                    f"  trades={result.get('trades', 0)} avg_R={result.get('avg_R')} "
                    f"PF={result.get('profit_factor')} return={result.get('return_pct')}%"
                )

    summary_path = root / "research_runs" / "simulator_summary.csv"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(summary_path, index=False)
    report = make_report(root, rows)
    print(f"\nWrote summary: {summary_path}")
    print(f"Wrote report:  {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

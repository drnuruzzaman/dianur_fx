"""Run the parameter-free Trendline fact-event store and funnel.

Usage:
    python -m research.run_tl_events --symbol EURUSD --start 2021-01-01
    python -m research.run_tl_events --all --start 2021-01-01

This command does not run a trading strategy. It creates the factual event store
used to size Strategy A/B/C before expensive execution tests.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import json

import pandas as pd

from sim.instruments import load
from sim.tl.engine import Params
from sim.tl.events import build_fact_events, validate_fact_events
from sim.tl.funnel import funnel, opportunity_summary


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Build causal Trendline fact events and funnel")
    p.add_argument("--root", default=".")
    p.add_argument("--symbol", default="EURUSD")
    p.add_argument("--all", action="store_true")
    p.add_argument("--start", default="2021-01-01")
    p.add_argument("--end", default=None)
    p.add_argument("--timeframes", nargs="+", default=["15m", "1h", "4h", "1d"])
    p.add_argument("--tol-atr", type=float, default=0.32,
                   help="registered structural engine tolerance; not a strategy parameter")
    p.add_argument("--min-sample", type=int, default=200)
    args = p.parse_args(argv)

    root = Path(args.root).resolve()
    symbols = ["EURUSD", "XAUUSD", "USDJPY"] if args.all else [args.symbol]
    params = Params(tol_atr=args.tol_atr)

    all_events = []
    for symbol in symbols:
        for tf in args.timeframes:
            print(f"Building events: {symbol} / {tf} ...")
            bars = load(symbol if symbol.endswith(".a") else symbol + ".a", tf, args.start, args.end)
            events = build_fact_events(symbol=symbol, bars=bars, timeframe=tf, params=params)
            errors = validate_fact_events(events)
            if errors:
                raise SystemExit(f"EVENT CORRECTNESS FAILURE {symbol}/{tf}: {'; '.join(errors)}")
            if not events.empty:
                all_events.append(events)
            print(f"  events={len(events)}")

    if all_events:
        events = pd.concat(all_events, ignore_index=True)
    else:
        events = pd.DataFrame(columns=[
            "event_id", "instrument", "timeframe", "trendline_id", "event_type",
            "occurred_at", "known_at", "price", "trendline_price", "distance",
        ])

    out = root / "research_runs" / "trendline_events"
    out.mkdir(parents=True, exist_ok=True)
    events.to_csv(out / "events.csv", index=False)
    f = funnel(events)
    f.to_csv(out / "funnel.csv", index=False)
    opp = opportunity_summary(events, min_sample=args.min_sample)
    opp.to_csv(out / "opportunity_gate.csv", index=False)
    (out / "config.json").write_text(json.dumps({
        "symbols": symbols,
        "timeframes": args.timeframes,
        "start": args.start,
        "end": args.end,
        "structural_tolerance_atr": args.tol_atr,
        "min_sample": args.min_sample,
    }, indent=2), encoding="utf-8")

    print(f"\nWrote: {out / 'events.csv'}")
    print(f"Wrote: {out / 'funnel.csv'}")
    print(f"Wrote: {out / 'opportunity_gate.csv'}")
    print("\nOpportunity gate:")
    if len(opp):
        print(opp.to_string(index=False))
    else:
        print("No factual events found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Automated trendline research pipeline.

This module is deliberately orchestration-first. It separates correctness gates
from edge gates and persists every intermediate artifact so a verdict can be
reproduced. Strategy-specific adapters are injected rather than silently
reusing existing strategy entry rules.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
from typing import Any, Callable, Iterable, Mapping, Sequence

import pandas as pd


@dataclass(frozen=True)
class ResearchScope:
    instrument: str
    timeframe: str
    hypothesis: str
    tolerance: float
    reason: str = ""
    structural_effect: float | None = None
    z_score: float | None = None


@dataclass(frozen=True)
class GateResult:
    name: str
    passed: bool
    status: str
    reason: str = ""
    metrics: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class ResearchResult:
    run_id: str
    created_at: str
    correctness: list[GateResult] = field(default_factory=list)
    structural: list[GateResult] = field(default_factory=list)
    economic: list[GateResult] = field(default_factory=list)
    edge: list[GateResult] = field(default_factory=list)
    scopes: list[ResearchScope] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        if any(not g.passed for g in self.correctness):
            return "CORRECTNESS_FAILURE"
        if not self.scopes:
            return "NO_SURVIVING_SCOPE"
        return "SURVIVES" if any(g.passed for g in self.edge) else "NO_EDGE_SURVIVES"


def make_run_id(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return "TL-" + now.strftime("%Y%m%d-%H%M%S")


def config_hash(config: Mapping[str, Any]) -> str:
    raw = json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def persist_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


def persist_json(value: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, default=str), encoding="utf-8")


def event_funnel(events: pd.DataFrame) -> pd.DataFrame:
    """Return the required fact-event funnel without strategy interpretation."""
    if events.empty:
        return pd.DataFrame(columns=["event_type", "count", "unique_trendlines", "unique_instruments"])
    required = {"event_type"}
    missing = required - set(events.columns)
    if missing:
        raise ValueError(f"events missing required columns: {sorted(missing)}")
    rows = []
    for event_type in ("TOUCH", "BREAK", "RETEST", "INVALIDATED"):
        part = events.loc[events.event_type.eq(event_type)]
        rows.append({
            "event_type": event_type,
            "count": int(len(part)),
            "unique_trendlines": int(part.trendline_id.nunique()) if "trendline_id" in part else None,
            "unique_instruments": int(part.instrument.nunique()) if "instrument" in part else None,
        })
    out = pd.DataFrame(rows)
    touch = max(int(out.loc[out.event_type.eq("TOUCH"), "count"].iloc[0]), 1)
    out["pct_of_touch"] = out["count"] / touch
    return out


def validate_event_contract(events: pd.DataFrame) -> list[str]:
    """Check the causal event contract; failures are correctness failures."""
    required = {
        "event_id", "instrument", "timeframe", "trendline_id", "event_type",
        "occurred_at", "known_at", "price", "trendline_price", "distance",
    }
    errors = [f"missing column: {x}" for x in sorted(required - set(events.columns))]
    if errors:
        return errors
    occurred = pd.to_datetime(events["occurred_at"], utc=True, errors="coerce")
    known = pd.to_datetime(events["known_at"], utc=True, errors="coerce")
    if occurred.isna().any() or known.isna().any():
        errors.append("invalid occurred_at/known_at")
    if (known < occurred).any():
        errors.append("known_at precedes occurred_at")
    allowed = {"TOUCH", "BREAK", "RETEST", "INVALIDATED"}
    bad = set(events.event_type.dropna()) - allowed
    if bad:
        errors.append(f"strategy opinions leaked into fact layer: {sorted(bad)}")
    return errors


def run_edge_gates(
    metrics: Mapping[str, Any],
    *,
    min_sample: int = 200,
    time_shift_percentile: float = 0.95,
) -> list[GateResult]:
    """Evaluate registered per-cell edge gates from already computed metrics."""
    n = int(metrics.get("n", 0))
    avg_r = float(metrics.get("avg_r", float("nan")))
    pf = float(metrics.get("profit_factor", float("nan")))
    observed = float(metrics.get("time_shift_observed", float("nan")))
    threshold = float(metrics.get("time_shift_p95", float("nan")))
    return [
        GateResult("sample_floor", n >= min_sample, "PASS" if n >= min_sample else "DROP",
                   metrics={"n": n, "minimum": min_sample}),
        GateResult("time_shift", observed >= threshold and observed == observed and threshold == threshold,
                   "PASS" if observed >= threshold and observed == observed and threshold == threshold else "DROP",
                   metrics={"observed": observed, "p95": threshold, "required_percentile": time_shift_percentile}),
        GateResult("avg_R", avg_r > 0, "PASS" if avg_r > 0 else "DROP", metrics={"avg_r": avg_r}),
        GateResult("profit_factor", pf > 1, "PASS" if pf > 1 else "DROP", metrics={"profit_factor": pf}),
    ]


def build_report_payload(result: ResearchResult, config: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "run_id": result.run_id,
        "created_at": result.created_at,
        "verdict": result.verdict,
        "config_hash": config_hash(config),
        "config": config,
        "scopes": [asdict(x) for x in result.scopes],
        "correctness": [asdict(x) for x in result.correctness],
        "structural": [asdict(x) for x in result.structural],
        "economic": [asdict(x) for x in result.economic],
        "edge": [asdict(x) for x in result.edge],
    }


def write_html_report(payload: Mapping[str, Any], path: Path) -> None:
    """Write a dependency-free report; frontend/UI can consume the same JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, indent=2, default=str).replace("</", "<\\/")
    verdict = payload.get("verdict", "UNKNOWN")
    path.write_text(
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>Trendline Research Report</title>"
        "<style>body{font-family:system-ui;max-width:1100px;margin:40px auto;padding:0 20px}"
        "pre{background:#f5f5f5;padding:16px;overflow:auto} .verdict{font-size:28px;font-weight:700}</style>"
        "</head><body><h1>Trendline Research Report</h1>"
        f"<div class='verdict'>Verdict: {verdict}</div>"
        "<h2>Run manifest</h2><pre id='json'></pre>"
        f"<script>const report={data};document.getElementById('json').textContent="
        "JSON.stringify(report,null,2);</script></body></html>",
        encoding="utf-8",
    )


class Pipeline:
    """Orchestrator. Callers provide existing project-specific adapters."""

    def __init__(self, root: str | Path, config: Mapping[str, Any]):
        self.root = Path(root)
        self.config = dict(config)
        self.result = ResearchResult(make_run_id(), datetime.now(timezone.utc).isoformat())
        self.run_dir = self.root / "research_runs" / self.result.run_id

    def persist_manifest(self) -> None:
        persist_json(build_report_payload(self.result, self.config), self.run_dir / "manifest.json")

    def save_diagnostics(self, df: pd.DataFrame) -> None:
        persist_csv(df, self.run_dir / "diagnostics.csv")

    def save_events(self, events: pd.DataFrame) -> None:
        errors = validate_event_contract(events)
        if errors:
            raise AssertionError("Event correctness gate failed: " + "; ".join(errors))
        persist_csv(events, self.run_dir / "events.csv")
        persist_csv(event_funnel(events), self.run_dir / "funnel.csv")

    def save_verdict(self, metrics: Mapping[str, Any]) -> None:
        gates = run_edge_gates(metrics, min_sample=int(self.config.get("min_sample", 200)))
        self.result.edge.extend(gates)
        payload = build_report_payload(self.result, self.config)
        persist_json(payload, self.run_dir / "verdict.json")
        write_html_report(payload, self.run_dir / "report.html")

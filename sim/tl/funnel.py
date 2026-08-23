"""Event-funnel diagnostics over the factual trendline event store.

This module deliberately does not decide profitability. It answers the cheap,
structural question: how much opportunity survives each factual transition?
"""
from __future__ import annotations

import pandas as pd

STAGES = ("TOUCH", "BREAK", "RETEST", "INVALIDATED")


def funnel(events: pd.DataFrame, *, group_by=("instrument", "timeframe")) -> pd.DataFrame:
    if events.empty:
        return pd.DataFrame(columns=[*group_by, "stage", "count", "pct_of_touch", "unique_trendlines"])
    rows = []
    groups = events.groupby(list(group_by), dropna=False) if group_by else [((), events)]
    for key, g in groups:
        if not isinstance(key, tuple):
            key = (key,)
        touch = int((g["event_type"] == "TOUCH").sum())
        for stage in STAGES:
            part = g[g["event_type"] == stage]
            rows.append({
                **dict(zip(group_by, key)),
                "stage": stage,
                "count": int(len(part)),
                "pct_of_touch": (len(part) / touch) if touch else 0.0,
                "unique_trendlines": int(part["trendline_id"].nunique()),
            })
    return pd.DataFrame(rows)


def opportunity_summary(events: pd.DataFrame, *, min_sample: int = 200) -> pd.DataFrame:
    """Upper-bound strategy opportunity counts derived only from fact events.

    These are sizing gates, not final strategy sample counts. Strategy A/B/C may
    further reduce them with their registered interpretation rules.
    """
    f = funnel(events)
    if f.empty:
        return pd.DataFrame(columns=["instrument", "timeframe", "hypothesis", "opportunities", "eligible"])
    mapping = {
        "bounce": "TOUCH",
        "breakout": "BREAK",
        "breakout_retest": "RETEST",
    }
    rows = []
    for _, r in f.iterrows():
        hypothesis = next((h for h, stage in mapping.items() if stage == r["stage"]), None)
        if hypothesis is None:
            continue
        n = int(r["count"])
        rows.append({
            "instrument": r["instrument"],
            "timeframe": r["timeframe"],
            "hypothesis": hypothesis,
            "opportunities": n,
            "min_sample": int(min_sample),
            "eligible": n >= min_sample,
        })
    return pd.DataFrame(rows)

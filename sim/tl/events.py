"""Causal, strategy-free trendline fact events.

The event layer answers only: "what objectively happened to a confirmed/live
trendline?" Strategy meanings such as Bounce, Breakout, and Retest+Rejection
belong above this layer.

Events emitted here:
    TOUCH          a tradeable line was grazed and registered a touch
    BREAK          a tradeable line was broken by the engine's structural rule
    RETEST         after a break, price returned to the broken line using the
                   engine's own structural tolerance
    INVALIDATED    a line was archived without ever producing a structural break

Every row carries occurred_at and known_at. For these candle-close structural
facts they are the same bar timestamp; strategy trigger/execution timestamps are
intentionally not produced here.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Iterable

import numpy as np
import pandas as pd

from .engine import Params, Snapshot, TrendlineEngine
from .mtf import TF_MS

EVENT_TYPES = ("TOUCH", "BREAK", "RETEST", "INVALIDATED")
REQUIRED_COLUMNS = (
    "event_id", "instrument", "timeframe", "trendline_id", "event_type",
    "occurred_at", "known_at", "price", "trendline_price", "distance",
)


@dataclass(frozen=True)
class FactEvent:
    event_id: str
    instrument: str
    timeframe: str
    trendline_id: str
    event_type: str
    occurred_at: str
    known_at: str
    price: float
    trendline_price: float
    distance: float

    def to_row(self) -> dict:
        return asdict(self)


def _utc_string(ts) -> str:
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    else:
        t = t.tz_convert("UTC")
    return t.isoformat()


def _make_id(tf: str, i: int, line_id: str, event_type: str) -> str:
    return f"{tf}:{i}:{line_id}:{event_type}"


def _touch_events(instrument: str, timeframe: str, bars: pd.DataFrame,
                  snapshots: list[Snapshot]) -> list[FactEvent]:
    events: list[FactEvent] = []
    prev_touches: dict[str, int] = {}
    for i, s in enumerate(snapshots):
        timestamp = bars.index[i]
        close = float(bars["close"].iloc[i])
        for line_id, px, touches in (
            (s.support_id, s.support_px, s.support_touches),
            (s.resistance_id, s.resistance_px, s.resistance_touches),
        ):
            if line_id is None or not np.isfinite(px):
                continue
            prior = prev_touches.get(line_id, 2)
            if touches > prior:
                events.append(FactEvent(
                    event_id=_make_id(timeframe, i, line_id, "TOUCH"),
                    instrument=instrument,
                    timeframe=timeframe,
                    trendline_id=line_id,
                    event_type="TOUCH",
                    occurred_at=_utc_string(timestamp),
                    known_at=_utc_string(timestamp),
                    price=close,
                    trendline_price=float(px),
                    distance=float(abs(close - px)),
                ))
            prev_touches[line_id] = max(prior, int(touches))
    return events


def _break_events(instrument: str, timeframe: str, bars: pd.DataFrame,
                  snapshots: list[Snapshot]) -> list[FactEvent]:
    events: list[FactEvent] = []
    for i, s in enumerate(snapshots):
        timestamp = bars.index[i]
        close = float(bars["close"].iloc[i])
        for line in s.broken_now:
            line_px = float(line.value_at(s.t))
            events.append(FactEvent(
                event_id=_make_id(timeframe, i, line.id, "BREAK"),
                instrument=instrument,
                timeframe=timeframe,
                trendline_id=line.id,
                event_type="BREAK",
                occurred_at=_utc_string(timestamp),
                known_at=_utc_string(timestamp),
                price=close,
                trendline_price=line_px,
                distance=float(abs(close - line_px)),
            ))
    return events


def _lifecycle_events(instrument: str, timeframe: str, bars: pd.DataFrame,
                      lines: pd.DataFrame, params: Params) -> list[FactEvent]:
    """Emit RETEST and INVALIDATED from the frozen line lifecycle table.

    Retest uses the engine's structural tolerance (tol_atr), not a strategy
    parameter. That keeps it a factual structural event; strategy-specific
    retest windows or rejection conditions are evaluated later.
    """
    events: list[FactEvent] = []
    if lines.empty:
        return events

    for row in lines.itertuples(index=False):
        line_id = str(getattr(row, "id"))
        timeframe_row = str(getattr(row, "timeframe"))
        if timeframe_row != timeframe:
            continue
        broken_at = getattr(row, "broken_at", None)
        archived_at = getattr(row, "archived_at", None)
        pivot_1_t = int(getattr(row, "pivot_1_t"))
        slope = float(getattr(row, "slope"))
        intercept = float(getattr(row, "intercept"))
        role = getattr(row, "support_resistance")

        if broken_at is not None:
            broken_time = pd.to_datetime(broken_at, unit="ms", utc=True)
            start = bars.index.searchsorted(broken_time, side="right")
            end = len(bars)
            if archived_at is not None:
                end_time = pd.to_datetime(archived_at, unit="ms", utc=True)
                end = min(end, int(bars.index.searchsorted(end_time, side="left")))
            seen = False
            for i in range(start, end):
                ts = bars.index[i]
                t_ms = int(pd.Timestamp(ts).value // 1_000_000)
                line_px = intercept + slope * (t_ms - pivot_1_t)
                atr = float(_atr_for_index(bars, i))
                tol = params.tol_atr * atr if np.isfinite(atr) else np.nan
                if not np.isfinite(tol):
                    continue
                px = float(bars["close"].iloc[i])
                if abs(px - line_px) <= tol:
                    events.append(FactEvent(
                        event_id=_make_id(timeframe, i, line_id, "RETEST"),
                        instrument=instrument,
                        timeframe=timeframe,
                        trendline_id=line_id,
                        event_type="RETEST",
                        occurred_at=_utc_string(ts),
                        known_at=_utc_string(ts),
                        price=px,
                        trendline_price=float(line_px),
                        distance=float(abs(px - line_px)),
                    ))
                    seen = True
                    break

        if archived_at is not None and broken_at is None:
            ts = pd.to_datetime(archived_at, unit="ms", utc=True)
            i = bars.index.searchsorted(ts, side="left")
            if i < len(bars):
                px = float(bars["close"].iloc[i])
                t_ms = int(ts.value // 1_000_000)
                line_px = intercept + slope * (t_ms - pivot_1_t)
                events.append(FactEvent(
                    event_id=_make_id(timeframe, int(i), line_id, "INVALIDATED"),
                    instrument=instrument,
                    timeframe=timeframe,
                    trendline_id=line_id,
                    event_type="INVALIDATED",
                    occurred_at=_utc_string(ts),
                    known_at=_utc_string(ts),
                    price=px,
                    trendline_price=float(line_px),
                    distance=float(abs(px - line_px)),
                ))
    return events


def _atr_for_index(bars: pd.DataFrame, i: int) -> float:
    if i < 14:
        return np.nan
    prev = bars["close"].iloc[:i + 1].to_numpy(float)
    hi = bars["high"].iloc[:i + 1].to_numpy(float)
    lo = bars["low"].iloc[:i + 1].to_numpy(float)
    tr = np.maximum.reduce([hi[1:] - lo[1:], abs(hi[1:] - prev[:-1]), abs(lo[1:] - prev[:-1])])
    if len(tr) < 14:
        return np.nan
    return float(np.mean(tr[-14:]))


def build_fact_events(
    instrument: str,
    bars: pd.DataFrame,
    *,
    timeframe: str = "15m",
    params: Params | None = None,
) -> pd.DataFrame:
    """Build long-format factual events from an incremental trendline walk."""
    params = params or Params()
    engine = TrendlineEngine(timeframe, TF_MS[timeframe], params)
    snapshots = engine.walk(bars)
    lines = pd.DataFrame([l.to_row() for s in snapshots for l in s.live])
    # `live` misses objects after archival, so prefer the engine's full objects if
    # the implementation exposes them; otherwise lifecycle events still come
    # from line rows retained by the feature builder.
    touch = _touch_events(instrument, timeframe, bars, snapshots)
    breaks = _break_events(instrument, timeframe, bars, snapshots)
    life = _lifecycle_events(instrument, timeframe, bars, lines, params)
    rows = [e.to_row() for e in (*touch, *breaks, *life)]
    if not rows:
        return pd.DataFrame(columns=REQUIRED_COLUMNS)
    out = pd.DataFrame(rows).sort_values(["occurred_at", "event_type", "trendline_id"]).reset_index(drop=True)
    return out[list(REQUIRED_COLUMNS)]


def validate_fact_events(events: pd.DataFrame) -> list[str]:
    errors: list[str] = []
    missing = [c for c in REQUIRED_COLUMNS if c not in events.columns]
    if missing:
        return [f"missing column: {x}" for x in missing]
    occurred = pd.to_datetime(events["occurred_at"], utc=True, errors="coerce")
    known = pd.to_datetime(events["known_at"], utc=True, errors="coerce")
    if occurred.isna().any() or known.isna().any():
        errors.append("invalid occurred_at/known_at")
    if (known < occurred).any():
        errors.append("known_at precedes occurred_at")
    if events["event_type"].isin(["BOUNCE", "BREAKOUT", "ENTRY", "LONG", "SHORT"]).any():
        errors.append("strategy opinion leaked into fact events")
    bad = set(events["event_type"].dropna()) - set(EVENT_TYPES)
    if bad:
        errors.append(f"unknown event types: {sorted(bad)}")
    if events["event_id"].duplicated().any():
        errors.append("duplicate event_id")
    return errors

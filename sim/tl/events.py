"""Causal, strategy-free trendline fact events."""
from __future__ import annotations

from dataclasses import dataclass, asdict

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
        for line_id, _role, px, _quality, touches in s.tradeable:
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
    """Emit structural RETEST and INVALIDATED facts."""
    events: list[FactEvent] = []
    if lines.empty:
        return events
    for row in lines.itertuples(index=False):
        line_id = str(getattr(row, "id"))
        if str(getattr(row, "timeframe")) != timeframe:
            continue
        broken_at = getattr(row, "broken_at", None)
        archived_at = getattr(row, "archived_at", None)
        pivot_1_t = int(getattr(row, "pivot_1_t"))
        slope = float(getattr(row, "slope"))
        intercept = float(getattr(row, "intercept"))

        if broken_at is not None:
            broken_time = pd.to_datetime(broken_at, unit="ms", utc=True)
            start = int(bars.index.searchsorted(broken_time, side="right"))
            end = len(bars)
            if archived_at is not None:
                end_time = pd.to_datetime(archived_at, unit="ms", utc=True)
                end = min(end, int(bars.index.searchsorted(end_time, side="left")))
            for i in range(start, end):
                ts = bars.index[i]
                t_ms = int(pd.Timestamp(ts).value // 1_000_000)
                line_px = intercept + slope * (t_ms - pivot_1_t)
                atr = _atr_for_index(bars, i)
                tol = params.tol_atr * atr if np.isfinite(atr) else np.nan
                if np.isfinite(tol):
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
                        break

        if archived_at is not None and broken_at is None:
            ts = pd.to_datetime(archived_at, unit="ms", utc=True)
            i = int(bars.index.searchsorted(ts, side="left"))
            if i < len(bars):
                px = float(bars["close"].iloc[i])
                t_ms = int(ts.value // 1_000_000)
                line_px = intercept + slope * (t_ms - pivot_1_t)
                events.append(FactEvent(
                    event_id=_make_id(timeframe, i, line_id, "INVALIDATED"),
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
    close = bars["close"].iloc[:i + 1].to_numpy(float)
    high = bars["high"].iloc[:i + 1].to_numpy(float)
    low = bars["low"].iloc[:i + 1].to_numpy(float)
    tr = np.maximum.reduce([high[1:] - low[1:], abs(high[1:] - close[:-1]), abs(low[1:] - close[:-1])])
    if len(tr) < 14:
        return np.nan
    return float(np.mean(tr[-14:]))


def build_fact_events(instrument: str, bars: pd.DataFrame, *,
                      timeframe: str = "15m", params: Params | None = None) -> pd.DataFrame:
    """Build long-format factual events from an incremental trendline walk."""
    params = params or Params()
    engine = TrendlineEngine(timeframe, TF_MS[timeframe], params, record_tradeable=True)
    snapshots = engine.walk(bars)
    unique_lines = {l.id: l for s in snapshots for l in s.live}
    lines = pd.DataFrame([l.to_row() for l in unique_lines.values()])
    rows = [e.to_row() for e in (
        *_touch_events(instrument, timeframe, bars, snapshots),
        *_break_events(instrument, timeframe, bars, snapshots),
        *_lifecycle_events(instrument, timeframe, bars, lines, params),
    )]
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
    bad_opinion = {"BOUNCE", "BREAKOUT", "ENTRY", "LONG", "SHORT"}
    if set(events["event_type"].dropna()) & bad_opinion:
        errors.append("strategy opinion leaked into fact events")
    bad = set(events["event_type"].dropna()) - set(EVENT_TYPES)
    if bad:
        errors.append(f"unknown event types: {sorted(bad)}")
    if events["event_id"].duplicated().any():
        errors.append("duplicate event_id")
    return errors

"""
events.py — the fact layer between the detector and any strategy.

The detector answers "what happened?". A strategy answers "should I trade it?".
Nothing here answers the second question: there is no side, no stop, no target
and no entry price in an event row. That separation is the whole point — a
strategy that changes its mind about stops must not be able to change the
history of what the market did.

WHAT AN EVENT IS

    CONFIRMED     a third distinct touch promoted a candidate; the market has
                  acknowledged the line. Emitted so the lifecycle
                  CANDIDATE -> CONFIRMED -> ACTIVE -> BROKEN -> ARCHIVED can be
                  rebuilt from the event store alone.
    TOUCH         price grazed an already-tradeable line within tolerance
    BREAK         a CLOSE went through a tradeable line beyond tolerance
    RETEST        after a BREAK, price came back to the broken line
    INVALIDATED   a tradeable line stopped being offered without ever breaking
                  (drifted too far, outranked, degenerate)
    REJECTION     the candle at a TOUCH or RETEST closed away from the line

TWO CLOCKS, ALWAYS BOTH

    occurred_at   bar OPEN time — when the thing happened
    known_at      bar CLOSE time — the first moment it could have been acted on

They differ by exactly one bar interval and that gap is where trendline
backtests lie. Every consumer must filter on `known_at`; `occurred_at` is for
drawing and for joining back to the chart. Nothing downstream is allowed to
assume they are the same.

PARAMETER-FREE, AND WHERE THAT STOPS BEING TRUE

TOUCH / BREAK / CONFIRMED / INVALIDATED carry exactly one parameter, the
engine's `tol_atr`, and that is a property of the detector, not of a trading
idea — it is recorded on every row so a result can be traced to the tolerance
that produced it.

RETEST and REJECTION are NOT parameter-free and are not pretended to be.
"came back to the line" needs a distance and a patience; "rejected" needs a
wick threshold. Their parameters are written into every row they produce
(`retest_atr`, `retest_bars`, `rejection_ratio`) and `param_free` is False, so
a sweep over them can never be mistaken for a fact about the market.

BREAKOUT is deliberately absent. "The break followed through by k ATR" is a
hypothesis with a free parameter and a forward-looking window; it belongs in
the strategy layer, built from a BREAK event plus bars. Emitting it here would
smuggle a strategy into the fact store.
"""

import hashlib
from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

from ..indicators import atr as atr_series
from .engine import Params, TrendlineEngine
from .mtf import TF_MS

TOUCH = 'TOUCH'
BREAK = 'BREAK'
RETEST = 'RETEST'
REJECTION = 'REJECTION'
INVALIDATED = 'INVALIDATED'
CONFIRMED = 'CONFIRMED'

#: every column an event row carries, in order. Downstream code asserts on this
#: rather than on whatever a particular run happened to produce.
SCHEMA = (
    'event_id', 'instrument', 'timeframe', 'trendline_id', 'event_type',
    'occurred_at', 'known_at', 'price', 'trendline_price', 'distance',
    # context that costs nothing to carry and is expensive to rebuild later
    'distance_atr', 'atr', 'role', 'direction', 'line_status', 'quality',
    'touches', 'bar',
    # provenance: what produced this row
    'tol_atr', 'param_free', 'parent_event_id',
    'retest_atr', 'retest_bars', 'rejection_ratio',
)


@dataclass
class EventParams:
    """Only the parameterised events have parameters. That is not an accident."""
    retest_atr: float = 0.4      # how close a return counts as a retest
    retest_bars: int = 24        # give up waiting for one after this many
    rejection_ratio: float = 0.5 # wick beyond the line, as a fraction of range
    rejection_close: float = 0.5 # close must finish this far back into the range


def event_id(instrument, tf, tol_atr, line_id, event_type, occurred_at, bar):
    """
    Stable across reruns and across data start dates.

    A bar INDEX is not identity — it shifts the moment `--start` changes — so
    the hash is over the timestamp. The bar number is carried as a column for
    debugging and deliberately kept OUT of the key.
    """
    key = '%s|%s|%.4f|%s|%s|%s' % (instrument, tf, tol_atr, line_id,
                                   event_type, pd.Timestamp(occurred_at).isoformat())
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def _rejection(o, h, l, c, role, ep: EventParams):
    """
    Did this candle reject the level, in its own geometry alone?

    Support: the low probed below and the close finished back in the upper part
    of the range. Resistance: mirrored. No reference to any later bar — a
    rejection is a fact about one candle, knowable at its close.
    """
    rng = h - l
    if rng <= 0:
        return False
    if role == 'support':
        wick = (min(o, c) - l) / rng
        pos = (c - l) / rng
    else:
        wick = (h - max(o, c)) / rng
        pos = (h - c) / rng
    return wick >= ep.rejection_ratio * 0.5 and pos >= ep.rejection_close


def build(bars, tf, instrument, params: Params = None, ep: EventParams = None):
    """
    Walk one instrument x timeframe and return the event store as a DataFrame
    with exactly `SCHEMA` columns, sorted by `known_at`.

    `bars` is indexed by bar OPEN time (the MT5 convention), so a bar's close —
    and therefore the moment its facts became knowable — is open + one interval.
    """
    params = params or Params()
    ep = ep or EventParams()
    tf_ms = TF_MS[tf]
    eng = TrendlineEngine(tf, tf_ms, params, record_facts=True)
    eng.walk(bars)

    close = np.asarray(bars['close'], dtype=float)
    high = np.asarray(bars['high'], dtype=float)
    low = np.asarray(bars['low'], dtype=float)
    opn = np.asarray(bars['open'], dtype=float)
    atr = np.asarray(atr_series(bars, 14), dtype=float)
    idx = bars.index
    n = len(close)
    step = pd.Timedelta(milliseconds=tf_ms)

    rows = []

    def emit(kind, i, line, price, line_price, param_free=True, parent=None,
             **extra):
        a = atr[i] if np.isfinite(atr[i]) and atr[i] > 0 else np.nan
        occurred = idx[i]
        row = {
            'event_id': event_id(instrument, tf, params.tol_atr, line.id, kind,
                                 occurred, i),
            'instrument': instrument, 'timeframe': tf, 'trendline_id': line.id,
            'event_type': kind, 'occurred_at': occurred, 'known_at': occurred + step,
            'price': price, 'trendline_price': line_price,
            'distance': price - line_price,
            'distance_atr': (price - line_price) / a if a == a else np.nan,
            'atr': a, 'role': line.role.value, 'direction': line.direction.value,
            'line_status': extra.pop('line_status', line.status.value),
            'quality': extra.pop('quality', line.quality_score),
            'touches': extra.pop('touches', line.touches), 'bar': i,
            'tol_atr': params.tol_atr, 'param_free': param_free,
            'parent_event_id': parent,
            'retest_atr': np.nan, 'retest_bars': np.nan,
            'rejection_ratio': np.nan,
        }
        row.update(extra)
        rows.append(row)
        return row['event_id']

    for f in eng.facts:
        i, line = f['bar'], f['line']
        eid = emit(f['kind'], i, line, f['price'], f['line_price'],
                   line_status=f['status_at'], quality=f['quality_at'],
                   touches=f['touches_at'])

        # a candle that touched may also have rejected: same bar, same close,
        # a second and strictly weaker fact hanging off the first
        if f['kind'] in (TOUCH, CONFIRMED) and _rejection(
                opn[i], high[i], low[i], close[i], line.role.value, ep):
            emit(REJECTION, i, line, close[i], f['line_price'],
                 param_free=False, parent=eid,
                 line_status=f['status_at'], quality=f['quality_at'],
                 touches=f['touches_at'], rejection_ratio=ep.rejection_ratio)

        if f['kind'] != BREAK:
            continue

        # --- the break happened; look forward for the return to the line --- #
        # The break side is fixed at the break: support breaks DOWN, so a
        # retest is price coming back UP to the line from below. Price that
        # closes back through the line has reclaimed it, not retested it, and
        # the wait ends there.
        side = -1 if line.role.value == 'support' else 1
        last = min(n - 1, i + ep.retest_bars)
        for k in range(i + 1, last + 1):
            a = atr[k]
            if not np.isfinite(a) or a <= 0:
                continue
            lv = line.value_at(int(idx[k].value // 1_000_000))
            if (close[k] - lv) * side < 0:
                break                       # reclaimed: not a retest
            if abs(close[k] - lv) <= ep.retest_atr * a:
                rid = emit(RETEST, k, line, close[k], lv, param_free=False,
                           parent=eid, line_status=f['status_at'],
                           quality=f['quality_at'], touches=f['touches_at'],
                           retest_atr=ep.retest_atr, retest_bars=ep.retest_bars)
                if _rejection(opn[k], high[k], low[k], close[k],
                              # a broken support is resistance now, and the
                              # rejection that matters is off its new side
                              'resistance' if line.role.value == 'support'
                              else 'support', ep):
                    emit(REJECTION, k, line, close[k], lv, param_free=False,
                         parent=rid, line_status=f['status_at'],
                         quality=f['quality_at'], touches=f['touches_at'],
                         rejection_ratio=ep.rejection_ratio)
                break                       # the FIRST return is the retest

    ev = pd.DataFrame(rows, columns=list(SCHEMA))
    if len(ev):
        ev = ev.sort_values(['known_at', 'event_type', 'trendline_id'],
                            kind='mergesort').reset_index(drop=True)
    return ev


def funnel(ev: pd.DataFrame) -> pd.DataFrame:
    """
    How many of each fact, and how many survived to the next one.

    This is the report that kills a strategy before it is written: if 900 breaks
    produce 40 retests, strategy C has no sample and no amount of backtesting
    will manufacture one.
    """
    if not len(ev):
        return pd.DataFrame()
    out = []
    for (sym, tf, tol), g in ev.groupby(['instrument', 'timeframe', 'tol_atr']):
        n = {k: int((g.event_type == k).sum())
             for k in (CONFIRMED, TOUCH, BREAK, RETEST, INVALIDATED, REJECTION)}
        parents = set(g.loc[g.event_type == REJECTION, 'parent_event_id'].dropna())
        touch_ids = set(g.loc[g.event_type == TOUCH, 'event_id'])
        break_ids = set(g.loc[g.event_type == BREAK, 'event_id'])
        retest_parents = set(g.loc[g.event_type == RETEST,
                                   'parent_event_id'].dropna())
        retest_ids = set(g.loc[g.event_type == RETEST, 'event_id'])
        out.append({
            'instrument': sym, 'timeframe': tf, 'tol_atr': tol,
            'lines_confirmed': n[CONFIRMED],
            'touch': n[TOUCH],
            'touch_rejected': len(touch_ids & parents),
            'break': n[BREAK],
            'break_retested': len(break_ids & retest_parents),
            'retest_pct': round(100 * len(break_ids & retest_parents)
                                / n[BREAK], 1) if n[BREAK] else np.nan,
            'retest_rejected': len(retest_ids & parents),
            'invalidated': n[INVALIDATED],
        })
    return pd.DataFrame(out)


#: no cell below this many events is reported on at all. A 27-event result in
#: this project reversed sign at 850; printing a number for it invites belief it
#: cannot support.
SAMPLE_FLOOR = 200


def cells(ev: pd.DataFrame, floor: int = SAMPLE_FLOOR) -> pd.DataFrame:
    """Which instrument x timeframe x event_type cells are big enough to test."""
    if not len(ev):
        return pd.DataFrame()
    g = (ev.groupby(['instrument', 'timeframe', 'tol_atr', 'event_type'])
           .size().rename('n').reset_index())
    g['testable'] = g.n >= floor
    return g.sort_values(['instrument', 'timeframe', 'event_type'])

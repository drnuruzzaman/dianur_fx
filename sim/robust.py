"""
robust.py — Stage 1: is there anything here at all?

Three tests, deliberately cheap, run before any expensive machinery:

  1. TIME-SHIFT CONTROL — the strategy's own signals, replayed at a random lag.
     Trade count, side mix and risk geometry are identical; only the ALIGNMENT
     with price is destroyed. If a strategy performs as well shifted 300 bars
     as it does in place, it is not using information in the market — it is
     harvesting the drift and volatility structure of the instrument, which any
     schedule of trades would have harvested too.

     This is a stronger control than random entries with fresh parameters,
     because everything except timing is held constant. Intents are recorded in
     ATR-relative terms (side, stop distance, target distance) and rebuilt at
     the shifted bar, so the risk taken is the same size in the same units.

  2. TRADE BOOTSTRAP — resample realised trades with replacement to get a
     DISTRIBUTION of final equity and max drawdown. The historical drawdown is
     one path; the one you would live through is usually worse.

  3. SAMPLE FLOOR — below `min_trades` no metric is reported at all. A 27-trade
     result in this project reversed sign at 850 trades; printing a number for
     it invites belief it cannot support.

Nothing here tunes anything. It only decides whether tuning is worth doing.
"""

from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np
import pandas as pd

from .core import FLAT, LONG, SHORT, Config, Intent, Simulator, Strategy
from .indicators import atr as atr_fn

MIN_TRADES = 200


# --------------------------------------------------------------------------- #
# recording what a strategy asked for, in units that travel                   #
# --------------------------------------------------------------------------- #
@dataclass
class Recorder(Strategy):
    """
    Wraps a strategy and records every intent in ATR-relative terms, so it can
    be replayed at a different bar without carrying stale absolute prices.
    """
    inner: Strategy = None
    name: str = 'recorder'

    def __post_init__(self):
        self.warmup = self.inner.warmup
        self.log = []           # (bar_index, side, stop_atr, target_atr)
        self._atr = None

    def prepare(self, bars):
        series = self.inner.prepare(bars)
        self._atr = atr_fn(bars, 14)
        self._close = np.asarray(bars['close'], dtype=float)
        return series

    def on_bar(self, view, position):
        intent = self.inner.on_bar(view, position)
        if intent is not None and intent.side != FLAT and position is None:
            a = self._atr[view.i]
            if np.isfinite(a) and a > 0:
                c = view.close()
                stop_atr = abs(c - intent.stop) / a if intent.stop is not None else None
                tgt_atr = abs(intent.target - c) / a if intent.target is not None else None
                self.log.append((view.i, intent.side, stop_atr, tgt_atr))
        return intent

    def params(self):
        return self.inner.params()


class TimeShiftControl(Strategy):
    """
    Replays a recorded signal schedule at `shift` bars offset, rebuilding stops
    and targets from the shifted bar's own price and ATR. Same trades, same
    risk, wrong moment.
    """
    name = 'time_shift_control'

    def __init__(self, log, shift, n_bars, max_bars=None, warmup=200):
        self.shift = shift
        self.max_bars = max_bars
        self.warmup = warmup
        # circular shift, so the schedule keeps its clustering
        self.plan = {}
        for (i, side, stop_atr, tgt_atr) in log:
            j = (i + shift) % n_bars
            self.plan.setdefault(j, (side, stop_atr, tgt_atr))
        self._atr = None

    def prepare(self, bars):
        self._atr = atr_fn(bars, 14)
        return {}

    def on_bar(self, view, position):
        if position is not None:
            if self.max_bars and view.i - position.entry_i >= self.max_bars:
                return Intent(FLAT, tag='time_stop')
            return None
        plan = self.plan.get(view.i)
        if plan is None:
            return None
        side, stop_atr, tgt_atr = plan
        a = self._atr[view.i]
        if not np.isfinite(a) or a <= 0 or not stop_atr:
            return None
        c = view.close()
        stop = c - stop_atr * a if side == LONG else c + stop_atr * a
        target = None
        if tgt_atr:
            target = c + tgt_atr * a if side == LONG else c - tgt_atr * a
        return Intent(side, stop=stop, target=target, tag='control')

    def params(self):
        return {'shift': self.shift}


# --------------------------------------------------------------------------- #
# the three tests                                                             #
# --------------------------------------------------------------------------- #
def time_shift_distribution(bars, spec, cfg, strategy_factory, symbol, tf,
                            n_shifts=60, seed=11, max_bars=None, fx=None):
    """
    Returns (real_result, DataFrame of control runs). Shifts are drawn from the
    middle of the range so a shift is never near 0 or n (which would reproduce
    the real alignment).
    """
    rec = Recorder(inner=strategy_factory())
    real = Simulator(spec, fx=fx, config=cfg).run(bars, rec, symbol, tf)
    n = len(bars)
    if not rec.log or not len(real.trades):
        return real, pd.DataFrame()

    # Match the holding period. A strategy that exits on its own signal (Donchian
    # leaves the channel; no target) has no exit rule the control can copy, so
    # without a time stop the control rides winners to the end of the data and
    # reports absurd R multiples. Median real duration is the fair substitute.
    if max_bars is None:
        max_bars = int(max(1, real.trades['bars_held'].median()))

    rng = np.random.default_rng(seed)
    lo, hi = max(50, n // 50), n - max(50, n // 50)
    shifts = rng.integers(lo, hi, size=n_shifts)
    rows = []
    for sh in shifts:
        ctrl = TimeShiftControl(rec.log, int(sh), n, max_bars=max_bars,
                                warmup=rec.warmup)
        res = Simulator(spec, fx=fx, config=cfg).run(bars, ctrl, symbol, tf)
        t = res.trades
        rows.append({
            'shift': int(sh), 'trades': len(t),
            'avg_R': float(t['r_multiple'].mean()) if len(t) else np.nan,
            'net': float(t['net_acct'].sum()) if len(t) else 0.0,
            'pf': _pf(t),
        })
    ctrl_df = pd.DataFrame(rows)
    ctrl_df.attrs['max_bars'] = max_bars
    return real, ctrl_df


def _pf(t):
    if not len(t):
        return np.nan
    win = t.loc[t['net_acct'] > 0, 'net_acct'].sum()
    loss = -t.loc[t['net_acct'] < 0, 'net_acct'].sum()
    return float(win / loss) if loss else np.inf


def percentile_of(value, sample):
    s = np.asarray([x for x in sample if np.isfinite(x)], dtype=float)
    if not len(s) or not np.isfinite(value):
        return np.nan
    return round(100.0 * float((s < value).mean()), 1)


def bootstrap(trades: pd.DataFrame, n=2000, seed=5):
    """
    Resample trades with replacement. Reports the drawdown you should expect
    rather than the one that happened to occur.
    """
    if not len(trades):
        return {}
    pnl = trades['net_acct'].to_numpy(float)
    rng = np.random.default_rng(seed)
    finals, dds = [], []
    for _ in range(n):
        draw = rng.choice(pnl, size=len(pnl), replace=True)
        eq = np.cumsum(draw)
        peak = np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:]
        dds.append(float(np.min(eq - peak)))
        finals.append(float(eq[-1]))
    hist_eq = np.cumsum(pnl)
    hist_peak = np.maximum.accumulate(np.concatenate([[0.0], hist_eq]))[1:]
    return {
        'net_p05': round(float(np.percentile(finals, 5)), 2),
        'net_p50': round(float(np.percentile(finals, 50)), 2),
        'net_p95': round(float(np.percentile(finals, 95)), 2),
        'prob_net_negative': round(float(np.mean(np.asarray(finals) < 0)), 3),
        'dd_historical': round(float(np.min(hist_eq - hist_peak)), 2),
        'dd_p50': round(float(np.percentile(dds, 50)), 2),
        'dd_p95': round(float(np.percentile(dds, 5)), 2),   # 5th pct = deepest
    }


def gates(real_trades, control: pd.DataFrame, min_trades=MIN_TRADES):
    """
    Binary gates with the numbers behind them. Never collapsed to a score.

    Three gates, and the third exists because the first two let a losing
    strategy through. USDJPY 15m ema_cross scored percentile 100.0 against its
    time-shift control out-of-sample -- and had PF 0.985 and avg R -0.003. The
    control was at -0.1064: at 15m the cost of a trade dominates, so randomly
    timed entries bleed, and merely not bleeding reads as a landslide win.

    gate_beats_control answers "is the timing better than chance". It does not
    answer "does this make money", and on a costly timeframe those come apart.
    gate_profitable asks the second question directly.
    """
    n = len(real_trades)
    out = {'trades': n, 'gate_sample': n >= min_trades}
    if n == 0:
        return out
    real_r = float(real_trades['r_multiple'].mean())
    out['avg_R'] = round(real_r, 4)
    out['pf'] = round(_pf(real_trades), 3)
    out['gate_profitable'] = bool(real_r > 0 and out['pf'] > 1.0)
    if len(control):
        pct = percentile_of(real_r, control['avg_R'])
        out.update({
            'control_runs': len(control),
            'control_avgR_p50': round(float(control['avg_R'].median()), 4),
            'control_avgR_p95': round(float(control['avg_R'].quantile(0.95)), 4),
            'percentile_vs_control': pct,
            'gate_beats_control': bool(np.isfinite(pct) and pct >= 95.0),
        })
    return out

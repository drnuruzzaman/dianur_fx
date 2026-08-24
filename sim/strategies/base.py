"""
base.py — the bridge between the trendline engine and the simulator.

The engine describes structure; the simulator enforces reality; strategies here
decide. Keeping those three apart is what lets the same trendline objects feed a
bounce strategy, a breakout strategy and a divergence strategy without any of
them knowing about each other.

A feature table (sim/tl/features.build) is passed in at construction. Two
guarantees are enforced rather than assumed:

  * Index identity — the feature table must be exactly the bars the simulator is
    walking. A silent misalignment here would be undetectable in the results, so
    it raises instead.
  * Causality — every column comes from the engine, which built them
    incrementally, and higher-timeframe columns came through the closed-bar-only
    MTF map. Nothing in a feature row could have been known later than its bar.

Confluence is RECORDED on every signal whether or not it gates entry, so
`confluence_mode='off'` and `'require'` runs are directly comparable. The spec
asks whether MTF confluence adds edge; that is a measurement, not an assumption.
"""

import numpy as np
import pandas as pd

from ..core import FLAT, LONG, SHORT, Intent, Strategy
from ..tl.mtf import confluence

REGIME_CODES = {'trending_up': 1.0, 'trending_down': 2.0,
                'sideways': 3.0, 'transition': 4.0}
DIR_CODES = {'up': 1.0, 'down': -1.0, 'flat': 0.0}

CONTEXT_TFS = ('1h', '4h', 'd1')


def _encode(values, table):
    return np.asarray([table.get(v, np.nan) if v is not None else np.nan
                       for v in values], dtype=float)


class MTFStrategy(Strategy):
    """Common plumbing: feature access, EMA filter, confluence, exits."""

    #: columns every subclass needs; subclasses extend it
    BASE_COLUMNS = ('15m_atr', '15m_ema_fast', '15m_ema_slow', '15m_range_pos')

    def __init__(self, features: pd.DataFrame, *, exec_tf='15m',
                 confluence_mode='off', min_confluence=0.5,
                 ema_filter=True, regime_filter=True,
                 risk_reward=2.0, stop_atr=1.0, max_bars=96, warmup=200):
        self.features = features
        self.exec_tf = exec_tf
        self.confluence_mode = confluence_mode      # 'off' | 'require' | 'prefer'
        self.min_confluence = min_confluence
        self.ema_filter = ema_filter
        self.regime_filter = regime_filter
        self.risk_reward = risk_reward
        self.stop_atr = stop_atr
        self.max_bars = max_bars
        self.warmup = warmup
        self.signal_log = []                        # every signal, gated or not

    # ---- plumbing ------------------------------------------------------- #
    #: BASE_COLUMNS declares the EXECUTION-frame columns with a '15m_' prefix
    #: because 15m is the default execution frame. It is a placeholder, not a
    #: literal: `columns()` rewrites it to whatever `exec_tf` actually is, so a
    #: strategy can be run at another execution timeframe without every
    #: subclass restating its column list. Context-frame columns (1h_, 4h_,
    #: d1_) are left alone -- those name real, specific frames.
    EXEC_PLACEHOLDER = '15m'

    def columns(self):
        pre = self.EXEC_PLACEHOLDER + '_'
        out = []
        for c in self.BASE_COLUMNS:
            if c.startswith(pre) and self.exec_tf != self.EXEC_PLACEHOLDER:
                out.append(f'{self.exec_tf}_{c[len(pre):]}')
            else:
                out.append(c)
        # Running at a higher execution frame makes the lower context frames
        # meaningless -- a 4h execution bar cannot take context from 1h. Drop
        # any context column whose frame is at or below the execution frame
        # rather than demanding a column the feature build could not produce.
        if self.exec_tf != self.EXEC_PLACEHOLDER:
            order = ['1m', '5m', '15m', '30m', '1h', '4h', 'd1', '1d']
            def rank(tf):
                tf = 'd1' if tf == '1d' else tf
                return order.index(tf) if tf in order else -1
            keep, ex = [], rank(self.exec_tf)
            for c in out:
                tf = c.split('_', 1)[0]
                if tf == self.exec_tf or rank(tf) < 0 or rank(tf) > ex:
                    keep.append(c)
            out = keep
        return tuple(dict.fromkeys(out))

    def prepare(self, bars):
        f = self.features
        if len(f) != len(bars) or not f.index.equals(bars.index):
            raise ValueError(
                'feature table does not match the bars being simulated '
                f'({len(f)} rows vs {len(bars)}) — rebuild features for this slice')
        out = {}
        for col in self.columns():
            if col not in f.columns:
                raise KeyError(f'feature table has no column {col!r}')
            series = f[col]
            if col.endswith('_regime'):
                out[col] = _encode(series, REGIME_CODES)
            elif col.endswith('_trend_direction'):
                out[col] = _encode(series, DIR_CODES)
            elif series.dtype == object:
                out[col] = np.asarray([np.nan if v is None else 1.0 for v in series])
            else:
                out[col] = series.to_numpy(dtype=float)
        self._series_names = tuple(out)
        return out

    # ---- helpers -------------------------------------------------------- #
    def context_directions(self, view):
        """Trend direction per context timeframe, as the engine saw it."""
        out = {}
        for tf in CONTEXT_TFS:
            col = f'{tf}_trend_direction'
            if col in self._series_names:
                v = view.series(col)
                out['1d' if tf == 'd1' else tf] = (
                    None if np.isnan(v) else
                    'up' if v > 0 else 'down' if v < 0 else 'flat')
        return out

    def confluence_for(self, view, side):
        want = 'up' if side == LONG else 'down'
        return confluence(self.context_directions(view), want)

    def gate(self, view, side, reason, extra=None):
        """
        Apply the confluence policy and record the decision either way. The log
        is what lets a later A/B answer 'did the filter help?' rather than
        assuming it did.
        """
        score, label = self.confluence_for(view, side)
        allowed = True
        if self.confluence_mode == 'require' and score < self.min_confluence:
            allowed = False
        self.signal_log.append({
            'i': view.i, 'side': side, 'reason': reason,
            'confluence': score, 'agreement': label, 'taken': allowed,
            **(extra or {}),
        })
        return allowed, score

    def ema_ok(self, view, side):
        if not self.ema_filter:
            return True
        f, s = view.series(f'{self.exec_tf}_ema_fast'), view.series(f'{self.exec_tf}_ema_slow')
        if np.isnan(f) or np.isnan(s):
            return False
        return f >= s if side == LONG else f <= s

    def regime_code(self, view, tf=None):
        return view.series(f'{tf or self.exec_tf}_regime')

    def atr(self, view):
        return view.series(f'{self.exec_tf}_atr')

    def manage(self, view, position):
        """Shared exit: time stop. Price stops/targets are the simulator's job."""
        if self.max_bars and view.i - position.entry_i >= self.max_bars:
            return Intent(FLAT, tag='time_stop')
        return None

    # populated by prepare() so context_directions can check availability
    _series_names = ()

    def on_bar(self, view, position):
        raise NotImplementedError

    def params(self):
        return {'confluence_mode': self.confluence_mode,
                'min_confluence': self.min_confluence,
                'ema_filter': self.ema_filter, 'regime_filter': self.regime_filter,
                'risk_reward': self.risk_reward, 'stop_atr': self.stop_atr,
                'max_bars': self.max_bars}

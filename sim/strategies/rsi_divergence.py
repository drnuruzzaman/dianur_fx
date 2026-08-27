"""
rsi_divergence.py — price and momentum disagreeing.

    Bullish: price prints a LOWER swing low while RSI prints a HIGHER low.
             Selling pressure is fading even though price is not.
    Bearish: price prints a HIGHER swing high while RSI prints a LOWER high.

THE LOOK-AHEAD TRAP, AND WHY THIS DOES NOT FALL IN IT

Divergence is the single easiest thing in technical analysis to backtest
dishonestly, because a swing low is only identifiable *after* the bars that
confirm it. Draw it on a finished chart and every divergence looks tradeable at
the exact low. Here, a pivot may only be used from its `confirmed_i` — the bar
`strength` candles later, when it genuinely became visible — which is carried in
the feature table by construction (sim/tl/pivots.py). The entry is therefore
always late, exactly as it is in life.

A divergence also has a shelf life: `valid_bars` after confirmation it is stale
and no longer traded, rather than being available forever.
"""

import numpy as np

from ..core import LONG, SHORT, Intent
from .base import MTFStrategy


class RsiDivergence(MTFStrategy):
    name = 'rsi_divergence'

    BASE_COLUMNS = (
        '15m_atr', '15m_ema_fast', '15m_ema_slow', '15m_regime',
        '15m_pivot_low_price', '15m_pivot_high_price',
        '15m_pivot_low_i', '15m_pivot_high_i',
        '1h_trend_direction', '4h_trend_direction', 'd1_trend_direction',
    )

    def __init__(self, features, *, rsi_len=14, min_rsi_gap=2.0, max_pivot_gap=60,
                 min_pivot_gap=5, oversold=40.0, overbought=60.0,
                 valid_bars=6, allow_short=True, **kw):
        super().__init__(features, **kw)
        self.rsi_len = rsi_len
        self.min_rsi_gap = min_rsi_gap        # RSI points, so the divergence is real
        self.max_pivot_gap = max_pivot_gap    # two swings further apart are unrelated
        self.min_pivot_gap = min_pivot_gap
        self.oversold = oversold              # bullish divergence needs weak RSI
        self.overbought = overbought
        self.valid_bars = valid_bars
        self.allow_short_side = allow_short
        self._armed = None                    # {side, until_i, pivot_price}

    def params(self):
        return {**super().params(), 'rsi_len': self.rsi_len,
                'min_rsi_gap': self.min_rsi_gap, 'oversold': self.oversold,
                'overbought': self.overbought, 'valid_bars': self.valid_bars,
                'max_pivot_gap': self.max_pivot_gap}

    # columns() is deliberately NOT overridden. It used to be, returning
    # BASE_COLUMNS verbatim, which bypassed two things the base does: rewriting
    # the '15m_' placeholder to the real execution frame, and dropping context
    # frames at or below it. The first meant this strategy could only ever run
    # at 15m -- it asked a 4h feature table for '15m_atr' and raised -- and it
    # failed that way silently until a cross-timeframe comparison was tried.

    def prepare(self, bars):
        """
        Detection comes from sim/divergence.py — the SAME function the chart
        draws from (js/chart/divergence.js is its mirror, compared in
        tests/test_parity.py). One definition, so a divergence you can see is a
        divergence the backtest traded.

        Events are keyed at each pivot's CONFIRMATION bar, which is what keeps
        this causal: nothing appears before it could have been known.
        """
        from ..divergence import as_arrays
        out = super().prepare(bars)
        out.update(as_arrays(
            bars, rsi_len=self.rsi_len, strength=3,
            min_rsi_gap=self.min_rsi_gap, min_pivot_gap=self.min_pivot_gap,
            max_pivot_gap=self.max_pivot_gap,
            oversold=self.oversold, overbought=self.overbought))
        from ..indicators import rsi as rsi_fn
        out['rsi'] = rsi_fn(bars, self.rsi_len)
        return out

    # ------------------------------------------------------------------ #
    def on_bar(self, view, position):
        if position is not None:
            self._armed = None
            return self.manage(view, position)

        a = self.atr(view)
        if np.isnan(a) or a <= 0:
            return None

        # a divergence confirmed on this bar arms the strategy for a few bars
        side_code = view.series('div_side')
        if side_code and not np.isnan(side_code) and side_code != 0:
            side = LONG if side_code > 0 else SHORT
            if side == SHORT and not self.allow_short_side:
                self._armed = None
            else:
                self._armed = {
                    'side': side, 'until_i': view.i + self.valid_bars,
                    'pivot_price': view.series('div_pivot_price'),
                    'reason': 'bullish_div' if side == LONG else 'bearish_div',
                    'rsi_gap': view.series('div_rsi_gap'),
                    'bars_apart': view.series('div_bars_apart'),
                }

        armed = self._armed
        if armed is None or view.i > armed['until_i']:
            self._armed = None
            return None

        side = armed['side']
        if not self.ema_ok(view, side):
            return None
        c = view.close()
        # the swing that formed the divergence is the natural invalidation point
        if side == LONG:
            stop = armed['pivot_price'] - self.stop_atr * a
            risk = c - stop
        else:
            stop = armed['pivot_price'] + self.stop_atr * a
            risk = stop - c
        if risk <= 0 or np.isnan(risk):
            return None
        target = (c + self.risk_reward * risk) if side == LONG else (c - self.risk_reward * risk)
        ok, _ = self.gate(view, side, armed['reason'],
                          {'rsi_gap': armed['rsi_gap'], 'bars_apart': armed['bars_apart']})
        if not ok:
            return None
        self._armed = None
        return Intent(side, stop=stop, target=target, tag=armed['reason'])

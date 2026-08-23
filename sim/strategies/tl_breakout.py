"""
tl_breakout.py — trade the line failing.

The mirror image of the bounce, deliberately: running both over the same lines
answers whether the detector finds levels that HOLD or levels that BREAK, which
is more useful than whether either happens to make money.

Entry:  the engine reports a line BROKEN on this bar (a close beyond tolerance),
        optionally waiting up to `retest_bars` for price to return to the broken
        line and hold it — the classic break-and-retest.
Stop:   back through the broken line by `stop_atr`. If price reclaims it, the
        break was a lie.
Target: `risk_reward` x risk.

Regime cuts the opposite way to the bounce: a break during a trend is
continuation, a break inside a dead range is usually noise.
"""

import numpy as np

from ..core import LONG, SHORT, Intent
from .base import MTFStrategy


class TrendlineBreakout(MTFStrategy):
    name = 'tl_breakout'

    BASE_COLUMNS = (
        '15m_atr', '15m_ema_fast', '15m_ema_slow', '15m_range_pos', '15m_regime',
        '15m_resistance_broken', '15m_support_broken',
        '15m_broken_resistance_price', '15m_broken_support_price',
        '15m_broken_resistance_quality', '15m_broken_support_quality',
        '1h_trend_direction', '4h_trend_direction', 'd1_trend_direction',
        '4h_regime',
    )

    def __init__(self, features, *, min_quality=40.0, retest_bars=0,
                 retest_atr=0.35, allow_short=True, **kw):
        super().__init__(features, **kw)
        self.min_quality = min_quality
        self.retest_bars = retest_bars       # 0 = enter on the break itself
        self.retest_atr = retest_atr
        self.allow_short_side = allow_short
        self._pending = None                 # awaiting a retest

    def params(self):
        return {**super().params(), 'min_quality': self.min_quality,
                'retest_bars': self.retest_bars, 'retest_atr': self.retest_atr}

    def on_bar(self, view, position):
        if position is not None:
            self._pending = None
            return self.manage(view, position)

        a = self.atr(view)
        if np.isnan(a) or a <= 0:
            return None
        regime = self.regime_code(view)
        c = view.close()

        if self.regime_filter and regime == 3:      # dead range: skip breaks
            self._pending = None
            return None

        # 1. a line broke on this bar
        for side, broke_col, px_col, q_col, tag in (
                (LONG, '15m_resistance_broken', '15m_broken_resistance_price',
                 '15m_broken_resistance_quality', 'break_up'),
                (SHORT, '15m_support_broken', '15m_broken_support_price',
                 '15m_broken_support_quality', 'break_dn')):
            if side == SHORT and not self.allow_short_side:
                continue
            if view.series(broke_col) != 1:
                continue
            q = view.series(q_col)
            if np.isnan(q) or q < self.min_quality:
                continue
            line_px = view.series(px_col)
            if np.isnan(line_px):
                continue
            if self.retest_bars > 0:
                self._pending = {'side': side, 'line_px': line_px, 'q': q,
                                 'expires_i': view.i + self.retest_bars}
                continue
            intent = self._enter(view, side, line_px, a, c, tag, q)
            if intent is not None:
                return intent

        # 2. or a pending retest came back
        p = self._pending
        if p is not None:
            if view.i > p['expires_i']:
                self._pending = None
            else:
                near = abs(c - p['line_px']) <= self.retest_atr * a
                held = (c > p['line_px']) if p['side'] == LONG else (c < p['line_px'])
                if near and held:
                    self._pending = None
                    return self._enter(view, p['side'], p['line_px'], a, c, 'retest', p['q'])
        return None

    def _enter(self, view, side, line_px, a, c, tag, quality):
        if not self.ema_ok(view, side):
            return None
        stop = (line_px - self.stop_atr * a) if side == LONG else (line_px + self.stop_atr * a)
        risk = (c - stop) if side == LONG else (stop - c)
        if risk <= 0:
            return None
        target = (c + self.risk_reward * risk) if side == LONG else (c - self.risk_reward * risk)
        ok, _ = self.gate(view, side, tag, {'quality': quality})
        if not ok:
            return None
        return Intent(side, stop=stop, target=target, tag=tag)

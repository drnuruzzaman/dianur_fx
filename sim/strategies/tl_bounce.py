"""
tl_bounce.py — trade the line holding.

Thesis: a confirmed trendline that price is approaching will hold, so buy at
support and sell at resistance. The natural home for this is a RANGE, which is
why the regime filter wants sideways or transition and refuses to buy support
while the execution timeframe is trending down — catching a falling knife is the
classic way this strategy dies.

Entry:  price within `entry_atr` of a tradeable support (or resistance) line,
        the line's quality above `min_quality`, regime permitting, EMA agreeing.
Stop:   beyond the line by `stop_atr`. If the line fails, the idea has failed.
Target: `risk_reward` x risk, or the opposing line if that is nearer.
"""

import numpy as np

from ..core import LONG, SHORT, Intent
from .base import MTFStrategy


class TrendlineBounce(MTFStrategy):
    name = 'tl_bounce'

    BASE_COLUMNS = (
        '15m_atr', '15m_ema_fast', '15m_ema_slow', '15m_range_pos', '15m_regime',
        '15m_support_distance', '15m_resistance_distance', '15m_support_price',
        '15m_resistance_price', '15m_trendline_strength',
        '1h_trend_direction', '4h_trend_direction', 'd1_trend_direction',
        '4h_regime',
    )

    def __init__(self, features, *, entry_atr=0.5, min_quality=45.0,
                 allow_short=True, **kw):
        super().__init__(features, **kw)
        self.entry_atr = entry_atr
        self.min_quality = min_quality
        self.allow_short_side = allow_short

    def params(self):
        return {**super().params(), 'entry_atr': self.entry_atr,
                'min_quality': self.min_quality}

    def on_bar(self, view, position):
        if position is not None:
            return self.manage(view, position)

        a = self.atr(view)
        if np.isnan(a) or a <= 0:
            return None
        quality = view.series(self.ex('trendline_strength'))
        if np.isnan(quality) or quality < self.min_quality:
            return None

        regime = self.regime_code(view)     # 1 up, 2 down, 3 sideways, 4 transition
        c = view.close()

        # --- long at support ---
        sup_d = view.series(self.ex('support_distance'))
        sup_px = view.series(self.ex('support_price'))
        if not np.isnan(sup_d) and 0 <= sup_d <= self.entry_atr and not np.isnan(sup_px):
            if not (self.regime_filter and regime == 2):
                if self.ema_ok(view, LONG):
                    stop = sup_px - self.stop_atr * a
                    risk = c - stop
                    if risk > 0:
                        target = c + self.risk_reward * risk
                        res_px = view.series(self.ex('resistance_price'))
                        if not np.isnan(res_px) and res_px > c:
                            target = min(target, res_px)
                        ok, _ = self.gate(view, LONG, 'bounce_support',
                                          {'quality': quality, 'dist_atr': sup_d})
                        if ok:
                            return Intent(LONG, stop=stop, target=target,
                                          tag='bounce_support')

        # --- short at resistance ---
        if not self.allow_short_side:
            return None
        res_d = view.series(self.ex('resistance_distance'))
        res_px = view.series(self.ex('resistance_price'))
        if not np.isnan(res_d) and 0 <= res_d <= self.entry_atr and not np.isnan(res_px):
            if not (self.regime_filter and regime == 1):
                if self.ema_ok(view, SHORT):
                    stop = res_px + self.stop_atr * a
                    risk = stop - c
                    if risk > 0:
                        target = c - self.risk_reward * risk
                        sup2 = view.series(self.ex('support_price'))
                        if not np.isnan(sup2) and sup2 < c:
                            target = max(target, sup2)
                        ok, _ = self.gate(view, SHORT, 'bounce_resistance',
                                          {'quality': quality, 'dist_atr': res_d})
                        if ok:
                            return Intent(SHORT, stop=stop, target=target,
                                          tag='bounce_resistance')
        return None

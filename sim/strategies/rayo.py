"""
rayo.py — RSS, the Rayo Scalping Strategy: trade the leg, boundary to boundary.

WHAT THE SCREENSHOT ACTUALLY SHOWS. The circles alternate: down leg, up leg,
down leg, up leg, inside a rising channel. They are not a trend with pullbacks
and they are not breakouts. Each circle is ONE LEG of an oscillation that starts
at one boundary and ends at the other. That is the thing to trade, and the
target is not a multiple of risk -- it is the other side of the channel.

WHAT V1 GOT WRONG, recorded because the failure is what shaped this.

V1 was a trend-continuation rule: it required the higher timeframes to agree
before buying a pullback. It measured -0.040 / -0.098 / -0.068 R on 5m across
the three eras and, decisively, **-0.019 / -0.078 / -0.047 with costs zeroed**.
A rule that loses in a frictionless simulator does not have an execution
problem, and every positive cell it produced sat in 2023-26, where gold rose
151% -- the trend was paying, not the setup. Requiring HTF agreement is what
made it a bull-market rule wearing a scalping costume.

RSS DROPS THE TREND GATE and is symmetric by construction. The screenshot's
legs go both ways; a rule that only buys can see half of them at best.

THE THREE CHANGES THAT MATTER, none of them measured on this project yet:

  TARGET IS THE OPPOSITE BOUNDARY, not `risk_reward` x risk. Every rule tested
  here so far exits on a fixed R multiple or a Donchian channel. The leg in the
  screenshot ends where the channel ends, and that distance moves bar to bar.
  `Intent.target` carries it.

  A CHANNEL-HEIGHT FLOOR IN ATR, which is the direct answer to friction rather
  than a hope about it. Cost measured ~0.021 R per trade at a swing-based stop.
  Requiring `min_height_atr` of channel before entering makes the reward leg
  large by construction, so cost as a FRACTION of the trade shrinks -- the one
  lever that actually moves the friction floor. A channel shorter than the
  spread is not a scalp, it is a donation.

  ENTRY AT THE BOUNDARY, NOT IN THE MIDDLE. `channel_position` is the
  proposal's own formula, (price - lower) / (upper - lower). Long only in the
  bottom `edge` of it, short only in the top. A leg entered mid-channel has
  half the target and the same stop.

MEASURED. IT FAILS, AND IT FAILS PRECISELY -- which is worth more than the six
vaguer failures before it.

Expectancy R by era (2017-19 / 2020-22 / 2023-26):

    5m    -0.238 / -0.125 / -0.038
    15m   -0.152 / -0.139 / -0.491
    30m   -0.945 / -0.466 / -0.221

Nine of nine negative, INCLUDING 2023-26 -- the era whose 151% gold rally
rescued every other rule tested here. No trend was carrying this one, which is
what dropping the HTF gate was supposed to achieve, and it is why the numbers
mean what they say.

WHY IT FAILS, from the exit reasons on 5m:

    stop      65-70%   mean R -1.08 to -1.16
    target    21-32%   mean R +1.75 to +1.98

THE PAYOFF STRUCTURE WORKS AND THE PREMISE DOES NOT. Winners really are worth
~1.8x the losers, so the channel-height floor and the boundary target did their
job. But price touching one boundary reaches the other only 21 / 26 / 32% of the
time; the rest of the time it goes THROUGH the boundary instead. With +1.87
against -1.11 the break-even hit rate is 37.2%, and the measured rate including
`signal` exits is 29 / 34 / 35%. Short by three to eight points, consistently.

That is the screenshot's model refuted with a number. An oscillation that
completes about a third of the time still draws a chart full of completed
oscillations -- the two-thirds that broke through do not look like anything
worth circling afterwards. The eye selects the survivors.

It is also the CLOSEST any fast-cell rule has come. Three points of hit rate is
not nothing, but closing it must happen on data nobody has looked at: every era
above has now been read, so tuning against them is fitting to the answer.

DONCHIAN IS NOT TOUCHED BY THIS FILE, deliberately. It is the validated 4h edge
(+0.178 R, 381 trades, placebo percentile 100). Overwriting it to chase a 5m
idea would trade the one measured thing for an unmeasured one.
"""

import numpy as np

from ..core import LONG, SHORT, Intent
from .base import MTFStrategy


class RayoScalp(MTFStrategy):
    name = 'rayo_scalp'

    BASE_COLUMNS = (
        '15m_atr', '15m_ema_fast', '15m_ema_slow', '15m_range_pos', '15m_regime',
        '15m_support_price', '15m_resistance_price',
        '15m_pivot_high_price', '15m_pivot_low_price',
        '1h_trend_direction', '4h_trend_direction', 'd1_trend_direction',
        '4h_regime',
    )

    def __init__(self, features, *,
                 edge=0.25,             # enter only within this fraction of a boundary
                 min_height_atr=4.0,    # the leg must be worth taking
                 turn_bars=2,           # closes confirming the turn away from the edge
                 stop_atr=1.0,          # beyond the boundary; the boundary IS the idea
                 target_buffer=0.15,    # stop short of the far edge, in channel fraction
                 allow_short=True, **kw):
        kw.setdefault('ema_filter', False)      # symmetric: no directional filter
        kw.setdefault('regime_filter', False)
        kw['stop_atr'] = stop_atr
        super().__init__(features, **kw)
        self.edge = edge
        self.min_height_atr = min_height_atr
        self.turn_bars = int(turn_bars)
        self.target_buffer = target_buffer
        self.allow_short_side = allow_short
        self._up = 0
        self._dn = 0
        self._last_close = None

    def params(self):
        return {**super().params(), 'edge': self.edge,
                'min_height_atr': self.min_height_atr,
                'turn_bars': self.turn_bars,
                'target_buffer': self.target_buffer}

    def on_bar(self, view, position):
        if position is not None:
            self._up = self._dn = 0
            return self.manage(view, position)

        a = self.atr(view)
        if not np.isfinite(a) or a <= 0:
            return None
        px = lambda k: view.series(self.ex(k))
        c = view.close()
        sup, res = px('support_price'), px('resistance_price')
        if not (np.isfinite(sup) and np.isfinite(res)) or res <= sup:
            return None

        # THE CHANNEL MUST BE WORTH TRADING, checked before anything else so a
        # narrow one costs no further work and -- the real point -- cannot
        # produce a trade whose target sits inside the spread.
        height = res - sup
        if height < self.min_height_atr * a:
            return None

        pos = (c - sup) / height          # the proposal's channel_position

        # Turn confirmation: consecutive closes moving away from the boundary.
        if self._last_close is not None:
            if c > self._last_close:
                self._up, self._dn = self._up + 1, 0
            elif c < self._last_close:
                self._dn, self._up = self._dn + 1, 0
        self._last_close = c

        # ---- long: at the floor, turning up, aiming at the ceiling ----
        if pos <= self.edge and self._up >= self.turn_bars:
            ok, _ = self.gate(view, LONG, 'rss_leg_up')
            if ok:
                return Intent(LONG,
                              stop=sup - self.stop_atr * a,
                              target=res - self.target_buffer * height,
                              tag='rss_long')

        # ---- short: at the ceiling, turning down, aiming at the floor ----
        if self.allow_short_side and pos >= (1.0 - self.edge) and self._dn >= self.turn_bars:
            ok, _ = self.gate(view, SHORT, 'rss_leg_dn')
            if ok:
                return Intent(SHORT,
                              stop=res + self.stop_atr * a,
                              target=sup + self.target_buffer * height,
                              tag='rss_short')

        return None

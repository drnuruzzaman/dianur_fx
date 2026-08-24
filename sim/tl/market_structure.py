"""
market_structure.py — BOS and CHoCH.

    BOS    Break of Structure / Market Structure Break.
           Price closes through the last swing in the SAME direction as the
           prevailing bias. Continuation: the trend made another leg.

    CHoCH  Change of Character / Market Structure Shift.
           Price closes through the last swing AGAINST the prevailing bias.
           The first evidence the trend may be over, and it flips the bias.

The distinction is not in the break -- it is the same event -- but in the state
it arrives in. Closing above the last swing high is a BOS while the bias is
already bullish and a CHoCH while it is bearish. That is the whole idea, and it
is why this needs a state machine rather than a per-bar rule.

Definitions follow "Market Structure CHoCH/BOS (Fractal)" by LuxAlgo
(TradingView, 2023, open source): CHoCH confirms a reversal, BOS occurs within
an established trend confirming a new higher high or lower low.

CLOSES, NOT WICKS, exactly as sim/tl/engine.py breaks a trendline: a wick
through the last swing is a test of it, a close through it is a failure of it.

A LEVEL IS CONSUMED WHEN IT BREAKS. Once price closes above the last swing high
that level is spent -- it cannot be broken twice -- and no bullish event can
fire again until a NEW swing high confirms. Without that, one strong trend
prints a BOS on every bar it makes a new high.

CAUSALITY. Only pivots that have reached `confirmed_i` are ever adopted, so the
reference level at bar i is one a bar-i observer could have known. Nothing here
reads forward.
"""

from dataclasses import dataclass
from typing import List, Optional

import numpy as np

from .pivots import find_pivots

BOS = 'bos'
CHOCH = 'choch'
BULL = 'bullish'
BEAR = 'bearish'
NEUTRAL = 'neutral'


@dataclass
class MSEvent:
    """One structural break."""
    kind: str            # bos | choch
    direction: str       # bullish | bearish
    i: int               # bar the close broke through
    t: int               # ms
    level: float         # the swing price that was broken
    level_i: int         # bar the broken swing occurred on
    bias_before: str
    bias_after: str
    close: float

    def to_row(self) -> dict:
        return {'kind': self.kind, 'direction': self.direction, 'i': self.i,
                't': self.t, 'level': self.level, 'level_i': self.level_i,
                'bias_before': self.bias_before, 'bias_after': self.bias_after,
                'close': self.close}


@dataclass
class MSParams:
    strength: int = 3          # fractal size for the swings
    close_confirm: bool = False   # see sim/tl/pivots.py find_pivots
    # A break must clear the level by this much ATR before it counts. Zero
    # reproduces the textbook rule; a small value stops a level being "broken"
    # by a close a tenth of a pip through it, which on a 15m chart happens
    # constantly and produces events nobody would call structure.
    buffer_atr: float = 0.0


def detect(bars, params: MSParams = None, atr=None, times=None):
    """
    Returns (events, arrays).

    `events` is a list of MSEvent in bar order. `arrays` holds per-bar state a
    feature table or a chart can read directly:

        bias        bullish | bearish | neutral, as known at that bar
        swing_high  the live unbroken swing high, NaN once consumed
        swing_low   the live unbroken swing low
        event       'bos' | 'choch' | '' on the bar it fired
        event_dir   'bullish' | 'bearish' | ''
    """
    p = params or MSParams()
    high = np.asarray(bars['high'], dtype=float)
    low = np.asarray(bars['low'], dtype=float)
    close = np.asarray(bars['close'], dtype=float)
    n = len(close)
    if times is None:
        times = np.asarray(bars.index.astype('int64') // 1_000_000)
    if atr is None and p.buffer_atr > 0:
        from ..indicators import atr as atr_series
        atr = atr_series(bars, 14)

    piv_hi, piv_lo = find_pivots(high, low, p.strength, close=close,
                                 close_confirm=getattr(p, 'close_confirm', False))
    # index pivots by the bar they become VISIBLE, not the bar they happened
    hi_by_conf = [[] for _ in range(n + 1)]
    lo_by_conf = [[] for _ in range(n + 1)]
    for q in piv_hi:
        if 0 <= q['confirmed_i'] < n:
            hi_by_conf[q['confirmed_i']].append(q)
    for q in piv_lo:
        if 0 <= q['confirmed_i'] < n:
            lo_by_conf[q['confirmed_i']].append(q)

    bias = NEUTRAL
    sh = sl = None                     # live unbroken swings: {price, i}
    events: List[MSEvent] = []

    a_bias = np.full(n, NEUTRAL, dtype=object)
    a_sh = np.full(n, np.nan)
    a_sl = np.full(n, np.nan)
    a_ev = np.full(n, '', dtype=object)
    a_dir = np.full(n, '', dtype=object)

    for i in range(n):
        # 1. newly visible swings replace the reference level
        for q in hi_by_conf[i]:
            sh = {'price': q['price'], 'i': q['i']}
        for q in lo_by_conf[i]:
            sl = {'price': q['price'], 'i': q['i']}

        buf = 0.0
        if p.buffer_atr > 0 and atr is not None and np.isfinite(atr[i]):
            buf = p.buffer_atr * atr[i]

        # 2. a CLOSE through a live level
        if sh is not None and close[i] > sh['price'] + buf:
            kind = BOS if bias == BULL else CHOCH
            before = bias
            bias = BULL
            events.append(MSEvent(kind, BULL, i, int(times[i]), sh['price'],
                                  sh['i'], before, bias, float(close[i])))
            a_ev[i], a_dir[i] = kind, BULL
            sh = None                  # consumed: it cannot break twice
        elif sl is not None and close[i] < sl['price'] - buf:
            kind = BOS if bias == BEAR else CHOCH
            before = bias
            bias = BEAR
            events.append(MSEvent(kind, BEAR, i, int(times[i]), sl['price'],
                                  sl['i'], before, bias, float(close[i])))
            a_ev[i], a_dir[i] = kind, BEAR
            sl = None

        a_bias[i] = bias
        a_sh[i] = sh['price'] if sh else np.nan
        a_sl[i] = sl['price'] if sl else np.nan

    return events, {'bias': a_bias, 'swing_high': a_sh, 'swing_low': a_sl,
                    'event': a_ev, 'event_dir': a_dir}


def latest(bars, params: MSParams = None):
    """State at the last bar — what a panel needs."""
    events, arr = detect(bars, params)
    if not len(bars):
        return None
    i = len(bars) - 1
    last = events[-1] if events else None
    return {'bias': arr['bias'][i], 'swing_high': arr['swing_high'][i],
            'swing_low': arr['swing_low'][i],
            'last_event': last.kind if last else None,
            'last_event_dir': last.direction if last else None,
            'last_event_i': last.i if last else None,
            'bars_since': (i - last.i) if last else None}

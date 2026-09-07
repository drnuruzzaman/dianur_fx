"""
swings.py — Layer B. The swing DETECTOR, as a state machine.

pivots.py answers "is bar i a fractal extreme" over a completed array. That is a
batch question, and it can only ever say yes or no. This asks the live question
instead: at each bar, what does an observer standing on that bar believe about
the swings behind them, and what changed since the previous bar?

That distinction produces three states rather than two:

    CANDIDATE    the wick has printed and beats everything to its LEFT. It is a
                 possible extreme. Nothing to its right exists yet.
    CONFIRMED    `strength` bars have since printed and none of them took the
                 level out. The candidate survived its test.
    INVALIDATED  a bar inside the confirmation window exceeded the level. The
                 candidate was tried and failed.

INVALIDATED is not bookkeeping. Under pivots.py a failed candidate is
indistinguishable from a bar that was never interesting -- both are simply
absent -- so nothing downstream can ask "how many attempts at this level have
failed?" or "is this market producing candidates that keep dying?". Those are
different questions from "where are the swings", and they need the failures kept.

TWO CLOCKS, and this is the point of the module.

    t_event      when the market MADE the extreme          (the wick's bar)
    t_known      when an observer could know it was a swing (t_event + strength)

Both are recorded on every swing. A backtest may read `price` and `t_event` for
plotting and attribution -- that is history, and history is allowed to be exact
-- but may only ACT on a swing once the simulated clock has passed `t_known`.
Collapsing the two is the classic fractal look-ahead: it makes a detector appear
to have called the top, when in truth it named it three bars late.

    10:00 ---- 10:15 ---- 10:30
      |                     |
      wick made        swing knowable

WICK SETS THE PRICE, CLOSES CONFIRM IT. The extreme itself is always the wick:
that is the price the market actually traded and the level everything downstream
measures against. What `close_confirm` changes is the TEST -- see find_pivots.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

import numpy as np

from .clockguard import require_naive


class SwingState(str, Enum):
    CANDIDATE = 'CANDIDATE'
    CONFIRMED = 'CONFIRMED'
    INVALIDATED = 'INVALIDATED'


@dataclass
class Swing:
    """One swing attempt, from candidate through to its resolution."""

    i: int                       # bar the extreme printed on
    t_event: int                 # ms of that bar — when the market MADE it
    price: float                 # the WICK. never a close.
    is_high: bool
    state: SwingState = SwingState.CANDIDATE
    t_known: Optional[int] = None    # ms it became knowable; None while candidate
    confirmed_i: Optional[int] = None
    invalidated_i: Optional[int] = None
    bars_survived: int = 0           # confirmation bars passed so far
    label: Optional[str] = None      # HH/HL/LH/LL, filled by Layer C

    @property
    def actionable_at(self) -> Optional[int]:
        """Bar index from which a strategy may legally use this swing."""
        return self.confirmed_i

    def to_row(self):
        return {'i': self.i, 't_event': self.t_event, 'price': self.price,
                'is_high': self.is_high, 'state': self.state.value,
                't_known': self.t_known, 'confirmed_i': self.confirmed_i,
                'invalidated_i': self.invalidated_i, 'label': self.label}


def _beats_left(series, i, strength, is_high):
    """Strict on the left — the flat-top rule, matching find_pivots."""
    for k in range(1, strength + 1):
        if is_high:
            if not series[i] > series[i - k]:
                return False
        else:
            if not series[i] < series[i - k]:
                return False
    return True


def _atr_filter(swings, atr, sensitivity):
    """
    Keep only swings that moved far enough from the LAST RETAINED swing.

    `Swing Threshold = ATR(n) x Sensitivity`. A fractal window asks "is this the
    highest of N bars", which is a question about SHAPE; this asks "did price
    actually travel", which is a question about SIZE. On XAUUSD a 3-bar fractal
    fires on moves a 3-bar fractal on EURUSD would never see, because the
    instruments move differently -- the ATR threshold is what makes the two
    comparable.

    CAUSAL BY CONSTRUCTION. The comparison is against the last swing already
    KEPT, never against the next one. The obvious implementation -- drop a swing
    because the following swing is too close -- needs a bar that has not printed
    and would be look-ahead wearing a plausible face.
    """
    if sensitivity <= 0:
        return swings
    out = []
    last = None
    for s in swings:
        a = atr[s.i] if s.i < len(atr) else float('nan')
        if not (a == a and a > 0):
            continue
        if last is None or abs(s.price - last.price) >= sensitivity * a:
            out.append(s)
            last = s
    return out


def detect(bars, strength=3, close_confirm=False, keep_invalidated=True,
           atr_sensitivity=0.0, atr_len=14):
    """
    Walk the bars once, emitting every swing attempt with its final state.

    Returns a list of Swing, ordered by the bar the extreme printed on. Every
    record carries both clocks, so a consumer can plot on `t_event` and act on
    `t_known` without the two ever being confused for each other.

    `atr_sensitivity` > 0 additionally requires each CONFIRMED swing to sit at
    least `atr_sensitivity x ATR` away from the previous retained swing -- the
    ZigZag-style size filter, applied causally. 0 disables it, which is the
    behaviour every measurement in this project was made under.

    A candidate is opened the moment a bar beats its left neighbours. It is then
    carried for `strength` bars:

      * a later bar exceeds the level          -> INVALIDATED at that bar
      * (close_confirm) a later bar closes back
        through the pivot bar's close          -> INVALIDATED at that bar
      * the window completes untouched         -> CONFIRMED at i + strength

    The right-hand comparison stays TOLERANT (`>=` for highs) so a flat top
    reports once rather than killing itself against its own twin — the same
    asymmetry find_pivots uses, for the same reason.
    """
    require_naive(bars, 'swings.detect bars')
    high = np.asarray(bars['high'], dtype=float)
    low = np.asarray(bars['low'], dtype=float)
    close = np.asarray(bars['close'], dtype=float)
    times = np.asarray(bars.index.astype('int64') // 1_000_000)
    n = len(high)
    # One list, appended at creation and mutated in place as the walk ages each
    # candidate. A second pass to collect would have to re-derive state that the
    # walk already knows.
    all_swings: List[Swing] = []
    if n < strength * 2 + 1:
        return all_swings

    open_swings: List[Swing] = []
    for i in range(strength, n):
        # --- age every open candidate against THIS bar ---------------------- #
        still: List[Swing] = []
        for s in open_swings:
            series = high if s.is_high else low
            exceeded = (series[i] > s.price) if s.is_high else (series[i] < s.price)
            closed_back = False
            if close_confirm:
                closed_back = ((close[i] >= close[s.i]) if s.is_high
                               else (close[i] <= close[s.i]))
            if exceeded or closed_back:
                s.state = SwingState.INVALIDATED
                s.invalidated_i = i
                continue
            s.bars_survived = i - s.i
            if s.bars_survived >= strength:
                s.state = SwingState.CONFIRMED
                s.confirmed_i = i
                s.t_known = int(times[i])
                continue
            still.append(s)
        open_swings = still

        # --- open new candidates on this bar -------------------------------- #
        for is_high, series in ((True, high), (False, low)):
            if _beats_left(series, i, strength, is_high):
                s = Swing(i=i, t_event=int(times[i]),
                          price=float(series[i]), is_high=is_high)
                all_swings.append(s)
                open_swings.append(s)

    # Whatever is still open at the last bar stays CANDIDATE: the market has not
    # said yet. Reporting those as confirmed is exactly the look-ahead the two
    # clocks exist to prevent.
    all_swings.sort(key=lambda s: (s.i, not s.is_high))
    if not keep_invalidated:
        all_swings = [s for s in all_swings
                      if s.state is not SwingState.INVALIDATED]
    if atr_sensitivity > 0:
        from ..indicators import atr as atr_series
        a = atr_series(bars, atr_len)
        # Applied to CONFIRMED swings only. An invalidated candidate never
        # became a swing, so filtering it on size would be answering a question
        # about something that does not exist.
        conf = [s for s in all_swings if s.state is SwingState.CONFIRMED]
        keep = {id(s) for s in _atr_filter(conf, a, atr_sensitivity)}
        all_swings = [s for s in all_swings
                      if s.state is not SwingState.CONFIRMED or id(s) in keep]
    return all_swings


def confirmed_by(swings, upto_i):
    """Only the swings a bar-`upto_i` observer could legally act on."""
    return [s for s in swings
            if s.state is SwingState.CONFIRMED and s.confirmed_i is not None
            and s.confirmed_i <= upto_i]

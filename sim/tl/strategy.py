"""
strategy.py — Layer E. The layer that answers "should I trade?"

Everything below this file describes the market. This is the only layer that
decides, and keeping it separate matters because the eleven detectors measured
in this project were each tested by wiring them STRAIGHT to a bracket: level
touched -> enter. That is Layer D masquerading as Layer E, and all eleven failed
the economic gate.

This does not assume stacking gates will rescue them. It makes the stack
explicit and measurable, which is a precondition for finding out.

WHAT IT CONSUMES

    A  observation      ATR regime, spread, session
    B  swing detector   a CONFIRMED swing, read through its knowledge clock
    C  structure        HH/HL/LH/LL bias agreeing with the trade
    D  trendline        a tradeable line, its quality, and a retest of it

EVERY REJECTION IS RECORDED. `Decision.blocked_by` names the first gate that
said no, and `evaluate` never short-circuits silently. Without that, a strategy
that takes four trades a year is indistinguishable from one that is broken, and
the tuning conversation becomes guesswork about which gate is doing the work.

CAUSALITY. Every input is read at bar `i` from state that was knowable at bar
`i`: swings via `confirmed_i <= i`, lines via the engine's own `_offerable`,
ATR and momentum from closed bars only. The layer never sees `i + 1`.
"""
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np

SIDE_LONG, SIDE_SHORT = 1, -1

#: Gates, in the order they are applied. Cheapest and most-often-fatal first,
#: so the rejection histogram is dominated by real structural absence rather
#: than by arithmetic that never had a chance to matter.
GATES = ('regime', 'swing', 'structure', 'trendline', 'retest',
         'momentum', 'htf', 'risk', 'spread', 'session', 'exposure')


@dataclass
class StrategyParams:
    # A — observation
    atr_pct_min: float = 15.0        # skip a market too dead to pay costs
    atr_pct_max: float = 90.0        # and one too wild for the stop to survive
    max_spread_atr: float = 0.08     # spread as a fraction of ATR
    sessions: tuple = ('london', 'newyork')   # empty = any
    # C — structure
    require_structure: bool = True   # bias must agree with the trade
    # D — trendline
    min_quality: float = 90.0
    retest_atr: float = 0.35         # how close counts as a retest
    # E — risk
    stop_atr: float = 1.0
    target_atr: float = 2.0
    min_rr: float = 1.5
    max_concurrent: int = 1          # position exposure


@dataclass
class Decision:
    i: int
    t: int
    side: int = 0
    take: bool = False
    blocked_by: Optional[str] = None
    reasons: List[str] = field(default_factory=list)
    entry: float = float('nan')
    stop: float = float('nan')
    target: float = float('nan')
    rr: float = float('nan')
    line_id: Optional[str] = None
    quality: float = float('nan')

    def to_row(self):
        return {'i': self.i, 't': self.t, 'side': self.side, 'take': self.take,
                'blocked_by': self.blocked_by, 'entry': self.entry,
                'stop': self.stop, 'target': self.target, 'rr': self.rr,
                'line_id': self.line_id, 'quality': self.quality}


def session_of(t_ms):
    """UTC hour -> session name. The windows SESSIONS in js/util.js uses."""
    h = (t_ms // 3600000) % 24
    if 7 <= h < 16:
        return 'london'
    if 12 <= h < 21:
        return 'newyork'
    if 0 <= h < 9:
        return 'tokyo'
    return 'sydney'


def atr_percentile(atr, i, lookback=500):
    """Where this bar's ATR sits in its own recent history."""
    lo = max(0, i - lookback)
    win = atr[lo:i + 1]
    win = win[np.isfinite(win)]
    if len(win) < 30 or not np.isfinite(atr[i]):
        return float('nan')
    return float((win <= atr[i]).mean() * 100)


def momentum_agrees(close, i, side, span=14):
    """Close vs its own mean `span` bars back. Deliberately crude: this is a
    VETO on trading against obvious momentum, not a signal in its own right."""
    if i < span:
        return False
    ref = float(np.mean(close[i - span:i]))
    return (close[i] > ref) if side > 0 else (close[i] < ref)


def evaluate(i, t, side, ctx, p: StrategyParams = None):
    """
    Run every gate for one candidate trade and return a Decision.

    `ctx` is a dict of what the lower layers already computed at this bar:
        atr, close, high, low        arrays
        line                         the Layer D line being retested (or None)
        line_value                   its price at this bar
        quality                      its score, frozen at this bar
        bias                         Layer C bias string at this bar
        swing_ok                     a CONFIRMED swing supports this side
        htf_bias                     higher-timeframe bias, or None to skip
        spread_price                 current spread in price units
        open_positions               how many are already on
    """
    p = p or StrategyParams()
    d = Decision(i=i, t=t, side=side)
    atr = ctx['atr']
    close = ctx['close']
    a = atr[i]

    def block(gate, why):
        d.blocked_by = gate
        d.reasons.append(why)
        return d

    # --- A: is the market in a state worth trading at all? ------------------ #
    if not np.isfinite(a) or a <= 0:
        return block('regime', 'no ATR')
    pct = atr_percentile(atr, i)
    if not np.isfinite(pct) or pct < p.atr_pct_min or pct > p.atr_pct_max:
        return block('regime', 'ATR percentile %.0f outside [%.0f, %.0f]'
                     % (pct, p.atr_pct_min, p.atr_pct_max))

    # --- B: a confirmed swing must support the side ------------------------- #
    if not ctx.get('swing_ok'):
        return block('swing', 'no confirmed swing supporting this side')

    # --- C: structure must agree -------------------------------------------- #
    if p.require_structure:
        want = 'up' if side > 0 else 'down'
        if ctx.get('bias') != want:
            return block('structure', 'bias %r, wanted %r' % (ctx.get('bias'), want))

    # --- D: a tradeable line of sufficient quality -------------------------- #
    line_v = ctx.get('line_value')
    q = ctx.get('quality', float('nan'))
    if ctx.get('line') is None or not np.isfinite(line_v or float('nan')):
        return block('trendline', 'no offerable line')
    d.line_id = getattr(ctx['line'], 'id', None)
    d.quality = q
    if not np.isfinite(q) or q < p.min_quality:
        return block('trendline', 'quality %.1f < %.1f' % (q, p.min_quality))

    # --- D: price must actually be RETESTING it ----------------------------- #
    dist = abs(close[i] - line_v) / a
    if dist > p.retest_atr:
        return block('retest', 'distance %.2f ATR > %.2f' % (dist, p.retest_atr))

    # --- momentum veto ------------------------------------------------------ #
    if not momentum_agrees(close, i, side):
        return block('momentum', 'momentum against the trade')

    # --- higher timeframe --------------------------------------------------- #
    htf = ctx.get('htf_bias')
    if htf is not None:
        want = 'up' if side > 0 else 'down'
        if htf != want:
            return block('htf', 'HTF bias %r, wanted %r' % (htf, want))

    # --- risk geometry ------------------------------------------------------ #
    entry = float(close[i])
    stop = entry - side * p.stop_atr * a
    target = entry + side * p.target_atr * a
    risk = abs(entry - stop)
    if risk <= 0:
        return block('risk', 'degenerate stop')
    rr = abs(target - entry) / risk
    d.entry, d.stop, d.target, d.rr = entry, stop, target, rr
    if rr < p.min_rr:
        return block('risk', 'R:R %.2f < %.2f' % (rr, p.min_rr))

    # --- cost --------------------------------------------------------------- #
    spread = ctx.get('spread_price', 0.0) or 0.0
    if spread > p.max_spread_atr * a:
        return block('spread', 'spread %.5f > %.2f ATR' % (spread, p.max_spread_atr))

    # --- session ------------------------------------------------------------ #
    if p.sessions and session_of(t) not in p.sessions:
        return block('session', 'session %s not traded' % session_of(t))

    # --- exposure ----------------------------------------------------------- #
    if ctx.get('open_positions', 0) >= p.max_concurrent:
        return block('exposure', 'already at max concurrent')

    d.take = True
    d.reasons.append('all gates passed')
    return d


# --------------------------------------------------------------------------- #
# The STRUCTURAL TRIGGER — Layer C as the thing that fires the entry.
#
# Until now Layer D fired every entry: price touches the line, enter. Measuring
# that produced net R -0.03 against a placebo of +0.17, which says the gates
# around the line were carrying the value and the line itself was a net cost of
# roughly 0.2 R. The obvious next question is whether a STRUCTURAL event fires
# better than a geometric one, with the trendline demoted to context.
#
# The event: a bullish BOS is a close through the last confirmed swing high
# while the structural bias is up -- "HL formed, then minor LH broken". That is
# a thing the market DID, not a place a line happens to be, so it cannot be
# moved by redrawing the line or by a one-tick difference in swing selection.
#
# DISPLACEMENT is optional and off by default. It requires the breaking bar to
# have travelled `displacement_atr` beyond the level, which is the difference
# between "price touched and we hoped" and "price proved the level held". It
# costs entry price to buy evidence; whether that trade is worth making is
# exactly what the measurement decides.
# --------------------------------------------------------------------------- #

TL_BUCKETS = ((0.25, '0-0.25'), (0.50, '0.25-0.50'), (1.00, '0.50-1.00'),
              (float('inf'), '>1.00'))


def tl_distance_bucket(price, line_value, atr_i):
    """
    Distance from the trendline in ATR, as a labelled bucket.

    The point of bucketing rather than gating: a binary "within 0.35 ATR" throws
    away the shape of the relationship. If expectancy falls monotonically with
    distance, the line carries real locational information even when it is a bad
    trigger. If it is flat, it does not.
    """
    if not np.isfinite(line_value) or not np.isfinite(atr_i) or atr_i <= 0:
        return None
    d = abs(price - line_value) / atr_i
    for edge, label in TL_BUCKETS:
        if d < edge:
            return label
    return '>1.00'


def structural_triggers(ms_events, high, low, close, atr, n,
                        displacement_atr=0.0):
    """
    Bar -> side, for every structural break that may fire an entry.

    `ms_events` comes from market_structure.detect, which already confirms its
    swings causally, so a trigger at bar i uses nothing after bar i.
    """
    out = {}
    for e in ms_events:
        i = e.i
        if i < 1 or i >= n:
            continue
        side = 1 if e.direction == 'bullish' else -1
        if displacement_atr > 0:
            a = atr[i]
            if not np.isfinite(a) or a <= 0:
                continue
            moved = ((close[i] - e.level) if side > 0 else (e.level - close[i]))
            if moved < displacement_atr * a:
                continue
        out[i] = side
    return out

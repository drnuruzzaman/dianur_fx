"""
targets.py — the reference R-multiples, in ONE place.

THESE ARE NOT TAKE-PROFITS. XAUUSD 4h Donchian passed its gates with no target
at all: 138 of 207 out-of-sample exits were the trailing 10-bar channel and
none was a target. tools/tp_sweep.py measured what capping costs -- a 1R cap
lifts the win rate from 35% to 49% and turns +43.7 net R into -2.1. It LOSES
MONEY. At 3R a cap keeps 82-85% of net R; only past 4R is it harmless, and by
then it fires on one trade in ten.

So the ladder STARTS AT 2R, deliberately clear of the range the measurement
condemned, and nothing that renders it may call it TP. They are levels to orient
against -- "price is about two units of risk away" -- not instructions.

WHY THIS FILE EXISTS. The same ladder was written down three times and all three
disagreed: js/chart/targets.js used 2/3.5/5, js/main.js hardcoded a single 2R
zone, and sim/signal.py used 1/2/3 -- quoting the one multiple the sweep
condemned, directly beneath a buy instruction. A reference that differs by
surface is not a reference. This is the definition; js/chart/targets.js mirrors
it and tests/test_targets_parity.py fails when they drift.
"""

#: (multiple of risk, what it is for). Centres, not ranges -- see the JS mirror
#: for why a 1.5R-wide band was the wrong shape.
REF_LADDER = ((2.0, 'first scale-out'),
              (3.5, 'the main target'),
              (5.0, 'the runner'))

#: Half the drawn band's height, in R.
BAND_HALF_R = 0.25

#: The lowest multiple that is safe to display. Anything at or below this was
#: measured to destroy the edge if acted on, so it is not offered as a level.
CONDEMNED_BELOW_R = 1.5


def label(r):
    """How a level is named on screen. NEVER 'TP'."""
    return '%gR ref' % r


def ref_levels(entry, stop, side):
    """
    [(R, price, label)] for one position, or [] when there is no R to measure.

    `side` is +1 long / -1 short. MetaTrader reports an unset stop as 0.0, which
    is finite and sits on the right side of any long entry -- a plain sign check
    accepts it and 'risk' becomes the entire price. No stop means no R.
    """
    if not side or entry is None or stop is None:
        return []
    if not (entry > 0) or not (stop > 0):
        return []
    risk = (entry - stop) * side
    if not (risk > 0):
        return []
    return [(r, entry + side * r * risk, label(r)) for r, _ in REF_LADDER]

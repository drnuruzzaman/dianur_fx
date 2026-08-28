"""
horizon.py — the channel length to use on a given timeframe.

THE EDGE IS A DURATION, NOT A BAR COUNT. This is the one structural finding the
sweeps produced, and it is the reason this file exists. Donchian N=20 was
validated on gold 4h, which is 80 hours of channel. Running "N=20" on 15m is
not the same rule at a finer resolution -- it is a 5-HOUR channel, a different
strategy that happens to share a number. Measured, out of sample:

    XAUUSD 15m  N=20   -0.0756 R     the same number, the wrong duration
    XAUUSD 15m  N=317  +0.1762 R     the same duration, a different number

and the 3.3-day horizon holds across three timeframes at three different bar
counts -- 4h N=20, 1h N=79, 15m N=317 -- all passing every gate in both eras.

So the timeframe selects N. The horizon stays fixed.

WHERE THIS APPLIES, AND WHERE IT DOES NOT. HORIZON_TFS is the intraday range
where a 3.3-day channel is a meaningful number of bars. Outside it the map is
deliberately NOT applied:

    1d   20 bars is already 20 days. A 3.3-day channel would be N=3, which is
         noise, not a channel. The daily cell keeps 20/10 and is a different
         regime, not a mis-set version of this one.
    1m   3.3 days is 4,752 bars. Arithmetically fine, never measured, and far
         outside the range the finding was established on.

`params_for_tf` reports which case a timeframe is in, so callers can say so
rather than quietly presenting an extrapolation as the validated rule.
"""

#: bars per 24h. The 24/5 market means a "day" is a trading day; these are the
#: nominal counts the sweeps used and must stay identical to them.
BARS_PER_DAY = {'1m': 1440, '5m': 288, '15m': 96, '30m': 48, '1h': 24,
                '4h': 6, '1d': 1}

#: The horizon that passed every gate in both eras on gold, at 4h, 1h and 15m.
#: 5.0 and 8.3 days also passed on some cells; 3.3 is the one that passed on
#: ALL THREE, so it is the one a live panel gets to use without an argument.
HORIZON_DAYS = 3.3

#: Where a 3.3-day channel is a sensible number of bars -- see the module note.
HORIZON_TFS = ('5m', '15m', '30m', '1h', '4h')

#: What the rule was originally validated as, and the fallback outside the
#: horizon range.
BASE = {'entry': 20, 'exit': 10, 'atr_len': 14, 'atr_mult': 2.0}


def n_for(tf, days=HORIZON_DAYS):
    """Channel length covering `days` of `tf` bars. Matches tools/horizon_sweep."""
    return max(5, round(days * BARS_PER_DAY[tf]))


def params_for_tf(tf, days=HORIZON_DAYS):
    """
    Rule parameters for this timeframe.

    `horizon_days` is None when the map does not apply, which is the signal to
    a caller that it is looking at the base 20/10 rule rather than a
    horizon-matched one.
    """
    if tf not in HORIZON_TFS:
        return dict(BASE, horizon_days=None, tf=tf)
    n = n_for(tf, days)
    # exit at N/2 keeps the validated 20/10 SHAPE across the family. Letting it
    # vary independently turns one degree of freedom into two.
    return dict(BASE, entry=n, exit=max(2, n // 2), horizon_days=days, tf=tf)


def strategy_for_tf(tf, days=HORIZON_DAYS):
    """The registered strategy name for this timeframe's horizon-matched N."""
    p = params_for_tf(tf, days)
    return 'donchian' if p['entry'] == BASE['entry'] else 'donchian_n%d' % p['entry']

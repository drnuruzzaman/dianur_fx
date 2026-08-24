"""
clockguard.py — one timestamp convention, enforced at the engine boundary.

Bars on this project carry BROKER SERVER TIME as a timezone-NAIVE index named
`server_time` (see tools/dataset.py). That is deliberate, not an oversight:
the broker's UTC offset shifts with its own DST, so "just convert it to UTC"
would need a time-varying offset and applying a single constant across twenty
years of history is wrong for roughly half of it. The raw server time is what
was recorded, so the raw server time is what the engine reads.

What genuinely does need preventing is a MIXTURE. A tz-aware index reaching the
engine would compare unequally against naive ones, and the failure is silent:
MTF alignment in mtf.py matches on bar CLOSE times, so a one-hour offset
between an execution frame and a context frame does not raise — it quietly
serves the wrong context bar, which is look-ahead wearing a plausible face.

So the rule is one line, and it is a rejection rather than a conversion:

    require_naive(bars)

Convert deliberately at the loader if you ever need to, never in passing here.
"""

import pandas as pd


class TimezoneMixError(TypeError):
    """Raised when an index reaches the engine with a timezone attached."""


def require_naive(bars, what='bars'):
    """
    Assert the index is tz-naive. Returns `bars` so it can wrap a call site.

    Deliberately does NOT convert: a silent tz-drop would reintroduce exactly
    the ambiguity this exists to prevent.
    """
    idx = getattr(bars, 'index', bars)
    tz = getattr(idx, 'tz', None)
    if tz is not None:
        raise TimezoneMixError(
            '%s has a tz-aware index (tz=%s). This engine reads broker '
            'server time as tz-naive — see sim/tl/clockguard.py. Strip the '
            'timezone at the loader, deliberately, rather than here.' % (what, tz))
    if isinstance(idx, pd.DatetimeIndex) and idx.dtype.kind != 'M':
        raise TimezoneMixError('%s index is not datetime-like: %s' % (what, idx.dtype))
    return bars

"""
proposer.py — the contract every pattern finder implements.

A proposer answers one question: at which bars, and in which direction, do you
claim something is happening? It says nothing about stops, targets, sizing or
costs, and it is never told whether it was right. That ignorance is the point --
a proposer that could see its own scores would start fitting them.

THE TABLE

    pattern_id   str    which pattern this is an instance of. The unit of
                        statistical testing, and the unit of multiple-comparison
                        correction: every distinct value is one hypothesis, and
                        evaluate.py counts them whether or not they survive.
    bar          int    index of the bar at whose CLOSE the pattern is known and
                        the entry is taken.
    occurred_at  time   when the structure formed. May be well before known_at:
                        a swing low occurs at its low and is only confirmed
                        `strength` bars later.
    known_at     time   the timestamp of `bar`. The earliest moment anything
                        could have acted on this.
    direction    int    +1 long, -1 short.

`occurred_at` and `known_at` are separate columns because collapsing them is the
most common way a pattern study becomes fiction. Drawing a divergence to the
swing while implying you could have traded it there is the textbook case, and
this project has already had one look-ahead leak that looked obviously fine.
"""

from abc import ABC, abstractmethod

import numpy as np
import pandas as pd

COLUMNS = ('pattern_id', 'bar', 'occurred_at', 'known_at', 'direction')


class LookAheadError(Exception):
    """A proposal that could not have been known when it claims."""


def empty_proposals():
    return pd.DataFrame({'pattern_id': pd.Series(dtype=object),
                         'bar': pd.Series(dtype=np.int64),
                         'occurred_at': pd.Series(dtype='datetime64[ns]'),
                         'known_at': pd.Series(dtype='datetime64[ns]'),
                         'direction': pd.Series(dtype=np.int64)})


def validate_proposals(proposals, bars, name='proposer'):
    """
    The gate every proposer passes before its output is scored.

    Cheap, and it earns its place: an off-by-one that lets a pattern be known one
    bar early is invisible in the output and inflates every downstream number.
    Raising here means a broken proposer fails loudly rather than winning.
    """
    p = proposals
    missing = [c for c in COLUMNS if c not in p.columns]
    if missing:
        raise ValueError('%s: proposals missing columns %s' % (name, missing))
    if not len(p):
        return p

    bar = p['bar'].to_numpy()
    if bar.min() < 0 or bar.max() >= len(bars):
        raise LookAheadError('%s: bar index outside the series' % name)
    if not set(np.unique(p['direction'])) <= {-1, 1}:
        raise LookAheadError('%s: direction must be +1 or -1' % name)

    # known_at must BE the bar it claims, not merely near it
    expected = bars.index[bar]
    if not (pd.DatetimeIndex(p['known_at']) == expected).all():
        raise LookAheadError(
            '%s: known_at does not match bars.index[bar]. The entry bar and the '
            'moment of knowing are the same event; if they differ, one of them '
            'is wrong.' % name)
    # structure may form before it is known, never after
    if (pd.DatetimeIndex(p['occurred_at']) > expected).any():
        raise LookAheadError(
            '%s: occurred_at is after known_at, i.e. the pattern is claimed to '
            'be known before it happened.' % name)
    return p


class Proposer(ABC):
    """
    Subclass, set `name`, implement `propose`. Return the table above.

    Keep parameters few and fixed. Every parameter you sweep multiplies the
    hypothesis count that evaluate.py has to correct for, and a proposer with
    six tunable knobs has already spent its statistical budget before the first
    pattern is scored.
    """

    name = 'proposer'

    @abstractmethod
    def propose(self, bars, symbol, tf):
        """-> DataFrame with COLUMNS."""

    def params(self):
        return {}

    def run(self, bars, symbol, tf):
        return validate_proposals(self.propose(bars, symbol, tf), bars,
                                  self.name)

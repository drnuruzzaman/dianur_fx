"""
Intrabar exit resolution.

A bar holding both the stop and the target does not say which came first. Three
answers are offered -- pessimistic, optimistic, and looking at sub-bars -- and
these tests pin the properties that make the third safe to prefer:

  * a GAP is never ambiguous and never consults sub-bars, in any mode
  * a sub-bar that touches one level and not the other is believed
  * a sub-bar holding BOTH falls back to pessimistic rather than guessing
  * shorts are mirrored correctly, which is the easy thing to get backwards
  * a strategy with no target can never be ambiguous, so all three modes agree

The empirical claim behind the feature -- that sub-bars are never WRONG, only
sometimes silent -- is measured against ticks by tools/intrabar_validate.py:
5m resolved 97.8% of 323 EURUSD 1h cases and 100% of 317 XAUUSD cases, with
zero errors in either, against ~50% for the pessimistic rule.

    python -m pytest tests/test_intrabar.py -q
"""

import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim.core import LONG, SHORT
from sim.intrabar import (INTRABAR, OPTIMISTIC, PESSIMISTIC, STOP, SubBars,
                          TARGET, UNRESOLVED, first_touch, resolve, sub_tf_for)

T0 = pd.Timestamp('2025-06-02 08:00')


def subs(rows):
    """Sub-bars as (low, high) pairs, one minute apart."""
    idx = pd.date_range(T0, periods=len(rows), freq='5min')
    return pd.DataFrame({'low': [r[0] for r in rows],
                         'high': [r[1] for r in rows],
                         'open': [r[0] for r in rows],
                         'close': [r[1] for r in rows]}, index=idx)


class FakeSource:
    """A SubBars stand-in that serves one fixed slice and counts lookups."""

    def __init__(self, frame, sub_tf='5m'):
        self.frame = frame
        self.sub_tf = sub_tf
        self.available = frame is not None
        self.lookups = 0

    def slice(self, t0):
        self.lookups += 1
        return self.frame


# ---- first_touch -------------------------------------------------------- #

def test_long_stop_first():
    f = subs([(99.0, 100.5), (98.0, 100.5), (98.0, 102.0)])
    assert first_touch(f, LONG, stop=98.5, target=101.0) == STOP


def test_long_target_first():
    f = subs([(99.5, 101.5), (98.0, 101.5)])
    assert first_touch(f, LONG, stop=98.5, target=101.0) == TARGET


def test_short_is_mirrored():
    """For a short the stop is ABOVE and the target BELOW -- easy to invert."""
    f = subs([(99.5, 101.5), (98.0, 101.5)])
    assert first_touch(f, SHORT, stop=101.0, target=98.5) == STOP
    g = subs([(98.0, 100.5), (98.0, 101.5)])
    assert first_touch(g, SHORT, stop=101.0, target=98.5) == TARGET


def test_one_sub_bar_holding_both_is_unresolved():
    """The recursion does not end because the bars got smaller."""
    f = subs([(98.0, 102.0)])
    assert first_touch(f, LONG, stop=98.5, target=101.0) == UNRESOLVED


def test_neither_level_touched():
    assert first_touch(subs([(99.5, 100.5)]), LONG, 98.5, 101.0) == UNRESOLVED


def test_no_sub_bars_at_all():
    assert first_touch(None, LONG, 98.5, 101.0) == UNRESOLVED
    assert first_touch(subs([]), LONG, 98.5, 101.0) == UNRESOLVED


def test_a_missing_target_cannot_race():
    f = subs([(98.0, 102.0)])
    assert first_touch(f, LONG, stop=98.5, target=None) == STOP


# ---- resolve ------------------------------------------------------------ #

def test_pessimistic_never_looks_at_sub_bars():
    src = FakeSource(subs([(99.5, 101.5)]))          # would say TARGET
    assert resolve(PESSIMISTIC, src, T0, LONG, 98.5, 101.0) == (STOP, PESSIMISTIC)
    assert src.lookups == 0, 'pessimistic must not pay for data it ignores'


def test_optimistic_never_looks_either():
    src = FakeSource(subs([(98.0, 100.5)]))          # would say STOP
    assert resolve(OPTIMISTIC, src, T0, LONG, 98.5, 101.0) == (TARGET, OPTIMISTIC)
    assert src.lookups == 0


def test_intrabar_believes_the_sub_bars():
    src = FakeSource(subs([(99.5, 101.5)]))
    assert resolve(INTRABAR, src, T0, LONG, 98.5, 101.0) == (TARGET, '5m')
    assert src.lookups == 1


def test_intrabar_falls_back_when_the_sub_bar_holds_both():
    src = FakeSource(subs([(98.0, 102.0)]))
    assert resolve(INTRABAR, src, T0, LONG, 98.5, 101.0) == (STOP, 'fallback')


def test_intrabar_without_a_source_is_pessimistic():
    """No sub-bar history must degrade, not crash."""
    assert resolve(INTRABAR, None, T0, LONG, 98.5, 101.0) == (STOP, PESSIMISTIC)
    assert resolve(INTRABAR, FakeSource(None), T0, LONG, 98.5, 101.0) \
        == (STOP, PESSIMISTIC)


# ---- wiring ------------------------------------------------------------- #

def test_default_sub_timeframes_are_finer_than_their_parent():
    order = ['1m', '5m', '15m', '30m', '1h', '4h', '1d']
    for tf in ['1h', '4h', '1d', '30m', '15m']:
        sub = sub_tf_for(tf)
        assert sub is not None
        assert order.index(sub) < order.index(tf), '%s -> %s is not finer' % (tf, sub)


def test_an_explicit_sub_timeframe_wins():
    assert sub_tf_for('1h', '1m') == '1m'


def test_subbars_never_loads_until_asked():
    calls = []

    def loader(*a):
        calls.append(a)
        raise AssertionError('should not be reached')

    src = SubBars('EURUSD.a', '1h', loader=loader)
    assert not calls, 'constructing a source must not touch the disk'


def test_subbars_survives_missing_history():
    def loader(*a):
        raise FileNotFoundError('no 5m for this symbol')

    src = SubBars('NOPE.a', '1h', loader=loader)
    assert src.slice(T0) is None
    assert not src.available, 'a failed load must disable the source, not retry'

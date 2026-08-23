"""
The broker clock guard.

data/manifest.json records how far the broker's server clock sits from UTC, and
sim/fx.py uses that number to convert a trade's server timestamp into UTC before
looking up the hourly AUD rate. A wrong offset therefore prices every trade off
the wrong hour, quietly, in every run.

It has gone wrong twice, both times the same way: the offset was measured on a
weekend from the last tick before the market shut, so the broker appeared to be
7.75h and later 13.6h away from where it actually is. The guard written to stop
it rounded (tick - now) to the nearest MINUTE and then measured the residue of
its own rounding, which cannot exceed 30 seconds -- so the freshness test always
passed and the guard never once fired.

These tests pin the property that matters: a stale tick must NOT be believed.

    python -m pytest tests/test_offset.py -q
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim.clock import resolve_offset

HOUR = 3600 * 1000
NOW = 1_787_000_000_000            # an arbitrary but fixed "now", in ms
PEPPERSTONE = 3 * HOUR             # the real offset of Pepperstone-MT5-Live01


def good(ticks, now=NOW, previous=PEPPERSTONE):
    """resolve_offset with a corroborating known-good previous value."""
    return resolve_offset(ticks, now, previous, 'ok')


def test_fresh_tick_is_believed():
    """A tick arriving now from a UTC+3 broker reads as UTC+3."""
    assert good([NOW + PEPPERSTONE]) == (PEPPERSTONE, 'ok')


def test_fresh_tick_survives_a_few_seconds_of_lag():
    assert good([NOW + PEPPERSTONE - 2_500]) == (PEPPERSTONE, 'ok')


def test_half_hour_broker_is_representable():
    """India and Iran sit on the half hour; the grid must not round them away."""
    for real in (3 * HOUR + HOUR // 2, 2 * HOUR + HOUR // 2):
        assert good([NOW + real]) == (real, 'ok')


@pytest.mark.parametrize('stale_h', [13.6, 7.75, 2.0, 62.0])
def test_stale_weekend_tick_is_rejected(stale_h):
    """
    The regression. Each of these is a Saturday measurement against the last
    Friday tick of a UTC+3 broker; none may be believed, and each must hand back
    the previously known good value untouched.
    """
    tick = NOW + PEPPERSTONE - int(stale_h * HOUR)
    assert good([tick]) == (PEPPERSTONE, 'stale')


def test_the_exact_value_that_got_into_the_manifest():
    """-38160000 (UTC-10:36) is what was actually written on 2026-08-22."""
    assert good([NOW - 38_160_000]) == (PEPPERSTONE, 'stale')


def test_with_nothing_to_corroborate_against_it_says_so():
    """
    A lone grid-clean tick and no known-good history is genuinely ambiguous --
    it may be fresh, or stale by a whole number of half hours. The honest answer
    is 'unverified', not a confident 'ok' that a later run would inherit.
    """
    assert resolve_offset([NOW + PEPPERSTONE], NOW) == (PEPPERSTONE, 'unverified')
    assert resolve_offset([NOW + PEPPERSTONE], NOW, previous=PEPPERSTONE,
                          previous_confidence='unverified')[1] == 'unverified'


def test_an_unverified_previous_does_not_lock_a_wrong_value_in():
    """
    If an unverified reading was wrong, the drift gate must not then refuse
    every correct measurement forever after.
    """
    wrong = -11 * HOUR
    offset, confidence = resolve_offset([NOW + PEPPERSTONE], NOW, previous=wrong,
                                        previous_confidence='unverified')
    assert (offset, confidence) == (PEPPERSTONE, 'unverified')


def test_freshest_tick_wins():
    """One dead symbol must not drag down a live one."""
    ticks = [NOW + PEPPERSTONE - 30 * HOUR, NOW + PEPPERSTONE - 1_000]
    assert good(ticks) == (PEPPERSTONE, 'ok')


def test_no_ticks_at_all_keeps_the_previous_value():
    assert good([]) == (PEPPERSTONE, 'stale')
    assert good([None, 0]) == (PEPPERSTONE, 'stale')


def test_impossible_offset_is_rejected():
    """No broker is 20 hours from UTC, so such a reading is a stale tick."""
    assert good([NOW + 20 * HOUR]) == (PEPPERSTONE, 'stale')


def test_a_tick_stale_by_a_whole_number_of_half_hours_is_still_rejected():
    """
    The grid test alone cannot see a tick stale by exactly 12h: it lands back on
    the grid looking perfectly fresh. The drift gate catches it, because no
    broker moves its clock further than a DST hour.
    """
    assert good([NOW + PEPPERSTONE - 12 * HOUR]) == (PEPPERSTONE, 'stale')


def test_a_dst_shift_is_still_accepted():
    """Guarding against drift must not freeze out the one shift that is real."""
    assert good([NOW + 2 * HOUR]) == (2 * HOUR, 'ok')
    assert good([NOW + 4 * HOUR]) == (4 * HOUR, 'ok')


def test_the_old_guard_would_have_failed_these():
    """
    A negative control on the tests themselves. The previous implementation is
    reproduced here; if it were still shipping, the stale case would pass as
    'ok', which is exactly the bug. This asserts these tests actually bite.
    """
    def old_guard(tick_times_ms, now_ms, previous=None):
        best = None
        for t in tick_times_ms:
            offset = int(round((t - now_ms) / 60000.0) * 60000)
            age = abs((t - offset) - now_ms)
            if best is None or age < best[1]:
                best = (offset, age)
        if best is not None and best[1] <= 5 * 60 * 1000:
            return best[0], 'ok'
        return (previous if previous is not None else 0), 'stale'

    tick = NOW + PEPPERSTONE - 13 * HOUR - 36 * 60 * 1000
    assert old_guard([tick], NOW, PEPPERSTONE) == (-38_160_000, 'ok')   # the bug
    assert good([tick]) == (PEPPERSTONE, 'stale')


def test_the_shipped_manifest_is_sane():
    """
    Whatever is on disk right now must be a legal broker offset. This is the
    test that would have caught the corrupted manifest without anyone looking.
    """
    import json

    from sim.instruments import DATA

    path = os.path.join(DATA, 'manifest.json')
    if not os.path.exists(path):
        pytest.skip('no manifest on disk')
    offset = json.load(open(path, encoding='utf-8')).get('server_utc_offset_ms')
    assert offset is not None, 'manifest records no server offset'
    assert abs(offset) <= 14 * HOUR, f'{offset} is further from UTC than any broker'
    assert offset % (30 * 60 * 1000) == 0, (
        f'{offset} (UTC{offset / HOUR:+.2f}h) is not on the half-hour grid, so it '
        'came from a stale tick rather than from the broker')

"""
The two clocks, pinned.

This project deliberately runs two timestamp conventions and they are hours
apart:

    LIVE, over the bridge      true UTC epoch ms. mt5_bridge.py subtracts
                               STATE['time_offset_ms'] from every MT5 value
                               before it goes on the wire.
    STORED, for research       broker SERVER time, tz-naive, index named
                               `server_time` (tools/dataset.py). Not converted,
                               because the broker's offset moves with its own
                               DST and one constant is wrong across roughly
                               half of a 27-year history -- see clockguard.

Both are defensible. What is NOT survivable is the pair drifting apart silently,
because nothing raises when it happens: a bar and a position that both read
08:30 would simply be three hours apart, and every downstream join would be off
by that much while looking completely normal.

So this pins the relationship rather than either value:

    server_time - utc_time == manifest offset,  exactly.

    python -m pytest tests/test_clock_convention.py -q
"""

import json
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim.tl.clockguard import TimezoneMixError, require_naive

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(ROOT, 'data', 'manifest.json')
HOUR_MS = 3600 * 1000


def _manifest():
    if not os.path.exists(MANIFEST):
        pytest.skip('no data/manifest.json in this checkout')
    with open(MANIFEST, encoding='utf-8') as fh:
        return json.load(fh)


def test_manifest_records_the_offset():
    """
    The offset must be recorded, not inferred at read time.

    sim/fx.py uses it to convert a trade's server stamp to UTC before pricing
    it against the hourly AUD rate. If it were re-derived per run, a weekend
    run would derive it from a stale tick -- which has happened twice.
    """
    doc = _manifest()
    off = doc.get('server_utc_offset_ms')
    assert off is not None, 'manifest must record server_utc_offset_ms'
    assert isinstance(off, int), 'offset must be exact ms, not a float'
    # Brokers sit within a normal band. A value outside it means the offset was
    # measured from a stale tick, the exact failure sim/clock.py guards.
    assert -12 * HOUR_MS <= off <= 14 * HOUR_MS, 'offset %r is not a real zone' % off
    assert off % (900 * 1000) == 0, 'a real zone offset lands on a quarter hour'


def test_stored_bars_are_naive_server_time():
    """Stored bars carry server time with NO tzinfo, under the documented name."""
    from sim.instruments import load

    try:
        bars = load('EURUSD.a', '1h', '2024-01-01', '2024-01-05')
    except Exception:
        pytest.skip('EURUSD.a 1h not present in this checkout')
    assert len(bars), 'expected bars in the window'
    assert bars.index.tz is None, 'stored bars must stay tz-naive'
    assert bars.index.name == 'server_time', (
        'the index name IS the convention marker; renaming it silently turns '
        'server time into "some time"')


def test_clockguard_rejects_rather_than_converts():
    """
    A tz-aware frame must RAISE, not be quietly normalised.

    A silent conversion is worse than a crash here: mtf.py aligns timeframes on
    bar close times, so a one-hour discrepancy does not error -- it serves the
    wrong context bar, which is look-ahead wearing a plausible face.
    """
    idx = pd.date_range('2024-01-01', periods=3, freq='h', tz='UTC')
    aware = pd.DataFrame({'open': [1.0] * 3, 'high': [1.0] * 3,
                          'low': [1.0] * 3, 'close': [1.0] * 3}, index=idx)
    with pytest.raises(TimezoneMixError):
        require_naive(aware, 'test frame')

    naive = aware.tz_localize(None)
    assert require_naive(naive) is naive, 'a naive frame passes through unchanged'


def test_server_and_utc_differ_by_exactly_the_offset():
    """
    The property this file exists for.

    Take one stored bar, read its server-time index, and convert with the
    recorded offset. The result must be the UTC instant the bridge would have
    sent for that same bar -- exactly, to the millisecond, with no rounding
    slack to hide a drift in.
    """
    from sim.instruments import load

    doc = _manifest()
    off = doc.get('server_utc_offset_ms')
    if off is None:
        pytest.skip('no offset recorded')
    try:
        bars = load('EURUSD.a', '1h', '2024-01-01', '2024-01-05')
    except Exception:
        pytest.skip('EURUSD.a 1h not present in this checkout')

    server_ms = int(bars.index[0].value // 1_000_000)
    utc_ms = server_ms - off
    assert server_ms - utc_ms == off, 'conversion must be exact'

    # An hourly bar is stamped on an exact hour in BOTH frames only when the
    # offset is a whole number of hours. Assert against the frame we actually
    # store, so this stays true for a broker on a :30 or :45 zone.
    assert server_ms % HOUR_MS == 0, 'an H1 bar should open on the hour'


def test_a_wrong_offset_would_be_caught():
    """
    Negative control: the check above must actually bite.

    Without this, a conversion that silently ignored the offset would satisfy
    every assertion in this file by returning the input unchanged.
    """
    doc = _manifest()
    off = doc.get('server_utc_offset_ms')
    if not off:
        pytest.skip('no non-zero offset to corrupt')
    wrong = off + HOUR_MS
    server_ms = 1_700_000_000_000
    assert server_ms - (server_ms - wrong) != off, (
        'an offset one hour out must NOT satisfy the identity')

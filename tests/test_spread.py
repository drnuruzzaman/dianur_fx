"""
The spread floor.

MetaTrader's per-bar spread column cannot be relied on to be populated. On this
feed EURUSD and USDJPY carry a spread through 2019 and then report 0 for
essentially every bar from 2020 onward; XAUUSD is the mirror, 0 before 2018 and
populated after. The simulator used to pass that straight through with a default
of 0, which meant entire eras were simulated with NO SPREAD -- including the
2021-2026 window that every early result was measured in.

The fix treats the recorded value as a lower bound and floors it at the broker's
own live spread for the instrument, captured into data/instruments.json. These
tests pin that: a silent history must never cost nothing.

    python -m pytest tests/test_spread.py -q
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim.core import Config, Simulator

SPEC = {
    'digits': 5, 'point': 1e-05, 'contract_size': 100000.0, 'tick_size': 1e-05,
    'tick_value': 1.0, 'volume_min': 0.01, 'volume_step': 0.01, 'volume_max': 100.0,
    'currency_base': 'EUR', 'currency_profit': 'USD', 'currency_margin': 'EUR',
    'swap_mode': 1, 'swap_long_points': 0.0, 'swap_short_points': 0.0,
    'swap_rollover_3days_weekday': 3, 'spread_points_now': 12,
    '_symbol': 'EURUSDLIKE', '_tf': '4h',
}


def sim(spec=None, **cfg):
    return Simulator(dict(spec or SPEC), fx=None, config=Config(**cfg))


def test_a_recorded_spread_is_charged():
    assert sim()._spread_price(30) == pytest.approx(30 * 1e-05)


def test_a_silent_bar_falls_back_to_the_brokers_live_spread():
    """The regression: 0 in the file must not mean 0 cost."""
    for recorded in (0, 0.0, None):
        assert sim()._spread_price(recorded) == pytest.approx(12 * 1e-05)


def test_the_floor_never_makes_a_real_spread_cheaper():
    """A genuinely wide historical spread must survive the floor untouched."""
    assert sim()._spread_price(50) == pytest.approx(50 * 1e-05)


def test_a_recorded_spread_tighter_than_the_broker_book_is_raised():
    """
    1-point medians appear in 2013 and 2018 on this feed -- a tenth of a pip as
    a whole bar's average, which no retail account ever traded. Floored.
    """
    assert sim()._spread_price(1) == pytest.approx(12 * 1e-05)


def test_an_explicit_config_default_still_wins():
    """Sensitivity runs need to be able to set the floor by hand."""
    s = sim(spread_points_default=40)
    assert s._spread_price(0) == pytest.approx(40 * 1e-05)
    assert s._spread_price(55) == pytest.approx(55 * 1e-05)


def test_a_spec_without_a_live_spread_charges_nothing_extra():
    """Synthetic specs in other tests have no spread_points_now; unchanged."""
    bare = {k: v for k, v in SPEC.items() if k != 'spread_points_now'}
    assert sim(bare)._spread_price(0) == 0.0


def test_the_shipped_specs_all_carry_a_live_spread():
    """
    Without this field the floor silently reverts to zero, which is the bug.
    Any instrument added later must bring its spread with it.
    """
    import json

    from sim.instruments import DATA

    path = os.path.join(DATA, 'instruments.json')
    if not os.path.exists(path):
        pytest.skip('no instruments.json')
    doc = json.load(open(path, encoding='utf-8'))['instruments']
    for name, s in doc.items():
        assert s.get('spread_points_now'), '%s has no spread_points_now' % name
        assert s['spread_points_now'] > 0, '%s records a zero spread' % name

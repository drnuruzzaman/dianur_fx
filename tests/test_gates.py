"""
The four gates, and what each one exists to reject.

Each was added because the ones before it let something through. Keeping that
history in tests rather than only in a docstring means a future simplification
that removes a gate fails here with the reason attached.

  gate_sample       too few trades to judge. Rejects 1d ema_cross at +1.42 R on
                    EIGHT trades, and Donchian N=200 at +0.81 R on 92.
  gate_beats_control the timing is no better than chance.
  gate_profitable   beats its control and still loses money. Rejects USDJPY 15m
                    ema_cross: percentile 100.0, PF 0.985, avg R -0.003.
  gate_effect       makes money, too little of it to matter or to survive the
                    cost model being wrong. Rejects XAUUSD 15m ema_cross at
                    +0.011 R over 2,690 trades.

    python -m pytest tests/test_gates.py -q
"""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim.robust import MIN_EFFECT_R, MIN_TRADES, gates


#: 1R in account currency. Sizing is constant-risk, so net_acct is
#: proportional to r_multiple -- and it matters that both are present:
#: gates() reads avg_R from r_multiple but computes PROFIT FACTOR from
#: net_acct, so a frame with only one of them silently measures half the gate.
R_IN_ACCT = 125.0


def trades(rs):
    """A trade frame carrying what the gates read: R and account P&L."""
    r = np.asarray(rs, dtype=float)
    return pd.DataFrame({'r_multiple': r, 'net_acct': r * R_IN_ACCT})


def with_mean(n, mean, spread=1.0):
    """n trades whose mean r_multiple is exactly `mean`."""
    rng = np.random.default_rng(5)
    r = rng.normal(0, spread, n)
    return trades(r - r.mean() + mean)


def control_at(mean, n=60):
    return pd.DataFrame({'avg_R': np.linspace(mean - 0.05, mean + 0.05, n)})


# ---- gate_sample --------------------------------------------------------- #

def test_sample_gate_rejects_a_flattering_handful():
    """1d ema_cross: +1.42 R on eight trades, the best number in that table."""
    g = gates(trades([1.4] * 6 + [-1.0, 2.0]), pd.DataFrame())
    assert g['trades'] == 8
    assert g['gate_sample'] is False
    assert g['gate_effect'] is True, 'the effect IS large; the sample is not'


def test_sample_gate_accepts_at_the_floor():
    g = gates(with_mean(MIN_TRADES, 0.2), pd.DataFrame())
    assert g['gate_sample'] is True
    g = gates(with_mean(MIN_TRADES - 1, 0.2), pd.DataFrame())
    assert g['gate_sample'] is False


# ---- gate_profitable ----------------------------------------------------- #

def test_profitable_gate_rejects_beating_the_control_while_losing():
    """
    The regression that put this gate in. USDJPY 15m ema_cross out of sample:
    percentile 100.0 against its control because randomly timed entries bled on
    cost, PF 0.985, avg R -0.003. Better than chance and still a loss.
    """
    t = with_mean(3970, -0.003)
    g = gates(t, control_at(-0.1064))
    assert g['percentile_vs_control'] == 100.0
    assert g['gate_beats_control'] is True
    assert g['gate_profitable'] is False


# ---- gate_effect --------------------------------------------------------- #

def test_effect_gate_rejects_a_trivial_edge():
    """XAUUSD 15m ema_cross: every other gate passed at +0.011 R."""
    # the control must sit clearly below +0.011 or the percentile gate is
    # what fails and the test stops being about effect size
    g = gates(with_mean(2690, 0.011), control_at(-0.08))
    assert g['gate_sample'] is True
    assert g['gate_profitable'] is True
    assert g['gate_beats_control'] is True
    assert g['gate_effect'] is False, (
        '+0.011 R is smaller than the uncertainty in the spread substitution')


def test_effect_gate_accepts_the_one_surviving_cell():
    """XAUUSD 4h donchian at +0.205 R must not be caught by this."""
    g = gates(with_mean(441, 0.205), control_at(-0.02))
    assert all(g[k] for k in ('gate_sample', 'gate_profitable', 'gate_effect',
                              'gate_beats_control'))


@pytest.mark.parametrize('mean,expected', [
    (0.0499, False), (MIN_EFFECT_R, True), (0.0501, True), (0.20, True),
    (0.0, False), (-0.10, False),
])
def test_effect_gate_boundary(mean, expected):
    g = gates(with_mean(500, mean), pd.DataFrame())
    assert g['gate_effect'] is expected, 'avg_R %.4f' % g['avg_R']


def test_the_threshold_is_reported_so_a_result_is_reproducible():
    """
    A pass/fail whose threshold is not recorded cannot be re-checked after the
    threshold changes. Every gates() call carries the number it used.
    """
    g = gates(with_mean(300, 0.1), pd.DataFrame(), min_effect=0.15)
    assert g['min_effect_R'] == 0.15
    assert g['gate_effect'] is False
    assert gates(with_mean(300, 0.1), pd.DataFrame())['min_effect_R'] == MIN_EFFECT_R


# ---- the gates are independent ------------------------------------------ #

def test_no_gate_is_implied_by_another():
    """
    If one gate implied another the extra one would be decoration. Each of these
    fails exactly one gate while passing the rest.
    """
    thin = gates(trades([0.3] * 20), pd.DataFrame())
    assert thin['gate_sample'] is False and thin['gate_effect'] is True

    trivial = gates(with_mean(2690, 0.011), control_at(-0.08))
    assert trivial['gate_sample'] is True and trivial['gate_effect'] is False

    losing = gates(with_mean(500, -0.003), control_at(-0.10))
    assert losing['gate_beats_control'] is True
    assert losing['gate_profitable'] is False

    chance = gates(with_mean(500, 0.20), control_at(0.25))
    assert chance['gate_profitable'] is True and chance['gate_effect'] is True
    assert chance['gate_beats_control'] is False


def test_gates_are_never_collapsed_into_a_score():
    """
    sim/robust.py says it: binary gates with the numbers behind them, never a
    score. A composite lets a weak sample be offset by a flattering profit
    factor, which is how a failing cell gets promoted.
    """
    g = gates(with_mean(500, 0.2), control_at(-0.02))
    assert not any(k.endswith('_score') for k in g), sorted(g)
    for k in ('gate_sample', 'gate_profitable', 'gate_effect', 'gate_beats_control'):
        assert isinstance(g[k], bool), '%s is %r' % (k, type(g[k]))

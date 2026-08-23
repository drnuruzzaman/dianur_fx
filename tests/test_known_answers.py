"""
Gate 1 — known answers.

Synthetic series whose correct result can be worked out by hand. These catch the
boring, catastrophic bugs: sign errors, double-counted costs, off-by-one bar
indexing, and fills that quietly happen on the signal bar.

    python -m pytest tests/test_known_answers.py -q
"""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim.core import FLAT, LONG, SHORT, Config, Intent, LookAheadError, Simulator, Strategy

# A deliberately simple instrument: 1 unit contract, 1.0 point, so price
# differences ARE money and every expected number is checkable by hand.
UNIT_SPEC = {
    'digits': 2, 'point': 1.0, 'contract_size': 1.0, 'tick_size': 1.0,
    'tick_value': 1.0, 'volume_min': 1.0, 'volume_step': 1.0, 'volume_max': 100.0,
    'currency_base': 'X', 'currency_profit': 'AUD', 'currency_margin': 'AUD',
    'swap_mode': 0, 'swap_long_points': 0.0, 'swap_short_points': 0.0,
    'swap_rollover_3days_weekday': 3, '_symbol': 'TEST', '_tf': '1d',
}

FREE = Config(risk_pct=100.0, start_equity=1000.0, slippage_points=0.0,
              spread_points_default=0.0, apply_swap=False)


def bars(prices, highs=None, lows=None, opens=None, spread=0.0):
    idx = pd.date_range('2020-01-01', periods=len(prices), freq='1D')
    c = np.asarray(prices, float)
    return pd.DataFrame({
        'open': c if opens is None else np.asarray(opens, float),
        'high': c if highs is None else np.asarray(highs, float),
        'low': c if lows is None else np.asarray(lows, float),
        'close': c,
        'tick_volume': np.ones(len(c)),
        'spread': np.full(len(c), spread, float),
    }, index=idx)


class EnterOnce(Strategy):
    """Enter on a given bar with a fixed stop/target, then never act again."""
    name = 'enter_once'

    def __init__(self, at=2, side=LONG, stop=None, target=None, exit_at=None):
        self.at, self.side, self.stop, self.target, self.exit_at = at, side, stop, target, exit_at

    def on_bar(self, view, position):
        if view.i == self.at and position is None:
            return Intent(self.side, stop=self.stop, target=self.target)
        if self.exit_at is not None and view.i == self.exit_at and position is not None:
            return Intent(FLAT)
        return None


def run(strategy, df, spec=None, cfg=None):
    sim = Simulator(spec or UNIT_SPEC, fx=None, config=cfg or FREE)
    return sim.run(df, strategy, 'TEST', '1d')


# --------------------------------------------------------------------------- #
def test_straight_line_long_returns_exact_difference():
    """A long held through a rising line earns exactly the price difference."""
    df = bars(range(100, 111))                     # 100..110, 11 bars
    r = run(EnterOnce(at=2, stop=50.0), df)
    assert len(r.trades) == 1
    t = r.trades.iloc[0]
    # signalled on bar 2 (=102), so filled at bar 3's OPEN (=103); held to the end (110)
    assert t['entry_price'] == pytest.approx(103.0)
    assert t['exit_price'] == pytest.approx(110.0)
    assert t['exit_reason'] == 'end_of_data'
    assert t['net_acct'] == pytest.approx(7.0 * t['lots'])


def test_flat_line_returns_exactly_minus_costs():
    """No move, so the entire result is the spread. One spread per round trip."""
    df = bars([100.0] * 10, spread=2.0)            # 2 points = 2.0 in price
    cfg = Config(risk_pct=100.0, start_equity=1000.0, slippage_points=0.0,
                 spread_points_default=0.0, apply_swap=False)
    r = run(EnterOnce(at=2, stop=90.0), df, cfg=cfg)
    t = r.trades.iloc[0]
    # long pays the ask (100+2), exits at the bid (100): loss is exactly the spread
    assert t['entry_price'] == pytest.approx(102.0)
    assert t['net_acct'] == pytest.approx(-2.0 * t['lots'])


def test_fill_is_next_bar_open_never_the_signal_bar():
    df = bars([100, 101, 102, 103, 104], opens=[100, 101, 102, 150, 104])
    r = run(EnterOnce(at=2, stop=50.0), df)
    t = r.trades.iloc[0]
    assert t['entry_price'] == pytest.approx(150.0), 'must fill at bar 3 open, not bar 2 close'
    assert pd.Timestamp(t['entry_time']) == df.index[3]


def test_stop_wins_when_one_bar_holds_both_stop_and_target():
    """The pessimistic rule. An OHLC bar cannot say which came first."""
    df = bars([100, 100, 100, 100, 100],
              highs=[100, 100, 100, 120, 100],       # target 110 inside
              lows=[100, 100, 100, 80, 100])         # stop 90 also inside
    r = run(EnterOnce(at=2, stop=90.0, target=110.0), df)
    t = r.trades.iloc[0]
    assert t['exit_reason'] == 'stop'
    assert t['exit_price'] == pytest.approx(90.0)
    assert t['net_acct'] < 0


def test_gap_through_stop_fills_at_the_open_not_the_stop():
    """A gap costs more than the stop said it would, and must be recorded so."""
    df = bars([100, 100, 100, 70, 70],
              opens=[100, 100, 100, 70, 70],
              highs=[100, 100, 100, 72, 72],
              lows=[100, 100, 100, 68, 68])
    r = run(EnterOnce(at=1, stop=90.0), df)
    t = r.trades.iloc[0]
    assert t['exit_reason'] == 'stop_gap'
    assert t['exit_price'] == pytest.approx(70.0), 'gap must fill at the open'
    assert t['net_acct'] < -20 * t['lots'] + 1e-9   # worse than the planned stop


def test_short_is_the_mirror_image():
    df = bars(range(110, 99, -1))                  # 110 down to 100
    r = run(EnterOnce(at=2, side=SHORT, stop=200.0), df)
    t = r.trades.iloc[0]
    assert t['entry_price'] == pytest.approx(107.0)
    assert t['exit_price'] == pytest.approx(100.0)
    assert t['net_acct'] == pytest.approx(7.0 * t['lots'])


def test_position_size_follows_the_risk_fraction():
    """1% of 10,000 = 100 risked over a 5.0 stop distance = 20 lots."""
    df = bars([100.0] * 8)
    cfg = Config(risk_pct=1.0, start_equity=10_000.0, slippage_points=0.0,
                 spread_points_default=0.0, apply_swap=False)
    r = run(EnterOnce(at=2, stop=95.0), df, cfg=cfg)
    assert r.trades.iloc[0]['lots'] == pytest.approx(20.0)


def test_trade_too_small_to_place_is_not_placed():
    """Rounding down must produce no trade, never a fractional pretend one."""
    df = bars([100.0] * 8)
    spec = dict(UNIT_SPEC, volume_min=10.0, volume_step=10.0)
    cfg = Config(risk_pct=0.01, start_equity=1000.0, slippage_points=0.0,
                 spread_points_default=0.0, apply_swap=False)
    r = run(EnterOnce(at=2, stop=95.0), df, spec=spec, cfg=cfg)
    assert len(r.trades) == 0
    assert r.stats['skipped_signals'] >= 1


def test_swap_is_charged_per_night_with_a_triple_day():
    """Points-based swap, tripled on the broker's rollover weekday."""
    df = bars([100.0] * 6)                          # 2020-01-01 is a Wednesday
    spec = dict(UNIT_SPEC, swap_mode=1, swap_long_points=-1.0,
                swap_rollover_3days_weekday=3)      # 3 = Wednesday in MT5's enum
    cfg = Config(risk_pct=100.0, start_equity=1000.0, slippage_points=0.0,
                 spread_points_default=0.0, apply_swap=True)
    r = run(EnterOnce(at=0, stop=50.0), df, spec=spec, cfg=cfg)
    t = r.trades.iloc[0]
    # entered bar 1 (Thu), marked Fri/Sat/Sun/Mon => 4 rollovers, none on a Wednesday
    assert t['swap_ccy'] == pytest.approx(-4.0 * t['lots'])


def test_the_view_refuses_to_read_the_future():
    class Peeker(Strategy):
        name = 'peeker'
        def on_bar(self, view, position):
            view.close(-1)                          # tomorrow
            return None

    with pytest.raises(LookAheadError):
        run(Peeker(), bars(range(100, 110)))


def test_unsorted_bars_are_rejected():
    df = bars(range(100, 105))
    with pytest.raises(ValueError):
        run(EnterOnce(), df.iloc[::-1])


def test_a_stop_slips_and_a_target_does_not():
    """
    A stop is a market order triggered in a fast market, so it fills WORSE than
    its trigger. A target is a limit: it fills at your price or not at all.
    Modelling both as zero — which the engine used to do — flatters every stop.
    """
    cfg = Config(risk_pct=100.0, start_equity=1000.0, slippage_atr=0.0,
                 slippage_points=1.0, stop_slippage_mult=1.5,
                 spread_points_default=0.0, apply_swap=False)

    # stopped out: fill is 1.0 x 1.5 = 1.5 below the stop
    df = bars([100, 100, 100, 100, 100],
              highs=[100, 100, 100, 100, 100],
              lows=[100, 100, 100, 80, 100])
    r = run(EnterOnce(at=1, stop=90.0), df, cfg=cfg)
    t = r.trades.iloc[0]
    assert t['exit_reason'] == 'stop'
    assert t['exit_price'] == pytest.approx(88.5), 'a stop must slip past its trigger'

    # target hit: fill is exactly the target, no slippage
    df = bars([100, 100, 100, 100, 100],
              highs=[100, 100, 100, 120, 100],
              lows=[100, 100, 100, 100, 100])
    r = run(EnterOnce(at=1, stop=50.0, target=110.0), df, cfg=cfg)
    t = r.trades.iloc[0]
    assert t['exit_reason'] == 'target'
    assert t['exit_price'] == pytest.approx(110.0), 'a limit fills at its price'


def test_slippage_is_atr_relative_when_configured():
    """
    The same setting must cost the same in ATR terms on any instrument, which a
    fixed point count does not: 2 points was 1.37% of ATR on gold and 3.86% on
    USDJPY, biasing every cross-instrument comparison.
    """
    df = bars([100.0] * 40)
    df['high'] = df['close'] + 2.0        # ATR ~ 4.0 in these units
    df['low'] = df['close'] - 2.0
    cfg = Config(risk_pct=100.0, start_equity=1000.0, slippage_atr=0.25,
                 slippage_points=99.0, spread_points_default=0.0, apply_swap=False)
    r = run(EnterOnce(at=20, stop=50.0), df, cfg=cfg)
    t = r.trades.iloc[0]
    # entry = open + 0.25 x ATR(=4.0) = 100 + 1.0, and NOT the 99-point fallback
    assert t['entry_price'] == pytest.approx(101.0, abs=0.05), \
        'slippage_atr must take precedence over slippage_points'

"""
test_signal.py — the live signal must say exactly what the backtest traded.

The point of these tests is not that evaluate() runs. It is that a signal shown
to a person about to place a real order agrees with the simulator that measured
the edge. Two implementations of the same rule that drift by a few points is
how a validated backtest turns into an unvalidated trade, and this project has
already had that happen with ATR.

The load-bearing test is test_signal_matches_simulator_entries: it replays real
gold bars, and for every entry the Simulator took it re-asks evaluate() on the
same bar and requires the same side and the same stop.
"""
import numpy as np
import pandas as pd
import pytest

from sim.core import Config, Simulator, size_lots
from sim.instruments import load, spec
from sim.signal import REF_R, evaluate
from sim.strategies.donchian import Donchian

SYMBOL, TF = 'XAUUSD.a', '4h'


def frame(rows):
    """rows = [(open, high, low, close)] on an hourly index."""
    idx = pd.date_range('2024-01-01', periods=len(rows), freq='4h')
    df = pd.DataFrame(rows, columns=['open', 'high', 'low', 'close'], index=idx)
    df.index.name = 'server_time'
    return df


@pytest.fixture(scope='module')
def sp():
    return spec(SYMBOL, TF)


@pytest.fixture(scope='module')
def bars():
    return load(SYMBOL, TF, '2024-01-01', '2025-06-30')


# --------------------------------------------------------------------------- #
# the statement itself                                                        #
# --------------------------------------------------------------------------- #

def test_returns_none_below_warmup(sp):
    s = Donchian()
    assert evaluate(frame([(1, 2, 0.5, 1)] * 5), s, sp) is None


def test_hold_is_a_real_answer_not_none(sp, bars):
    """A signal service that returns None on 'nothing to do' cannot log the
    fact that it looked. 'hold' must be a Signal."""
    flat = bars.iloc[:200]
    sig = evaluate(flat, Donchian(), sp, position_side=0)
    assert sig is not None
    assert sig.action in ('hold', 'buy', 'sell')
    assert sig.state == 'flat'


def test_no_take_profit_is_stated(sp, bars):
    """The validated rule has no TP. If the instruction text ever stops saying
    so, someone will read the reference R levels as targets."""
    sig = None
    for i in range(60, 400):
        s = evaluate(bars.iloc[:i], Donchian(), sp, equity=10000)
        if s and s.is_entry():
            sig = s
            break
    assert sig is not None, 'no entry found in the window'
    text = sig.instruction()
    assert 'NO take-profit' in text
    assert 'orientation only' in text
    assert 'TP1' not in text and 'TP2' not in text


def test_reference_targets_are_r_multiples_of_the_stop(sp, bars):
    for i in range(60, 400):
        sig = evaluate(bars.iloc[:i], Donchian(), sp, equity=10000)
        if sig and sig.is_entry():
            side = 1 if sig.action == 'buy' else -1
            for r, px in sig.ref_targets:
                want = sig.est_entry + side * r * sig.stop_distance
                assert px == pytest.approx(want, abs=0.01)
            assert [r for r, _ in sig.ref_targets] == list(REF_R)
            return
    pytest.fail('no entry found')


def test_stop_is_on_the_losing_side(sp, bars):
    """A stop above a long's entry would be nonsense that a person might act
    on. MT5 reports unset stops as 0.0, which has bitten this project before."""
    seen = 0
    for i in range(60, 1200):
        sig = evaluate(bars.iloc[:i], Donchian(), sp, equity=10000)
        if not (sig and sig.is_entry()):
            continue
        seen += 1
        assert sig.stop > 0
        if sig.action == 'buy':
            assert sig.stop < sig.bar_close
            assert sig.est_entry > sig.bar_close      # pays spread + slip
        else:
            assert sig.stop > sig.bar_close
            assert sig.est_entry < sig.bar_close
    assert seen > 3, 'too few entries to be a real check (%d)' % seen


def test_channel_exit_only_for_the_side_held(sp, bars):
    """exit_lo closes a long, exit_hi closes a short. Reporting the wrong one
    hands the reader a level on the wrong side of the market."""
    window = bars.iloc[:300]
    s = Donchian()
    series = s.prepare(window)
    i = len(window) - 1
    lo, hi = series['exit_lo'][i], series['exit_hi'][i]

    flat = evaluate(window, s, sp, position_side=0)
    assert flat.channel_exit is None

    lng = evaluate(window, s, sp, position_side=1)
    assert lng.channel_exit == pytest.approx(lo)

    sht = evaluate(window, s, sp, position_side=-1)
    assert sht.channel_exit == pytest.approx(hi)


def test_exit_action_when_price_leaves_the_channel(sp):
    """Constructed: 20 bars up, then a collapse, so a held long must be told to
    close rather than to hold."""
    rows = [(100 + i, 101 + i, 99 + i, 100 + i) for i in range(40)]
    rows += [(139, 139, 100, 101)]          # closes far below the 10-bar low
    df = frame(rows)
    sig = evaluate(df, Donchian(), sp, position_side=1)
    assert sig.action == 'exit'
    assert sig.state == 'long'
    assert 'CLOSE the LONG' in sig.instruction()


def test_flat_never_gets_an_exit_and_held_never_gets_an_entry(sp, bars):
    for i in range(60, 800):
        w = bars.iloc[:i]
        assert evaluate(w, Donchian(), sp, position_side=0).action != 'exit'
        for side in (1, -1):
            assert not evaluate(w, Donchian(), sp,
                                position_side=side).is_entry()


def test_both_clocks_recorded(sp, bars):
    """bar_time is UTC, bar_time_server joins to data/bars/. Confusing them
    gave zero overlap out of 579 identical bars once already."""
    t = bars.index[299]
    sig = evaluate(bars.iloc[:300], Donchian(), sp,
                   bar_time_server=t + pd.Timedelta(hours=3))
    assert sig.bar_time == str(t)
    assert sig.bar_time_server == str(t + pd.Timedelta(hours=3))


# --------------------------------------------------------------------------- #
# sizing: one implementation, not two                                         #
# --------------------------------------------------------------------------- #

def test_size_lots_matches_the_simulator_method(sp):
    sim = Simulator(sp, fx=None, config=Config(risk_pct=0.5, apply_swap=False))
    for equity in (500, 5_000, 12_480, 250_000):
        for dist in (0.5, 3.25, 20.0, 68.7):
            assert (sim._size(equity, dist, None)
                    == size_lots(sp, equity, dist, risk_pct=0.5, fx=None))


def test_size_rounds_down_never_up(sp):
    step = sp['volume_step']
    for equity in (1_000, 3_333, 7_777, 12_480):
        lots = size_lots(sp, equity, 41.7, risk_pct=0.5, fx=None)
        if lots:
            assert abs(round(lots / step) - lots / step) < 1e-9
            risk = 41.7 * sp['contract_size'] * lots
            assert risk <= equity * 0.5 / 100.0 + 1e-9


def test_tiny_account_reports_zero_lots_loudly(sp, bars):
    """0 lots is a legitimate answer, and the instruction has to say so rather
    than quietly printing 0.00 next to a tradeable-looking signal."""
    for i in range(60, 600):
        sig = evaluate(bars.iloc[:i], Donchian(), sp, equity=5.0)
        if sig and sig.is_entry():
            assert sig.lots == 0
            text = sig.instruction()
            assert 'rounds to 0 lots' in text
            # and the arithmetic, so 'not tradeable' is not a dead end
            assert sig.min_lot_min == sp['volume_min']
            # rounded to cents for display; fx is None here so no conversion
            want = sig.stop_distance * sp['contract_size'] * sp['volume_min']
            assert sig.min_lot_risk_acct == round(want, 2)
            assert sig.min_lot_risk_pct == round(100.0 * want / 5.0, 2)
            assert 'not a suggestion to raise risk' in text
            return
    pytest.fail('no entry found')


def test_zero_lot_arithmetic_absent_when_tradeable(sp, bars):
    """The min-lot explanation must appear ONLY when size is 0, or it reads as
    a recommended risk level on every signal."""
    for i in range(60, 900):
        sig = evaluate(bars.iloc[:i], Donchian(), sp, equity=5_000_000)
        if sig and sig.is_entry():
            assert sig.lots > 0
            assert sig.min_lot_risk_pct is None
            assert 'NOT TRADEABLE' not in sig.instruction()
            return
    pytest.fail('no entry found')


# --------------------------------------------------------------------------- #
# THE ONE THAT MATTERS: the signal agrees with the measured backtest          #
# --------------------------------------------------------------------------- #

def test_signal_matches_simulator_entries(sp, bars):
    """For every entry the Simulator took, evaluate() on that same bar must
    return the same side and the same stop.

    This is the whole justification for showing the signal to a person: it is
    the rule that was measured, not a lookalike.
    """
    res = Simulator(sp, fx=None,
                    config=Config(risk_pct=0.5, apply_swap=False)).run(
        bars, Donchian(), SYMBOL, TF)
    trades = res.trades
    assert len(trades) > 15, 'not enough trades to be a real check'

    pos = bars.index.get_indexer(trades.entry_time.to_numpy())
    checked = 0
    for row, entry_i in zip(trades.itertuples(), pos):
        # the simulator fills at entry_i; the DECISION was the bar before
        d = entry_i - 1
        if d < Donchian().warmup:
            continue
        sig = evaluate(bars.iloc[:d + 1], Donchian(), sp, position_side=0)
        want = 'buy' if row.side > 0 else 'sell'
        assert sig.action == want, (
            'bar %s: simulator entered %s, signal said %s'
            % (bars.index[d], want, sig.action))
        assert sig.stop == pytest.approx(row.stop_price, abs=1e-6), (
            'bar %s: stop %.4f vs simulator %.4f'
            % (bars.index[d], sig.stop, row.stop_price))
        checked += 1
    assert checked > 15, 'only %d entries actually compared' % checked


def test_no_missed_signals_while_flat(sp, bars):
    """The mirror of the above: when the channel breaks and the simulator was
    FLAT, the signal must fire. Nothing may be silently dropped.

    Restricted to bars where the simulator was demonstrably flat across the
    whole bar (`exposure == 0` at d and d-1). That restriction is the point of
    the test rather than a convenience: the channel keeps breaking WHILE a
    trend trade runs, and on this window 228 of 287 firing bars were bars that
    already held a position. Those legitimately produce no new entry, and an
    earlier version of this test counted them as disagreements and failed.
    """
    res = Simulator(sp, fx=None,
                    config=Config(risk_pct=0.5, apply_swap=False)).run(
        bars, Donchian(), SYMBOL, TF)
    expo = res.equity['exposure'].to_numpy()
    entry_i = set(bars.index.get_indexer(res.trades.entry_time.to_numpy()))

    s = Donchian()
    series = {k: np.asarray(v, float) for k, v in s.prepare(bars).items()}
    close = bars['close'].to_numpy(float)
    fired_flat, missed = 0, []
    for d in range(s.warmup, len(bars) - 1):
        hi, lo, a = series['hi'][d], series['lo'][d], series['atr'][d]
        if not np.isfinite(a) or a <= 0:
            continue
        if not ((np.isfinite(hi) and close[d] > hi)
                or (np.isfinite(lo) and close[d] < lo)):
            continue
        if not (expo[d] == 0 and expo[d - 1] == 0):
            continue                       # held a position; no entry expected
        fired_flat += 1
        if (d + 1) not in entry_i:
            missed.append(str(bars.index[d]))

    assert fired_flat > 20, 'only %d flat breakouts; not a real check' % fired_flat
    assert not missed, ('%d of %d flat breakouts produced no trade: %s'
                        % (len(missed), fired_flat, missed[:5]))

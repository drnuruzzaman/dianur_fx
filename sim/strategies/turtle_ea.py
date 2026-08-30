"""
turtle_ea.py -- the rule set published as DonchianTurtle EA v3 "Consensus",
reimplemented here so it can be measured against this project's controls.

SOURCE. github.com/ymodulus21/donchianturtle-ea, an MQL5 expert advisor. The
rules were read from its description; NO CODE WAS COPIED, and none was needed:
every component is standard and already lives in sim/indicators.py.

WHAT IT CLAIMS TO BE
  entry     close clears the 20-bar OR the 55-bar high (its two "systems")
  regime    close above the 200 MA
  strength  ADX >= 25
  consensus at least 2 of: RSI > 50, MACD above signal, close > SMA(50)
  guard     no entry while ATR > 3x its own average
  stop      entry - 2.0 x ATR
  manage    at +1R the stop moves to break-even; past +2R it trails 1.5 x ATR
  exit      close below the 10-bar low

LONG ONLY, and that is the source's design rather than a simplification made
here. Every filter it specifies is directional -- above the MA, RSI above 50,
MACD bullish, price above the SMA -- and both systems are described as HIGH
breakouts. Mirroring them for shorts would be inventing a strategy and then
attributing it to someone else.

WHAT IS DELIBERATELY NOT IMPLEMENTED, because it is money management rather
than a signal, and because this engine reports in R:
  * volatility-scaled sizing (1.5x in low ATR, 0.5x in high)
  * the 20% drawdown circuit breaker
  * "0.5% risk per system" with two concurrent positions
R is size-invariant, so none of these can move avg_R or profit factor. They
change the equity path and the probability of ruin, which is a different
question from whether the SIGNAL has an edge -- and conflating the two is how a
sizing scheme gets credited with an edge it did not produce.

THE HONEST EXPECTATION, written before the run rather than after. Nearly every
component here has already been measured in this project and failed: the 200
EMA regime filter (runs/edge_matrix_donchian_ema200.csv -- worse on the only
validated cell), the ADX gate (runs/adx_XAUUSDa.csv), the trailing exit and the
fixed-R exit (sim/strategies/exits.py), and RSI-driven entries. Stacking
filters that each fail alone is not obviously a route to succeeding together.
The break-even stop is the part most likely to hurt: it converts the scratches
this rule must sit through into exits, and tools/tp_sweep.py already showed the
tail IS the strategy.
"""

import numpy as np

from ..core import FLAT, LONG, Intent, Strategy
from ..indicators import adx, atr, macd, rsi, sma


def _expanding_mean(values):
    """Mean of everything seen SO FAR. Never of the future."""
    out = np.full(len(values), np.nan, dtype=float)
    total, n = 0.0, 0
    for i, v in enumerate(values):
        if np.isfinite(v):
            total += float(v)
            n += 1
        if n:
            out[i] = total / n
    return out


class TurtleEA(Strategy):
    name = 'turtle_ea'

    def __init__(self, entry1=20, entry2=55, exit=10, atr_len=14, atr_mult=2.0,
                 regime_len=200, adx_len=14, min_adx=25.0, rsi_len=14,
                 sma_len=50, be_at_r=1.0, trail_at_r=2.0, trail_atr=1.5,
                 atr_spike=3.0):
        self.entry1, self.entry2, self.exit = int(entry1), int(entry2), int(exit)
        self.atr_len, self.atr_mult = int(atr_len), float(atr_mult)
        self.regime_len, self.sma_len = int(regime_len), int(sma_len)
        self.adx_len, self.min_adx = int(adx_len), float(min_adx)
        self.rsi_len = int(rsi_len)
        self.be_at_r, self.trail_at_r = float(be_at_r), float(trail_at_r)
        self.trail_atr, self.atr_spike = float(trail_atr), float(atr_spike)
        # MACD needs slow + signal before it means anything; ADX needs ~2x its
        # length on top of its own Wilder warmup.
        self.warmup = max(self.entry2, self.regime_len, 2 * self.adx_len,
                          self.sma_len, 26 + 9, self.atr_len) + 2

    def params(self):
        return {'entry1': self.entry1, 'entry2': self.entry2, 'exit': self.exit,
                'atr_len': self.atr_len, 'atr_mult': self.atr_mult,
                'regime_len': self.regime_len, 'adx_len': self.adx_len,
                'min_adx': self.min_adx, 'rsi_len': self.rsi_len,
                'sma_len': self.sma_len, 'be_at_r': self.be_at_r,
                'trail_at_r': self.trail_at_r, 'trail_atr': self.trail_atr,
                'atr_spike': self.atr_spike}

    def prepare(self, bars):
        close = bars['close']
        a = np.asarray(atr(bars, self.atr_len), dtype=float)
        line, signal, _hist = macd(bars)
        return {
            # shift(1) for the reason donchian.py states: a breakout tested
            # against a window containing its own bar is unsatisfiable, since
            # the high already contains the close.
            'hi1': bars['high'].rolling(self.entry1).max().shift(1).to_numpy(float),
            'hi2': bars['high'].rolling(self.entry2).max().shift(1).to_numpy(float),
            'exit_lo': bars['low'].rolling(self.exit).min().shift(1).to_numpy(float),
            'atr': a,
            # "ATR > 3x average" needs an average, and the source does not say
            # which. An expanding mean is the one choice that cannot read ahead.
            'atr_avg': _expanding_mean(a),
            'regime': np.asarray(sma(close, self.regime_len), dtype=float),
            'adx': np.asarray(adx(bars, self.adx_len), dtype=float),
            'rsi': np.asarray(rsi(bars, self.rsi_len), dtype=float),
            'macd': np.asarray(line, dtype=float),
            'macd_sig': np.asarray(signal, dtype=float),
            'sma': np.asarray(sma(close, self.sma_len), dtype=float),
        }

    def on_bar(self, view, position):
        a = view.series('atr')
        if not np.isfinite(a) or a <= 0:
            return None
        c = view.close()

        if position is not None:
            lo = view.series('exit_lo')
            if np.isfinite(lo) and c < lo:
                return Intent(FLAT, tag='channel_exit')

            # R is measured from `risk_price`: the entry-to-stop distance
            # FIXED AT FILL. Two wrong alternatives were tried on the way here:
            # the live stop, which shrinks as it ratchets and drags the trail
            # on ever earlier (a feedback loop, not a rule), and `atr_mult * a`,
            # which recomputes from the CURRENT ATR and so moves the +1R line
            # every bar. Only the distance actually risked is R.
            risk = float(position.risk_price)
            gain_r = (c - position.entry_price) / risk if risk else 0.0

            want = None
            if gain_r >= self.trail_at_r:
                held = view.i - position.entry_i + 1
                peak = view.highest(held, back=0)
                if np.isfinite(peak):
                    want = peak - self.trail_atr * a
            elif gain_r >= self.be_at_r:
                want = position.entry_price
            # ratchet only: a stop that can fall is not a stop
            if want is not None and np.isfinite(want) and want > position.stop:
                position.stop = float(want)
            return None

        hi1, hi2 = view.series('hi1'), view.series('hi2')
        broke = ((np.isfinite(hi1) and c > hi1)
                 or (np.isfinite(hi2) and c > hi2))
        if not broke:
            return None

        avg = view.series('atr_avg')
        if np.isfinite(avg) and avg > 0 and a > self.atr_spike * avg:
            return None                                    # ATR spike guard

        regime = view.series('regime')
        if not np.isfinite(regime) or c <= regime:
            return None                                    # below the 200 MA

        adx_now = view.series('adx')
        if not np.isfinite(adx_now) or adx_now < self.min_adx:
            return None                                    # not trending

        votes = 0
        r = view.series('rsi')
        if np.isfinite(r) and r > 50:
            votes += 1
        m, ms = view.series('macd'), view.series('macd_sig')
        if np.isfinite(m) and np.isfinite(ms) and m > ms:
            votes += 1
        s50 = view.series('sma')
        if np.isfinite(s50) and c > s50:
            votes += 1
        if votes < 2:
            return None                                    # consensus not met

        return Intent(LONG, stop=c - self.atr_mult * a, tag='turtle_break')

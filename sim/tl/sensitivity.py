"""
sensitivity.py — per-instrument, per-side detection and validation thresholds.

    sensitivity = f(pivot_window, ATR_prominence, volatility_regime)

Two stages, because they answer different questions and the evidence says they
need different treatment:

    SWING SENSITIVITY      how big a wiggle counts as a swing point
    TRENDLINE SENSITIVITY  how much respect a line must earn to be offered

WHY THE ASYMMETRY LIVES IN THE SECOND STAGE, NOT THE FIRST

Measured on EURUSD / USDJPY / XAUUSD, 1h and 4h, 2015-2026, the prominence
distribution of swing HIGHS and swing LOWS is the same to within 2%:

    median prominence, ATR units      highs      lows
    EURUSD 1h                          2.407     2.396
    USDJPY 1h                          2.345     2.382
    XAUUSD 4h                          2.370     2.413

So there is no basis for detecting highs and lows with different windows or
different prominence bars -- doing so would be an assumption dressed as a
calibration.

The asymmetry is in what happens NEXT. Paired line-vs-placebo hold rates,
2021-2026, 71k approaches:

    support      +2.37 pp   (z = +6.96)
    resistance   +0.20 pp   (z = +0.57)

Support lines carry an edge; resistance lines carry none. That is a large,
highly significant behavioural difference between the two sides, and it belongs
at the VALIDATION stage: the same swing detection feeds both, and resistance
then has to clear a higher bar before anything is offered, because a resistance
line at a given quality has been measured to be worth less than a support line
at the same quality.

(One caution, stated rather than buried: channel rails showed the OPPOSITE sign
-- lower rail -3.05 pp, upper +0.63 pp -- on 2,555 approaches, neither
significant. The line result is far larger and far better powered, so it is the
one acted on, but the two are not consistent and a future era could move this.)

PROMINENCE IS A PERCENTILE OF THE INSTRUMENT'S OWN DISTRIBUTION

`Params.min_swing_atr` was a fixed constant, and it was mis-scaled: prominence
is measured over a +/-strength window, and a 7-bar high-to-low range is ~2.4 ATR
by construction, so the documented "useful" settings of 0.5 and 1.0 filtered
0.03% and 1.6% of pivots respectively. Reading the threshold off the
instrument's own measured distribution fixes that permanently and makes it
portable: `prominence_pct = 40` means "drop the least prominent 40% of swings on
THIS instrument at THIS timeframe", which means the same thing on gold and on
yen without anyone re-deriving a constant.

VOLATILITY REGIME widens the pivot window when the market is fast. In a
high-ATR regime a 3-bar fractal fires on noise that a calm market would not
produce, so `strength` rises by one at the 75th ATR percentile and two at the
90th -- the market has to travel further before a turn counts.

CAUSALITY. `calibrate(bars, upto=i)` reads only rows <= i, so a walk-forward
consumer can recalibrate as it goes without leaking. The chart calls it with
`upto=None` because "now" is the last bar it has.
"""

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from ..indicators import atr as atr_series
from .pivots import find_pivots

# Base pivot window per timeframe. Higher timeframes carry fewer, larger swings,
# so a wider window costs little and rejects more noise.
BASE_STRENGTH = {
    '1m': 3, '5m': 3, '15m': 3, '30m': 4,
    '1h': 4, '4h': 5, '1d': 5, '1w': 5,
}

CALM, NORMAL, HIGH, EXTREME = 'calm', 'normal', 'high', 'extreme'


@dataclass
class SideSensitivity:
    """Thresholds for one side of the book."""
    side: str                       # 'support' | 'resistance'
    strength: int                   # pivot window (swing sensitivity)
    min_prominence_atr: float       # ATR prominence bar (swing sensitivity)
    tol_atr: float                  # touch/break tolerance (trendline sens.)
    min_quality: float              # offer bar (trendline sensitivity)

    def to_row(self):
        return {'side': self.side, 'strength': self.strength,
                'min_prominence_atr': round(self.min_prominence_atr, 3),
                'tol_atr': round(self.tol_atr, 3),
                'min_quality': round(self.min_quality, 1)}


@dataclass
class Sensitivity:
    """One instrument, one timeframe, both sides."""
    symbol: str
    timeframe: str
    support: SideSensitivity
    resistance: SideSensitivity
    vol_regime: str = NORMAL
    atr_pct: float = 50.0           # current ATR's percentile in its own history
    prominence_p50_high: float = 0.0
    prominence_p50_low: float = 0.0
    n_pivots: int = 0

    def for_role(self, role) -> SideSensitivity:
        name = getattr(role, 'value', role)
        return self.support if name == 'support' else self.resistance

    def to_row(self):
        return {'symbol': self.symbol, 'timeframe': self.timeframe,
                'vol_regime': self.vol_regime, 'atr_pct': round(self.atr_pct, 1),
                'prom_p50_high': round(self.prominence_p50_high, 3),
                'prom_p50_low': round(self.prominence_p50_low, 3),
                'n_pivots': self.n_pivots,
                **{'sup_' + k: v for k, v in self.support.to_row().items()},
                **{'res_' + k: v for k, v in self.resistance.to_row().items()}}


@dataclass
class SensitivityParams:
    prominence_pct: float = 40.0    # drop the least prominent N% of swings
    atr_window: int = 14
    vol_lookback: int = 500         # bars the ATR percentile is taken over
    base_tol_atr: float = 0.32
    base_min_quality: float = 90.0
    # Resistance carries no measured placebo-adjusted edge while support carries
    # +2.37 pp, so resistance has to be better than support to be offered at all.
    # Deliberately modest: the finding is one era, and a bar so high that no
    # resistance line is ever offered would silently make the engine one-sided.
    resistance_quality_bonus: float = 0.0
    # A resistance line is also given slightly LESS tolerance, so a close through
    # it counts as a break sooner. Same reasoning, same restraint.
    resistance_tol_scale: float = 1.00
    min_pivots: int = 40            # below this the distribution is not worth reading


def _vol_regime(atr, i, lookback):
    """Where current ATR sits in its own recent distribution."""
    lo = max(0, i - lookback + 1)
    seg = atr[lo:i + 1]
    seg = seg[np.isfinite(seg)]
    if len(seg) < 30 or not np.isfinite(atr[i]):
        return NORMAL, 50.0
    pct = float((seg < atr[i]).mean() * 100.0)
    if pct >= 90:
        return EXTREME, pct
    if pct >= 75:
        return HIGH, pct
    if pct <= 25:
        return CALM, pct
    return NORMAL, pct


def _strength_for(tf, regime):
    """
    Base window by timeframe, widened when the market is fast.

    A 3-bar fractal in a high-ATR regime fires on excursions a calm market would
    not produce, so the window grows rather than the threshold -- widening the
    window asks price to travel further in TIME, which is what actually
    distinguishes a turn from a spike.
    """
    s = BASE_STRENGTH.get(tf, 3)
    if regime == HIGH:
        s += 1
    elif regime == EXTREME:
        s += 2
    return s


def prominence_values(bars, strength, upto=None, atr=None):
    """
    Prominence of every pivot, in ATR units, split by side.

    Prominence is the depth of the turn: for a high, how far below it the window
    reached; for a low, how far above. Returns (highs, lows) as arrays.
    """
    high = np.asarray(bars['high'], dtype=float)
    low = np.asarray(bars['low'], dtype=float)
    n = len(high) if upto is None else min(upto + 1, len(high))
    if atr is None:
        atr = atr_series(bars, 14)
    piv_hi, piv_lo = find_pivots(high[:n], low[:n], strength)

    def _prom(pivots, is_high):
        out = []
        for p in pivots:
            i = p['i']
            a = atr[i]
            if not np.isfinite(a) or a <= 0:
                continue
            lo_i = max(0, i - strength)
            hi_i = min(n, i + strength + 1)
            d = (high[i] - np.min(low[lo_i:hi_i])) if is_high \
                else (np.max(high[lo_i:hi_i]) - low[i])
            out.append(d / a)
        return np.asarray(out, dtype=float)

    return _prom(piv_hi, True), _prom(piv_lo, False)


def calibrate(bars, timeframe, symbol='', params: SensitivityParams = None,
              upto=None) -> Sensitivity:
    """
    Derive both sides' thresholds from this instrument's own behaviour.

    Reads only rows <= `upto`, so a walk-forward consumer can recalibrate as it
    goes. `upto=None` means the last bar available.
    """
    p = params or SensitivityParams()
    n_all = len(bars)
    i = (n_all - 1) if upto is None else min(upto, n_all - 1)
    atr = atr_series(bars, p.atr_window)

    regime, pct = _vol_regime(atr, i, p.vol_lookback)
    strength = _strength_for(timeframe, regime)

    hi_prom, lo_prom = prominence_values(bars, strength, upto=i, atr=atr)

    # The prominence bar is a percentile of the instrument's OWN distribution,
    # measured per side. The two distributions are near-identical in practice --
    # which is the point: reading them rather than assuming them is what makes
    # the number portable, and it is also what proves the sides do not need
    # different detection.
    def _bar(vals):
        if len(vals) < p.min_pivots:
            return 0.0
        return float(np.percentile(vals, p.prominence_pct))

    hi_bar, lo_bar = _bar(hi_prom), _bar(lo_prom)

    # A swing HIGH is what a resistance line is built from; a swing LOW feeds
    # support. Naming that mapping explicitly here is worth the line -- getting
    # it backwards is silent and the result still looks plausible.
    support = SideSensitivity(
        side='support', strength=strength, min_prominence_atr=lo_bar,
        tol_atr=p.base_tol_atr, min_quality=p.base_min_quality)
    resistance = SideSensitivity(
        side='resistance', strength=strength, min_prominence_atr=hi_bar,
        tol_atr=p.base_tol_atr * p.resistance_tol_scale,
        min_quality=min(100.0, p.base_min_quality + p.resistance_quality_bonus))

    return Sensitivity(
        symbol=symbol, timeframe=timeframe,
        support=support, resistance=resistance,
        vol_regime=regime, atr_pct=pct,
        prominence_p50_high=float(np.median(hi_prom)) if len(hi_prom) else 0.0,
        prominence_p50_low=float(np.median(lo_prom)) if len(lo_prom) else 0.0,
        n_pivots=len(hi_prom) + len(lo_prom),
    )


def rolling(bars, timeframe, symbol='', params: SensitivityParams = None,
            refresh: int = 500, window: int = 2000, warmup: int = 400):
    """
    A callable `i -> Sensitivity`, recalibrated every `refresh` bars from a
    TRAILING window. This is the design as intended, and the reason it matters:

    `volatility_regime` is a statement about NOW. Freezing one calibration for a
    whole backtest applies a momentary reading to a decade -- tested that way,
    USDJPY 4h happened to start 2011 in an extreme regime, kept strength=7 for
    ten years, and returned the worst cell in the run at -3.66 pp. The
    prominence percentile is a stable distributional property and travelled
    fine across 27 years; the regime component did not.

    CAUSAL BY CONSTRUCTION: the calibration served at bar i is computed from
    bars <= the last refresh point at or before i, so nothing after bar i is
    ever read. `warmup` holds the first calibration until there is enough
    history for the percentile to mean anything.

    Memoised per refresh block, so a walk costs `n / refresh` calibrations
    rather than n.
    """
    p = params or SensitivityParams()
    cache = {}

    def at(i):
        if i is None:
            i = len(bars) - 1
        block = max(warmup, (int(i) // refresh) * refresh)
        block = min(block, len(bars) - 1)
        sv = cache.get(block)
        if sv is None:
            lo = max(0, block - window + 1)
            # A trailing window, not the whole history: a calibration meant to
            # describe the current regime should forget the last decade of it.
            sub = bars.iloc[lo:block + 1]
            sv = calibrate(sub, timeframe, symbol, params=p, upto=len(sub) - 1)
            cache[block] = sv
        return sv

    return at

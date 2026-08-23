"""
indicators.py — Python mirror of js/chart/indicators.js.

Deliberately a mirror, not a re-derivation: the live chart and the backtest must
agree, so this follows the JS line for line (same seeding of the EMA, same
Wilder smoothing, same NaN warm-up) and tests/test_parity.py compares the two
implementations over real bars. Divergence fails a test rather than quietly
making a backtest disagree with what you see on screen.

Every function is CAUSAL: the value at index i depends only on bars <= i. That
property is what makes it legal for the simulator to precompute a whole series
and index into it; tests/test_invariants.py proves it by recomputation.
"""

import numpy as np
import pandas as pd

NAN = float('nan')


def _arr(x):
    return np.asarray(x, dtype=float)


def sma(values, length):
    """Simple moving average; NaN until `length` values exist (matches the JS)."""
    s = pd.Series(_arr(values))
    return s.rolling(length).mean().to_numpy()


def ema(values, length):
    """
    EMA seeded with an SMA of the first `length` values, then k = 2/(len+1).
    This is what indicators.js does; pandas' default ewm seeding is different,
    so the seeding is done explicitly rather than delegated.
    """
    v = _arr(values)
    out = np.full(len(v), NAN)
    if len(v) < length:
        return out
    k = 2.0 / (length + 1.0)
    prev = float(np.mean(v[:length]))
    out[length - 1] = prev
    for i in range(length, len(v)):
        prev = v[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def wilder(values, length):
    """Wilder smoothing (RSI/ATR), seeded with the mean of the first `length`."""
    v = _arr(values)
    out = np.full(len(v), NAN)
    if len(v) < length:
        return out
    prev = float(np.mean(v[:length]))
    out[length - 1] = prev
    for i in range(length, len(v)):
        prev = (prev * (length - 1) + v[i]) / length
        out[i] = prev
    return out


def stdev(values, length, basis):
    """Population standard deviation about `basis`, as the JS computes it."""
    v = _arr(values)
    b = _arr(basis)
    out = np.full(len(v), NAN)
    for i in range(length - 1, len(v)):
        if np.isnan(b[i]):
            continue
        window = v[i - length + 1:i + 1]
        out[i] = float(np.sqrt(np.mean((window - b[i]) ** 2)))
    return out


def true_range(high, low, close):
    h, l, c = _arr(high), _arr(low), _arr(close)
    out = np.empty(len(h))
    out[0] = h[0] - l[0]
    prev = c[:-1]
    out[1:] = np.maximum.reduce([h[1:] - l[1:], np.abs(h[1:] - prev), np.abs(l[1:] - prev)])
    return out


def atr(bars, length=14):
    return wilder(true_range(bars['high'], bars['low'], bars['close']), length)


def rsi(bars, length=14, source='close'):
    """Wilder RSI. The JS drops the first bar before smoothing; so does this."""
    v = _arr(bars[source])
    gains = np.maximum(0.0, np.diff(v))
    losses = np.maximum(0.0, -np.diff(v))
    ag = wilder(gains, length)
    al = wilder(losses, length)
    out = np.full(len(v), NAN)
    with np.errstate(divide='ignore', invalid='ignore'):
        rs = np.where(al == 0, np.inf, ag / np.where(al == 0, 1, al))
        vals = np.where(al == 0, 100.0, 100.0 - 100.0 / (1.0 + rs))
    out[1:] = np.where(np.isnan(ag), NAN, vals)
    return out


def macd(bars, fast=12, slow=26, signal=9, source='close'):
    """
    Returns (macd, signal, histogram). Mirrors the JS quirk exactly: the signal
    line is an EMA of the macd series with NaNs replaced by 0 before smoothing,
    then re-masked where macd is NaN.
    """
    v = _arr(bars[source])
    f, s = ema(v, fast), ema(v, slow)
    line = f - s
    defined = np.where(np.isnan(line), 0.0, line)
    sig = ema(defined, signal)
    sig = np.where(np.isnan(line), NAN, sig)
    return line, sig, line - sig


def bollinger(bars, length=20, mult=2.0, source='close'):
    v = _arr(bars[source])
    basis = sma(v, length)
    sd = stdev(v, length, basis)
    return basis + mult * sd, basis, basis - mult * sd


def vwap_session(bars):
    """Session VWAP, reset on each UTC day change — as the JS does it."""
    tp = (_arr(bars['high']) + _arr(bars['low']) + _arr(bars['close'])) / 3.0
    vol = _arr(bars['tick_volume']) if 'tick_volume' in bars else np.ones(len(tp))
    vol = np.where(vol <= 0, 1.0, vol)
    days = pd.DatetimeIndex(bars.index).day.to_numpy()
    out = np.empty(len(tp))
    pv = vv = 0.0
    prev_day = None
    for i in range(len(tp)):
        if days[i] != prev_day:
            pv = vv = 0.0
            prev_day = days[i]
        pv += tp[i] * vol[i]
        vv += vol[i]
        out[i] = pv / vv if vv else NAN
    return out


def stochastic(bars, k=14, d=3, smooth=3):
    h, l, c = _arr(bars['high']), _arr(bars['low']), _arr(bars['close'])
    raw = np.full(len(c), NAN)
    for i in range(k - 1, len(c)):
        hi = float(np.max(h[i - k + 1:i + 1]))
        lo = float(np.min(l[i - k + 1:i + 1]))
        raw[i] = 50.0 if hi == lo else (c[i] - lo) / (hi - lo) * 100.0
    kline = sma(np.where(np.isnan(raw), 0.0, raw), smooth)
    kline = np.where(np.isnan(raw), NAN, kline)
    dline = sma(np.where(np.isnan(kline), 0.0, kline), d)
    dline = np.where(np.isnan(kline), NAN, dline)
    return kline, dline


def heikin_ashi(bars):
    """Returns a DataFrame with the transformed OHLC, same recursion as the JS."""
    o, h, l, c = (_arr(bars[k]) for k in ('open', 'high', 'low', 'close'))
    ho = np.empty(len(o)); hc = np.empty(len(o))
    hh = np.empty(len(o)); hl = np.empty(len(o))
    po = pc = None
    for i in range(len(o)):
        cc = (o[i] + h[i] + l[i] + c[i]) / 4.0
        oo = (o[i] + c[i]) / 2.0 if po is None else (po + pc) / 2.0
        ho[i], hc[i] = oo, cc
        hh[i] = max(h[i], oo, cc)
        hl[i] = min(l[i], oo, cc)
        po, pc = oo, cc
    return pd.DataFrame({'open': ho, 'high': hh, 'low': hl, 'close': hc}, index=bars.index)

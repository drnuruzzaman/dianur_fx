"""
mtf.py — multi-timeframe alignment and the confluence engine.

THE LOOK-AHEAD PROBLEM THIS SOLVES

The single most common way a multi-timeframe backtest lies to itself: at 09:15
on the 15M chart it reads "the 4H trend is up" from the 4H candle that opened at
08:00 — a candle that does not close until 12:00. Its high, low and close are
future information. Every 15M bar inside that candle gets a peek at where the
4H bar ended up.

The guard here is structural, not advisory:

  * An HTF bar is USABLE only once `htf_open + htf_duration <= exec_bar_close`.
  * Alignment is computed once, as an index map, by searching that condition —
    so there is no code path that could read an unclosed bar even by mistake.
  * `strict=True` (the default) re-verifies the map and raises on any violation,
    so a future refactor cannot silently reintroduce the leak.

The cost is honest lag: for the first 4 hours of a 4H candle, the 4H context is
the PREVIOUS candle's. That lag is real in live trading too, which is the point.
"""

import numpy as np
import pandas as pd

def to_ms(index) -> np.ndarray:
    """
    A DatetimeIndex as integer milliseconds, at ANY pandas resolution.

    `index.astype('int64')` returns the index's own unit, and since pandas 2.0
    that unit is no longer always nanoseconds -- `read_csv(parse_dates=...)`
    infers `datetime64[us]` (and pandas 3 does so by default), where the old
    `astype('int64') // 1_000_000` silently yields SECONDS. Every trendline
    slope is then 1000x wrong and every MTF close time is compared against
    millisecond TF_MS constants, so the alignment guard checks a nonsense
    condition and raises nothing. Converting the unit first is what makes the
    result independent of how the bars happened to be loaded.
    """
    return np.asarray(index.astype('datetime64[ms]').astype('int64'),
                      dtype=np.int64)


TF_MS = {'1m': 60_000, '5m': 300_000, '15m': 900_000, '30m': 1_800_000,
         '1h': 3_600_000, '4h': 14_400_000, '1d': 86_400_000, '1w': 604_800_000}


class LookAheadViolation(AssertionError):
    """Raised when an alignment would expose an unclosed higher-timeframe bar."""


def align_index(exec_index: pd.DatetimeIndex, exec_tf: str,
                htf_index: pd.DatetimeIndex, htf_tf: str, strict=True) -> np.ndarray:
    """
    For each execution bar, the index of the newest HTF bar that had already
    CLOSED by the time the execution bar closed. -1 where none has.

    Both indexes are bar OPEN times (the MT5 convention), so a bar's close is
    open + duration.
    """
    exec_ms = to_ms(exec_index)
    htf_ms = to_ms(htf_index)
    exec_close = exec_ms + TF_MS[exec_tf]
    htf_close = htf_ms + TF_MS[htf_tf]

    # newest htf bar whose CLOSE <= this bar's close
    pos = np.searchsorted(htf_close, exec_close, side='right') - 1

    if strict:
        bad = np.where(pos >= 0)[0]
        if len(bad):
            used_close = htf_close[pos[bad]]
            leak = used_close > exec_close[bad]
            if leak.any():
                k = bad[leak][0]
                raise LookAheadViolation(
                    'alignment would expose an unclosed %s bar at %s'
                    % (htf_tf, exec_index[k]))
    return pos


class MTFContext:
    """
    Context timeframes bound to an execution timeframe, with the alignment map
    baked in. Ask it for anything and you can only ever get closed-bar values.
    """

    def __init__(self, exec_tf: str, exec_index: pd.DatetimeIndex, strict=True):
        self.exec_tf = exec_tf
        self.exec_index = exec_index
        self.strict = strict
        self.frames = {}          # tf -> dict of aligned arrays
        self.maps = {}            # tf -> alignment map

    def add(self, tf: str, htf_index: pd.DatetimeIndex, columns: dict):
        """
        Register a context timeframe. `columns` maps a name to an array indexed
        in HTF space; each is projected into execution space through the map, so
        every consumer gets closed-bar data by construction.
        """
        pos = align_index(self.exec_index, self.exec_tf, htf_index, tf, self.strict)
        self.maps[tf] = pos
        aligned = {}
        for name, values in columns.items():
            values = np.asarray(values, dtype=object if _is_obj(values) else float)
            out = np.full(len(pos), None if values.dtype == object else np.nan,
                          dtype=values.dtype)
            live = pos >= 0
            out[live] = values[pos[live]]
            aligned[name] = out
        aligned['_htf_bar_index'] = pos
        self.frames[tf] = aligned
        return self

    def get(self, tf: str, name: str):
        return self.frames[tf][name]

    def timeframes(self):
        return list(self.frames)

    def lag_bars(self, tf: str):
        """
        How stale the context is, in execution bars — the honest cost of the
        guard. Reported so it can be sanity-checked rather than assumed.
        """
        pos = self.maps[tf]
        exec_ms = to_ms(self.exec_index)
        htf_ms = np.full(len(pos), np.nan)
        return {
            'mean': float(np.mean(np.diff(np.where(np.diff(pos) != 0)[0]))
                          if (np.diff(pos) != 0).sum() > 1 else np.nan),
            'expected': TF_MS[tf] / TF_MS[self.exec_tf],
        }


def _is_obj(values):
    a = np.asarray(values)
    return a.dtype == object or a.dtype.kind in 'US'


# --------------------------------------------------------------------------- #
# confluence                                                                  #
# --------------------------------------------------------------------------- #
AGREE_FULL = 'full'
AGREE_PARTIAL = 'partial'
AGREE_NONE = 'none'
AGREE_CONFLICT = 'conflict'


def confluence(direction_by_tf: dict, want: str, weights: dict = None):
    """
    Score how much the context timeframes agree with a proposed direction.

    Returns (score in -1..1, label). The engine does NOT assume agreement helps:
    the score is recorded on every signal whether or not it is used as a filter,
    so a backtest can compare 'filter on' against 'filter off' and answer the
    question with numbers. See tools/confluence_ab.py.
    """
    weights = weights or {'1h': 1.0, '4h': 1.5, '1d': 2.0}
    total = agree = 0.0
    for tf, d in direction_by_tf.items():
        w = weights.get(tf, 1.0)
        if d is None:
            continue
        total += w
        if d == want:
            agree += w
        elif d != 'flat':
            agree -= w
    if total == 0:
        return 0.0, AGREE_NONE
    score = agree / total
    label = (AGREE_FULL if score >= 0.99 else
             AGREE_PARTIAL if score > 0 else
             AGREE_NONE if score == 0 else AGREE_CONFLICT)
    return round(float(score), 3), label

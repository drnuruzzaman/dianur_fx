"""
segments.py — the market's history as a sequence of EPISODES, not a per-bar label.

`regime.py` answers "what is the market doing at bar i". Every annotated chart
answers a different question: "this was a downward channel, then a range, then
an upward channel". That is a sequence with boundaries, and boundaries are what
a per-bar label cannot give you -- reading them off by eye means deciding where
one episode ends, which is exactly the judgement this makes explicit.

TWO THINGS STOP IT PRODUCING CONFETTI

  MINIMUM LENGTH. A three-bar excursion into `transition` is not an episode, it
  is noise inside the episode either side of it. Runs shorter than `min_bars`
  are absorbed into their neighbour rather than emitted.

  HYSTERESIS. A regime must hold for `confirm_bars` consecutive bars before a
  new segment opens. Without it every oscillation across a threshold starts a
  new episode, and the segmentation says more about the threshold than about
  the market.

CAUSALITY. Segments are built by a forward walk and each one's start is fixed
when it opens, so `segments_upto(i)` returns what a bar-i observer could have
known. The LAST segment is open-ended and its end moves as bars arrive -- which
is honest: you never know the current episode has ended until it has.
"""

from dataclasses import dataclass
from typing import List

import numpy as np

from . import regime as reg


@dataclass
class Segment:
    """One episode of a single regime."""

    kind: str                 # trending_up | trending_down | sideways | transition
    i0: int
    i1: int
    t0: int
    t1: int
    bars: int
    high: float = 0.0         # extremes reached inside the episode
    low: float = 0.0
    ret_atr: float = 0.0      # close-to-close move, in ATR at the start
    closed: bool = True       # the final segment is still forming

    @property
    def label(self) -> str:
        return {
            reg.TRENDING_UP: 'Uptrend',
            reg.TRENDING_DOWN: 'Downtrend',
            reg.SIDEWAYS: 'Range',
            reg.TRANSITION: 'Transition',
        }.get(self.kind, self.kind)

    def to_row(self) -> dict:
        return {
            'kind': self.kind, 'label': self.label,
            'i0': self.i0, 'i1': self.i1, 't0': self.t0, 't1': self.t1,
            'bars': self.bars, 'high': self.high, 'low': self.low,
            'ret_atr': round(self.ret_atr, 3), 'closed': self.closed,
        }


@dataclass
class SegmentParams:
    min_bars: int = 12        # shorter runs are absorbed, not emitted
    confirm_bars: int = 3     # consecutive bars before a new episode opens
    max_segments: int = 12    # most recent N


def _runs(kinds, confirm):
    """
    Raw runs with hysteresis: a new run opens only once `confirm` consecutive
    bars agree, so a single bar flipping across a threshold does not split an
    episode in two.
    """
    n = len(kinds)
    if not n:
        return []
    out = []
    cur = kinds[0]
    start = 0
    j = 1
    while j < n:
        if kinds[j] == cur:
            j += 1
            continue
        # candidate change: does it hold?
        k = j
        while k < n and k - j < confirm and kinds[k] == kinds[j]:
            k += 1
        if k - j >= confirm or k >= n:
            out.append((cur, start, j - 1))
            cur = kinds[j]
            start = j
            j += 1
        else:
            j = k
    out.append((cur, start, n - 1))
    return out


def _absorb(runs, min_bars):
    """
    Merge runs shorter than `min_bars` into whichever NEIGHBOUR is longer. A
    short run between two different regimes is genuinely ambiguous, and giving
    it to the longer side is the choice that changes the picture least.
    """
    if not runs:
        return []
    runs = [list(r) for r in runs]
    changed = True
    while changed and len(runs) > 1:
        changed = False
        for idx, (kind, i0, i1) in enumerate(runs):
            if i1 - i0 + 1 >= min_bars:
                continue
            prev_len = (runs[idx - 1][2] - runs[idx - 1][1]) if idx > 0 else -1
            next_len = (runs[idx + 1][2] - runs[idx + 1][1]) if idx + 1 < len(runs) else -1
            if prev_len < 0 and next_len < 0:
                continue
            if prev_len >= next_len:
                runs[idx - 1][2] = i1
            else:
                runs[idx + 1][1] = i0
            runs.pop(idx)
            changed = True
            break
    # neighbouring runs of the same kind can now be adjacent: fuse them
    fused = [runs[0]]
    for r in runs[1:]:
        if r[0] == fused[-1][0]:
            fused[-1][2] = r[2]
        else:
            fused.append(r)
    return fused


def build(bars, regimes=None, atr=None, params: SegmentParams = None,
          times=None, upto=None):
    """
    Episodes over `bars`, oldest first. `upto` truncates to a bar-i observer.
    """
    p = params or SegmentParams()
    n = len(bars)
    if not n:
        return []
    i_end = (n - 1) if upto is None else min(upto, n - 1)

    if regimes is None:
        regimes, _, feats = reg.compute(bars)
        atr = feats['atr'] if atr is None else atr
    if atr is None:
        from ..indicators import atr as atr_series
        atr = atr_series(bars, 14)
    if times is None:
        times = np.asarray(bars.index.astype('int64') // 1_000_000)

    high = np.asarray(bars['high'], dtype=float)
    low = np.asarray(bars['low'], dtype=float)
    close = np.asarray(bars['close'], dtype=float)

    kinds = list(regimes[:i_end + 1])
    runs = _absorb(_runs(kinds, p.confirm_bars), p.min_bars)

    out = []
    for k, (kind, i0, i1) in enumerate(runs):
        a0 = atr[i0] if i0 < len(atr) and np.isfinite(atr[i0]) else 0.0
        seg = Segment(
            kind=kind, i0=i0, i1=i1,
            t0=int(times[i0]), t1=int(times[i1]),
            bars=i1 - i0 + 1,
            high=float(np.max(high[i0:i1 + 1])),
            low=float(np.min(low[i0:i1 + 1])),
            ret_atr=float((close[i1] - close[i0]) / a0) if a0 else 0.0,
            closed=(k < len(runs) - 1),
        )
        out.append(seg)
    return out[-p.max_segments:]

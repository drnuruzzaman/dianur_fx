"""
trendline.py — the existing trendline engine, wearing the proposer interface.

Not a rewrite. It calls sim/tl/engine.py and reads Snapshot.tradeable, which is
already frozen per bar and already look-ahead safe. What changes is only what
comes out: instead of a bespoke measurement pipeline, it emits the same table a
motif finder emits, and is scored by the same code against the same null.

That makes the trendline the CONTROL PATTERN for the whole discovery effort. It
is a hypothesis a human found plausible enough to build a project around, and it
has been measured to carry a small, real, wrongly-signed effect. Any discovered
pattern that cannot beat it has not earned its complexity.

Two pattern_ids are emitted, because they are different claims and deserve
separate hypotheses rather than being averaged together:

    tl_approach_support     price arrives at a confirmed support line
    tl_approach_resistance  price arrives at a confirmed resistance line

Direction is the BOUNCE direction -- long at support, short at resistance. The
break hypothesis is the same events with the sign flipped, and evaluate.py
reports a signed deviation, so both are readable from one run. A pattern that
holds less often than base is a fade signal stated in the negative.
"""

import numpy as np

from ..indicators import atr as atr_series
from ..tl.engine import Params, TrendlineEngine
from ..tl.mtf import TF_MS
from .proposer import Proposer, empty_proposals

import pandas as pd


class TrendlineApproach(Proposer):
    name = 'trendline_approach'

    def __init__(self, tol_atr=0.10, near_atr=0.4, far_atr=1.5,
                 min_quality=0.0):
        self.tol_atr = tol_atr
        self.near_atr = near_atr
        self.far_atr = far_atr
        self.min_quality = min_quality

    def params(self):
        return {'tol_atr': self.tol_atr, 'near_atr': self.near_atr,
                'far_atr': self.far_atr, 'min_quality': self.min_quality}

    def propose(self, bars, symbol, tf):
        eng = TrendlineEngine(tf, TF_MS[tf], Params(tol_atr=self.tol_atr),
                              record_tradeable=True)
        snaps = eng.walk(bars)
        close = np.asarray(bars['close'], dtype=float)
        atr = atr_series(bars, 14)

        # A line must go away before it can approach again, or sitting on a
        # level for fifty bars counts as fifty independent observations and
        # every sample size in the study is a fiction.
        armed = {}
        rows = []
        for snap in snaps:
            i = snap.i
            a = atr[i]
            if not np.isfinite(a) or a <= 0:
                continue
            for line_id, role, value, quality, _touches in snap.tradeable:
                dist = abs(close[i] - value) / a
                if dist >= self.far_atr:
                    armed[line_id] = True
                    continue
                if dist > self.near_atr or not armed.get(line_id, False):
                    continue
                if quality < self.min_quality:
                    continue
                armed[line_id] = False
                rows.append({
                    'pattern_id': 'tl_approach_%s' % role,
                    'bar': i,
                    # The engine confirmed this line on an earlier bar; the
                    # approach itself is news as of this close, so the two
                    # timestamps coincide here. They will not for a proposer
                    # whose structure completes before it is confirmed.
                    'occurred_at': bars.index[i],
                    'known_at': bars.index[i],
                    'direction': 1 if role == 'support' else -1,
                })
        if not rows:
            return empty_proposals()
        return pd.DataFrame(rows)

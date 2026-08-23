"""
sax.py — discover candidate patterns instead of specifying them.

A window of the last k bars is normalised, discretised into a short word, and
that word IS the pattern id. Every bar whose window spells the same word is an
instance of the same pattern, and evaluate.py then asks whether that word's bars
resolved differently from matched control bars.

WHY SYMBOLIC, AND NOT A NEURAL NET

The hypothesis space has to be COUNTABLE. `alphabet ** word` is a number you can
write down before you look at any data, which is what makes the multiple-
comparison correction honest -- evaluate.py corrects for patterns considered,
and a model whose hypotheses cannot be enumerated cannot be corrected for at
all. A four-letter alphabet at word length five is 1024 hypotheses per
configuration and the deflated noise bar for that is z = 3.26; you know the bar
before you start, and you cannot move it by looking.

It is also drawable. A word maps back to a shape, so a survivor can be rendered
on the chart in js/chart/ and inspected by eye -- the same argument that made
the trendline engine worth porting for parity.

NORMALISATION, AND WHAT IT DELIBERATELY THROWS AWAY

The window is expressed relative to its own first close and divided by the ATR
at the window's start. Both are known at the window's start, so nothing here
reads forward. Level and scale are discarded on purpose: "EURUSD near 1.10" and
"gold near 2400" should be the same pattern if their shapes match, and a scheme
that kept the level would find the price, not the shape.

The ATR divisor is taken ONCE at the window start rather than per bar, so the
word still carries whether volatility expanded or contracted across the window.
Per-bar normalisation would erase that, and it is most of what a candle pattern
is actually about.

TWO CHANNELS

  path   where the close sat, bar by bar, in ATR units from the window start.
  body   each bar's close-minus-open over its own high-minus-low: where in its
         range the bar closed. This is the candle-pattern channel, and it is
         scale-free already.

Words concatenate the two, so 'path=cbbd|body=xy' is a different hypothesis from
'path=cbbd|body=yx'. Set body_bars to 0 to test shape alone.
"""

import numpy as np
import pandas as pd

from ..indicators import atr as atr_series
from .proposer import Proposer, empty_proposals

ALPHABET = 'abcdefghijklmnopqrstuvwxyz'


class SaxMotifs(Proposer):
    """
    Emits one pattern_id per distinct word, at the bar the window ends.

    `direction` is +1 for every proposal. That is not an oversight: a word and
    its mirror image are different words, so the finder does not need to be told
    which way to bet -- if a shape predicts down, its word simply shows a
    negative deviation, and evaluate.py reports a signed one. Forcing a
    direction per word from the data would be fitting the answer.
    """

    name = 'sax_motifs'

    def __init__(self, window=6, path_letters=4, body_letters=3, body_bars=2,
                 atr_len=14, min_count=100, rank_window=2000):
        self.window = window
        self.path_letters = path_letters
        self.body_letters = body_letters
        self.body_bars = body_bars
        self.atr_len = atr_len
        self.min_count = min_count
        self.rank_window = rank_window

    def params(self):
        return {'window': self.window, 'path_letters': self.path_letters,
                'body_letters': self.body_letters, 'body_bars': self.body_bars,
                'min_count': self.min_count}

    def hypothesis_space(self):
        """
        How many distinct words COULD exist. Reported before the sweep, because
        the honest denominator for the correction is the space you searched, and
        it is knowable in advance.
        """
        return (self.path_letters ** (self.window - 1)
                * self.body_letters ** self.body_bars)

    def propose(self, bars, symbol, tf):
        n = len(bars)
        w = self.window
        if n < w + self.atr_len + 10:
            return empty_proposals()

        close = np.asarray(bars['close'], dtype=float)
        openp = np.asarray(bars['open'], dtype=float)
        high = np.asarray(bars['high'], dtype=float)
        low = np.asarray(bars['low'], dtype=float)
        atr = atr_series(bars, self.atr_len)

        # --- path channel -------------------------------------------------
        # window ENDS at bar i, so it spans i-w+1 .. i and its anchor is the
        # close at i-w+1. Every quantity below is known by the close of bar i.
        idx = np.arange(w - 1, n)
        anchor_i = idx - (w - 1)
        anchor_px = close[anchor_i]
        anchor_atr = atr[anchor_i]

        steps = np.empty((len(idx), w - 1), dtype=float)
        for j in range(1, w):
            with np.errstate(invalid='ignore', divide='ignore'):
                steps[:, j - 1] = (close[anchor_i + j] - anchor_px) / anchor_atr

        path = _letters(steps, self.path_letters, self.rank_window)

        # --- body channel -------------------------------------------------
        parts = [path]
        if self.body_bars > 0:
            rng_px = np.maximum(high - low, 1e-12)
            frac = (close - openp) / rng_px
            body = np.empty((len(idx), self.body_bars), dtype=float)
            for j in range(self.body_bars):
                body[:, j] = frac[idx - (self.body_bars - 1 - j)]
            parts.append(_letters(body, self.body_letters, self.rank_window))

        words = np.char.add(np.char.add(parts[0], '|'), parts[1]) \
            if len(parts) > 1 else parts[0]

        valid = np.isfinite(anchor_atr) & (anchor_atr > 0)
        words = np.where(valid, words, '')
        keep = words != ''
        if not keep.any():
            return empty_proposals()

        bar = idx[keep]
        word = words[keep]

        # Drop words too rare to test. They are still COUNTED as considered --
        # evaluate.py corrects for hypotheses looked at, not hypotheses kept --
        # but scoring a word with nine instances is noise with a name.
        uniq, counts = np.unique(word, return_counts=True)
        common = set(uniq[counts >= self.min_count])
        sel = np.array([x in common for x in word])
        if not sel.any():
            return empty_proposals()

        bar, word = bar[sel], word[sel]
        return pd.DataFrame({
            'pattern_id': word,
            'bar': bar,
            # The shape began forming w-1 bars ago and is only complete now.
            # Recording both is the difference between "this shape happened"
            # and "this shape could have been acted on".
            'occurred_at': bars.index[bar - (w - 1)],
            'known_at': bars.index[bar],
            'direction': np.ones(len(bar), dtype=np.int64),
        })


def _letters(values, k, rank_window):
    """
    Column-wise trailing-rank discretisation, joined into one string per row.

    Trailing rather than global for the same reason the strata are: a global
    quantile edge is fitted to the whole series including its future. Here it
    would be worse than in the strata, because the words ARE the hypotheses --
    a shifting edge would silently redefine what a pattern is partway through
    the test.
    """
    out = np.full(len(values), '', dtype=object)
    for c in range(values.shape[1]):
        col = pd.Series(values[:, c])
        pct = col.rolling(rank_window, min_periods=max(50, k * 20)).rank(pct=True)
        v = pct.to_numpy()
        lab = np.where(np.isfinite(v),
                       np.array(list(ALPHABET))[
                           np.clip((np.nan_to_num(v) * k).astype(int), 0, k - 1)],
                       '')
        out = np.char.add(out.astype(str), lab.astype(str))
    # a row where any column could not be ranked yet is not a word at all
    full = np.array([len(x) == values.shape[1] for x in out])
    return np.where(full, out.astype(str), '')

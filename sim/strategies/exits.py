"""
exits.py — the SAME Donchian entry, four different ways of leaving.

    donchian            Exit A, the baseline: opposite 10-bar channel
    donchian_exit_ema   Exit B: close back through EMA(20)
    donchian_exit_trail Exit C: ATR trailing stop, k x ATR from the extreme
    donchian_exit_2r    Exit D: fixed target at 2R

WHY THIS EXPERIMENT IS WORTH MORE THAN ANOTHER FILTER. Every diagnostic so far
says the entry is not the problem: gold 4h clears its time-shift control at
percentile 98.3, and even USDJPY 1h beat all sixty of its controls (percentile
100.0) while still losing money -- the timing carries information, the exit and
the costs give it back. A filter changes WHICH trades are taken. This changes
what happens to the trades already being taken, and it is the only lever that
can act on all 363 of them instead of a 43% subset.

THE ENTRY IS HELD IDENTICAL, deliberately and by inheritance rather than by
copying: all four subclass Donchian and override only the branch that runs while
a position is open. If the entry logic drifted between variants the comparison
would measure two things at once and attribute both to the exit.

ONE EXIT AT A TIME. B, C and D each REPLACE the channel exit rather than joining
it. Leaving the channel in place and adding a target measures a hybrid, and a
hybrid that beats the baseline tells you nothing about which half did the work.
The fixed 2-ATR stop stays in all four because it is the risk definition the
position size is derived from -- removing it would change R itself and make the
avg_R column incomparable across variants.

CAUSALITY OF THE TRAILING STOP. `Simulator.run` checks stops at step 3 and calls
`on_bar` at step 5, so a stop written during bar i is first honoured on bar i+1.
The trail is therefore computed from closed data and applied to the future, with
no bar ever tested against a stop derived from its own high. The trail also only
ever TIGHTENS: a stop allowed to widen is not a stop, and a bug that let it
would quietly convert losses into larger losses.
"""

import numpy as np

from ..core import FLAT, LONG, SHORT, Intent
from .donchian import Donchian


class _DonchianExit(Donchian):
    """Shared plumbing: Donchian's entry, a replaceable exit, no channel exit."""

    def _exit_now(self, view, position):
        raise NotImplementedError

    def on_bar(self, view, position):
        a = view.series('atr')
        if not np.isfinite(a) or a <= 0:
            return None
        if position is not None:
            return self._exit_now(view, position)
        # entry: Donchian's, untouched. Called with position=None so the base
        # cannot take its own channel-exit branch.
        return Donchian.on_bar(self, view, None)


class DonchianExitEma(_DonchianExit):
    """Exit B — leave when the close crosses back through an EMA."""

    name = 'donchian_exit_ema'

    def __init__(self, entry=20, exit=10, atr_len=14, atr_mult=2.0,
                 trigger='close', ema_len=20):
        Donchian.__init__(self, entry=entry, exit=exit, atr_len=atr_len,
                          atr_mult=atr_mult, trigger=trigger)
        self.ema_len = int(ema_len)
        self.name = 'donchian_exit_ema'
        self.warmup = max(self.warmup, self.ema_len + 2)

    def params(self):
        return {**Donchian.params(self), 'ema_len': self.ema_len}

    def prepare(self, bars):
        series = Donchian.prepare(self, bars)
        # shift(1): the EMA a bar is compared against must not contain that bar
        series['ema'] = (bars['close'].ewm(span=self.ema_len, adjust=False)
                         .mean().shift(1).to_numpy(float))
        return series

    def _exit_now(self, view, position):
        ema = view.series('ema')
        if not np.isfinite(ema):
            return None
        c = view.close()
        if position.side == LONG and c < ema:
            return Intent(FLAT, tag='ema_exit')
        if position.side == SHORT and c > ema:
            return Intent(FLAT, tag='ema_exit')
        return None


class DonchianExitTrail(_DonchianExit):
    """Exit C — an ATR trailing stop, k x ATR from the extreme since entry."""

    name = 'donchian_exit_trail'

    def __init__(self, entry=20, exit=10, atr_len=14, atr_mult=2.0,
                 trigger='close', trail_k=3.0):
        Donchian.__init__(self, entry=entry, exit=exit, atr_len=atr_len,
                          atr_mult=atr_mult, trigger=trigger)
        self.trail_k = float(trail_k)
        self.name = 'donchian_exit_trail'

    def params(self):
        return {**Donchian.params(self), 'trail_k': self.trail_k}

    def _exit_now(self, view, position):
        a = view.series('atr')
        held = view.i - position.entry_i + 1
        if held < 1:
            return None
        if position.side == LONG:
            peak = view.highest(held, back=0)      # entry bar .. now, all closed
            want = peak - self.trail_k * a
            # ratchet only: max() so the stop can rise and never fall
            if np.isfinite(want) and want > position.stop:
                position.stop = float(want)
        else:
            trough = view.lowest(held, back=0)
            want = trough + self.trail_k * a
            if np.isfinite(want) and want < position.stop:
                position.stop = float(want)
        # the engine does the leaving; this rule never issues a FLAT of its own
        return None


class DonchianExitFixedR(_DonchianExit):
    """Exit D — a fixed target at `r_mult` R, and nothing else."""

    name = 'donchian_exit_2r'

    def __init__(self, entry=20, exit=10, atr_len=14, atr_mult=2.0,
                 trigger='close', r_mult=2.0):
        Donchian.__init__(self, entry=entry, exit=exit, atr_len=atr_len,
                          atr_mult=atr_mult, trigger=trigger)
        self.r_mult = float(r_mult)
        self.name = 'donchian_exit_%gr' % self.r_mult

    def params(self):
        return {**Donchian.params(self), 'r_mult': self.r_mult}

    def on_bar(self, view, position):
        if position is not None:
            return None                 # the target and the stop are the engine's
        intent = Donchian.on_bar(self, view, None)
        if intent is None or intent.side == FLAT or intent.stop is None:
            return intent
        c = view.close()
        risk = abs(c - intent.stop)
        if risk <= 0:
            return None
        tgt = c + self.r_mult * risk * (1 if intent.side == LONG else -1)
        return Intent(intent.side, stop=intent.stop, target=tgt, tag=intent.tag)

    def _exit_now(self, view, position):
        return None

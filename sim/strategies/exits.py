"""
exits.py — the SAME Donchian entry, four different ways of leaving.

    donchian            Exit A, the baseline: opposite 10-bar channel
    donchian_exit_ema   Exit B: close back through EMA(20)
    donchian_exit_trail Exit C: ATR trailing stop, k x ATR from the extreme
    donchian_exit_2r    Exit D: fixed target at 2R
    donchian_exit_liq   Exit E: target at the nearest live LIQUIDITY level
    donchian_exit_ct    Exit F: the channel exit AND an ATR trail, together

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

ONE EXIT AT A TIME, FOR B TO E. Each REPLACES the channel exit rather than
joining it. Leaving the channel in place and adding a target measures a hybrid,
and a hybrid that beats the baseline tells you nothing about which half did the
work.

EXIT F IS THE DELIBERATE EXCEPTION, added after A and C were both measured. The
objection above is about ATTRIBUTION, and it only bites while the halves are
unknown: the channel is +0.1799 R alone and the trail +0.1219 R alone on gold
4h, so a hybrid now has two measured baselines to be read against rather than
none. It is run BECAUSE those two are the best of the four, which is a different
question from whether adding things helps.
The fixed 2-ATR stop stays in all four because it is the risk definition the
position size is derived from -- removing it would change R itself and make the
avg_R column incomparable across variants.

CAUSALITY OF THE TRAILING STOP. `Simulator.run` checks stops at step 3 and calls
`on_bar` at step 5, so a stop written during bar i is first honoured on bar i+1.
The trail is therefore computed from closed data and applied to the future, with
no bar ever tested against a stop derived from its own high. The trail also only
ever TIGHTENS: a stop allowed to widen is not a stop, and a bug that let it
would quietly convert losses into larger losses.
MEASURED, ALL SIX, XAUUSD 4h, both eras, net of spread and slippage. Every row
re-run on one engine and span rather than quoted, so the table is internally
comparable:

    exit                 OOS avg R / PF / win% / maxDD    IS avg R / PF / win% / maxDD
    A channel (ships)    +0.2082  1.437  35.9  -13.6      +0.1896  1.349  35.1  -16.5
    B ema                +0.2340  1.599  38.6   -9.5      +0.0227  1.049  35.2  -36.0
    C trail 3ATR         +0.1808  1.418  38.5   -7.3      +0.1159  1.252  37.9  -15.9
    D fixed 2R           +0.0234  1.036  35.6  -18.2      +0.1325  1.211  38.8  -15.8
    E liquidity target   +0.0358  1.183  67.0  -21.0      +0.2121  1.950  67.3  -30.8
    F channel + trail    +0.1663  1.396  38.9   -7.1      +0.0703  1.153  37.2  -22.9

NOTHING BEATS THE CHANNEL IN BOTH ERAS. B and E each win one and lose one; C, D
and F lose both. The baseline is the only arm above +0.18 R twice.

EXIT F IS REFUTED, AND FOR THE REASON THE CLASS DOCSTRING PREDICTED. Because the
trail only ever tightens, F can only exit EARLIER than the channel -- and it
does, overwhelmingly: 204 of 239 exits are the stop against the baseline 137 of
206 on the signal. Cutting losers sooner also cuts winners sooner, and on a rule
whose arithmetic depends on a few trades running for weeks that is the wrong
trade to make. It does buy the lowest drawdown on the board out of sample
(-7.1 R), and gives it straight back in sample (-22.9 R).

EXIT E IS THE INTERESTING FAILURE, and it is a textbook instance of something
this project already wrote down. Price REACHES the next live liquidity level far
more often than not -- the target closed 374 of 473 trades out of sample and 315
of 419 in sample, a 75-79% hit rate -- and the win rate doubles from ~35% to
~67%. That is a real structural fact about levels and it converts to nothing:
out of sample the expectancy is +0.0358 against the baseline +0.2082, an 83%
reduction, because the losers are correspondingly bigger. "Why nothing converts"
in README.md put it exactly: sweeping 36 stop-target combinations, the hit rate
runs 13% to 86% and "every gain in accuracy is bought with a worse payoff, at
almost exactly the rate that cancels it. A high win rate is not an edge."

So the answer to "does a target fail because targets fail, or because 2R was
arbitrary" is: because targets fail. A level-based target is a far better
target -- three quarters of them are reached -- and it still loses to no target
at all. Capping the winners is the thing that costs, not where the cap is put.

E ALSO COSTS DRAWDOWN, which the win rate hides. Net R over max drawdown is 3.15
and 2.65 for the channel, against 0.80 and 2.89 for E: worse out of sample by a
factor of four, and no better in sample despite twice the trades.
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


class DonchianExitLiquidity(_DonchianExit):
    """
    Exit E -- target the nearest live LIQUIDITY LEVEL instead of an R multiple.

    THE QUESTION EXIT D COULD NOT ASK. `donchian_exit_2r` showed a fixed target
    is the worst of the four (+0.0615 R against the channel +0.1799), but 2R is
    a number with no relationship to the chart. This asks whether a target fails
    because targets fail, or because THAT target was arbitrary: price plausibly
    travels to the next shelf of resting orders and stalls there, and if so a
    level-based target should beat an R-based one even though both cap winners.

    THE LEVELS COME FROM js/chart/liquidity.js, dumped per bar by
    tools/liquidity_dump.mjs -- the audited detector rather than a second Python
    copy of it. `levelsAt` returns only levels already born and not yet dead, so
    the target is chosen from what a reader had at the signal bar.

    THE TARGET IS FIXED AT THE SIGNAL, not tracked. A target following the
    nearest level as price moved would exit on the level being reached OR on a
    nearer one appearing -- two mechanisms reported as one number. `no_level`
    counts trades with nothing in the way, which run on the stop alone.
    """

    name = 'donchian_exit_liq'

    def __init__(self, entry=20, exit=10, atr_len=14, atr_mult=2.0,
                 trigger='close', liq_path='data/liq_XAUUSDa_4h.csv'):
        Donchian.__init__(self, entry=entry, exit=exit, atr_len=atr_len,
                          atr_mult=atr_mult, trigger=trigger)
        self.liq_path = liq_path
        self.name = 'donchian_exit_liq'
        self.no_level = 0

    def params(self):
        return {**Donchian.params(self), 'liq_path': self.liq_path}

    def prepare(self, bars):
        import os as _os
        import pandas as pd
        series = Donchian.prepare(self, bars)
        n = len(bars)
        above = np.full(n, np.nan)
        below = np.full(n, np.nan)
        if _os.path.exists(self.liq_path):
            liq = pd.read_csv(self.liq_path)
            # keyed by unix SECONDS; aligned on the bar stamp so a mismatched
            # span cannot silently shift every level by one bar
            liq['ts'] = pd.to_datetime(liq['ts'], unit='s')
            liq = liq.set_index('ts')
            idx = bars.index
            if getattr(idx, 'tz', None) is not None:
                idx = idx.tz_localize(None)
            j = liq.reindex(idx)
            above = j['above_price'].to_numpy(float)
            below = j['below_price'].to_numpy(float)
        series['liq_above'] = above
        series['liq_below'] = below
        return series

    def on_bar(self, view, position):
        if position is not None:
            return None                 # the target and the stop are the engine
        intent = Donchian.on_bar(self, view, None)
        if intent is None or intent.side == FLAT or intent.stop is None:
            return intent
        c = view.close()
        lvl = view.series('liq_above' if intent.side == LONG else 'liq_below')
        # the level must lie IN FRONT of the trade; one behind is not a target
        ahead = (np.isfinite(lvl)
                 and (lvl > c if intent.side == LONG else lvl < c))
        if not ahead:
            self.no_level += 1
            return intent               # stop only, no target
        return Intent(intent.side, stop=intent.stop, target=float(lvl),
                      tag=intent.tag)

    def _exit_now(self, view, position):
        return None


class DonchianExitChannelTrail(_DonchianExit):
    """
    Exit F -- the channel exit AND an ATR trail, whichever comes first.

    The two best exits measured, run together. The channel leaves on a close
    through the opposite band; the trail ratchets the stop up under the
    position. They fail differently -- the channel gives back open profit while
    waiting for a close, the trail is stopped by noise the channel would ride
    through -- so the hybrid is not obviously the sum of either.

    THE TRAIL NEVER WIDENS THE STOP, as in Exit C: `max` for a long, `min` for a
    short. So this can only ever exit EARLIER than the channel alone -- it
    cannot beat the baseline by holding longer, only by cutting losers sooner,
    which is the specific claim under test.
    """

    name = 'donchian_exit_ct'

    def __init__(self, entry=20, exit=10, atr_len=14, atr_mult=2.0,
                 trigger='close', trail_k=3.0):
        Donchian.__init__(self, entry=entry, exit=exit, atr_len=atr_len,
                          atr_mult=atr_mult, trigger=trigger)
        self.trail_k = float(trail_k)
        self.name = 'donchian_exit_ct'

    def params(self):
        return {**Donchian.params(self), 'trail_k': self.trail_k}

    def _exit_now(self, view, position):
        a = view.series('atr')
        held = view.i - position.entry_i + 1
        if np.isfinite(a) and a > 0 and held >= 1:
            if position.side == LONG:
                peak = view.highest(held, back=0)
                want = peak - self.trail_k * a
                if np.isfinite(want) and want > position.stop:
                    position.stop = float(want)
            else:
                trough = view.lowest(held, back=0)
                want = trough + self.trail_k * a
                if np.isfinite(want) and want < position.stop:
                    position.stop = float(want)
        # AND the channel, unchanged from the baseline
        c = view.close()
        if position.side == LONG and c < view.series('exit_lo'):
            return Intent(FLAT, tag='channel_exit')
        if position.side == SHORT and c > view.series('exit_hi'):
            return Intent(FLAT, tag='channel_exit')
        return None

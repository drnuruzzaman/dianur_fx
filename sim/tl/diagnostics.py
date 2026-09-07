"""
diagnostics.py — does a detected line carry information, independent of any
trading rule?

Every result so far measured *strategies* built on lines, which conflates the
detector with entry timing, stops, sizing and costs. This measures the detector
alone, and it is a cleaner question: when price approaches a CONFIRMED line, is
what happens next different from what happens at an arbitrary nearby level?

METHOD

  1. APPROACH — price was more than `far_atr` from the line, and comes within
     `near_atr` of it. Sitting on a line for fifty bars is one approach, not
     fifty, so a line must go away before it can approach again.

  2. OUTCOME — from the approach bar, two barriers `move_atr` either side of the
     line (which slopes, so they are recomputed each bar). Whichever a CLOSE
     crosses first decides:
         support   : up first  -> HOLD,  down first -> BREAK
         resistance: down first -> HOLD,  up first  -> BREAK
     Neither within `horizon` bars -> CHOP.
     Symmetric by construction, so the null is 50/50 between hold and break.

  3. NULLS — a hold rate above 50% proves nothing on its own, because price
     oscillates and the barriers are not hit with equal ease everywhere. Two
     controls:
         placebo  — the same line shifted `placebo_atr` sideways: identical
                    slope, identical approach dynamics, no structural claim.
                    This is the test that matters: is THIS level special, or
                    would any nearby parallel level do?
         random   — the same barrier width applied at random bars around the
                    current price, measuring the unconditional base rate.

This is a measurement, not a strategy: outcomes look forward deliberately. The
LINE, however, must have been CONFIRMED at the approach bar — which is why the
engine freezes its tradeable set per bar (Snapshot.tradeable) rather than
letting this read a final status.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..indicators import atr as atr_series
from ..intrabar import (INTRABAR, PESSIMISTIC, STOP, SubBars, TARGET,
                        UNRESOLVED, first_touch)
from .engine import _break_strength
from .engine import Params, TrendlineEngine
from .lines import Role
from .mtf import TF_MS


@dataclass
class DiagParams:
    near_atr: float = 0.4        # within this of the line counts as an approach
    far_atr: float = 1.5         # must get this far away before approaching again
    move_atr: float = 1.0        # barrier distance either side of the line
    # Symmetric barriers make the null exactly 50/50, which is what the
    # structural test needs. But no one trades a symmetric bracket around a
    # line -- a bounce puts its stop just through the line and its target
    # further away -- and a hold rate measured at one geometry says nothing
    # about expectancy at another. These let the ECONOMIC test measure the
    # probability at the geometry it actually intends to trade. The null is no
    # longer 50/50 once they differ, which is exactly why the placebo arm
    # (same asymmetry, nearby level) is what the comparison rests on.
    stop_atr: float = None       # None -> move_atr
    target_atr: float = None     # None -> move_atr
    # Phases measured. 'approach' is the bounce hypothesis and enters at the
    # line. 'breakout' enters where the break barrier was crossed, and 'retest'
    # waits for price to come back to the broken line first -- neither of which
    # the approach measurement can speak to, because it stops measuring exactly
    # where they begin. HOLD always means the trade's own target came first, so
    # one expectancy calculation serves all three.
    phases: tuple = ('approach', 'breakout', 'retest')
    retest_atr: float = 0.4      # how close a return counts as a retest
    retest_bars: int = 24        # give up waiting for one after this many
    horizon: int = 48            # bars to resolve an outcome
    # HOW a bar is resolved, which is a different question from WHICH events
    # exist -- see _walk. CLOSE is the default because it is what every number
    # already in runs/ was measured with; changing the default would silently
    # restate published results.
    resolution: str = None       # None -> CLOSE
    sub_tf: str = None           # override the sub-timeframe INTRABAR consults
    placebo_atr: float = 1.5     # parallel-line offset for the placebo
    seed: int = 7


HOLD, BREAK, CHOP = 'hold', 'break', 'chop'

#: A fourth resolution mode, and the one this module was written with: barriers
#: are tested against the CLOSE only. It is not in sim/intrabar.MODES because it
#: is not an answer to "which came first inside the bar" -- it declines to look
#: inside the bar at all. A wick straight through the stop that closes back
#: above it leaves a real stop order filled and this mode blind to it, which is
#: precisely the bias the other three exist to bound.
CLOSE = 'close'
RESOLUTIONS = (CLOSE, PESSIMISTIC, INTRABAR)


@dataclass
class _Ctx:
    """
    Everything the barrier walk needs, plus the tally of what it had to guess.

    The counters are on the context rather than returned per call because the
    interesting quantity is a RATE over a whole run: how often the OHLC bar was
    genuinely unable to say, and how often finer data settled it. A resolver
    that never reports its own fallback rate cannot be audited.
    """
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    times: object
    mode: str = CLOSE
    sub: object = None                  # SubBars, or None
    ambiguous: int = 0                  # bars holding BOTH barriers
    resolved_by: dict = None

    def __post_init__(self):
        if self.mode is None:
            self.mode = CLOSE
        if self.mode not in RESOLUTIONS:
            raise ValueError('unknown resolution %r; expected one of %r'
                             % (self.mode, RESOLUTIONS))
        if self.resolved_by is None:
            self.resolved_by = {}

    def _tally(self, how):
        self.resolved_by[how] = self.resolved_by.get(how, 0) + 1


def _walk(ctx, i, horizon, n, target_val, stop_val, direction):
    """
    Walk forward from bar `i` and return (TARGET | STOP | None, bar).

    `target_val`/`stop_val` are callables of the bar index, so a barrier that
    tracks a SLOPING line keeps sloping -- which is the whole difference
    between a trendline barrier and a fixed bracket. `direction` is +1 when the
    target sits above the entry, -1 when below.

    THE MODES DIFFER ONLY ON BARS THAT REACHED BOTH BARRIERS. A bar that
    touched one and not the other is a fact and every mode agrees on it; the
    tie-break is the only judgement call, so it is the only place they may
    disagree. That is what makes them comparable, and it is why `ambiguous` is
    counted: it is the exact upper bound on how much any two modes can differ.

        CLOSE        never looks inside the bar, so it can never be ambiguous
                     -- and can never see a wick through the stop either.
        PESSIMISTIC  gives every tie to the stop. Safe, and the number the
                     gates are quoted at.
        INTRABAR     goes and looks, on the sub-timeframe. Validated against
                     ticks at zero errors (see sim/intrabar.py), and falls back
                     to the stop when the finer bars still cannot tell.

    Returns (None, None) when the horizon expires with neither reached, which
    the caller reads as CHOP.
    """
    end = min(n - 1, i + horizon)
    for j in range(i + 1, end + 1):
        tv, sv = target_val(j), stop_val(j)

        if ctx.mode == CLOSE:
            c = ctx.close[j]
            if (c >= tv) if direction > 0 else (c <= tv):
                ctx._tally(CLOSE)
                return TARGET, j
            if (c <= sv) if direction > 0 else (c >= sv):
                ctx._tally(CLOSE)
                return STOP, j
            continue

        if direction > 0:
            hit_t, hit_s = ctx.high[j] >= tv, ctx.low[j] <= sv
        else:
            hit_t, hit_s = ctx.low[j] <= tv, ctx.high[j] >= sv

        if hit_t and hit_s:
            ctx.ambiguous += 1
            if ctx.mode == INTRABAR and ctx.sub is not None and ctx.sub.available:
                verdict = first_touch(ctx.sub.slice(ctx.times[j]),
                                      direction, sv, tv)
                if verdict != UNRESOLVED:
                    ctx._tally(ctx.sub.sub_tf)
                    return verdict, j
                ctx._tally('fallback')
                return STOP, j
            # PESSIMISTIC, or INTRABAR with nothing finer to consult. Both give
            # the tie to the stop, and both say so, so the two are
            # distinguishable in the tally afterwards.
            ctx._tally('fallback' if ctx.mode == INTRABAR else PESSIMISTIC)
            return STOP, j
        if hit_t:
            ctx._tally(ctx.mode)
            return TARGET, j
        if hit_s:
            ctx._tally(ctx.mode)
            return STOP, j
    return None, None


def _outcome(hit):
    """The walk's answer in this module's vocabulary. HOLD is always the
    favourable barrier, whichever side of the line it happens to sit on."""
    if hit == TARGET:
        return HOLD
    if hit == STOP:
        return BREAK
    return CHOP


def _resolve(ctx, line_val, i, side, move, horizon, n, up=None, down=None):
    """
    First barrier reached, walking forward from an approach at bar `i`.

    `line_val` is a callable giving the line's value at any bar, so a sloping
    line keeps sloping. `side` is +1 for support (price above the line), -1 for
    resistance -- and it doubles as the trade's direction, because a bounce off
    support is long and a bounce off resistance is short.

    `up`/`down` allow the two barriers to sit at different distances; both
    default to `move`, the symmetric case the structural test uses. The HOLD
    barrier is the one `side` points at, so it maps to the walk's TARGET and
    the break barrier to its STOP.
    """
    up = move if up is None else up
    down = move if down is None else down
    hold_at, break_at = (up, down) if side > 0 else (down, up)
    hit, j = _walk(ctx, i, horizon, n,
                   lambda k: line_val(k) + side * hold_at,
                   lambda k: line_val(k) - side * break_at,
                   side)
    return _outcome(hit), j


def _resolve_bracket(ctx, i, direction, target_px, stop_px, horizon, n):
    """
    A fixed bracket around an entry that has already happened, rather than
    barriers tracking a sloping line.

    Used for the phases that begin AFTER the line has been left behind: a
    breakout enters at the break, a retest enters on the way back. From that
    point the line is history and what matters is whether price continues.
    `direction` is +1 when the trade is long, -1 when short.
    """
    hit, j = _walk(ctx, i, horizon, n,
                   lambda k: target_px, lambda k: stop_px, direction)
    return _outcome(hit), j


def run(bars, tf, params: Params = None, dp: DiagParams = None,
        sensitivity=None, symbol=None):
    """
    Returns (events DataFrame, summary DataFrame).

    One row per approach, per arm ('line', 'placebo', 'random'), with the
    outcome, the line's quality at that bar, and the ATR at that bar.

    `symbol` is needed only by `resolution='intrabar'`, which loads the
    sub-timeframe to settle bars that reached both barriers. Without it that
    mode still runs and simply falls back to the stop every time -- which is
    PESSIMISTIC, and the returned `resolved_by` tally says so rather than
    letting a run silently claim a resolution it never performed.

    The events frame carries two diagnostics in `.attrs`:
        ambiguous_bars  bars that reached both barriers -- the exact upper
                        bound on how far any two resolutions can differ
        resolved_by     how each decided bar was settled, by name
    """
    dp = dp or DiagParams()
    eng = TrendlineEngine(tf, TF_MS[tf], params or Params(),
                          record_tradeable=True, sensitivity=sensitivity)
    snaps = eng.walk(bars)

    close = np.asarray(bars['close'], dtype=float)
    high = np.asarray(bars['high'], dtype=float)
    low = np.asarray(bars['low'], dtype=float)
    atr = atr_series(bars, 14)
    n = len(close)
    rng = np.random.default_rng(dp.seed)

    mode = dp.resolution or CLOSE
    sub = None
    if mode == INTRABAR and symbol:
        sub = SubBars(symbol, tf, dp.sub_tf)
    # ONE context for the whole run, so `ambiguous` and `resolved_by` are a
    # rate over every barrier walked rather than per-approach noise.
    ctx = _Ctx(high=high, low=low, close=close, times=bars.index,
               mode=mode, sub=sub)

    # per-line state: has price been far enough away to allow a new approach?
    armed = {}
    # line geometry, so a barrier can be recomputed at any future bar
    geom = {}
    rows = []

    for snap in snaps:
        i = snap.i
        a = atr[i]
        if not np.isfinite(a) or a <= 0:
            continue
        for k_idx, (line_id, role, value, quality, touches) in enumerate(snap.tradeable):
            status = (snap.tradeable_status[k_idx]
                      if k_idx < len(snap.tradeable_status) else '')
            geom.setdefault(line_id, {})[i] = value
            dist = abs(close[i] - value) / a
            was_armed = armed.get(line_id, False)
            if dist >= dp.far_atr:
                armed[line_id] = True
                continue
            if dist > dp.near_atr or not was_armed:
                continue
            armed[line_id] = False        # consumed; must go away again

            side = 1 if role == 'support' else -1
            # the line's slope in price-per-bar, from the two most recent
            # observations of it, so the barrier tracks the line going forward
            seen = geom[line_id]
            ks = sorted(seen)
            slope = 0.0
            if len(ks) > 1:
                slope = (seen[ks[-1]] - seen[ks[0]]) / max(1, ks[-1] - ks[0])
            base = value
            move = dp.move_atr * a
            # HOLD is the favourable outcome, so the target barrier is the one
            # a hold has to reach: above for support, below for resistance.
            tgt = (dp.target_atr if dp.target_atr is not None else dp.move_atr) * a
            stp = (dp.stop_atr if dp.stop_atr is not None else dp.move_atr) * a
            up_move, down_move = (tgt, stp) if side > 0 else (stp, tgt)

            def val_at(j, base=base, slope=slope, i0=i):
                return base + slope * (j - i0)

            # dist_atr is what turns this measurement into an economic one:
            # the approach fires anywhere within near_atr of the line, so entry
            # sits OFF the line by that much, and the two barriers are therefore
            # NOT equidistant from it. A bounce risks (move + dist) to make
            # (move - dist). Recording it is what lets tools/r_conversion.py
            # ask whether a hold-rate edge survives the geometry it is traded on.
            # occurred_at is recorded because a bar INDEX is not reproducible --
            # it shifts the moment the data start date changes.
            # `bar` is overwritten to the break bar on the breakout/retest
            # rows, so the APPROACH bar is carried separately. Without it the
            # two arms cannot be paired for those phases: they break on
            # different bars, and pairing on the break bar silently selects only
            # the cases where they coincide -- which are exactly the cases where
            # the outcome is identical by construction, because the post-break
            # bracket is anchored on the entry price and never references the
            # line again.
            common = dict(status=status, bar=i, approach_bar=i,
                          occurred_at=bars.index[i], dist_atr=dist,
                          line_id=line_id, role=role, quality=quality,
                          touches=touches, atr=a, tf=tf)
            tgt_atr = dp.target_atr if dp.target_atr is not None else dp.move_atr
            stp_atr = dp.stop_atr if dp.stop_atr is not None else dp.move_atr
            off = dp.placebo_atr * a * (1 if rng.random() < 0.5 else -1)

            def arm_val(arm, o=off):
                return val_at if arm == 'line' else (lambda j: val_at(j) + o)

            for arm in ('line', 'placebo'):
                lv = arm_val(arm)
                outcome, j_res = _resolve(ctx, lv, i, side, move,
                                          dp.horizon, n, up_move, down_move)
                if 'approach' in dp.phases:
                    rows.append({**common, 'arm': arm, 'phase': 'approach',
                                 'outcome': outcome})
                if outcome != BREAK or j_res is None:
                    continue

                # --- the break happened; everything below enters AFTER it ---
                bdir = -side                    # support breaks down, resistance up
                ent = close[j_res]
                tp = ent + bdir * tgt_atr * a
                sl = ent - bdir * stp_atr * a
                if 'breakout' in dp.phases:
                    bo, _ = _resolve_bracket(ctx, j_res, bdir, tp, sl,
                                             dp.horizon, n)
                    # How energetic was the candle that actually broke it? The
                    # engine used to record every break identically; this is the
                    # claim that a big committed candle differs from a small
                    # hesitant one, measured at the bar the barrier was crossed.
                    bs = _break_strength(high, low, close, j_res, atr[j_res],
                                         Role.SUPPORT if side > 0 else Role.RESISTANCE)
                    rows.append({**common, 'arm': arm, 'phase': 'breakout',
                                 'bar': j_res, 'occurred_at': bars.index[j_res],
                                 'dist_atr': 0.0, 'outcome': bo,
                                 'break_body_atr': bs['body_atr'],
                                 'break_range_atr': bs['range_atr'],
                                 'break_close_pos': bs['close_pos'],
                                 'break_conviction': bs['conviction']})

                if 'retest' in dp.phases:
                    # price must come back to the broken line before continuing
                    k = None
                    last = min(n - 1, j_res + dp.retest_bars)
                    for m in range(j_res + 1, last + 1):
                        if abs(close[m] - lv(m)) <= dp.retest_atr * a:
                            k = m
                            break
                    if k is not None:
                        ent2 = close[k]
                        tp2 = ent2 + bdir * tgt_atr * a
                        sl2 = ent2 - bdir * stp_atr * a
                        rt, _ = _resolve_bracket(ctx, k, bdir, tp2, sl2,
                                                 dp.horizon, n)
                        rows.append({**common, 'arm': arm, 'phase': 'retest',
                                     'bar': k, 'occurred_at': bars.index[k],
                                     'dist_atr': 0.0, 'outcome': rt})

            # random: same barrier width, arbitrary bar, no line at all
            rj = int(rng.integers(20, max(21, n - dp.horizon - 1)))
            if np.isfinite(atr[rj]) and atr[rj] > 0 and 'approach' in dp.phases:
                rbase = close[rj]
                r_up = (tgt_atr if side > 0 else stp_atr) * atr[rj]
                r_dn = (stp_atr if side > 0 else tgt_atr) * atr[rj]
                r_out, _ = _resolve(ctx, lambda j, b=rbase: b, rj, side,
                                    dp.move_atr * atr[rj], dp.horizon, n,
                                    r_up, r_dn)
                # the random arm fires at an unrelated bar and has no line to
                # be offset from, so its bar/time/distance are its own
                rows.append({**common, 'arm': 'random', 'phase': 'approach',
                             'bar': rj, 'occurred_at': bars.index[rj],
                             'dist_atr': np.nan, 'outcome': r_out})

    ev = pd.DataFrame(rows)
    ev.attrs['resolution'] = mode
    ev.attrs['ambiguous_bars'] = ctx.ambiguous
    ev.attrs['resolved_by'] = dict(ctx.resolved_by)
    ev.attrs['sub_lookups'] = sub.lookups if sub is not None else 0
    return ev, summarise(ev)


def summarise(ev: pd.DataFrame) -> pd.DataFrame:
    """Hold / break / chop rates per arm, with a binomial standard error."""
    if not len(ev):
        return pd.DataFrame()
    out = []
    for arm, g in ev.groupby('arm'):
        decided = g[g['outcome'] != CHOP]
        n = len(decided)
        holds = int((decided['outcome'] == HOLD).sum())
        rate = holds / n if n else np.nan
        se = np.sqrt(rate * (1 - rate) / n) if n else np.nan
        out.append({
            'arm': arm, 'approaches': len(g),
            'decided': n, 'chop%': round(100 * (1 - n / len(g)), 1),
            'hold%': round(100 * rate, 1) if n else np.nan,
            'se%': round(100 * se, 1) if n else np.nan,
        })
    df = pd.DataFrame(out).set_index('arm')
    if 'line' in df.index and 'placebo' in df.index:
        edge = df.loc['line', 'hold%'] - df.loc['placebo', 'hold%']
        se = np.sqrt(df.loc['line', 'se%'] ** 2 + df.loc['placebo', 'se%'] ** 2)
        df.attrs['edge_vs_placebo'] = round(float(edge), 2)
        df.attrs['edge_se'] = round(float(se), 2)
        df.attrs['z'] = round(float(edge / se), 2) if se else np.nan
    return df


def by_quality(ev: pd.DataFrame, bins=(25, 50, 65, 80, 90, 101)) -> pd.DataFrame:
    """
    Does quality_score discriminate? If the hold rate is flat across buckets,
    the score is decoration.
    """
    line = ev[ev['arm'] == 'line'].copy()
    if not len(line):
        return pd.DataFrame()
    line['bucket'] = pd.cut(line['quality'], bins=list(bins), right=False)
    rows = []
    for b, g in line.groupby('bucket', observed=True):
        decided = g[g['outcome'] != CHOP]
        n = len(decided)
        holds = int((decided['outcome'] == HOLD).sum())
        rows.append({'quality': str(b), 'approaches': len(g), 'decided': n,
                     'hold%': round(100 * holds / n, 1) if n else np.nan})
    return pd.DataFrame(rows)


def by_touches(ev: pd.DataFrame) -> pd.DataFrame:
    """Same question for retest count, which is what quality mostly encodes."""
    line = ev[ev['arm'] == 'line'].copy()
    if not len(line):
        return pd.DataFrame()
    line['t'] = line['touches'].clip(upper=8)
    rows = []
    for t, g in line.groupby('t'):
        decided = g[g['outcome'] != CHOP]
        n = len(decided)
        rows.append({'touches': int(t), 'approaches': len(g), 'decided': n,
                     'hold%': round(100 * (decided['outcome'] == HOLD).sum() / n, 1)
                     if n else np.nan})
    return pd.DataFrame(rows)

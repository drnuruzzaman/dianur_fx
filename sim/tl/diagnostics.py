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
                    slope, no structural claim. This is meant to be the test
                    that matters: is THIS level special, or would any nearby
                    parallel level do?

                    *** KNOWN DEFECT -- READ BEFORE TRUSTING ANY placebo NUMBER
                    IN THIS REPO, INCLUDING runs/tl_placebo_summary.csv ***

                    The placebo does NOT have identical approach dynamics, and
                    the claim that it does was wrong. An approach fires when
                    price comes within `near_atr` (0.4) of the LINE. The
                    placebo sits `placebo_atr` (1.5) away from that line, so at
                    the entry bar price is ~1.5 ATR from the placebo -- further
                    than either of the placebo's own barriers, which are placed
                    at stop/target distances of ~1 ATR around the PLACEBO's
                    level, not around price.

                    Price therefore starts BEYOND one of the placebo's two
                    barriers 100% of the time, and the placebo resolves on the
                    first bar 100% of the time (measured on EURUSD 4h, 1452
                    approaches, against 42% for the line). Which barrier it
                    starts beyond is decided by the sign of `off`, a coin flip.

                    So the placebo arm is not a nearby level being tested. It is
                    a fair coin, resolved immediately, and its hold rate sits
                    near 50% for every instrument, timeframe and geometry --
                    which is exactly the pattern in tl_placebo_summary.csv, and
                    was read there as "a placebo holds about half the time"
                    rather than as the symptom it is.

                    Consequences: `edge_vs_placebo` measures a real level
                    against a coin flip, not against an alternative level, and
                    every downstream comparison inherits that -- the structural
                    gate, the r_conversion grids, and the paired test added on
                    top of them. The paired statistic is computed correctly; the
                    quantity it differences against is not a control.

                    Fixing it is a design choice, not a bug fix, because the
                    obvious repair breaks something else. Detecting approaches
                    to the SHIFTED line independently gives a real control with
                    matched dynamics, but those approaches happen at different
                    bars, so the arms no longer pair and the paired test has to
                    go. A time-shifted control (same line, wrong era) keeps the
                    geometry honest and is what the project's own plan calls
                    for at gate 8B. Anchoring both arms' barriers to the entry
                    PRICE keeps pairing but makes the placebo's level
                    irrelevant, which is what the `random` arm already does.
         random   — the same barrier width applied at random bars around the
                    current price, measuring the unconditional base rate.

This is a measurement, not a strategy: outcomes look forward deliberately. The
LINE, however, must have been CONFIRMED at the approach bar — which is why the
engine freezes its tradeable set per bar (Snapshot.tradeable) rather than
letting this read a final status.
"""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..indicators import atr as atr_series
from ..intrabar import INTRABAR, PESSIMISTIC, STOP, TARGET, SubBars
from ..intrabar import resolve as ib_resolve
from .engine import Params, TrendlineEngine
from .mtf import TF_MS

# A fourth resolution mode, on top of sim/intrabar.py's three. CLOSE asks only
# where the bar CLOSED, which is what this module measured originally: it can
# never see a barrier that was touched and given back, so a stop that price
# spiked through and recovered from does not count. That is the right basis for
# a question about where price ENDS UP, and the wrong one for a question about
# what a bracket order would have PAID -- a real stop fills on the touch.
CLOSE = 'close'
RESOLUTIONS = (CLOSE, PESSIMISTIC, INTRABAR)


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
    placebo_atr: float = 1.5     # parallel-line offset for the placebo
    seed: int = 7
    # How a bar that reached BOTH barriers is settled. CLOSE keeps the original
    # measurement (closes only, so such a bar cannot arise); PESSIMISTIC gives
    # it to the stop; INTRABAR goes and looks at sub-bars and only falls back to
    # the stop when even those cannot tell. The economic gate must not use
    # CLOSE: it prices a bracket order, and a bracket fills on the touch.
    resolution: str = CLOSE
    sub_tf: str = None           # None -> intrabar.DEFAULT_SUB_TF for this tf


HOLD, BREAK, CHOP = 'hold', 'break', 'chop'


@dataclass
class _Ctx:
    """Everything the barrier walk needs that does not change per event."""
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    times: pd.Index
    mode: str = CLOSE
    sub: object = None
    ambiguous: int = 0                      # bars that reached both barriers
    resolved_by: dict = field(default_factory=dict)


def _hits(ctx, j, tgt_px, stop_px, direction):
    """Did bar j reach the target, the stop, both, or neither?"""
    if ctx.mode == CLOSE:
        c = ctx.close[j]
        return ((c >= tgt_px, c <= stop_px) if direction > 0
                else (c <= tgt_px, c >= stop_px))
    hi, lo = ctx.high[j], ctx.low[j]
    return ((hi >= tgt_px, lo <= stop_px) if direction > 0
            else (lo <= tgt_px, hi >= stop_px))


def _walk(ctx, i, horizon, n, tgt_at, stop_at, direction):
    """
    Walk forward from bar i+1 to whichever barrier is reached first.

    `tgt_at(j)` and `stop_at(j)` return the two barrier prices at bar j, so a
    barrier that tracks a sloping line and one that is fixed are the same walk.
    `direction` is +1 when the target sits above the entry, -1 when below.

    Returns (TARGET | STOP | None, j). None means neither inside the horizon.

    The barrier prices are taken at the bar's own index, so a sloping line is
    held constant WITHIN a bar. Over one bar the line moves a small fraction of
    a tolerance, and the alternative -- interpolating a level across sub-bars --
    would add precision the line's own two-pivot fit does not have.
    """
    end = min(n - 1, i + horizon)
    for j in range(i + 1, end + 1):
        tp, sp = tgt_at(j), stop_at(j)
        hit_t, hit_s = _hits(ctx, j, tp, sp, direction)
        if hit_t and hit_s:
            # The bar holds both. This is the whole reason the module exists:
            # guessing here is how a backtest flatters itself.
            verdict, how = ib_resolve(ctx.mode, ctx.sub, ctx.times[j],
                                      direction, sp, tp)
            ctx.ambiguous += 1
            ctx.resolved_by[how] = ctx.resolved_by.get(how, 0) + 1
            return verdict, j
        if hit_t:
            return TARGET, j
        if hit_s:
            return STOP, j
    return None, None


def _outcome(hit):
    """
    TARGET/STOP/None -> the diagnostic vocabulary.

    Every phase orients its barriers so that the TARGET is the favourable one:
    the bounce holding, or the break continuing. One mapping therefore serves
    all three, and one expectancy calculation prices all three.
    """
    return CHOP if hit is None else (HOLD if hit == TARGET else BREAK)


def run(bars, tf, params: Params = None, dp: DiagParams = None, symbol=None):
    """
    Returns (events DataFrame, summary DataFrame).

    One row per approach, per arm ('line', 'placebo', 'random'), with the
    outcome, the line's quality at that bar, and the ATR at that bar.

    `symbol` is needed only for dp.resolution == INTRABAR, which loads sub-bars
    to settle bars that reached both barriers. Without it the walk degrades to
    PESSIMISTIC on those bars and says so in ev.attrs.
    """
    dp = dp or DiagParams()
    if dp.resolution not in RESOLUTIONS:
        raise ValueError('resolution must be one of %s' % (RESOLUTIONS,))
    eng = TrendlineEngine(tf, TF_MS[tf], params or Params(), record_tradeable=True)
    snaps = eng.walk(bars)

    close = np.asarray(bars['close'], dtype=float)
    atr = atr_series(bars, 14)
    n = len(close)
    rng = np.random.default_rng(dp.seed)

    ctx = _Ctx(high=np.asarray(bars['high'], dtype=float),
               low=np.asarray(bars['low'], dtype=float),
               close=close, times=bars.index, mode=dp.resolution,
               sub=(SubBars(symbol, tf, dp.sub_tf)
                    if dp.resolution == INTRABAR and symbol else None))

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
        for line_id, role, value, quality, touches in snap.tradeable:
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
            # HOLD is the favourable outcome, so the target barrier is the one
            # a hold has to reach: above for support, below for resistance.
            # `side` carries that orientation into the walk, which is why one
            # pair of closures covers both roles.
            tgt = (dp.target_atr if dp.target_atr is not None else dp.move_atr) * a
            stp = (dp.stop_atr if dp.stop_atr is not None else dp.move_atr) * a

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
            # approach_bar is the PAIRING key. `bar` is overwritten by the
            # breakout and retest rows with the bar they actually entered on,
            # which differs between the line and placebo arms -- so it cannot
            # join the two arms of one approach. This can, and that join is
            # what makes a paired test possible.
            common = dict(bar=i, approach_bar=i, occurred_at=bars.index[i],
                          dist_atr=dist, line_id=line_id, role=role,
                          quality=quality, touches=touches, atr=a, tf=tf)
            tgt_atr = dp.target_atr if dp.target_atr is not None else dp.move_atr
            stp_atr = dp.stop_atr if dp.stop_atr is not None else dp.move_atr
            off = dp.placebo_atr * a * (1 if rng.random() < 0.5 else -1)

            def arm_val(arm, o=off):
                return val_at if arm == 'line' else (lambda j: val_at(j) + o)

            for arm in ('line', 'placebo'):
                lv = arm_val(arm)
                hit, j_res = _walk(ctx, i, dp.horizon, n,
                                   lambda j, f=lv: f(j) + side * tgt,
                                   lambda j, f=lv: f(j) - side * stp, side)
                outcome = _outcome(hit)
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
                    bo = _outcome(_walk(ctx, j_res, dp.horizon, n,
                                        lambda j, v=tp: v, lambda j, v=sl: v,
                                        bdir)[0])
                    rows.append({**common, 'arm': arm, 'phase': 'breakout',
                                 'bar': j_res, 'occurred_at': bars.index[j_res],
                                 'dist_atr': 0.0, 'outcome': bo})

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
                        rt = _outcome(_walk(ctx, k, dp.horizon, n,
                                            lambda j, v=tp2: v,
                                            lambda j, v=sl2: v, bdir)[0])
                        rows.append({**common, 'arm': arm, 'phase': 'retest',
                                     'bar': k, 'occurred_at': bars.index[k],
                                     'dist_atr': 0.0, 'outcome': rt})

            # random: same barrier width, arbitrary bar, no line at all
            rj = int(rng.integers(20, max(21, n - dp.horizon - 1)))
            if np.isfinite(atr[rj]) and atr[rj] > 0 and 'approach' in dp.phases:
                rbase = close[rj]
                r_tgt = rbase + side * tgt_atr * atr[rj]
                r_stp = rbase - side * stp_atr * atr[rj]
                r_out = _outcome(_walk(ctx, rj, dp.horizon, n,
                                       lambda j, v=r_tgt: v,
                                       lambda j, v=r_stp: v, side)[0])
                # the random arm fires at an unrelated bar and has no line to
                # be offset from, so its bar/time/distance are its own
                rows.append({**common, 'arm': 'random', 'phase': 'approach',
                             'bar': rj, 'occurred_at': bars.index[rj],
                             'dist_atr': np.nan, 'outcome': r_out})

    ev = pd.DataFrame(rows)
    # How often finer data was actually needed, and how often it answered. A
    # high fallback rate means the run is closer to PESSIMISTIC than it claims.
    ev.attrs['resolution'] = dp.resolution
    ev.attrs['ambiguous_bars'] = ctx.ambiguous
    ev.attrs['resolved_by'] = dict(ctx.resolved_by)
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

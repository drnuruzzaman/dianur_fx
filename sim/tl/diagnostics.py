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
from .engine import Params, TrendlineEngine
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
    placebo_atr: float = 1.5     # parallel-line offset for the placebo
    seed: int = 7


HOLD, BREAK, CHOP = 'hold', 'break', 'chop'


def _resolve(bars_c, line_val, i, side, move, horizon, n, up=None, down=None):
    """
    First barrier touched by a CLOSE, walking forward. `line_val` is a callable
    giving the line's value at any bar, so a sloping line keeps sloping.
    `side` is +1 for support (price above), -1 for resistance.

    `up`/`down` allow the two barriers to sit at different distances; both
    default to `move`, which is the symmetric case the structural test uses.
    """
    up = move if up is None else up
    down = move if down is None else down
    end = min(n - 1, i + horizon)
    for j in range(i + 1, end + 1):
        v = line_val(j)
        if bars_c[j] >= v + up:
            return (HOLD if side > 0 else BREAK), j
        if bars_c[j] <= v - down:
            return (BREAK if side > 0 else HOLD), j
    return CHOP, None


def _resolve_bracket(bars_c, i, direction, target_px, stop_px, horizon, n):
    """
    A fixed bracket around an entry that has already happened, rather than
    barriers tracking a sloping line.

    Used for the phases that begin AFTER the line has been left behind: a
    breakout enters at the break, a retest enters on the way back. From that
    point the line is history and what matters is whether price continues.
    `direction` is +1 when the trade is long, -1 when short.
    """
    end = min(n - 1, i + horizon)
    for j in range(i + 1, end + 1):
        c = bars_c[j]
        if direction > 0:
            if c >= target_px:
                return HOLD, j
            if c <= stop_px:
                return BREAK, j
        else:
            if c <= target_px:
                return HOLD, j
            if c >= stop_px:
                return BREAK, j
    return CHOP, None


def run(bars, tf, params: Params = None, dp: DiagParams = None, instrument=''):
    """
    Returns (events DataFrame, summary DataFrame).

    One row per approach, per arm ('line', 'placebo', 'random'), with the
    outcome, the line's quality at that bar, and the ATR at that bar.

    Rows carry everything needed to REPRODUCE the gate decision and nothing
    that would let a reader skip reproducing it: the instrument, the timeframe,
    the tolerance the engine ran at, the line's id, both clocks, and the bar the
    outcome resolved on. A summary is a claim; this is the evidence.
    """
    dp = dp or DiagParams()
    params = params or Params()
    eng = TrendlineEngine(tf, TF_MS[tf], params, record_tradeable=True)
    snaps = eng.walk(bars)

    close = np.asarray(bars['close'], dtype=float)
    atr = atr_series(bars, 14)
    n = len(close)
    step = pd.Timedelta(milliseconds=TF_MS[tf])
    rng = np.random.default_rng(dp.seed)

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
            # known_at is the bar CLOSE: the approach is decided on the close
            # of bar i, so that is the first moment anything could have acted
            # on it. Carrying only occurred_at is how a diagnostic quietly
            # becomes a claim about information you did not have yet.
            # `bar` is overwritten by the later phases (a breakout row is
            # stamped with the break bar, not the approach bar), so it cannot
            # identify a pair. approach_bar never moves: it is the identity of
            # the approach that produced BOTH arms, which is the only key that
            # keeps a line and its placebo on the same row.
            common = dict(instrument=instrument, tf=tf, bar=i, approach_bar=i,
                          occurred_at=bars.index[i],
                          known_at=bars.index[i] + step,
                          dist_atr=dist, line_id=line_id, role=role,
                          quality=quality, touches=touches, atr=a,
                          tol_atr=params.tol_atr,
                          move_atr=dp.move_atr, horizon=dp.horizon,
                          near_atr=dp.near_atr, far_atr=dp.far_atr,
                          placebo_atr=dp.placebo_atr, seed=dp.seed)
            tgt_atr = dp.target_atr if dp.target_atr is not None else dp.move_atr
            stp_atr = dp.stop_atr if dp.stop_atr is not None else dp.move_atr
            off = dp.placebo_atr * a * (1 if rng.random() < 0.5 else -1)

            def arm_val(arm, o=off):
                return val_at if arm == 'line' else (lambda j: val_at(j) + o)

            for arm in ('line', 'placebo'):
                lv = arm_val(arm)
                outcome, j_res = _resolve(close, lv, i, side, move,
                                          dp.horizon, n, up_move, down_move)
                if 'approach' in dp.phases:
                    rows.append({**common, 'arm': arm, 'phase': 'approach',
                                 'outcome': outcome,
                                 'resolved_bar': j_res,
                                 'resolved_at': bars.index[j_res]
                                 if j_res is not None else pd.NaT})
                if outcome != BREAK or j_res is None:
                    continue

                # --- the break happened; everything below enters AFTER it ---
                bdir = -side                    # support breaks down, resistance up
                ent = close[j_res]
                tp = ent + bdir * tgt_atr * a
                sl = ent - bdir * stp_atr * a
                if 'breakout' in dp.phases:
                    bo, j_bo = _resolve_bracket(close, j_res, bdir, tp, sl,
                                                dp.horizon, n)
                    rows.append({**common, 'arm': arm, 'phase': 'breakout',
                                 'bar': j_res, 'occurred_at': bars.index[j_res],
                                 'known_at': bars.index[j_res] + step,
                                 'dist_atr': 0.0, 'outcome': bo,
                                 'resolved_bar': j_bo,
                                 'resolved_at': bars.index[j_bo]
                                 if j_bo is not None else pd.NaT})

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
                        rt, j_rt = _resolve_bracket(close, k, bdir, tp2, sl2,
                                                     dp.horizon, n)
                        rows.append({**common, 'arm': arm, 'phase': 'retest',
                                     'bar': k, 'occurred_at': bars.index[k],
                                     'known_at': bars.index[k] + step,
                                     'dist_atr': 0.0, 'outcome': rt,
                                     'resolved_bar': j_rt,
                                     'resolved_at': bars.index[j_rt]
                                     if j_rt is not None else pd.NaT})

            # random: same barrier width, arbitrary bar, no line at all
            rj = int(rng.integers(20, max(21, n - dp.horizon - 1)))
            if np.isfinite(atr[rj]) and atr[rj] > 0 and 'approach' in dp.phases:
                rbase = close[rj]
                r_up = (tgt_atr if side > 0 else stp_atr) * atr[rj]
                r_dn = (stp_atr if side > 0 else tgt_atr) * atr[rj]
                r_out, _ = _resolve(close, lambda j, b=rbase: b, rj, side,
                                    dp.move_atr * atr[rj], dp.horizon, n,
                                    r_up, r_dn)
                # the random arm fires at an unrelated bar and has no line to
                # be offset from, so its bar/time/distance are its own
                rows.append({**common, 'arm': 'random', 'phase': 'approach',
                             'bar': rj, 'occurred_at': bars.index[rj],
                             'known_at': bars.index[rj] + step,
                             'dist_atr': np.nan, 'outcome': r_out,
                             'resolved_bar': None, 'resolved_at': pd.NaT})

    ev = pd.DataFrame(rows)
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


#: the columns a persisted diagnostic row must carry. The rule is simple: from
#: this file alone, and without rerunning anything, a reader must be able to
#: recompute the gate decision and see which approach produced which half of it.
#: A summary CSV cannot be audited, only believed.
PAIRED_SCHEMA = (
    'instrument', 'timeframe', 'trendline_id', 'tolerance', 'phase',
    'occurred_at', 'known_at', 'resolved_at', 'distance_atr',
    'real_result', 'placebo_result', 'is_paired', 'random_hold_rate',
    'effect_pp', 'effect_se_pp', 'z_score', 'n_real', 'n_placebo', 'n_pairs',
    # geometry, so a row can be reproduced without guessing the DiagParams
    'role', 'quality', 'touches', 'atr', 'bar',
    'move_atr', 'near_atr', 'far_atr', 'placebo_atr', 'horizon', 'seed',
)

#: only the approach phase is a true paired comparison. See `paired()`.
PAIRED_PHASES = ('approach',)


def _pivot(df, keys, value):
    if value not in df.columns:
        return pd.DataFrame(index=pd.MultiIndex.from_arrays(
            [[]] * len(keys), names=keys))
    return df.pivot_table(index=keys, columns='arm', values=value,
                          aggfunc='first')


def paired(ev: pd.DataFrame) -> pd.DataFrame:
    """
    One row per approach, with the real line's outcome and its placebo's beside
    it, and the cell's effect stamped on every row.

    WHY THE PAIRING IS CONDITIONAL

    In the APPROACH phase the two arms are genuinely paired: the same approach
    is measured at the real level and at a level 1.5 ATR away, so every approach
    yields exactly one outcome per arm and the honest unit is the pair.

    The BREAKOUT and RETEST phases are NOT paired and are not stored as if they
    were. Those phases only exist for an arm whose approach ended in a break —
    and the line can hold where its placebo breaks. The two arms are therefore
    conditioned on different subsets of approaches, so putting them on one row
    would invent a correspondence that does not exist. Those rows carry one
    side, `is_paired` is False, and the effect is computed with the UNPAIRED
    two-proportion error, which is wider and correctly so.

    Getting this wrong is not cosmetic: McNemar's error uses only the
    discordant pairs and is much tighter than the unpaired one. Applying it to
    arms that were never paired manufactures significance out of bookkeeping.
    """
    if not len(ev):
        return pd.DataFrame(columns=list(PAIRED_SCHEMA))

    keys = ['instrument', 'tf', 'line_id', 'tol_atr', 'phase', 'approach_bar']
    # The random arm fires at an unrelated bar against no line at all, so it is
    # not the other half of any pair: it is an unconditional base rate, carried
    # per cell rather than per row.
    arms = ev[ev.arm.isin(['line', 'placebo'])]
    wide = _pivot(arms, keys, 'outcome')
    for arm in ('line', 'placebo'):
        if arm not in wide.columns:
            wide[arm] = None

    # Per-arm timing: in the post-break phases the two arms broke on different
    # bars, so there is no single 'when'. Prefer the real line's own bar and
    # fall back to the placebo's, which is the arm that produced the row.
    def coalesced(value):
        pv = _pivot(arms, keys, value)
        if 'line' not in pv.columns:
            return pv.get('placebo')
        if 'placebo' not in pv.columns:
            return pv['line']
        return pv['line'].where(pv['line'].notna(), pv['placebo'])

    timing = {v: coalesced(v) for v in
              ('occurred_at', 'known_at', 'resolved_at', 'bar')}
    # line properties, identical across arms by construction
    meta = (ev[ev.arm == 'line'].drop_duplicates(subset=keys).set_index(keys))
    static = [c for c in ('dist_atr', 'role', 'quality', 'touches', 'atr',
                          'move_atr', 'near_atr', 'far_atr', 'placebo_atr',
                          'horizon', 'seed') if c in meta.columns]

    out = pd.DataFrame(index=wide.index)
    for v, col in timing.items():
        out[v] = col
    for c in static:
        out[c] = meta[c]
    out['line'] = wide['line']
    out['placebo'] = wide['placebo']
    out = out.reset_index()

    # cell-level unconditional base rate from the random arm
    rnd = ev[ev.arm == 'random']
    base = {}
    if len(rnd):
        d = rnd[rnd.outcome != CHOP]
        if len(d):
            base = (d.assign(h=(d.outcome == HOLD))
                     .groupby(['instrument', 'tf', 'tol_atr', 'phase'])['h']
                     .mean().to_dict())

    res = pd.DataFrame({
        'instrument': out['instrument'], 'timeframe': out['tf'],
        'trendline_id': out['line_id'], 'tolerance': out['tol_atr'],
        'phase': out['phase'],
        'occurred_at': out['occurred_at'], 'known_at': out['known_at'],
        'resolved_at': out['resolved_at'], 'distance_atr': out.get('dist_atr'),
        'real_result': out['line'], 'placebo_result': out['placebo'],
        'is_paired': out['phase'].isin(PAIRED_PHASES),
        'random_hold_rate': [
            round(100 * base[k], 2) if k in base else np.nan
            for k in zip(out['instrument'], out['tf'], out['tol_atr'],
                         out['phase'])],
        'role': out.get('role'), 'quality': out.get('quality'),
        'touches': out.get('touches'), 'atr': out.get('atr'),
        'bar': out['bar'], 'move_atr': out.get('move_atr'),
        'near_atr': out.get('near_atr'), 'far_atr': out.get('far_atr'),
        'placebo_atr': out.get('placebo_atr'), 'horizon': out.get('horizon'),
        'seed': out.get('seed'),
    })

    for c in ('effect_pp', 'effect_se_pp', 'z_score'):
        res[c] = np.nan
    for c in ('n_real', 'n_placebo', 'n_pairs'):
        res[c] = 0

    cell_keys = ['instrument', 'timeframe', 'tolerance', 'phase']
    for cell, g in res.groupby(cell_keys, dropna=False):
        real = g.real_result[g.real_result.notna() & (g.real_result != CHOP)]
        plac = g.placebo_result[g.placebo_result.notna()
                                & (g.placebo_result != CHOP)]
        n_r, n_p = len(real), len(plac)
        if not n_r or not n_p:
            continue
        r = float((real == HOLD).mean())
        p = float((plac == HOLD).mean())

        both = g[(g.real_result.notna()) & (g.placebo_result.notna())
                 & (g.real_result != CHOP) & (g.placebo_result != CHOP)]
        is_paired = bool(cell[cell_keys.index('phase')] in PAIRED_PHASES)
        n_pairs = len(both) if is_paired else 0
        if is_paired and n_pairs:
            # McNemar: only the DISCORDANT pairs -- the approaches where the
            # line and its placebo disagreed -- say anything about a difference
            # measured on the same events.
            r = float((both.real_result == HOLD).mean())
            p = float((both.placebo_result == HOLD).mean())
            b = int(((both.real_result == HOLD)
                     & (both.placebo_result != HOLD)).sum())
            c_ = int(((both.real_result != HOLD)
                      & (both.placebo_result == HOLD)).sum())
            se = np.sqrt(b + c_) / n_pairs if (b + c_) else np.nan
        else:
            se = np.sqrt(r * (1 - r) / n_r + p * (1 - p) / n_p)

        m = np.ones(len(res), bool)
        for k, v in zip(cell_keys, cell):
            m &= (res[k] == v)
        res.loc[m, 'effect_pp'] = round(100 * (r - p), 3)
        res.loc[m, 'effect_se_pp'] = round(100 * se, 3) if se == se else np.nan
        res.loc[m, 'z_score'] = (round(float((r - p) / se), 3)
                                 if se == se and se > 0 else np.nan)
        res.loc[m, ['n_real', 'n_placebo', 'n_pairs']] = [n_r, n_p, n_pairs]
    return res[list(PAIRED_SCHEMA)]

"""
struct_diagnostics.py — the placebo test, applied to zones and channel rails.

diagnostics.py asked whether a confirmed TRENDLINE is a special place to arrive
at. The answer, across three eras, was no. Channels and zones were then built
because they are what an annotated chart actually shows -- but "useful to look
at" and "predictive" are different claims, and only one of them has been tested.
This tests the other.

SAME METHOD, DELIBERATELY. Approach, symmetric barriers, parallel placebo. Using
a different measurement for the new objects would make the results
incomparable, and the whole point is to be able to say whether a zone is better
or worse than a line at the same question.

WHAT COUNTS AS AN APPROACH

  ZONE     price was more than `far_atr` from the band, then comes within
           `near_atr` of the EDGE it is approaching. The nearer edge is the one
           that matters: a zone is a region, and the side you arrive at is the
           side that has to hold.

  CHANNEL  the same, against each RAIL separately. A rail is a sloping level,
           so this is the closest analogue to the line test -- and the point of
           running it is that a rail belonging to a corridor is a different
           claim from a lone trendline, even when the geometry is identical.

THE PLACEBO is the same object shifted `placebo_atr` sideways in price: for a
zone, a band of identical width at a nearby price; for a rail, a parallel level.
Identical shape, identical approach dynamics, no structural claim. If the real
object holds no more often than that, the structure is decoration.

CAUSALITY. Objects are re-detected every `refresh` bars from bars <= that bar,
and only used for approaches AFTER the bar they were detected on. Outcomes look
forward, deliberately -- this is a measurement, not a strategy.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..indicators import atr as atr_series
from .channels import ChannelParams
from .channels import detect as detect_channels
from .diagnostics import BREAK, CHOP, HOLD, _resolve
from .engine import Params, TrendlineEngine
from .mtf import TF_MS
from .fvg import FVGParams
from .fvg import detect as detect_fvg
from .supply_demand import SDParams
from .supply_demand import detect as detect_sd
from .zones import ZoneParams
from .zones import detect as detect_zones


@dataclass
class StructDiagParams:
    near_atr: float = 0.4
    far_atr: float = 1.5
    move_atr: float = 1.0
    # Symmetric barriers make the null exactly 50/50, which is what the
    # STRUCTURAL test needs. Nobody trades a symmetric bracket around a level
    # though, and a hold rate measured at one geometry says nothing about
    # expectancy at another. These let the ECONOMIC test re-measure the
    # probability at the geometry it actually intends to trade. The null stops
    # being 50/50 once they differ, which is exactly why the placebo arm, given
    # the SAME asymmetry, is what the comparison rests on.
    stop_atr: float = None        # None -> move_atr
    target_atr: float = None      # None -> move_atr
    horizon: int = 48
    placebo_atr: float = 1.5
    # Re-detecting on every bar means re-running find_pivots (zones) and a full
    # engine walk (channels) thousands of times. Detection is causal at the bar
    # it runs on, and objects only serve approaches AFTER that bar, so refreshing
    # periodically costs freshness, never correctness.
    refresh: int = 25
    seed: int = 7


def _approach_rows(kind, obj_id, level_fn, side, meta, close, atr, i, n, dp, rng):
    """
    One approach, resolved for both arms.

    `level_fn(j)` gives the level's price at any bar, so a sloping channel rail
    keeps sloping while the barriers track it. `side` is +1 when the level is
    expected to hold from above (support-like) and -1 from below.
    """
    a = atr[i]
    move = dp.move_atr * a
    tgt = (dp.target_atr if dp.target_atr is not None else dp.move_atr) * a
    stp = (dp.stop_atr if dp.stop_atr is not None else dp.move_atr) * a
    # HOLD is the favourable outcome, so the target barrier is the one a hold
    # has to reach: above for a support-like level, below for resistance-like.
    up_move, down_move = (tgt, stp) if side > 0 else (stp, tgt)
    off = dp.placebo_atr * a * (1 if rng.random() < 0.5 else -1)
    rows = []
    for arm in ('structure', 'placebo'):
        lv = level_fn if arm == 'structure' else (lambda j: level_fn(j) + off)
        outcome, _ = _resolve(close, lv, i, side, move, dp.horizon, n,
                              up_move, down_move)
        rows.append({**meta, 'kind': kind, 'obj_id': obj_id, 'bar': i,
                     'arm': arm, 'outcome': outcome})
    return rows


def run_zones(bars, tf, zp: ZoneParams = None, dp: StructDiagParams = None):
    """Does price respect a detected zone more than a nearby band would?"""
    zp = zp or ZoneParams()
    dp = dp or StructDiagParams()
    close = np.asarray(bars['close'], dtype=float)
    atr = atr_series(bars, 14)
    n = len(close)
    rng = np.random.default_rng(dp.seed)
    times = np.asarray(bars.index.astype('int64') // 1_000_000)

    rows = []
    armed = {}
    live = []
    for i in range(60, n):
        a = atr[i]
        if not np.isfinite(a) or a <= 0:
            continue
        if (i - 60) % dp.refresh == 0:
            live = detect_zones(bars, i, tf, atr, zp, times=times)
        for z in live:
            # the edge price is arriving at decides the direction the zone has
            # to defend; inside the band there is nothing to approach
            if close[i] < z.low:
                edge, side = z.low, -1          # resistance from below
            elif close[i] > z.high:
                edge, side = z.high, 1          # support from above
            else:
                armed[z.id] = False
                continue
            dist = abs(close[i] - edge) / a
            if dist >= dp.far_atr:
                armed[z.id] = True
                continue
            if dist > dp.near_atr or not armed.get(z.id, False):
                continue
            armed[z.id] = False
            meta = {'occurred_at': bars.index[i], 'tf': tf, 'atr': a,
                    'strength': z.strength, 'touches': z.touches,
                    'width_atr': z.width_atr, 'dist_atr': dist}
            rows.extend(_approach_rows('zone', z.id, lambda j, e=edge: e,
                                       side, meta, close, atr, i, n, dp, rng))
    return pd.DataFrame(rows)


def run_channels(bars, tf, params: Params = None, cp: ChannelParams = None,
                 dp: StructDiagParams = None):
    """Does price respect a channel RAIL more than a parallel level would?"""
    cp = cp or ChannelParams()
    dp = dp or StructDiagParams()
    close = np.asarray(bars['close'], dtype=float)
    atr = atr_series(bars, 14)
    n = len(close)
    rng = np.random.default_rng(dp.seed)
    times = np.asarray(bars.index.astype('int64') // 1_000_000)

    rows = []
    armed = {}
    state = {'live': []}

    def on_bar(i, snap, lines):
        """
        Detect DURING the walk. `snap.live` cannot be filtered on
        `is_tradeable` after the fact -- the objects keep mutating and almost
        all of them end BROKEN, which silently reduced this measurement to
        eleven channels across eleven years.
        """
        a = atr[i]
        if not np.isfinite(a) or a <= 0 or i < 60:
            return
        if (i - 60) % dp.refresh == 0:
            state['live'] = detect_channels(lines, bars, atr, i, tf, cp,
                                            times=times) if lines else []
        for ch in state['live']:
            for which, side in (('lower', 1), ('upper', -1)):
                rail = ch.lower if which == 'lower' else ch.upper
                # The RAIL's line id is stable across refreshes; ch.id encodes
                # the overlap window and changes every time, which reset the
                # armed state before price could complete an excursion.
                key = rail.id
                v = rail.value_at(int(times[i]))
                if not np.isfinite(v):
                    continue
                dist = abs(close[i] - v) / a
                # A channel is a corridor price stays INSIDE, so "went away and
                # came back" means crossing the MIDDLE, not travelling 1.5 ATR
                # clear of a rail it may never be that far from.
                pos = ch.position_at(int(times[i]), close[i])
                if np.isfinite(pos) and abs(pos - 0.5) <= 0.15:
                    armed[key] = True
                    continue
                if dist > dp.near_atr or not armed.get(key, False):
                    continue
                armed[key] = False
                meta = {'occurred_at': bars.index[i], 'tf': tf, 'atr': a,
                        'strength': ch.quality_score,
                        'touches': (ch.touches_lower if which == 'lower'
                                    else ch.touches_upper),
                        'width_atr': ch.width_atr, 'dist_atr': dist,
                        'rail': which, 'channel_kind': ch.kind,
                        'containment': ch.containment}
                rows.extend(_approach_rows(
                    'channel', key,
                    lambda j, r=rail: r.value_at(int(times[min(j, n - 1)])),
                    side, meta, close, atr, i, n, dp, rng))

    TrendlineEngine(tf, TF_MS[tf], params or Params()).walk(bars, on_bar=on_bar)
    return pd.DataFrame(rows)


def summarise(ev: pd.DataFrame) -> pd.DataFrame:
    """Hold rate per arm, with the binomial SE and the paired edge."""
    if not len(ev):
        return pd.DataFrame()
    out = []
    for arm, g in ev.groupby('arm'):
        decided = g[g['outcome'] != CHOP]
        m = len(decided)
        holds = int((decided['outcome'] == HOLD).sum())
        rate = holds / m if m else np.nan
        se = np.sqrt(rate * (1 - rate) / m) if m else np.nan
        out.append({'arm': arm, 'approaches': len(g), 'decided': m,
                    'chop%': round(100 * (1 - m / len(g)), 1),
                    'hold%': round(100 * rate, 1) if m else np.nan,
                    'se%': round(100 * se, 1) if m else np.nan})
    df = pd.DataFrame(out).set_index('arm')
    if 'structure' in df.index and 'placebo' in df.index:
        edge = df.loc['structure', 'hold%'] - df.loc['placebo', 'hold%']
        se = np.sqrt(df.loc['structure', 'se%'] ** 2 + df.loc['placebo', 'se%'] ** 2)
        df.attrs['edge_vs_placebo'] = round(float(edge), 2)
        df.attrs['edge_se'] = round(float(se), 2)
        df.attrs['z'] = round(float(edge / se), 2) if se else np.nan
    return df


def paired(ev: pd.DataFrame, by=None, bins=None) -> pd.DataFrame:
    """
    Paired structure-vs-placebo edge, optionally bucketed.

    Paired rather than pooled because the two arms share an approach bar: the
    same market conditions produced both, so the difference has a much smaller
    variance than two independent rates would.
    """
    if not len(ev):
        return pd.DataFrame()
    idx = ['obj_id', 'bar']
    keep = [c for c in ('strength', 'touches', 'width_atr', 'containment',
                        'channel_kind', 'rail') if c in ev.columns]
    w = ev.pivot_table(index=idx + keep, columns='arm', values='outcome',
                       aggfunc='first').reset_index()
    if 'structure' not in w.columns or 'placebo' not in w.columns:
        return pd.DataFrame()
    w = w[(w['structure'] != CHOP) & (w['placebo'] != CHOP)].dropna(
        subset=['structure', 'placebo'])
    if not len(w):
        return pd.DataFrame()
    w['S'] = (w['structure'] == HOLD).astype(int)
    w['P'] = (w['placebo'] == HOLD).astype(int)

    def _stat(g):
        d = g['S'] - g['P']
        sd = d.std(ddof=1)
        z = d.mean() / (sd / np.sqrt(len(d))) if len(d) > 2 and sd > 0 else np.nan
        return pd.Series({'n': len(g),
                          'structure%': round(100 * g['S'].mean(), 2),
                          'placebo%': round(100 * g['P'].mean(), 2),
                          'edge': round(100 * d.mean(), 2),
                          'z': round(z, 2) if z == z else np.nan})

    if by is None:
        return _stat(w).to_frame().T
    if bins is not None:
        w = w.copy()
        w['_b'] = pd.cut(w[by], bins)
        return w.groupby('_b', observed=True).apply(_stat, include_groups=False).reset_index()
    return w.groupby(by, observed=True).apply(_stat, include_groups=False).reset_index()

def zone_approaches(bars, tf, zp: ZoneParams = None, dp: StructDiagParams = None):
    """
    Every zone approach, WITHOUT resolving an outcome.

    Detection is the expensive half (find_pivots over the lookback, every
    `refresh` bars) and it does not depend on the barrier geometry at all. A
    geometry sweep that calls run_zones 25 times therefore redoes identical work
    24 times. This returns the approach set once so `resolve_approaches` can
    replay it at any number of geometries.
    """
    zp = zp or ZoneParams()
    dp = dp or StructDiagParams()
    close = np.asarray(bars['close'], dtype=float)
    atr = atr_series(bars, 14)
    n = len(close)
    times = np.asarray(bars.index.astype('int64') // 1_000_000)

    out = []
    armed = {}
    live = []
    for i in range(60, n):
        a = atr[i]
        if not np.isfinite(a) or a <= 0:
            continue
        if (i - 60) % dp.refresh == 0:
            live = detect_zones(bars, i, tf, atr, zp, times=times)
        for z in live:
            if close[i] < z.low:
                edge, side = z.low, -1
            elif close[i] > z.high:
                edge, side = z.high, 1
            else:
                armed[z.id] = False
                continue
            dist = abs(close[i] - edge) / a
            if dist >= dp.far_atr:
                armed[z.id] = True
                continue
            if dist > dp.near_atr or not armed.get(z.id, False):
                continue
            armed[z.id] = False
            out.append({'bar': i, 'edge': edge, 'side': side,
                        'occurred_at': bars.index[i], 'tf': tf, 'atr': a,
                        'obj_id': z.id, 'strength': z.strength,
                        'touches': z.touches, 'width_atr': z.width_atr,
                        'dist_atr': dist})
    return out, close, atr, n


def resolve_approaches(approaches, close, atr, n, dp: StructDiagParams = None):
    """Replay a pre-computed approach set at one barrier geometry."""
    dp = dp or StructDiagParams()
    rng = np.random.default_rng(dp.seed)
    rows = []
    for ap in approaches:
        i, side, edge = ap['bar'], ap['side'], ap['edge']
        meta = {k: v for k, v in ap.items() if k not in ('bar', 'edge', 'side')}
        rows.extend(_approach_rows('zone', ap['obj_id'],
                                   lambda j, e=edge: e, side, meta,
                                   close, atr, i, n, dp, rng))
    return pd.DataFrame(rows)


def run_sd_zones(bars, tf, sp: SDParams = None, dp: StructDiagParams = None):
    """
    Placebo test for IMPULSE-ORIGIN zones (sim/tl/supply_demand.py).

    Same method as run_zones so the two ways of finding a zone are directly
    comparable: approach the nearer edge, symmetric barriers, and a placebo band
    of identical width `placebo_atr` away.

    `fresh` and `impulse_atr` ride along on every row, because the technique's
    central claim -- that an untested zone is worth more than a used one -- is
    the part most worth falsifying.
    """
    sp = sp or SDParams()
    dp = dp or StructDiagParams()
    close = np.asarray(bars['close'], dtype=float)
    atr = atr_series(bars, 14)
    n = len(close)
    rng = np.random.default_rng(dp.seed)
    times = np.asarray(bars.index.astype('int64') // 1_000_000)

    rows = []
    armed = {}
    live = []
    for i in range(60, n):
        a = atr[i]
        if not np.isfinite(a) or a <= 0:
            continue
        if (i - 60) % dp.refresh == 0:
            live = detect_sd(bars, tf, atr, sp, upto=i, times=times)
        for z in live:
            if i <= z.confirmed_i:
                continue                       # not knowable yet
            if close[i] < z.low:
                edge, side = z.low, -1
            elif close[i] > z.high:
                edge, side = z.high, 1
            else:
                armed[z.id] = False
                continue
            dist = abs(close[i] - edge) / a
            if dist >= dp.far_atr:
                armed[z.id] = True
                continue
            if dist > dp.near_atr or not armed.get(z.id, False):
                continue
            armed[z.id] = False
            meta = {'occurred_at': bars.index[i], 'tf': tf, 'atr': a,
                    'strength': z.strength, 'touches': z.touches,
                    'width_atr': z.width_atr, 'dist_atr': dist,
                    'fresh': z.fresh, 'impulse_atr': z.impulse_atr,
                    'zone_kind': z.kind}
            rows.extend(_approach_rows('sd_zone', z.id, lambda j, e=edge: e,
                                       side, meta, close, atr, i, n, dp, rng))
    return pd.DataFrame(rows)


def run_fvg(bars, tf, fp: FVGParams = None, dp: StructDiagParams = None):
    """
    Placebo test for Fair Value Gaps, using the same approach method as zones so
    the two are directly comparable.

    THE FILL RATE IS NOT THE TEST. 93-98% of gaps get filled, which is the
    statistic the technique is usually sold on and which proves nothing: price
    wanders, and a band of the same width 1.5 ATR away gets touched at a similar
    rate. What matters is whether ARRIVING at a gap leads somewhere different
    from arriving at an arbitrary nearby level.
    """
    fp = fp or FVGParams()
    dp = dp or StructDiagParams()
    close = np.asarray(bars['close'], dtype=float)
    atr = atr_series(bars, 14)
    n = len(close)
    rng = np.random.default_rng(dp.seed)
    times = np.asarray(bars.index.astype('int64') // 1_000_000)

    rows = []
    armed = {}
    live = []
    for i in range(60, n):
        a = atr[i]
        if not np.isfinite(a) or a <= 0:
            continue
        if (i - 60) % dp.refresh == 0:
            live = [g for g in detect_fvg(bars, tf, atr, fp, upto=i, times=times)
                    if g.open and i - g.i <= fp.max_age]
        for g in live:
            if i <= g.i:
                continue                       # not knowable yet
            if close[i] < g.low:
                edge, side = g.low, -1
            elif close[i] > g.high:
                edge, side = g.high, 1
            else:
                armed[g.id] = False
                continue
            dist = abs(close[i] - edge) / a
            if dist >= dp.far_atr:
                armed[g.id] = True
                continue
            if dist > dp.near_atr or not armed.get(g.id, False):
                continue
            armed[g.id] = False
            meta = {'occurred_at': bars.index[i], 'tf': tf, 'atr': a,
                    'strength': g.size_atr * 40, 'touches': 0,
                    'width_atr': g.high - g.low, 'dist_atr': dist,
                    'fvg_kind': g.kind, 'size_atr': g.size_atr,
                    'age': i - g.i}
            rows.extend(_approach_rows('fvg', g.id, lambda j, e=edge: e,
                                       side, meta, close, atr, i, n, dp, rng))
    return pd.DataFrame(rows)

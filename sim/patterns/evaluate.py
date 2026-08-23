"""
evaluate.py — is this pattern distinguishable from nothing?

One scorer for every proposer, so that "is the discovered motif better than a
trendline" is a question with an answer rather than a comparison of two
different measurement pipelines.

THE NULL, IN TWO PARTS

  fair    From optional stopping, a driftless price reaches the target before
          the stop with probability stop/(stop+target) -- which is exactly the
          breakeven hit rate at that geometry. Analytic, exact, needs no control
          arm. This is what a placebo was always trying to approximate, and it
          does not have a placebo's failure modes: the previous attempt at one
          resolved on the first bar 100% of the time and was a coin flip.

  base    The same outcome table over EVERY bar in the series, direction
          matched. This is the unconditional rate, and it is not `fair`: real
          prices are only approximately martingales, discrete bars overshoot
          barriers, and the ambiguous-bar tie-break pushes outcomes toward the
          stop. `base` absorbs all of that.

The statistic is the pattern's hold rate against `base`, not against `fair`,
because every one of those distortions applies to the pattern's bars too and
cancels in the difference. `fair` is still reported: base minus fair is the size
of the measurement's own bias, and if that number is large the run deserves
suspicion before any pattern in it is believed.

`base` IS STRATIFIED BY TIME, and that is not a refinement -- it is the
difference between this harness working and reproducing the project's oldest
mistake. A single base rate over the whole series controls for a market's
AVERAGE drift, but a pattern does not fire uniformly across the series. It
clusters in particular years and regimes, and it therefore inherits whatever
those years did. The first pattern ever run through this harness -- trendline
approaches on EURUSD 4h -- looked like a +9pp level effect at z=5.7, holding in
4 of 4 folds. Forcing the direction revealed that LONG beat base at support and
at resistance alike, and short lost at both: the pattern was not "a line holds",
it was "these bars drifted up". On XAUUSD the same proposer produced the mirror
image, which is trend beta wearing a trendline costume.

So the null a pattern is measured against is the base rate of ITS OWN eras, in
ITS OWN direction, and a pattern that only beats the unstratified rate has found
the market's drift rather than anything of its own.

WHY THE COMPARISON IS FREE OF THE PAIRING PROBLEM

`base` is computed over all bars, so it is not a second arm that has to be
matched event-for-event with the first. There is no overlap to condition on and
nothing to pair, which is what went wrong when a placebo arm fired on a
different subset of events than the arm it was compared against.

MULTIPLICITY IS THE MAIN EVENT

A discovery sweep tests thousands of hypotheses. Benjamini-Hochberg controls the
false discovery rate across them, and `n_hypotheses` records how many were
tested -- including patterns that failed the sample floor, because considering
them and discarding them is still looking. Alongside it, `expected_max_z` says
what the best of that many worthless candidates would score anyway. A pattern
whose z does not clear that line has told you nothing, whatever its p-value.
"""

import numpy as np
import pandas as pd

from ..indicators import atr as atr_series

from ..intrabar import PESSIMISTIC
from ..stats import benjamini_hochberg, expected_max_z, two_sided_p
from .nulls import base_by_cell, covariate_strata, time_shift_null
from .outcomes import (CHOP, STOP_FIRST, TARGET_FIRST, UNDEFINED, fair_value,
                       triple_barrier)


def fold_ids(n_bars, n_folds, horizon):
    """
    Contiguous time blocks, with the last `horizon` bars of each block embargoed.

    A pattern firing near a block boundary resolves its outcome inside the NEXT
    block, so without the embargo the blocks are not independent samples and
    per-fold agreement flatters itself. Embargoed bars get -1 and are scored in
    no fold at all.

    Contiguous rather than interleaved because the question is whether the
    effect holds across ERAS. Shuffled folds would answer a question nobody
    asked and would hide exactly the regime-dependence worth finding.
    """
    if n_folds < 2:
        return np.zeros(n_bars, dtype=np.int64)
    edges = np.linspace(0, n_bars, n_folds + 1).astype(int)
    ids = np.full(n_bars, -1, dtype=np.int64)
    for f in range(n_folds):
        lo, hi = edges[f], edges[f + 1]
        ids[lo:max(lo, hi - horizon)] = f
    return ids


def _rate(outcomes):
    """(decided, holds) among outcomes that actually resolved."""
    decided = (outcomes == TARGET_FIRST) | (outcomes == STOP_FIRST)
    return int(decided.sum()), int((outcomes == TARGET_FIRST).sum())


def _score(pid, bar, trade_dir, proposed, table, base, base_flat, strata,
           folds, atr, n_bars, stop_atr, target_atr, rr, fair, min_events,
           n_folds, n_shifts, seed, spec, slippage_atr):
    """
    One (pattern, direction, geometry) cell, or None if too small to test.

    `trade_dir` is the side actually taken, and everything below is measured at
    that side's own geometry: its own outcome table, its own base rate, its own
    hit rate, its own friction. Nothing is derived from the opposite side by
    symmetry, because none of it is symmetric.
    """
    out = table[trade_dir][bar]
    n_ev = len(out)
    n_und = int((out == UNDEFINED).sum())
    dec, hold = _rate(out)
    if dec < min_events:
        return None
    p_hat = hold / dec

    decided = (out == TARGET_FIRST) | (out == STOP_FIRST)
    p0_i = base[trade_dir][strata[bar[decided]]]
    p0 = float(p0_i.mean())

    # The null is a sum of non-identical Bernoullis -- a Poisson binomial --
    # because each event carries its own era-matched p0. Its variance is the
    # sum of the per-event variances; collapsing to one averaged p0 in a plain
    # binomial SE overstates the spread whenever the p0 differ, and overstating
    # the spread hides effects.
    var = float((p0_i * (1 - p0_i)).sum())
    z = (hold - p0_i.sum()) / np.sqrt(var) if var > 0 else np.nan

    # ...and that binomial still assumes the events are independent, which
    # overlapping outcome windows guarantee they are not. The shifted null is
    # the one that decides.
    dirs = np.full(len(bar), trade_dir, dtype=np.int64)
    z_s, p_s, _ = time_shift_null(table, base, strata, bar, dirs, n_bars,
                                  n_shifts=n_shifts, seed=seed)

    # Per-fold sign agreement. Not a test -- a shape check, in the spirit of the
    # neighbourhood tool: a real effect shows up in most eras, a lucky one lives
    # in one or two.
    f_pos = f_tot = 0
    fb = folds[bar]
    for f in range(max(1, n_folds)):
        sel = decided & (fb == f)
        d_f, h_f = _rate(out[sel])
        if d_f < 30:
            continue
        f_tot += 1
        f_pos += (h_f / d_f - base[trade_dir][strata[bar[sel]]].mean()) > 0

    dev = p_hat - p0
    # A deviation is not money. Friction in R scales as 1/stop_distance, so the
    # same broker costs a different number of R at every geometry, and a fast
    # timeframe with a tight stop can cost more per trade than any realistic
    # edge is worth.
    gross_R = p_hat * rr - (1 - p_hat)
    fr = np.nan
    if spec is not None:
        risk_px = stop_atr * float(np.nanmean(atr[bar]))
        if risk_px > 0:
            spread_px = float(spec.get('spread_points_now') or 0) * spec['point']
            fr = spread_px / risk_px + 2 * slippage_atr / stop_atr

    return {
        'pattern_id': pid, 'direction': trade_dir,
        'as_proposed': trade_dir == proposed,
        'stop_atr': stop_atr, 'target_atr': target_atr, 'rr': round(rr, 2),
        'n_events': n_ev, 'n_decided': dec,
        'chop_pct': round(100 * (n_ev - dec - n_und) / n_ev, 1),
        'undefined': n_und,
        'hold_pct': round(100 * p_hat, 2),
        'base_pct': round(100 * p0, 2),
        # the unstratified rate, kept so the size of the drift confound stays
        # visible rather than merely corrected away
        'base_flat_pct': round(100 * base_flat[trade_dir], 2),
        'fair_pct': round(100 * fair, 2),
        'dev_pp': round(100 * dev, 2),
        # a percentage point converts to R at (1 + rr): a win swings the outcome
        # from -1 to +rr. Usually much smaller than it looks.
        'edge_R': round(dev * (1 + rr), 4),
        'gross_R': round(gross_R, 4),
        'friction_R': round(fr, 4) if np.isfinite(fr) else np.nan,
        'net_R': round(gross_R - fr, 4) if np.isfinite(fr) else np.nan,
        'z': round(z, 2) if np.isfinite(z) else np.nan,
        'p_binom': two_sided_p(z),
        'z_shift': round(z_s, 2) if np.isfinite(z_s) else np.nan,
        # BH runs on the PARAMETRIC tail of the shifted z, not the raw
        # permutation count: at `n_shifts` displacements the count cannot go
        # below 1/(n_shifts+1), while BH over thousands of hypotheses needs
        # thresholds far smaller, so the count would fail every wide sweep for
        # want of resolution rather than for want of an effect. The cost is a
        # normality assumption; p_perm sits beside it, and the two disagreeing
        # says the assumption has broken.
        'p': two_sided_p(z_s),
        'p_perm': p_s,
        'folds_agree': '%d/%d' % (f_pos, f_tot),
        'fold_frac': round(f_pos / f_tot, 2) if f_tot else np.nan,
    }


def evaluate(bars, proposals, symbol, tf, geometries, horizon=48,
             resolution=PESSIMISTIC, min_events=200, n_folds=4, alpha=0.05,
             n_strata=20, vol_buckets=3, mom_buckets=1, mom_lookback=20,
             n_shifts=400, seed=0, spec=None, slippage_atr=0.02,
             only=None):
    """
    Score every (pattern, direction, geometry) cell. One row each.

    `geometries` is an iterable of (stop_atr, target_atr).
    `only` restricts scoring to an explicit list of (pattern_id, direction,
    stop_atr, target_atr) tuples -- the out-of-sample pass, where the
    hypotheses were chosen on earlier data and the multiplicity is however many
    survived, not however many were searched.

    BOTH directions of every pattern are scored. A shape that predicts down is
    traded short, and a short's R:R at a given geometry is not the mirror of the
    long's -- different barrier distances from entry, a separately measured hit
    rate, different friction. Sign-flipping a long's economics to describe a
    short is simply wrong.

    But it does NOT simply double the hypothesis count, because some of those
    cells are the same experiment written twice. A long with stop a and target b
    places its barriers at b above the entry and a below; a short with stop b
    and target a places them in exactly the same two places, and its hold rate
    is the long's complement. Whenever a grid contains both (a, b) and (b, a) --
    which every grid does along its diagonal, where a long and a short at the
    same stop and target are literally the same pair of barriers -- those cells
    collide. They are deduplicated by their actual barrier configuration, so the
    correction counts experiments rather than table rows.
    """
    n = len(bars)
    atr = atr_series(bars, 14)
    folds = fold_ids(n, n_folds, horizon)
    strata, n_cells, strata_desc = covariate_strata(
        bars, time_blocks=n_strata, vol_buckets=vol_buckets,
        mom_buckets=mom_buckets, mom_lookback=mom_lookback)
    wanted = set(only) if only is not None else None
    rows = []
    considered = 0
    # (pattern, distance above entry, distance below entry) -- the identity of
    # an experiment, independent of which side of it you call the target
    seen = set()

    for stop_atr, target_atr in geometries:
        rr = target_atr / stop_atr
        fair = fair_value(stop_atr, target_atr)

        # One outcome table per direction, reused by every pattern. This is the
        # whole reason a sweep over thousands of patterns is affordable.
        table, base, base_flat = {}, {}, {}
        for d in (1, -1):
            out, _amb = triple_barrier(bars, d, stop_atr, target_atr,
                                       horizon=horizon, resolution=resolution,
                                       symbol=symbol, tf=tf)
            table[d] = out
            per, _cell_n, overall = base_by_cell(out, strata, n_cells)
            base[d] = per
            base_flat[d] = overall

        for pid, g in proposals.groupby('pattern_id', sort=True):
            bar = g['bar'].to_numpy()
            proposed = int(pd.Series(g['direction']).mode().iloc[0])
            for trade_dir in (1, -1):
                if wanted is not None and \
                        (pid, trade_dir, stop_atr, target_atr) not in wanted:
                    continue
                up, down = ((target_atr, stop_atr) if trade_dir > 0
                            else (stop_atr, target_atr))
                if (pid, up, down) in seen:
                    continue          # same two barriers, already scored
                seen.add((pid, up, down))
                considered += 1
                row = _score(pid, bar, trade_dir, proposed, table, base,
                             base_flat, strata, folds, atr, n, stop_atr,
                             target_atr, rr, fair, min_events, n_folds,
                             n_shifts, seed, spec, slippage_atr)
                if row is not None:
                    rows.append(row)

    df = pd.DataFrame(rows)
    if not len(df):
        df.attrs['n_hypotheses'] = considered
        df.attrs['expected_max_z'] = expected_max_z(considered)
        return df

    df['survives_bh'] = benjamini_hochberg(df['p'].to_numpy(), alpha)
    # Hypotheses CONSIDERED, not reported. A pattern dropped for having too few
    # events was still looked at, and pretending otherwise is how a sweep
    # launders its own multiplicity.
    df.attrs['n_hypotheses'] = considered
    df.attrs['expected_max_z'] = expected_max_z(considered)
    # Deflation is applied to the SHIFTED z, because that is the one whose
    # null is credible. Applying it to the binomial z would deflate a number
    # that was already inflated and call the result conservative.
    #
    # Floored at the ordinary two-sided 5% critical value. Searching one or two
    # candidates gives an expected maximum near zero, and without the floor a
    # z of 0.3 would "beat the noise expectation" -- deflation is there to RAISE
    # the bar when you have looked hard, never to lower it when you have not.
    thresh = max(df.attrs['expected_max_z'], 1.96)
    df.attrs['threshold_z'] = thresh
    df['beats_expected_max'] = df['z_shift'].abs() > thresh
    df.attrs['strata'] = strata_desc
    df.attrs['n_cells'] = n_cells
    return df.sort_values('z_shift', key=abs, ascending=False,
                          na_position='last').reset_index(drop=True)


def summarise(df):
    """One paragraph a human can act on, or refuse to."""
    if not len(df):
        return 'no pattern cleared the sample floor.'
    emz = df.attrs.get('expected_max_z', np.nan)
    n_h = df.attrs.get('n_hypotheses', len(df))
    bh = int(df['survives_bh'].sum())
    beat = int(df['beats_expected_max'].sum())
    best = df.iloc[0]
    infl = (df['z'].abs() / df['z_shift'].abs()).replace(
        [np.inf, -np.inf], np.nan).median()
    return (
        '%d hypotheses considered, %d scored. null = %s (%d cells)\n'
        'best |z_shift| = %.2f (%s %s, stop %.1f / target %.1f, n=%d, '
        'dev %+.2f pp)\n'
        '  its binomial z was %.2f; median inflation across the sweep %.1fx\n'
'expected best-of-%d under pure noise: |z| = %.2f (bar used: %.2f)\n'
        '%d survive BH at 5%%; %d exceed the noise expectation.\n%s'
        % (n_h, len(df), df.attrs.get('strata'), df.attrs.get('n_cells', 1),
           abs(best['z_shift']), best['pattern_id'],
           'long' if best['direction'] > 0 else 'short', best['stop_atr'],
           best['target_atr'], best['n_decided'], best['dev_pp'],
           abs(best['z']), infl if np.isfinite(infl) else float('nan'),
           n_h, emz, df.attrs.get('threshold_z', emz), bh, beat,
           _economics(df)))


def _economics(df):
    """Whether anything that cleared the statistics also clears its costs."""
    live = df[df['beats_expected_max']] if 'beats_expected_max' in df else df
    if not len(live):
        return 'NOTHING HERE IS DISTINGUISHABLE FROM NOISE.'
    if 'net_R' not in df or df['net_R'].isna().all():
        return ('Candidates to walk forward -- not results. '
                '(no instrument spec passed, so costs were not applied)')
    pays = live[live['net_R'] > 0]
    if not len(pays):
        worst = live['friction_R'].min()
        return ('%d beat the noise bar, NONE of them cover costs -- best gross '
                '%+.4f R against a friction floor of %.4f R.\n'
                'Statistically real and economically dead is the usual outcome '
                'at this timeframe; it is still a result.'
                % (len(live), live['gross_R'].max(), worst))
    return ('%d beat the noise bar and %d also clear costs (best net %+.4f R). '
            'Walk these forward.' % (len(live), len(pays), pays['net_R'].max()))

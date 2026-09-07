#!/usr/bin/env python
"""
regime.py — WHERE does the edge live? Splits the validated cell's trades.

    python tools/regime.py --symbol XAUUSD.a --tf 4h --start 2018-01-01

This is step 3 of the sequence and it was skipped. A filter was added before it
-- `donchian_mtf`, gating entries on the daily trend -- and it behaved exactly
like the guess it was: better in sample (+0.2304 at percentile 100), WORSE out
of sample (+0.1603 against the base rule's +0.2191), and under the trade floor
in both eras. This tool exists so the next filter, if there is one, is chosen
from evidence about which regime carries the return.

EX-ANTE VERSUS EX-POST, which is the whole design of this file.

    EX-ANTE   known at the entry bar. A bucket that splits the edge here COULD
              become a filter, because you would have known which bucket you
              were in before committing.
    EX-POST   known only once the trade is over. These are DIAGNOSTIC ONLY and
              can never be a filter, however cleanly they split.

The distinction matters because the most striking split in this data is almost
certainly ex-post. "The rule does well when gold trends" is true and useless:
you cannot filter on a trend you have not seen yet. Reading an ex-post split as
a trading rule is how a backtest becomes a time machine, so the two families
are computed separately, printed under separate headings, and the ex-post block
carries a refusal in its own output.

WHAT THIS CANNOT DO. The validated cell has ~360 trades. Split four ways that
is ~90 per bucket, far under the 200-trade floor this project uses to call
anything measured. So every number here is DESCRIPTIVE. A bucket difference is
a place to look, not a finding, and any filter it suggests has to be tested on
data this split never saw. Each bucket therefore carries a bootstrap interval
rather than a bare mean -- with 90 trades the interval is wide enough that most
apparent differences will overlap, which is the honest picture and the reason
to show it rather than a league table of means.
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim.core import Config, Simulator
from sim.fx import FX
from sim.indicators import atr as atr_fn
from sim.instruments import account_currency, load, spec
from sim.strategies import BASELINES

BOOT = 2000
#: The project's sample floor. Buckets are always under it; the constant is
#: here so the printout can say by how much rather than implying otherwise.
MIN_TRADES = 200


def boot_ci(vals, lo=5, hi=95, n=BOOT, seed=0):
    """Percentile bootstrap of the mean. Fixed seed: a CI that moves between
    runs invites re-rolling until it says something."""
    v = np.asarray(vals, dtype=float)
    if len(v) < 3:
        return (np.nan, np.nan)
    rng = np.random.default_rng(seed)
    means = v[rng.integers(0, len(v), size=(n, len(v)))].mean(axis=1)
    return (float(np.percentile(means, lo)), float(np.percentile(means, hi)))


def report(df, by, title, note=''):
    """One split. Prints n, avg R with a bootstrap band, PF and win rate."""
    print('\n%s' % title)
    if note:
        print('  %s' % note)
    print('  %-22s %6s %9s %-20s %7s %7s'
          % ('bucket', 'n', 'avg R', '90% interval', 'PF', 'win%'))
    for name, g in df.groupby(by, observed=True, sort=False):
        r = g['r_multiple'].to_numpy(float)
        if not len(r):
            continue
        lo, hi = boot_ci(r)
        win = 100.0 * (r > 0).mean()
        gain = g.loc[g.net_acct > 0, 'net_acct'].sum()
        loss = -g.loc[g.net_acct < 0, 'net_acct'].sum()
        pf = (gain / loss) if loss > 0 else float('inf')
        flag = '' if len(g) >= MIN_TRADES else '  under floor'
        print('  %-22s %6d %+9.4f [%+.4f, %+.4f] %7s %6.1f%%%s'
              % (str(name), len(g), r.mean(), lo, hi,
                 ('%.3f' % pf) if np.isfinite(pf) else 'inf', win, flag))


def qbucket(series, labels):
    """Quartile buckets, tolerant of ties. Returns a labelled categorical."""
    try:
        return pd.qcut(series, len(labels), labels=labels, duplicates='drop')
    except ValueError:
        return pd.cut(series, len(labels), labels=labels)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--symbol', default='XAUUSD.a')
    ap.add_argument('--tf', default='4h')
    ap.add_argument('--strategy', default='donchian')
    ap.add_argument('--start', default='2018-01-01')
    ap.add_argument('--end', default=None)
    ap.add_argument('--trend-len', type=int, default=50,
                    help='bars of prior return used as the trend measure')
    args = ap.parse_args()

    bars = load(args.symbol, args.tf, args.start, args.end)
    sp = spec(args.symbol, args.tf)
    res = Simulator(sp, fx=FX.build(account_currency()),
                    config=Config(risk_pct=0.5, apply_swap=False)).run(
                        bars, BASELINES[args.strategy](), args.symbol, args.tf)
    t = res.trades.copy()
    if t.empty:
        sys.exit('no trades')

    close = bars['close'].to_numpy(float)
    a = np.asarray(atr_fn(bars, 14), dtype=float)
    idx = {ts: k for k, ts in enumerate(bars.index)}
    ei = t.entry_time.map(idx).to_numpy()
    xi = t.exit_time.map(idx).to_numpy()

    n = args.trend_len
    # ---------------- EX-ANTE: everything below is known at the entry bar ----
    # Prior return over `trend_len` bars, in ATR units so it is comparable
    # across a period where gold's ATR tripled. Uses bars STRICTLY BEFORE the
    # entry bar's own close is acted on -- the entry fills at ei, the decision
    # was ei-1, so the window ends there.
    prior = np.array([
        (close[i - 1] - close[max(0, i - 1 - n)]) / a[i - 1]
        if i >= 2 and np.isfinite(a[i - 1]) and a[i - 1] > 0 else np.nan
        for i in ei])
    t['trend_atr'] = prior
    t['trend_abs'] = np.abs(prior)
    # Does the trade agree with the prior move? A breakout normally does; the
    # cases where it does not are counter-trend entries.
    t['with_trend'] = np.where(np.sign(prior) == np.sign(t.side.to_numpy()),
                               'with prior trend', 'against prior trend')
    # Volatility percentile at entry, against the trailing year of bars.
    atr_pct = []
    for i in ei:
        w = a[max(0, i - 250):i]
        w = w[np.isfinite(w)]
        atr_pct.append(100.0 * (w < a[i - 1]).mean()
                       if len(w) > 30 and np.isfinite(a[i - 1]) else np.nan)
    t['atr_pct'] = atr_pct
    t['direction'] = np.where(t.side > 0, 'long', 'short')
    t['year'] = t.entry_time.dt.year

    # STEP 5 -- BREAKOUT QUALITY. How far beyond the channel the signal bar
    # actually closed, in ATR. Measured at the SIGNAL bar (entry_i - 1), which is
    # where the decision was made; the entry bar itself is the fill and its close
    # was not known when the order went in.
    #
    # Positive in both directions: it is displacement past the channel, not
    # signed return, so a long closing 0.4 ATR above the upper band and a short
    # closing 0.4 ATR below the lower band land in the same bucket.
    #
    # The hypothesis cuts both ways and that is why it is worth bucketing rather
    # than assuming. A bigger break may mean stronger commitment -- or it may
    # mean the move already happened and the entry is late. The MFE/MAE columns
    # are carried alongside for exactly that reason.
    hi_s = np.asarray(BASELINES[args.strategy]().prepare(bars)['hi'], dtype=float)
    lo_s = np.asarray(BASELINES[args.strategy]().prepare(bars)['lo'], dtype=float)
    brk = []
    for i, side in zip(ei, t.side.to_numpy()):
        j = i - 1                       # the signal bar
        if j < 1 or not np.isfinite(a[j]) or a[j] <= 0:
            brk.append(np.nan)
            continue
        band = hi_s[j] if side > 0 else lo_s[j]
        if not np.isfinite(band):
            brk.append(np.nan)
            continue
        brk.append((close[j] - band) / a[j] * (1 if side > 0 else -1))
    t['breakout_atr'] = brk

    # ---------------- EX-POST: known only after the fact ---------------------
    # How far the MARKET moved while the trade was open. This is the number the
    # walk-forward correlation was pointing at, and it is unusable as a filter.
    t['mkt_move_atr'] = [
        (close[x] - close[i]) / a[i] if np.isfinite(a[i]) and a[i] > 0 else np.nan
        for i, x in zip(ei, xi)]
    t['mkt_abs'] = t.mkt_move_atr.abs()

    print('%s %s %s   %s..%s   %d trades, avg R %+.4f'
          % (args.symbol, args.tf, args.strategy, bars.index[0].date(),
             bars.index[-1].date(), len(t), t.r_multiple.mean()))
    print('bootstrap 90%% intervals, %d resamples. Buckets are DESCRIPTIVE: the '
          'floor is %d\ntrades and every bucket below is under it.'
          % (BOOT, MIN_TRADES))

    print('\n' + '=' * 78)
    print('EX-ANTE  — known at the entry bar, so these COULD become a filter')
    print('=' * 78)
    q = ['Q1 lowest', 'Q2', 'Q3', 'Q4 highest']
    t['trend_bucket'] = qbucket(t.trend_abs, q)
    report(t.dropna(subset=['trend_abs']), 'trend_bucket',
           'by PRIOR trend strength (|%d-bar return| in ATR, before entry)' % n)
    report(t, 'with_trend', 'by agreement with the prior move')
    t['vol_bucket'] = qbucket(t.atr_pct, q)
    report(t.dropna(subset=['atr_pct']), 'vol_bucket',
           'by VOLATILITY at entry (ATR percentile vs trailing 250 bars)')
    t['brk_bucket'] = qbucket(t.breakout_atr, q)
    report(t.dropna(subset=['breakout_atr']), 'brk_bucket',
           'by BREAKOUT QUALITY (how far past the channel the signal closed, ATR)',
           'Cuts both ways: a bigger break may be commitment, or may be a late '
           'entry after the move. MFE/MAE below say which.')
    # MFE and MAE per bucket answer the late-entry question directly: a late
    # entry shows a small MFE with a large MAE, a committed one the reverse.
    for name, g in t.dropna(subset=['breakout_atr']).groupby('brk_bucket',
                                                             observed=True):
        if len(g):
            print('    %-14s MFE %+.2f R   MAE %-+.2f R   bars held %.0f'
                  % (name, g.mfe_r.mean(), -abs(g.mae_r.mean()),
                     g.bars_held.mean()))
    report(t, 'direction', 'by DIRECTION')
    report(t.sort_values('year'), 'year', 'by YEAR')
    report(t, 'exit_reason', 'by EXIT type')

    print('\n' + '=' * 78)
    print('EX-POST  — known only after the trade closed. DIAGNOSTIC ONLY.')
    print('  These cannot be filters. A split here says what the rule needed the')
    print('  market to do, not anything you could have known beforehand.')
    print('=' * 78)
    t['mkt_bucket'] = qbucket(t.mkt_abs, q)
    report(t.dropna(subset=['mkt_abs']), 'mkt_bucket',
           'by MARKET MOVE during the trade (|move| in entry ATR)',
           'If the edge is concentrated in the top bucket, the rule is '
           'harvesting trend rather than predicting it.')

    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       'runs', 'regime_%s_%s_%s.csv'
                       % (args.symbol.replace('.', ''), args.tf, args.strategy))
    keep = ['entry_time', 'exit_time', 'side', 'direction', 'r_multiple',
            'net_acct', 'exit_reason', 'bars_held', 'trend_atr', 'trend_abs',
            'with_trend', 'atr_pct', 'year', 'mkt_move_atr', 'mkt_abs',
            'breakout_atr', 'mfe_r', 'mae_r']
    t[keep].to_csv(out, index=False)
    print('\nwrote %s' % out)


if __name__ == '__main__':
    main()

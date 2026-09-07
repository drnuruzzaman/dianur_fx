#!/usr/bin/env python
"""
Should the exit follow price, and does STRUCTURE know where to put it?

    python tools/exit_trail_eval.py
    python tools/exit_trail_eval.py --symbols XAUUSD.a --tfs 4h

THE COMPLAINT THAT PROMPTED THIS, with its numbers. The Donchian exit is the
extreme of the last N/2 bars -- on XAUUSD 5m a 39.6-hour window. A short from
4524.84 sat +7.17 R open with price at 4322.49 and its exit still at 4461.69,
139 points away, because the high that set it printed 29.8 hours earlier and had
not rolled out yet. Taking it realises +2.24 R: 31% of what was open. Structure
behind price sat at 4323.91 and would have realised +7.12 R.

THAT NUMBER IS THE MOST FLATTERING ONE OBTAINABLE and is not evidence of
anything: it is one trade sampled at its maximum excursion, where any tightening
looks brilliant. This measures the idea over every trade instead.

WHAT IS COMPARED.

  baseline     the rule as validated -- channel exit, no trail.
  structure    exit also trails to the nearest structure BEHIND price: swing,
               S/R, supply/demand, trendline, BOS/CHoCH. Ratchets only, and the
               tighter of it and the channel always wins, so the rule's own exit
               is never loosened.
  atrMatched   THE CONTROL. A trail at a fixed ATR multiple, with the multiple
               chosen so its average distance from price equals the structural
               trail's. Knows nothing about the chart.
  atr2, atr4   two conventional widths, so the control is not a single point and
               a reader can see which way distance alone moves the result.

WHY THE CONTROL DECIDES IT. A trail that sits closer exits sooner, and exiting
sooner changes the return distribution all by itself. If `structure` beats the
baseline but not `atrMatched`, then the gain was proximity and not knowledge --
structure would have shown it knows nothing about WHERE an exit belongs. This is
exactly how eleven entry gates died in tools/entry_filter_eval.py, and the only
reason it was caught there was a matched control.

THE CONTROL IS MATCHED BEFORE ANYTHING IS SCORED. The structural trail is run
once without being allowed to act, purely to record its average distance; the
ATR multiple is set from that. Choosing it after seeing returns would make the
control an accomplice.

HOW ELSE THIS IS KEPT HONEST -- the same design the entry-filter study used.

  TWO ERAS, AND IT MUST SURVIVE BOTH. 2016-2020 was range-bound gold and
  2021-2026 was the run to 4,700. A change that helps in one and hurts in the
  other is a bet on the regime.

  NET R OVER THE SAME CALENDAR IS THE HEADLINE. An exit that closes earlier frees
  the rule to re-enter, so the configurations do not take the same trades and
  average R per trade is not comparable across rows. Trades are paired by
  CALENDAR BLOCK, and the bootstrap resamples blocks.

  GROSS. No spread, slippage or swap. A trail that takes MORE trades pays that
  cost more often, so the trailed rows are flattered relative to the baseline.
"""

import argparse
import glob
import gzip
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NODE = shutil.which('node')
RUNNER = os.path.join(ROOT, 'tools', 'exit_trail_runner.mjs')

DEFAULT_SYMBOLS = ['XAUUSD.a', 'EURUSD.a', 'USDJPY.a', 'GBPUSD.a']
DEFAULT_TFS = ['4h', '1h']

#: The project's existing era split, as used by runs/adx_XAUUSDa.csv.
ERAS = [('2016-2020', 2016, 2020), ('2021-2026', 2021, 2026)]

N_BLOCKS = 20


def load_bars(symbol, tf, year_from=2016):
    files = sorted(glob.glob(os.path.join(ROOT, 'data', 'bars', symbol, tf, '*.csv.gz')))
    rows = []
    for f in files:
        year = int(os.path.basename(f)[:4])
        if year < year_from:
            continue
        with gzip.open(f, 'rt') as fh:
            head = fh.readline().strip().split(',')
            ix = {k: i for i, k in enumerate(head)}
            for line in fh:
                c = line.strip().split(',')
                if len(c) < 5:
                    continue
                rows.append({'t': int(c[ix['ts']]) * 1000,
                             'o': float(c[ix['open']]), 'h': float(c[ix['high']]),
                             'l': float(c[ix['low']]), 'c': float(c[ix['close']]),
                             'v': 1})
    rows.sort(key=lambda r: r['t'])
    return rows


def year_of(ms):
    # UTC year, good enough to bucket an era: a trade on 1 January is not what
    # decides any of this.
    import datetime
    return datetime.datetime.utcfromtimestamp(ms / 1000).year


def block_totals(trades, lo_t, hi_t, blocks=N_BLOCKS):
    """Net R per contiguous block of CALENDAR TIME, by entry."""
    if hi_t <= lo_t:
        return [0.0] * blocks
    width = (hi_t - lo_t) / blocks
    out = [0.0] * blocks
    for t in trades:
        b = min(blocks - 1, max(0, int((t['entryTime'] - lo_t) / width)))
        out[b] += t['r']
    return out


def paired_block_ci(a, b, n=2000, seed=17):
    k = min(len(a), len(b))
    if not k:
        return (float('nan'), float('nan'))
    diffs = [a[j] - b[j] for j in range(k)]
    rnd = random.Random(seed)
    sums = []
    for _ in range(n):
        sums.append(sum(diffs[rnd.randrange(k)] for _ in range(k)))
    sums.sort()
    return (sums[int(0.025 * n)], sums[int(0.975 * n)])




def block_ci(totals, n=2000, seed=13):
    """Percentile bootstrap over blocks, on the TOTAL across the window."""
    k = len(totals)
    if not k:
        return (float('nan'), float('nan'))
    rnd = random.Random(seed)
    sums = []
    for _ in range(n):
        sums.append(sum(totals[rnd.randrange(k)] for _ in range(k)))
    sums.sort()
    return (sums[int(0.025 * n)], sums[int(0.975 * n)])


def summarise(trades):
    rs = [t['r'] for t in trades]
    if not rs:
        return {'n': 0, 'win': float('nan'), 'avgR': float('nan'),
                'netR': 0.0, 'pf': float('nan'), 'bars': float('nan')}
    wins = [r for r in rs if r > 0]
    bad = -sum(r for r in rs if r <= 0)
    return {'n': len(rs), 'win': 100.0 * len(wins) / len(rs),
            'avgR': sum(rs) / len(rs), 'netR': sum(rs),
            'pf': (sum(wins) / bad) if bad > 0 else float('inf'),
            'bars': sum(t['bars'] for t in trades) / len(trades)}


def run_cell(symbol, tf, year_from):
    bars = load_bars(symbol, tf, year_from)
    if len(bars) < 3000:
        return None
    tmp = tempfile.mkdtemp(prefix='exittrail_')
    try:
        bp = os.path.join(tmp, 'bars.json')
        with open(bp, 'w') as fh:
            json.dump(bars, fh)
        cp = os.path.join(tmp, 'cfg.json')
        with open(cp, 'w') as fh:
            json.dump({'barsPath': bp, 'tf': tf, 'cell': symbol + '|' + tf}, fh)
        out = subprocess.run([NODE, '--max-old-space-size=4096', RUNNER, cp],
                             cwd=ROOT, capture_output=True, text=True, timeout=14400)
        if out.returncode != 0:
            print('  ! node failed: ' + out.stderr[-800:], file=sys.stderr)
            return None
        return json.loads(out.stdout.strip().splitlines()[-1])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


ORDER = ['baseline', 'structure', 'atrMatched', 'atr2', 'atr4']


def era_trades(trades, y0, y1):
    return [t for t in trades if y0 <= year_of(t['entryTime']) <= y1]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--symbols', nargs='+', default=DEFAULT_SYMBOLS)
    ap.add_argument('--tfs', nargs='+', default=DEFAULT_TFS)
    ap.add_argument('--from-year', type=int, default=2016)
    ap.add_argument('--json', help='also write the raw per-cell results here')
    args = ap.parse_args()

    if NODE is None:
        print('node is not installed', file=sys.stderr)
        return 2

    everything = {}
    pooled = {e[0]: {} for e in ERAS}

    print('')
    print('GROSS of spread, slippage and swap. Both eras, whole grid.')
    print('NET R over the same calendar is the comparison: the exits do not take')
    print('the same trades, so avg R per trade is shown but does not decide.')
    print('')

    for symbol in args.symbols:
        for tf in args.tfs:
            res = run_cell(symbol, tf, args.from_year)
            if not res:
                continue
            cell = symbol + ' ' + tf
            everything[cell] = res
            print('%s   (%d bars; the structural trail sits %.2f ATR from price, '
                  'so the matched control is k=%s)'
                  % (cell, res['bars'], res['structDistAtr'], res['matchedK']))
            hdr = '  %-12s%5s%7s%6s   ' % ('exit', 'n', 'win%', 'held')
            print(hdr + '   '.join('%-30s' % e[0] for e in ERAS))

            blocks = {}
            for name in ORDER:
                tr_all = res['runs'][name]
                s_all = summarise(tr_all)
                cells = []
                for era, y0, y1 in ERAS:
                    tr = era_trades(tr_all, y0, y1)
                    base = era_trades(res['runs']['baseline'], y0, y1)
                    if not base:
                        cells.append('%-30s' % '-')
                        continue
                    lo_t = min(t['entryTime'] for t in base)
                    hi_t = max(t['entryTime'] for t in base)
                    bt = block_totals(tr, lo_t, hi_t)
                    blocks.setdefault(name, {})[era] = bt
                    pooled[era].setdefault(name, []).extend(bt)
                    net = summarise(tr)['netR']
                    if name == 'baseline':
                        cells.append('%+9.1f%21s' % (net, 'baseline'))
                    else:
                        lo, hi = paired_block_ci(bt, blocks['baseline'][era])
                        cells.append('%+9.1f  %+7.1f [%+5.1f,%+5.1f]'
                                     % (net, net - summarise(base)['netR'], lo, hi))
                print('  %-12s%5d%7.1f%6.0f   ' % (name, s_all['n'], s_all['win'],
                                                   s_all['bars'])
                      + '   '.join(cells))
            print('')

    if not pooled[ERAS[0][0]]:
        return 0

    print('POOLED ACROSS EVERY CELL, PER ERA')
    print('  %-12s' % 'exit' + '   '.join('%-30s' % e[0] for e in ERAS)
          + '     verdict')
    for name in ORDER:
        cells, wins, losses = [], 0, 0
        for era, _, _ in ERAS:
            bt = pooled[era].get(name, [])
            bb = pooled[era]['baseline']
            tot = sum(bt)
            if name == 'baseline':
                cells.append('%+9.1f%21s' % (tot, 'baseline'))
                continue
            lo, hi = paired_block_ci(bt, bb)
            if lo > 0:
                wins += 1
            elif hi < 0:
                losses += 1
            cells.append('%+9.1f  %+7.1f [%+5.1f,%+5.1f]'
                         % (tot, tot - sum(bb), lo, hi))
        if name == 'baseline':
            verdict = ''
        elif wins == len(ERAS):
            verdict = 'BETTER IN BOTH ERAS'
        elif losses == len(ERAS):
            verdict = 'WORSE IN BOTH ERAS'
        else:
            verdict = 'not demonstrated'
        print('  %-12s' % name + '   '.join(cells) + '     ' + verdict)

    # The comparison the whole study exists for.
    print('')
    print('STRUCTURE AGAINST ITS MATCHED CONTROL - does it know WHERE, or only')
    print('HOW CLOSE? Same average distance from price; only placement differs.')
    beat = 0
    for era, _, _ in ERAS:
        st = pooled[era].get('structure', [])
        ct = pooled[era].get('atrMatched', [])
        lo, hi = paired_block_ci(st, ct)
        d = sum(st) - sum(ct)
        if lo > 0:
            beat += 1
        print('  %s: structure - atrMatched = %+.1f net R [%+.1f, %+.1f]'
              % (era, d, lo, hi))
    print('  -> ' + ('structure beats an equally close dumb trail in BOTH eras'
                     if beat == len(ERAS)
                     else 'structure is NO BETTER than an equally close dumb trail'))

    print('')
    print('Read the control line first. Beating the channel while sitting closer')
    print('to price is not evidence that structure knows anything: a trail that')
    print('exits sooner changes the result on its own. Everything is gross, and')
    print('a configuration that takes more trades pays that cost more often.')

    if args.json:
        with open(args.json, 'w') as fh:
            json.dump(everything, fh)
        print('')
        print('raw results -> ' + args.json)
    return 0


if __name__ == '__main__':
    sys.exit(main())

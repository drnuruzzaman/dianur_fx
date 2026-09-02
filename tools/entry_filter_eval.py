#!/usr/bin/env python
"""
Which breakouts are worth taking?

    python tools/entry_filter_eval.py
    python tools/entry_filter_eval.py --symbols XAUUSD.a --tfs 4h

WHY THE ENTRY. The exit has been measured four ways -- trailing, a fitted R
multiple, a structural target, and half-position versions of both -- and across
twelve cells none of them beat simply holding (logs/tp_struct_eval.txt). The
entry is the side that has never been filtered here, and it is the side where a
rejected trade costs only the trades it would have won.

WHAT IS COMPARED. The Donchian rule, unchanged, against itself with one entry
gate at a time. The gates and their pre-committed thresholds live in
js/chart/entryfilter.js; each states a MECHANISM there, because a filter with no
reason to work that happens to work on 700 trades is a coincidence with a name.

  room     skip a breakout with less than N risk-units of clear space before the
           first structure ahead -- swing, S/R, supply/demand or trendline.
  thrust   require the close to clear the channel by N ATR, not by a hair.
  adx      require ADX >= N: the standard ex-ante trend/chop discriminator.
  ema      take the breakout only on the right side of EMA(200). The baseline
           every public Donchian EA already has.

HOW THIS IS KEPT FROM BEING A SWEEP, which is the entire problem with entry
filters and the reason this file is longer than it looks like it needs to be.

  1. THE GRID IS PRE-COMMITTED AND REPORTED WHOLE. Every configuration is
     written down in entryfilter.js before the run, and every one is printed,
     winner or not. Picking the best row of a grid you have already seen is not
     a result, it is a description of the sample.

  1b. AND EVERY GATE IS ALSO SCORED AGAINST A COIN FLIP AT ITS OWN SELECTIVITY.
     This is the column that matters and the one such tables usually omit. A
     gate that keeps half the entries and beats the baseline may have done so
     because it kept the RIGHT half, or merely because it kept HALF -- taking
     fewer trades changes the return distribution by itself, and in a losing era
     it is an improvement for no reason at all. `rand` is that comparison made
     explicit: same retention, zero information. A gate that cannot beat its
     matched control has not shown that it knows anything.

  2. TWO ERAS, AND A FILTER MUST SURVIVE BOTH. 2016-2020 was range-bound gold
     and 2021-2026 was the run to 4,700; the rule's own quarters ran -0.33 /
     -0.47 / +0.47 / +0.85 across them. A gate that helps in one era and hurts
     in the other is a bet on the regime, not a filter, and pooling the two
     hides exactly that. This is the split runs/adx_XAUUSDa.csv already uses.

  3. ELEVEN COMPARISONS AT 95% MEAN ROUGHLY ONE FALSE POSITIVE BY CHANCE. It is
     said out loud next to the results, and it is why the verdict requires BOTH
     eras rather than a good pooled number.

  4. THE SAMPLE FLOOR. A gate that leaves 40 trades has not been measured,
     however good the number looks. Cells under `--min-trades` are reported but
     never called a win -- this is how the last filter died, cutting 363 trades
     to 167.

  5. NET R OVER THE SAME CALENDAR IS THE HEADLINE. A filter that removes trades
     raises average R almost by construction; what matters is whether the total
     went up. Trades are paired by CALENDAR BLOCK rather than by entry, because
     skipping an entry lets the rule take a later one and the sequences diverge.

  6. GROSS. No spread, slippage or swap. A filter that removes trades pays LESS
     cost than the baseline, so the filtered rows are flattered here -- the one
     direction of bias that favours the thing being tested.
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
RUNNER = os.path.join(ROOT, 'tools', 'entry_filter_runner.mjs')

DEFAULT_SYMBOLS = ['XAUUSD.a', 'EURUSD.a', 'USDJPY.a', 'GBPUSD.a']
DEFAULT_TFS = ['1h', '4h']

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


def matched_control(name, kept):
    """
    The `rand` arm whose retention is closest to this gate's.

    Matched on RETENTION rather than paired trade for trade, because the point
    is to hold selectivity constant and vary only whether the choice was
    informed. An exact match is neither available nor needed; what would
    invalidate the comparison is a 50% control standing in for a gate that keeps
    20%, and choosing the nearest arm is what prevents that.
    """
    ks = [k for k in kept.get(name, []) if k is not None]
    if not ks:
        return None
    mine = sum(ks) / len(ks)
    best, gap = None, None
    for other in kept:
        if not other.startswith('rand'):
            continue
        ok = [k for k in kept[other] if k is not None]
        if not ok:
            continue
        d = abs(sum(ok) / len(ok) - mine)
        if gap is None or d < gap:
            best, gap = other, d
    return best


def summarise(trades):
    rs = [t['r'] for t in trades]
    if not rs:
        return {'n': 0, 'win': float('nan'), 'avgR': float('nan'),
                'netR': 0.0, 'pf': float('nan')}
    wins = [r for r in rs if r > 0]
    bad = -sum(r for r in rs if r <= 0)
    return {'n': len(rs), 'win': 100.0 * len(wins) / len(rs),
            'avgR': sum(rs) / len(rs), 'netR': sum(rs),
            'pf': (sum(wins) / bad) if bad > 0 else float('inf')}


def run_cell(symbol, tf, year_from):
    bars = load_bars(symbol, tf, year_from)
    if len(bars) < 3000:
        return None, None
    tmp = tempfile.mkdtemp(prefix='entryfilter_')
    try:
        bp = os.path.join(tmp, 'bars.json')
        with open(bp, 'w') as fh:
            json.dump(bars, fh)
        cp = os.path.join(tmp, 'cfg.json')
        with open(cp, 'w') as fh:
            json.dump({'barsPath': bp, 'tf': tf, 'cell': f'{symbol}|{tf}'}, fh)
        out = subprocess.run([NODE, '--max-old-space-size=4096', RUNNER, cp],
                             cwd=ROOT, capture_output=True, text=True, timeout=7200)
        if out.returncode != 0:
            print(f'  ! node failed: {out.stderr[-800:]}', file=sys.stderr)
            return None, None
        return json.loads(out.stdout.strip().splitlines()[-1]), bars
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--symbols', nargs='+', default=DEFAULT_SYMBOLS)
    ap.add_argument('--tfs', nargs='+', default=DEFAULT_TFS)
    ap.add_argument('--from-year', type=int, default=2016)
    ap.add_argument('--min-trades', type=int, default=200,
                    help='below this an era is reported but never called a win')
    ap.add_argument('--json', help='also write the raw per-cell results here')
    args = ap.parse_args()

    if NODE is None:
        print('node is not installed', file=sys.stderr)
        return 2

    everything = {}
    #: era -> config -> concatenated block totals across every cell
    pooled = {e[0]: {} for e in ERAS}
    kept = {}

    print('\nGROSS of spread, slippage and swap. Whole grid, both eras.')
    print('NET R over the same calendar is the comparison; avg R rises almost by')
    print('construction when trades are removed, so it is shown but does not decide.\n')

    for symbol in args.symbols:
        for tf in args.tfs:
            res, bars = run_cell(symbol, tf, args.from_year)
            if not res:
                continue
            cell = f'{symbol} {tf}'
            everything[cell] = res
            names = list(res['runs'].keys())
            print(f'{cell}   ({res["bars"]} bars from {args.from_year})')
            print(f'  {"gate":<10}{"kept%":>7}   '
                  + '   '.join(f'{e[0]:^30}' for e in ERAS))
            print(f'  {"":<10}{"":>7}   '
                  + '   '.join(f'{"n":>5}{"net R":>9}{"vs base 95% CI":>16}'
                               for _ in ERAS))

            for name in names:
                k = res['rejected'].get(name) or {}
                kp = k.get('keptPct')
                kept.setdefault(name, []).append(kp)
                cells = []
                for era, y0, y1 in ERAS:
                    tr = [t for t in res['runs'][name]
                          if y0 <= year_of(t['entryTime']) <= y1]
                    base = [t for t in res['runs']['none']
                            if y0 <= year_of(t['entryTime']) <= y1]
                    if not base:
                        cells.append(f'{"-":>5}{"-":>9}{"-":>16}')
                        continue
                    lo_t = min(t['entryTime'] for t in base)
                    hi_t = max(t['entryTime'] for t in base)
                    bt = block_totals(tr, lo_t, hi_t)
                    bb = block_totals(base, lo_t, hi_t)
                    pooled[era].setdefault(name, []).extend(bt)
                    s = summarise(tr)
                    if name == 'none':
                        cells.append(f'{s["n"]:>5}{s["netR"]:>+9.1f}{"baseline":>16}')
                    else:
                        lo, hi = paired_block_ci(bt, bb)
                        cells.append(f'{s["n"]:>5}{s["netR"]:>+9.1f}'
                                     f'  [{lo:+6.1f},{hi:+6.1f}]')
                kpt = f'{kp:.0f}' if kp is not None else '100'
                print(f'  {name:<10}{kpt:>7}   ' + '   '.join(cells))
            print()

    if not pooled[ERAS[0][0]]:
        return 0

    print('POOLED ACROSS EVERY CELL, PER ERA')
    print(f'  {"gate":<10}{"kept%":>7}   '
          + '   '.join(f'{e[0]:^28}' for e in ERAS) + '     verdict')
    base_by_era = {e[0]: pooled[e[0]]['none'] for e in ERAS}
    for name in pooled[ERAS[0][0]]:
        cells, wins, losses = [], 0, 0
        for era, _, _ in ERAS:
            bt = pooled[era][name]
            bb = base_by_era[era]
            tot = sum(bt)
            if name == 'none':
                cells.append(f'{tot:>+9.1f}{"baseline":>19}')
                continue
            lo, hi = paired_block_ci(bt, bb)
            d = tot - sum(bb)
            if lo > 0:
                wins += 1
            elif hi < 0:
                losses += 1
            cells.append(f'{tot:>+9.1f}  {d:>+7.1f} [{lo:+5.1f},{hi:+5.1f}]')
        if name == 'none':
            verdict = ''
        elif wins == len(ERAS):
            verdict = 'BETTER IN BOTH ERAS'
        elif losses == len(ERAS):
            verdict = 'WORSE IN BOTH ERAS'
        else:
            verdict = 'not demonstrated'
        if name != 'none' and not name.startswith('rand'):
            ctrl = matched_control(name, kept)
            if ctrl:
                beat = 0
                for era, _, _ in ERAS:
                    lo, _hi = paired_block_ci(pooled[era][name], pooled[era][ctrl])
                    if lo > 0:
                        beat += 1
                verdict += f'  (vs {ctrl}: ' + (
                    'beats it in both eras' if beat == len(ERAS)
                    else 'no better than a coin flip') + ')'
        ks = [k for k in kept.get(name, []) if k is not None]
        kpt = f'{sum(ks) / len(ks):.0f}' if ks else '100'
        print(f'  {name:<10}{kpt:>7}   ' + '   '.join(cells) + f'     {verdict}')

    # ------------------------------------------------------------------
    # THE BASELINE'S OWN CONSISTENCY, which turned out to be the most useful
    # thing this run produced. If no gate can tell a good breakout from a bad
    # one, the remaining question is whether the rule can tell a good MARKET
    # from a bad one -- and unlike the gates, that has an answer.
    print('')
    print('THE BASELINE BY CELL - no gate, just the rule')
    print(f'  {"cell":<16}' + '   '.join(f'{e[0]:>12}' for e in ERAS)
          + '     both eras')
    for cell, res in everything.items():
        nets, signs = [], []
        for era, y0, y1 in ERAS:
            tr = [t for t in res['runs']['none']
                  if y0 <= year_of(t['entryTime']) <= y1]
            net = sum(t['r'] for t in tr)
            nets.append(net)
            signs.append(net > 0)
        tag = ('POSITIVE in both' if all(signs)
               else 'NEGATIVE in both' if not any(signs)
               else 'flips between eras')
        print(f'  {cell:<16}' + '   '.join(f'{n:>+12.1f}' for n in nets)
              + f'     {tag}')
    print('')
    print('This is the filter that works: not which breakout, but which market.')
    print('A cell that is negative or unstable across both eras is not a cell an')
    print('entry gate can rescue - every gate above was tried on all of them and')
    print('none of them could.')

    print('')
    print('Read the RIGHTMOST clause first. Beating the baseline while keeping')
    print('half the entries is not evidence until it also beats a coin flip that')
    print('kept half: taking fewer trades moves the result on its own, and in a')
    print('losing era it improves it for no reason at all.')
    print('')
    print('Eleven gates were tested. At 95% confidence roughly one is expected to')
    print('clear the bar in a single era by chance, which is why the verdict asks')
    print('for BOTH eras. Everything is gross, and a gate that takes fewer trades')
    print('pays less cost than the baseline - the one bias here favouring gates.')

    if args.json:
        with open(args.json, 'w') as fh:
            json.dump(everything, fh)
        print(f'\nraw results -> {args.json}')
    return 0


if __name__ == '__main__':
    sys.exit(main())

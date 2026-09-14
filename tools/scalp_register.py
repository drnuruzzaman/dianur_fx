#!/usr/bin/env python
"""
scalp_register.py — measure every Rayo cell and write the registry.

    python tools/scalp_register.py --stop-atr 5.0          # measure -> runs/
    python tools/scalp_register.py --stop-atr 5.0 --apply  # ... and register it

THE NUMBERS IN configs/alerts.json USED TO BE TYPED IN BY HAND from a scratch
script that no longer exists, which is how the portfolio claim in `quality`
came to be unreproducible (see rayo.py). This file is that script, kept. Every
figure a cell carries is produced here and nowhere else, and the settings that
produced it are written into the file beside them.

WHAT EACH FIGURE MEANS, stated once so the four surfaces that display them
cannot each invent their own gloss:

    n                 fills. Tickets that expired unfilled are not trades.
    win_pct           share of fills closed at the target, GROSS -- a fill that
                      hit TP1 and then lost to costs still counts as a win here,
                      because win rate and expectancy answer different questions.
    eras              MEAN NET R PER FILL inside each sub-era, costs and swap in.
    expected_net_r    the MEDIAN of those four, not the mean of the pooled
                      trades. A single era carrying the result is the failure
                      mode this is chosen to resist.
    worst/best        min and max of the four.
    total_r_per_year  total net R over the window, divided by its years. This is
                      the number that decides whether a cell is worth having:
                      R per fill can be excellent on nine trades a year.
    drawdown_r        worst peak-to-trough of the cumulative net R curve. In R,
                      so it is comparable across instruments and scale-free in
                      account size.

TRADEABLE IS FOUR TESTS, NOT ONE. `expected_net_r > 0` alone would promote a
cell that is positive per fill and makes nothing:

    1. median sub-era > 0          the edge is not one era
    2. total_r_per_year > 0        the signs agree
    3. n >= MIN_FILLS              it is not too thin to distinguish from luck
    4. keeps STRESS_KEEP of itself  at STRESS x the spread, the edge survives

Test 4 was added 2026-09-14 and immediately changed the answer. `spread_points_now`
is what the broker quoted on the day it was captured; it widens on news, at the
roll, and on a worse account. Run the median sub-era again at 1.5x that spread
and EURUSD 15m goes +0.0124 -> -0.0046 and GBPUSD 15m +0.0082 -> -0.0062, while
XAUUSD 15m lands on +0.0009, which is zero. Tests 1-3 promote all three. They
are the cells a hand-written note had already warned about -- "positive at the
19 points captured today and zero at 24" -- and this is that judgement made
mechanical instead of remembered.

AND THE TEST IS A FRACTION, NOT A SIGN, WHICH WAS CHOSEN AFTER SEEING THE DATA
-- recorded here because that is exactly the kind of decision that later reads
as though it had always been obvious. `stress > 0` passed XAUUSD 15m on +0.0009,
which is not a margin, it is a rounding artefact wearing a plus sign. Requiring
the stressed median to retain STRESS_KEEP of the unstressed one asks the
question the sign test was trying to ask. At 0.25 the answer separates cleanly:
XAU 15m keeps 7%, and every 30m/1h cell keeps 48-93%. Nothing sits near the
line, so the constant is not carrying the result.

Test 3 is the one that was applied by hand before and never written down: XAU 4h
passes 1 and 2 with +0.0548 R on 243 fills and was marked journal-only anyway,
correctly, because 26 trades a year at +1.4 R/yr cannot carry an account. Cells
excluded ONLY by thinness are marked `thin` so the reason is legible.

THE HORIZON IS PER TIMEFRAME. A trade unresolved after the horizon is scored
`open` and contributes nothing. 24h is right for 1m-1h and absurd for 4h, where
it is six bars -- the trades would be abandoned rather than resolved. 1d needs a
month.

1d IS MEASURABLE NOW AND WAS NOT BEFORE. It was registered `measurable: false`
because the session gate rejected every daily bar (all of them are stamped at
broker hour 0). The gate is off, so the bars now produce tickets. That is a
consequence of turning the session off, not a new discovery, and it is why this
tool measures every row rather than trusting the flag it finds.
"""

import argparse
import io
import json
import os
import shutil
import statistics
import sys
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pandas as pd                                             # noqa: E402

from tools.scalp_portfolio import Cell, run_arm                 # noqa: E402
from tools import scalper as SC                                 # noqa: E402

CFG = os.path.join(ROOT, 'configs', 'alerts.json')

#: Era boundaries. 2021-01-01, NOT 12-31 -- getting that wrong once made eight
#: registered cells irreproducible.
ERAS = [('2017-19', '2017-01-01', '2019-01-01'),
        ('2019-21', '2019-01-01', '2021-01-01'),
        ('2021-23', '2021-01-01', '2023-01-01'),
        ('2023-26', '2023-01-01', '2026-09-14')]

#: Hours before an unresolved trade is abandoned, by execution frame.
HORIZON_H = {'1m': 24, '5m': 24, '15m': 24, '30m': 24, '1h': 24,
             '4h': 120, '1d': 720}

#: The thinness floor, about 42 fills a year over a nine-year window.
MIN_FILLS = 400

#: Spread multiple a cell must stay positive at. 1.5x, because the captured
#: quote is a fair-weather number and 24/19 -- the gold widening that was
#: already known to flip the 15m cell -- is 1.26x.
STRESS = 1.5

#: ...and how much of the edge must survive it. See the docstring: a bare sign
#: test passes a cell whose stressed median is +0.0009.
STRESS_KEEP = 0.25


def drawdown_r(nets):
    """Worst peak-to-trough of the cumulative R curve, in R."""
    peak = cum = worst = 0.0
    for x in nets:
        cum += x
        peak = max(peak, cum)
        worst = max(worst, peak - cum)
    return worst


def measure(symbol, tf, start, end, session, args):
    h = HORIZON_H.get(tf, 24)
    c = Cell(symbol, tf, start, end, session, args.stop_atr, args.swing,
             args.fast, args.slow, args.expire)
    if not c.tk:
        return {'measurable': False, 'n': 0}
    tr = run_arm([c], None, horizon_ms=h * 3600 * 1000, slots=1)
    if not tr:
        return {'measurable': False, 'n': 0}

    nets = [t['net'] for t in tr]
    eras = {}
    for name, a, b in ERAS:
        lo = int(pd.Timestamp(a).value // 10 ** 6)
        hi = int(pd.Timestamp(b).value // 10 ** 6)
        sel = [t['net'] for t in tr if lo <= t['ms'] < hi]
        if sel:
            eras[name] = round(sum(sel) / len(sel), 4)
    vals = list(eras.values())
    years = (pd.Timestamp(end) - pd.Timestamp(start)).days / 365.25
    return {
        'measurable': True,
        'expected_net_r': round(statistics.median(vals), 4) if vals else None,
        'total_r_per_year': round(sum(nets) / years, 2),
        'worst_era_net_r': min(vals) if vals else None,
        'best_era_net_r': max(vals) if vals else None,
        'n': len(tr),
        'win_pct': round(100.0 * sum(1 for t in tr if t['gross'] > 0) / len(tr), 1),
        'drawdown_r': round(drawdown_r(nets), 1),
        'horizon_hours': h,
        'eras': eras,
    }


def stress_median(symbol, tf, start, end, session, args):
    """The median sub-era again, at STRESS x the captured spread.

    MEASURED, NOT EXTRAPOLATED. Subtracting an extra spread charge from the
    stored mean would be arithmetic on an average and would miss that a wider
    spread also changes WHICH trades clear -- the fill is unaffected but the
    cost is per-trade and the cheap trades are not the winning ones.
    """
    h = HORIZON_H.get(tf, 24)
    c = Cell(symbol, tf, start, end, session, args.stop_atr, args.swing,
             args.fast, args.slow, args.expire, 0.0, STRESS)
    tr = run_arm([c], None, horizon_ms=h * 3600 * 1000, slots=1)
    if not tr:
        return None
    vals = []
    for _, a, b in ERAS:
        lo = int(pd.Timestamp(a).value // 10 ** 6)
        hi = int(pd.Timestamp(b).value // 10 ** 6)
        sel = [t['net'] for t in tr if lo <= t['ms'] < hi]
        if sel:
            vals.append(sum(sel) / len(sel))
    return round(statistics.median(vals), 4) if vals else None


def verdict(m, control):
    """tradeable / thin / journal, and the sentence that says why."""
    if not m.get('measurable'):
        return False, False, ('NOT MEASURABLE. The rule produced no resolvable '
                              'ticket on this frame over the window.')
    exp, ryr, n = m['expected_net_r'], m['total_r_per_year'], m['n']
    agree = (exp is not None and exp > 0 and ryr > 0)
    thin = agree and n < MIN_FILLS
    stress = m.get('stress_net_r')
    fragile = (agree and not thin and stress is not None
               and stress < STRESS_KEEP * exp)
    ok = agree and not thin and not fragile

    if control:
        return False, thin, (
            'CONTROL, expected to lose. %s is negative on every instrument -- '
            'the cost fraction, not the entry. Measured %+0.4f R per fill, '
            '%+0.1f R/yr, drawdown %.1f R.'
            % (m.get('_tf', 'this frame'), exp, ryr, m['drawdown_r']))
    if ok:
        return True, False, (
            'Tradeable. Measured with swap on %d fills, %.1f%% win, drawdown '
            '%.1f R. Median of four sub-eras (%+0.4f) and total R/yr (%+0.1f) '
            'agree in sign, and it keeps %.0f%% of the edge (%+0.4f) at %.1fx '
            'the captured spread.'
            % (n, m['win_pct'], m['drawdown_r'], exp, ryr,
               (100.0 * stress / exp) if stress is not None else float('nan'),
               stress if stress is not None else float('nan'), STRESS))
    if fragile:
        return False, False, (
            'Journal only -- SPREAD-FRAGILE. Positive as measured (%+0.4f R on '
            'the median sub-era, %+0.1f R/yr over %d fills) but only %+0.4f at '
            '%.1fx the captured spread, which is %.0f%% of it -- under the %.0f%% '
            'a cell has to keep. The edge is about the size of a plausible '
            'widening, so it is a quote, not a margin.'
            % (exp, ryr, n, stress, STRESS, 100.0 * stress / exp,
               100.0 * STRESS_KEEP))
    if thin:
        return False, True, (
            'Journal only -- THIN. Positive on the median sub-era (%+0.4f) and '
            'on total R/yr (%+0.1f), but only %d fills in the window, about %d '
            'a year. Too few to tell from luck, and too little return to carry '
            'an account.' % (exp, ryr, n, round(n / 9.7)))
    return False, False, (
        'Journal only. Median sub-era %+0.4f and total R/yr %+0.1f do not both '
        'clear zero (%d fills, %.1f%% win, drawdown %.1f R). Signals are '
        'recorded, not traded.'
        % (exp if exp is not None else 0.0, ryr, n, m['win_pct'], m['drawdown_r']))


def _meta(args):
    return {
        'measured_on': date.today().isoformat(),
        'stop_atr': args.stop_atr, 'swing': args.swing,
        'fast': args.fast, 'slow': args.slow, 'expire': args.expire,
        'exit_tp': args.exit_tp, 'session': args.session,
        'window': [args.start, args.end],
        'min_fills': MIN_FILLS, 'spread_stress_mult': STRESS,
        'spread_stress_keep': STRESS_KEEP,
        'eras': [e[0] for e in ERAS],
        'command': ('python tools/scalp_register.py --stop-atr %g --from %s '
                    '--to %s --session %s'
                    % (args.stop_atr, args.start, args.end, args.session)),
    }


def _save(args, rows):
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with io.open(args.out, 'w', encoding='utf-8') as fh:
        fh.write(json.dumps({'meta': _meta(args), 'watch': rows}, indent=2,
                            ensure_ascii=False) + '\n')


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--stop-atr', type=float, default=5.0)
    ap.add_argument('--from', dest='start', default='2017-01-01')
    ap.add_argument('--to', dest='end', default='2026-09-14')
    ap.add_argument('--session', default='none')
    ap.add_argument('--swing', type=int, default=20)
    ap.add_argument('--fast', type=int, default=20)
    ap.add_argument('--slow', type=int, default=50)
    ap.add_argument('--expire', type=int, default=12)
    ap.add_argument('--exit-tp', type=int, default=1, choices=(1, 2, 3))
    ap.add_argument('--out', default=os.path.join(ROOT, 'runs', 'scalp_register.json'))
    ap.add_argument('--resume', action='store_true',
                    help='keep cells already measured in --out')
    ap.add_argument('--apply', action='store_true',
                    help='merge the measurement into configs/alerts.json')
    args = ap.parse_args()

    SC.EXIT_TP[0] = args.exit_tp
    session = SC.parse_session(args.session)

    with io.open(CFG, encoding='utf-8') as fh:
        cfg = json.load(fh)
    watch = cfg['scalper']['watch']

    # RESUMED FROM --out, and --out is rewritten after EVERY cell. A 1m cell
    # is eight minutes of work and the first run of this tool died on row 7
    # (a missing '1d' key), throwing away six cells that were already correct.
    done = {}
    if args.resume and os.path.exists(args.out):
        with io.open(args.out, encoding='utf-8') as fh:
            prev = json.load(fh)
        if prev.get('meta', {}).get('stop_atr') == args.stop_atr:
            done = {(r['symbol'], r['tf']): r for r in prev.get('watch', [])}
            print('resuming: %d cell(s) already measured at stop %g'
                  % (len(done), args.stop_atr))

    rows = []
    print('%-12s %-4s %7s %7s %10s %9s %8s %9s  %s'
          % ('symbol', 'tf', 'fills', 'win%', 'median R', 'R/yr', 'dd R',
             '@%.1fx spr' % STRESS, 'verdict'))
    print('-' * 96)
    for c in watch:
        sym, tf = c['symbol'], c['tf']
        # A CACHED ROW GIVES BACK THE MEASUREMENT, NOT THE VERDICT. The
        # expensive part is walking nine years of 1m bars; deciding what the
        # result means is free, and re-deciding it is the whole point of a
        # resumed run -- the first version returned the stored row intact and so
        # silently skipped a newly added test.
        if (sym, tf) in done:
            prev_row = done[(sym, tf)]
            m = {k: v for k, v in prev_row.items()
                 if k not in ('symbol', 'tf', 'enabled', 'tradeable', 'control',
                              'note', 'thin', 'stress_net_r')}
            sys.stderr.write('  cached %s %s (re-deciding)\n' % (sym, tf))
        else:
            sys.stderr.write('  measuring %s %s ...\n' % (sym, tf))
            m = measure(sym, tf, args.start, args.end, session, args)
        # THE STRESS ARM ONLY RUNS ON CANDIDATES. It is a second full pass
        # over the cell, and a cell that is already negative as measured cannot
        # be rescued by charging it more.
        if (m.get('measurable') and m.get('expected_net_r') is not None
                and m['expected_net_r'] > 0 and m['total_r_per_year'] > 0
                and m['n'] >= MIN_FILLS):
            sys.stderr.write('    stress %.1fx spread ...\n' % STRESS)
            m['stress_net_r'] = stress_median(sym, tf, args.start, args.end,
                                              session, args)
        m['_tf'] = tf
        trade, thin, note = verdict(m, c.get('control', False))
        m.pop('_tf', None)
        row = dict(symbol=sym, tf=tf, enabled=c.get('enabled', False),
                   tradeable=trade, control=c.get('control', False), **m)
        if thin:
            row['thin'] = True
        row['note'] = note
        rows.append(row)
        if m.get('measurable'):
            st = m.get('stress_net_r')
            print('%-12s %-4s %7d %6.1f%% %+10.4f %+9.1f %8.1f %9s  %s'
                  % (sym, tf, m['n'], m['win_pct'], m['expected_net_r'],
                     m['total_r_per_year'], m['drawdown_r'],
                     ('%+.4f' % st) if st is not None else '-',
                     'TRADEABLE' if trade else ('thin' if thin else 'journal')))
        else:
            print('%-12s %-4s %7s  %s' % (sym, tf, '-', 'not measurable'))
        _save(args, rows)
        sys.stdout.flush()

    meta = _meta(args)
    _save(args, rows)
    print('\nwrote %s' % os.path.relpath(args.out, ROOT))
    print('%d tradeable, %d thin, %d journal, %d not measurable'
          % (sum(1 for r in rows if r['tradeable']),
             sum(1 for r in rows if r.get('thin')),
             sum(1 for r in rows if not r['tradeable'] and not r.get('thin')
                 and r.get('measurable')),
             sum(1 for r in rows if not r.get('measurable'))))

    if not args.apply:
        print('\nnothing written to configs/alerts.json -- pass --apply')
        return 0

    shutil.copy2(CFG, CFG + '.pre-stop%g.bak' % args.stop_atr)
    cfg['scalper']['watch'] = rows
    cfg['scalper']['registered'] = meta['measured_on']
    cfg['scalper']['measured_with'] = meta['command']
    cfg['scalper']['measurement'] = meta
    cfg['scalper']['rule'] = (
        'rayo_scalper break mode: swing %d, EMA %d/%d, stop %g ATR, entry 0.10 '
        'ATR beyond the swing, exit TP%d (0.9R), expire %d bars, SESSION OFF '
        '(24h) -- the gate measured +42%% on total R/yr and is switched off by '
        'request; pass --session 7-21 to compare against pre-2026-09-10 numbers.'
        % (args.swing, args.fast, args.slow, args.stop_atr, args.exit_tp,
           args.expire))
    with io.open(CFG, 'w', encoding='utf-8') as fh:
        fh.write(json.dumps(cfg, indent=2, ensure_ascii=False) + '\n')
    print('registered into configs/alerts.json (backup alongside)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

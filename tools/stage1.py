#!/usr/bin/env python
"""
stage1.py — the kill switch. Run every strategy against its own time-shifted
signal schedule, with a sample floor and a bootstrap drawdown.

    python tools/stage1.py --tfs 1h,4h --shifts 60

Prints one row per cell with the gates. No tuning happens here: this only
decides whether tuning is worth doing.
"""
import argparse, os, sys, time
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim.core import Config
from sim.fx import FX
from sim.instruments import account_currency, load, spec
from sim.robust import bootstrap, gates, time_shift_distribution
from sim.strategies import BASELINES as _B  # noqa: F401
from sim.strategies import MTF_STRATEGIES as _MTF
from sim.strategies import Donchian, EmaCross, MeanRevert

BASELINES = {**_B, **_MTF}

#: the context frame the MTF variants are gated on, and how far back to load it
CONTEXT_TF = '1d'
CONTEXT_LEN = 20
CONTEXT_LEAD_YEARS = 3


def make_factory(name, cls, symbol, tf, end):
    """
    A zero-argument factory for `time_shift_distribution`, which constructs the
    strategy once per shift.

    The MTF variants need their context series, and it is loaded with several
    years of LEAD so the context channel is already formed at the first
    execution bar -- otherwise the filter silently returns 0 (no opinion) for
    the first stretch of the window and the cell measures a different rule at
    the start than at the end.
    """
    if name not in _MTF:
        return cls
    ctx_start = '%d-01-01' % (int((end or '2027')[:4]) - 40)
    ctx = load(symbol, CONTEXT_TF, ctx_start, end)
    return lambda: cls(context_bars=ctx, context_tf=CONTEXT_TF,
                       exec_tf=tf, context_len=CONTEXT_LEN)


def _passes(g):
    """
    All FOUR gates.

    Beating the control is not the same as making money, and making money is not
    the same as making enough of it to be worth the risk or to survive the cost
    model being somewhat wrong. See sim.robust.MIN_EFFECT_R.
    """
    return bool(g.get('gate_sample') and g.get('gate_beats_control')
                and g.get('gate_profitable') and g.get('gate_effect'))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--symbols', default='XAUUSD.a,USDJPY.a')
    ap.add_argument('--tfs', default='1h,4h')
    ap.add_argument('--start', default='2021-01-01')
    ap.add_argument('--end', default=None,
                    help='exclusive upper bound, e.g. 2021-01-01 for a pre-2021 '
                         'out-of-sample run')
    ap.add_argument('--strategies', default=','.join(BASELINES),
                    help='subset of: ' + ','.join(BASELINES))
    ap.add_argument('--label', default='',
                    help='suffix for runs/stage1<label>.csv, so an out-of-sample '
                         'run cannot overwrite the in-sample one it is judged against')
    ap.add_argument('--swap', action='store_true',
                    help='charge overnight carry. Off by default because the '
                         'baselines were built to compare SIGNALS, but gold pays '
                         '-82.92 points a night to be long and that is -0.12 R a '
                         'trade -- more than spread and slippage together. Any '
                         'result meant to describe a tradeable edge needs this on.')
    ap.add_argument('--shifts', type=int, default=60)
    ap.add_argument('--min-trades', type=int, default=200)
    args = ap.parse_args()

    chosen = {k: v for k, v in BASELINES.items() if k in args.strategies.split(',')}
    if not chosen:
        sys.exit('no known strategy in %r; pick from %s'
                 % (args.strategies, ','.join(BASELINES)))

    cfg = Config(risk_pct=0.5, apply_swap=args.swap)
    fx = FX.build(account_currency())                # sizing needs AUD conversion
    rows = []
    for sym in args.symbols.split(','):
        for tf in args.tfs.split(','):
            bars = load(sym, tf, args.start, args.end)
            if bars.empty:
                print('  %-10s %-3s no bars in %s..%s' % (sym, tf, args.start, args.end))
                continue
            span = '%s..%s' % (bars.index[0].date(), bars.index[-1].date())
            for name, cls in chosen.items():
                t0 = time.time()
                factory = make_factory(name, cls, sym, tf, args.end)
                real, ctrl = time_shift_distribution(
                    bars, spec(sym, tf), cfg, factory, sym, tf,
                    n_shifts=args.shifts, fx=fx)
                g = gates(real.trades, ctrl, args.min_trades)
                g.update(bootstrap(real.trades))
                g.update({'symbol': sym, 'tf': tf, 'strategy': name, 'span': span,
                          'bars': len(bars),
                          'ctrl_max_bars': ctrl.attrs.get('max_bars') if len(ctrl) else None,
                          'secs': round(time.time() - t0, 1)})
                rows.append(g)
                print('  %-10s %-3s %-10s trades=%-5s avgR=%+.4f ctrl_p50=%+.4f '
                      'pct=%s sample=%s pass=%s'
                      % (sym, tf, name, g.get('trades'), g.get('avg_R', 0),
                         g.get('control_avgR_p50', 0), g.get('percentile_vs_control'),
                         'OK' if g.get('gate_sample') else 'LOW',
                         'YES' if _passes(g) else 'no'), flush=True)

    df = pd.DataFrame(rows)
    cols = ['symbol', 'tf', 'strategy', 'span', 'trades', 'gate_sample', 'avg_R', 'pf',
            'gate_profitable', 'gate_effect',
            'control_avgR_p50', 'control_avgR_p95',
            'percentile_vs_control', 'gate_beats_control',
            'dd_historical', 'dd_p95', 'prob_net_negative']
    print('\n=== STAGE 1 GATES ===')
    print(df[[c for c in cols if c in df.columns]].to_string(index=False))
    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       'runs', 'stage1%s.csv' % args.label)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    df.to_csv(out, index=False)
    print('\nwrote %s' % out)
    passed = df[df.apply(_passes, axis=1)]
    print('cells passing ALL FOUR gates: %d of %d' % (len(passed), len(df)))
    if len(passed):
        print(passed[['symbol', 'tf', 'strategy', 'trades', 'avg_R', 'pf',
                      'percentile_vs_control']].to_string(index=False))


if __name__ == '__main__':
    main()

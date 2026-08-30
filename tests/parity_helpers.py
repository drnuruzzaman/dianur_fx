"""
Shared plumbing for the JS-versus-Python strategy parity tests.

One place to drive node and one place to run the engine, because the thing being
tested is that two implementations agree -- and a test suite that reimplements
the comparison per strategy has the same defect it is checking for.

The engine is what gets compared, never a hand-rolled walk of `on_bar`. The
first version of these tests did the latter and got 215 intents against the
module's 205: the harness modelled the position gate but no stop, so once the
engine had been stopped out the harness still believed it held and went on
raising channel exits. Any harness faithful enough to compare against IS the
engine.
"""

import json
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

NODE = shutil.which('node')

#: module -> the export that walks a whole series and returns {trades, ...}
JS_ENTRY = {
    'donchian': ('js/chart/donchian.js', 'signalsAsOf'),
    'donchian_ema200': ('js/chart/donchian.js', 'donchianRule|emaLen=200'),
    'ema_cross': ('js/chart/emacross.js', 'emaCrossRule'),
    'turtle_ea': ('js/chart/turtle_ea.js', 'turtleEaRule'),
}


def load_cell(symbol, tf, start, end):
    from sim.instruments import load
    try:
        return load(symbol, tf, start, end)
    except Exception:                                     # noqa: BLE001
        pytest.skip('no bars on disk for %s %s' % (symbol, tf))


def python_trades(bars, symbol, tf, strategy):
    """
    Round trips from the real engine: entry bar, side, stop, exit reason.

    Costs are left on -- the panel quotes the raw rule and the engine pays
    spread and slippage, so PRICES differ by design and only timing, side, stop
    and reason are compared by the callers.
    """
    from sim.core import Config, Simulator
    from sim.fx import FX
    from sim.instruments import account_currency, spec
    from sim.strategies import BASELINES

    # fx must be REAL. With fx=None the risk budget is never converted out of
    # the account currency, so 125 AUD against a JPY pair's per-lot risk of
    # ~41,000 rounds to zero lots and EVERY signal is skipped -- the cell
    # reported no trades at all and read as a broken rule rather than broken
    # sizing. The same mistake collapsed the first Stage 1 harness.
    res = Simulator(spec(symbol, tf), fx=FX.build(account_currency()),
                    config=Config(risk_pct=0.5, apply_swap=False)).run(
                        bars, BASELINES[strategy](), symbol, tf)
    idx = {t: k for k, t in enumerate(bars.index)}
    return [{'entry_i': idx[t.entry_time], 'side': int(t.side),
             'stop': float(t.stop_price), 'reason': t.exit_reason,
             'exit_i': idx[t.exit_time]} for t in res.trades.itertuples()]


def js_trades(bars, strategy, tmp_path):
    """Drive the JS rule under node and return its trade list."""
    rel, export = JS_ENTRY[strategy]
    payload = [{'t': int(t.value // 10 ** 6), 'o': r.open, 'h': r.high,
                'l': r.low, 'c': r.close} for t, r in bars.iterrows()]
    barf = tmp_path / 'bars.json'
    barf.write_text(json.dumps(payload), encoding='utf-8')
    url = os.path.join(ROOT, rel).replace(os.sep, '/').replace('C:', 'file:///C:')
    rules_url = (os.path.join(ROOT, 'js', 'chart', 'rules.js')
                 .replace(os.sep, '/').replace('C:', 'file:///C:'))
    # `signalsAsOf` walks a series itself; a bare rule object needs the walker.
    # `name|k=v` overrides a default, so a parameterised variant can be driven
    # without a second JS file -- the rule IS the same object, which is the
    # point of testing the variant at all.
    if '|' in export:
        name, override = export.split('|', 1)
        k, v = override.split('=', 1)
        call = ("rules.runRule(bars, mod.%s, { ...mod.%s.defaults, %s: %s })"
                % (name, name, k, v))
    elif export.endswith('AsOf'):
        call = 'mod.%s(bars)' % export
    else:
        call = 'rules.runRule(bars, mod.%s)' % export
    script = '''
      import fs from 'node:fs';
      const mod = await import('%s');
      const rules = await import('%s');
      const bars = JSON.parse(fs.readFileSync(process.argv[1], 'utf8'));
      const sig = %s;
      console.log(JSON.stringify({ trades: sig.trades, state: sig.state }));
    ''' % (url, rules_url, call)
    res = subprocess.run([NODE, '--input-type=module', '-e', script, str(barf)],
                         cwd=ROOT, capture_output=True, text=True, timeout=900)
    if res.returncode != 0:
        pytest.fail('node failed: %s' % res.stderr[-1500:])
    return json.loads(res.stdout)['trades']


def compare(want, got, label, check_stop=True):
    """
    Same entry bar, same side, same stop to 1e-9, same exit bar and reason.

    The engine skips a signal it cannot size into whole lots and the panel has
    no opinion on sizing, so only entries both took are compared -- and at least
    90% of them must be shared, or the two are not running the same rule.

    `check_stop=False` FOR MANAGED STOPS, and it does NOT mean "compare less".
    The exit checks become tolerances with a stated floor rather than
    equalities, because the divergence is structural rather than a concession. The engine fills at `open + spread + slippage` while the
    JS walker fills at the raw open -- deliberate, and harmless for every rule
    whose stop is derived from the SIGNAL CLOSE, because that value is identical
    on both sides. A stop that is later moved to break-even or trailed is
    derived from the FILL instead, so the two cost models put it in genuinely
    different places, and `stop_price` records the final one.

    The costs cannot simply be switched off for the comparison: the recorded
    spread column is populated on 100% of bars for this cell, so
    `_spread_price` charges whatever config says. And `Trade` carries no
    `risk_price`, so the original stop cannot be reconstructed from the Python
    side either.

    A different stop price also means the stop is TOUCHED on a different bar, so
    the divergence cascades into exit timing. Measured on turtle_ea: sides and
    exit reasons agree on 100% of shared trades, and exit bars on 94% (2 of 34
    on 4h, 2 of 38 on 1h), the stragglers landing 5 and 14 bars apart. So the
    floor below is 90% -- above what the cost model explains, and low enough
    that it cannot pass a rule that genuinely disagrees.

    What is fully verified either way is the ENTRY: which bar, which way. For
    this strategy that is the whole filter stack -- channels, regime MA, ADX,
    the consensus vote and the ATR guard -- which is the part worth checking.
    """
    shared = {t['entry_i'] for t in want} & {t['entryI'] for t in got}
    assert len(shared) >= 0.9 * max(len(want), len(got)), (
        '%s: only %d entries shared of %d python / %d js'
        % (label, len(shared), len(want), len(got)))

    w = {t['entry_i']: t for t in want if t['entry_i'] in shared}
    g = {t['entryI']: t for t in got if t['entryI'] in shared}
    for i in sorted(shared):
        assert g[i]['side'] == w[i]['side'], '%s: side at entry bar %d' % (label, i)
        assert g[i]['reason'] == w[i]['reason'], (
            '%s: exit reason for entry %d: js %r python %r'
            % (label, i, g[i]['reason'], w[i]['reason']))
        if check_stop:
            assert g[i]['stop'] == pytest.approx(w[i]['stop'], rel=0, abs=1e-9), (
                '%s: STOP at entry bar %d: js %.10f python %.10f'
                % (label, i, g[i]['stop'], w[i]['stop']))
            assert g[i]['exitI'] == w[i]['exit_i'], (
                '%s: exit bar for entry %d' % (label, i))

    if not check_stop:
        agree = sum(1 for i in shared if g[i]['exitI'] == w[i]['exit_i'])
        assert agree >= 0.9 * len(shared), (
            '%s: exit bars agree on only %d of %d shared trades -- a managed '
            'stop explains a few, not this many' % (label, agree, len(shared)))

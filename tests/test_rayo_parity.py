"""
The Rayo Scalper's two implementations must post the same ticket.

`sim/strategies/rayo.py` and `js/chart/scalper.js` are a hand-kept mirror: the
Python scores the backtest and the browser draws the live panel and the Signal
Board. Nothing checked they agreed until 2026-09-14, when they did not.

WHAT WENT WRONG, and why it hid for so long. The Python rounded every price to
THREE DECIMALS -- `round(float(entry), 3)` -- which is exactly right for gold
(2 digits) and yen (3), and destroys a 5-digit instrument. EURUSD tickets came
out as entry 1.146 / SL 1.141 / TP1 1.15, so the rounding step of 0.0005 was
10% of a 30m stop and essentially 100% of a 1m one. The JS never rounded. Every
EUR/GBP/AUD number in configs/alerts.json was measured through that, and the 1m
control cells reported 10-25% win rates that read as a strategy result and were
a formatting error.

A gold-only parity check would still pass today. These tests run a 5-digit
instrument for that reason, and compare with NO TOLERANCE: both sides compute
from the same closes with the same arithmetic, so anything above floating-point
noise is a real divergence.
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

#: One 2-digit instrument and one 5-digit one. The second is the regression.
CELLS = [('XAUUSD.a', '30m'), ('EURUSD.a', '30m')]
WINDOW = ('2025-01-01', '2025-04-01')


def _bars(symbol, tf):
    from sim.instruments import load
    try:
        b = load(symbol, tf, *WINDOW)
    except Exception:                                     # noqa: BLE001
        pytest.skip('no bars on disk for %s %s' % (symbol, tf))
    if len(b) < 300:
        pytest.skip('too few bars for %s %s' % (symbol, tf))
    return b


def _payload(bars):
    return [{'t': int(t.value // 10 ** 6), 'o': float(r.open), 'h': float(r.high),
             'l': float(r.low), 'c': float(r.close)} for t, r in bars.iterrows()]


@pytest.mark.parametrize('symbol,tf', CELLS)
def test_ticket_prices_keep_instrument_precision(symbol, tf):
    """entry - SL is EXACTLY the risk, on every instrument.

    The cheapest possible statement of the rounding bug: the stop is defined as
    `stop_atr x ATR` from the entry, so if a ticket's own numbers do not satisfy
    that identity, the prices have been mangled between computing them and
    storing them. Under round(x, 3) this failed on EURUSD by up to 10%.
    """
    from sim.strategies.rayo import DEFAULTS, tickets
    tk = tickets(_bars(symbol, tf), 'break', 20, 20, 50, DEFAULTS['stop_atr'], None)
    assert tk, 'no tickets for %s %s' % (symbol, tf)
    for t in tk[:400]:
        assert abs(abs(t['entry'] - t['sl']) - t['risk']) < 1e-9, (
            '%s %s: entry-SL %.8f is not the risk %.8f'
            % (symbol, tf, abs(t['entry'] - t['sl']), t['risk']))
        # TP1 sits 0.9R from the entry, in the direction of the trade.
        want = t['entry'] + (0.9 if t['side'] == 'buy' else -0.9) * t['risk']
        assert abs(t['tp'][0] - want) < 1e-9


def test_five_digit_prices_are_not_rounded_to_three():
    """The regression, stated as the thing a reader would actually notice.

    A EURUSD entry of 1.146 is not a EURUSD price. Kept separate from the
    identity test above because it is the SYMPTOM: the identity says the numbers
    are inconsistent, this says what they looked like.
    """
    from sim.strategies.rayo import DEFAULTS, tickets
    tk = tickets(_bars('EURUSD.a', '30m'), 'break', 20, 20, 50,
                 DEFAULTS['stop_atr'], None)
    assert tk
    finer = sum(1 for t in tk if round(t['entry'], 3) != t['entry'])
    assert finer > 0.5 * len(tk), (
        'EURUSD entries are landing on 3-decimal prices: %d of %d carry more '
        'precision' % (finer, len(tk)))


@pytest.mark.skipif(NODE is None, reason='node not installed')
@pytest.mark.parametrize('symbol,tf', CELLS)
def test_js_twin_agrees(symbol, tf, tmp_path):
    """Same side, entry, SL, ladder and trend age, on both implementations."""
    from sim.strategies.rayo import DEFAULTS, tickets

    bars = _bars(symbol, tf)
    want = {t['ms']: t for t in tickets(bars, 'break', 20, 20, 50,
                                        DEFAULTS['stop_atr'], None)}
    barf = tmp_path / 'bars.json'
    barf.write_text(json.dumps(_payload(bars)), encoding='utf-8')
    url = (os.path.join(ROOT, 'js', 'chart', 'scalper.js')
           .replace(os.sep, '/').replace('C:', 'file:///C:'))
    # THE JS DECIDES ON bars.length - 2, the last CLOSED bar, so the slice that
    # asks it about bar i is bars[0 .. i+1]. Every seventh bar keeps the test
    # near a second while still covering hundreds of decisions.
    script = '''
      import fs from 'node:fs';
      const mod = await import('%s');
      const bars = JSON.parse(fs.readFileSync(process.argv[1], 'utf8'));
      const out = {};
      for (let i = 120; i < bars.length - 1; i += 7) {
        const t = mod.ticket(bars.slice(0, i + 2), { session: null });
        if (t) out[bars[i].t] = { side: t.side, entry: t.entry, sl: t.stop,
                                  tp: t.tp, age: t.age };
      }
      console.log(JSON.stringify(out));
    ''' % url
    res = subprocess.run([NODE, '--input-type=module', '-e', script, str(barf)],
                         cwd=ROOT, capture_output=True, text=True, timeout=900)
    if res.returncode != 0:
        pytest.fail('node failed: %s' % res.stderr[-1500:])
    got = json.loads(res.stdout)
    assert len(got) > 50, 'JS produced almost no tickets (%d)' % len(got)

    checked = 0
    for ms, g in got.items():
        p = want.get(int(ms))
        assert p is not None, 'JS posted a ticket at %s and Python did not' % ms
        checked += 1
        assert g['side'] == p['side'], ms
        assert abs(g['entry'] - p['entry']) < 1e-9, (ms, g['entry'], p['entry'])
        assert abs(g['sl'] - p['sl']) < 1e-9, (ms, g['sl'], p['sl'])
        assert abs(g['tp'][0] - p['tp'][0]) < 1e-9, (ms, g['tp'][0], p['tp'][0])
        assert g['age'] == p['age'], (ms, g['age'], p['age'])
    assert checked > 50


@pytest.mark.skipif(NODE is None, reason='node not installed')
def test_stop_atr_matches_across_twins():
    """One number, two files. DEFAULTS['stop_atr'] and STOP_ATR are the rule."""
    from sim.strategies.rayo import DEFAULTS
    url = (os.path.join(ROOT, 'js', 'chart', 'scalper.js')
           .replace(os.sep, '/').replace('C:', 'file:///C:'))
    res = subprocess.run(
        [NODE, '--input-type=module', '-e',
         "const m = await import('%s'); console.log(m.STOP_ATR);" % url],
        cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert res.returncode == 0, res.stderr[-800:]
    assert float(res.stdout.strip()) == float(DEFAULTS['stop_atr'])

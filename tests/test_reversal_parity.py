"""
The reversal lamp's EMA witness, JS versus Python, bar for bar.

WHY ONLY THE EMA WITNESS. It is the only one of the four that was MEASURED --
tools/rayo_flip_exit_eval.py and tools/rayo_flip_stage1.py both close on
`up = ef > es` turning against the trade -- so it is the only one where the
chart can contradict a number this project has published. CHoCH already has
tests/test_ms_parity.py, RSI is the same Wilder series indicators.js draws, and
the higher-frame sign is injected by the caller rather than computed here.

WHAT WOULD BREAK WITHOUT IT. The lamp says "EMA" on a live ticket using a
second implementation of the same pair the backtest used to reject closing on
it. If the two ever disagree, the chart is warning about a flip the measurement
never saw -- which is precisely the failure this repo keeps writing tests to
avoid (see js/chart/scalper.js, "A MIRROR OF tools/scalper.py, and the risk
that comes with one").
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

CASES = [
    ('XAUUSD.a', '15m', '2025-01-01', '2026-09-11'),
    ('XAUUSD.a', '1h', '2024-01-01', '2026-08-21'),
    ('USDJPY.a', '15m', '2025-01-01', '2026-08-21'),
]


def _bars(symbol, tf, start, end):
    from sim.instruments import load
    try:
        df = load(symbol, tf, start, end)
    except Exception:                                     # noqa: BLE001
        pytest.skip('no bars on disk for %s %s' % (symbol, tf))
    if len(df) < 200:
        pytest.skip('too few bars for %s %s' % (symbol, tf))
    return df


def _js_against(bars_payload, side, tmp_path):
    """[bool] per CLOSED bar, from js/chart/reversal.js's own EMA witness."""
    barf = tmp_path / 'bars.json'
    barf.write_text(json.dumps(bars_payload), encoding='utf-8')
    url = (os.path.join(ROOT, 'js', 'chart', 'reversal.js')
           .replace(os.sep, '/').replace('C:', 'file:///C:'))
    # THE WITNESS IS PROBED THROUGH THE PUBLIC FUNCTION, one fill at a time,
    # rather than by exporting the internals for the test. A test that reaches
    # past the API can pass while the thing the chart calls is broken.
    script = '''
      import fs from 'node:fs';
      const mod = await import('%s');
      const bars = JSON.parse(fs.readFileSync(process.argv[1], 'utf8'));
      const side = process.argv[2];
      const out = [];
      for (let i = 0; i < bars.length - 1; i++) {
        // bars.slice(0, i + 2): the forming bar is dropped inside reversalFor,
        // so i + 2 makes bar i the last CLOSED one.
        const rev = mod.reversalFor(bars.slice(0, i + 2),
                                    { side, fillMs: bars[0].t },
                                    { htfSign: 0, graceBars: 0 });
        out.push(rev === null ? null
                 : rev.witnesses.find((w) => w.key === 'ema').on);
      }
      console.log(JSON.stringify(out));
    ''' % url
    res = subprocess.run([NODE, '--input-type=module', '-e', script,
                          str(barf), side],
                         cwd=ROOT, capture_output=True, text=True, timeout=1800)
    if res.returncode != 0:
        pytest.fail('node failed: %s' % res.stderr[-1500:])
    return json.loads(res.stdout)


@pytest.mark.skipif(NODE is None, reason='node not installed')
@pytest.mark.parametrize('symbol,tf,start,end', CASES)
@pytest.mark.parametrize('side', ['buy', 'sell'])
def test_ema_witness_matches_python(symbol, tf, start, end, side, tmp_path):
    from sim.strategies.rayo import ema

    df = _bars(symbol, tf, start, end)
    # A BOUNDED SLICE. The witness is a per-bar function of the EMAs, so 400
    # bars exercise it as thoroughly as 5000 and the node probe is O(n^2) --
    # it re-runs the whole detector per bar, deliberately, because that is how
    # the chart calls it.
    df = df.iloc[:400]
    payload = [{'t': int(t.value // 10 ** 6), 'o': r.open, 'h': r.high,
                'l': r.low, 'c': r.close} for t, r in df.iterrows()]

    # THE PYTHON SIDE IS THE MEASUREMENT'S OWN LINE, copied from
    # tools/rayo_flip_stage1.py: `up = ef > es`, then negated for a long.
    ef = ema(df['close'], 20).to_numpy(float)
    es = ema(df['close'], 50).to_numpy(float)
    up = ef > es
    want = [(not bool(up[i])) if side == 'buy' else bool(up[i])
            for i in range(len(df) - 1)]

    got = _js_against(payload, side, tmp_path)
    assert len(got) == len(want)

    # NULLS ARE THE MODULE DECLINING TO SPEAK -- fewer than slow + 2 closed
    # bars -- and are not a disagreement. Everything it DOES answer must match.
    checked = 0
    for i, (w, g) in enumerate(zip(want, got)):
        if g is None:
            assert i < 60, 'reversal.js went quiet at bar %d, long past warm-up' % i
            continue
        assert g == w, ('EMA witness disagrees at bar %d (%s %s %s): '
                        'js=%s python=%s' % (i, symbol, tf, side, g, w))
        checked += 1
    assert checked > 250, 'only %d bars actually compared' % checked

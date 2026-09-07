"""
The fitted STOP, checked for the ways a measured width can flatter itself.

`js/chart/stopfit.js` places the stop at a quantile of the HEAT a favourable
path takes -- the worst adverse excursion suffered before the favourable peak --
so it is measured in ATR and never in R. That distinction is the whole design:
R is *defined* by the stop, so a stop measured in R measures itself.

This file used to test a fitted TAKE-PROFIT too, and most of it was that. The
take-profit is gone -- from the walker, from both panels and from this suite --
because logs/tp_struct_eval.txt measured it across twelve cells out of sample
and no target beat the trailing exit on net R. `logs/tp_struct_eval.txt` holds
that run. What is left is the stop, and the two things that could make it
dishonest:

  1. CIRCULARITY. If the width depended on a stop width, the number would mean
     nothing. Checked behaviourally: passing a wildly different `stopAtr` must
     not move the answer, because it is not an input.

  2. A CLAIM THAT IS NOT RE-CHECKED. The width is rounded to a hundredth of an
     ATR after the quantile is taken, and rounding moves the survival rate it
     claims to deliver. `survival` is therefore counted against the ROUNDED
     width, which is the number actually drawn.

    python -m pytest tests/test_stopfit.py -q
"""

import json
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NODE = shutil.which('node')

pytestmark = pytest.mark.skipif(NODE is None, reason='node is not installed')


def run_node(script):
    out = subprocess.run([NODE, '--input-type=module', '-e', script],
                         cwd=ROOT, capture_output=True, text=True, timeout=180)
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout.strip().splitlines()[-1])


#: A deterministic series with both directions in it, so neither side's
#: excursion distribution is degenerate.
SYNTH = """
function bars(n) {
  const out = []; let t = 1700000000000;
  for (let i = 0; i < n; i++) {
    const p = 100 + Math.sin(i / 11) * 3 + Math.sin(i / 43) * 7 + i * 0.004;
    const o = p - 0.05, c = p + 0.05;
    out.push({ t: t + i * 900000, o, h: Math.max(o, c) + 0.4,
               l: Math.min(o, c) - 0.4, c, v: 1 });
  }
  return out;
}
"""


def test_the_stop_is_chosen_without_reference_to_a_stop():
    """
    The circularity guard. `fitStop` must not depend on `stopAtr` -- if it did,
    the stop would be measuring itself and the number would mean nothing.
    Checked behaviourally: passing a wildly different `stopAtr` through the
    options must not change the answer, because it is not an input.
    """
    script = SYNTH + """
    import { fitStop } from './js/chart/stopfit.js';
    const b = bars(1600);
    const a = fitStop(b, { side: 1, horizon: 40, q: 0.75 });
    const c = fitStop(b, { side: 1, horizon: 40, q: 0.75, stopAtr: 9.0 });
    console.log(JSON.stringify({ same: a.atr === c.atr, atr: a.atr,
                                 source: a.source }));
    """
    got = run_node(script)
    assert got['same'] is True, got
    assert got['source'] == 'measured'

def test_the_stop_delivers_the_survival_it_claims():
    """
    The stop is placed at the q-th percentile of the heat a favourable path
    takes, so it must survive about q of those paths. Counted against the
    ROUNDED width, which is the number actually drawn.
    """
    script = SYNTH + """
    import { fitStop } from './js/chart/stopfit.js';
    const out = {};
    for (const q of [0.5, 0.75, 0.9]) {
      const f = fitStop(bars(1600), { side: 1, horizon: 40, q });
      out[q] = { atr: f.atr, survival: f.survival };
    }
    console.log(JSON.stringify(out));
    """
    got = run_node(script)
    for q in ('0.5', '0.75', '0.9'):
        assert abs(got[q]['survival'] - float(q)) < 0.03, (q, got[q])
    # and a wider quantile must give a wider stop
    assert got['0.5']['atr'] < got['0.75']['atr'] < got['0.9']['atr'], got

def test_a_short_series_gives_an_assumed_stop():
    script = SYNTH + """
    import { fitStop } from './js/chart/stopfit.js';
    const f = fitStop(bars(120), { side: 1, horizon: 40, fallbackAtr: 2.0 });
    console.log(JSON.stringify({ atr: f.atr, source: f.source,
                                 survival: f.survival }));
    """
    got = run_node(script)
    assert got['source'] == 'fallback'
    assert got['atr'] == 2.0

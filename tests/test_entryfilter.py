"""
Entry gates: what they may do, what they may see, and whether they are honest.

An entry filter is the easiest way to overfit a strategy -- there are unlimited
things to condition on and the sample shrinks with every one -- so the tests
here are mostly about the ways a gate can look like it works without working.

  1. IT MAY ONLY SUPPRESS ENTRIES. A gate that could reach an exit would be a
     second exit rule competing with the channel that carries the edge.
     sim/strategies/emafilter.py records what happened the last time a gate
     reached further than its name suggested: two strategies silently became
     different strategies and nothing failed loudly.

  2. IT MAY SEE NOTHING PAST ITS OWN BAR. The usual contract, and the usual
     reason to check it: a filter shown the future is indistinguishable from a
     good one.

  3. UNMEASURABLE IS NOT REJECTED. During warmup ADX is NaN. Treating that as
     "not trending" silently drops every early trade, and the resulting sample
     looks improved when it has only been truncated.

  4. THE NEGATIVE CONTROL MUST ACTUALLY BE ONE. `rand` exists so that "took
     fewer trades" can be told apart from "took better trades", and it is the
     row the whole grid is read against. Its first implementation kept 75% of
     entries while claiming 50%, because `^` returns a signed int32 -- a control
     nobody checks is worse than no control.

  5. THE FAST PATH IS THE SAME PATH. `levels.js` hands its detectors a bounded
     window instead of every bar from zero, which is what makes the `room` gate
     runnable over ten years of 1h bars. It is an optimisation and must return
     exactly what the unbounded version returns.

    python -m pytest tests/test_entryfilter.py -q
"""

import json
import os
import shutil
import subprocess
import tempfile

import numpy as np
import pandas as pd
import pytest

from sim.indicators import adx as py_adx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NODE = shutil.which('node')

pytestmark = pytest.mark.skipif(NODE is None, reason='node is not installed')


def run_node(script):
    out = subprocess.run([NODE, '--input-type=module', '-e', script],
                         cwd=ROOT, capture_output=True, text=True, timeout=600)
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout.strip().splitlines()[-1])


SYNTH = """
function bars(n) {
  const out = []; let t = 1700000000000;
  for (let i = 0; i < n; i++) {
    const p = 100 + Math.sin(i / 11) * 3 + Math.sin(i / 43) * 7 + i * 0.004;
    const o = p - 0.05, c = p + 0.05;
    out.push({ t: t + i * 3600000, o, h: Math.max(o, c) + 0.4,
               l: Math.min(o, c) - 0.4, c, v: 1 });
  }
  return out;
}
"""


def test_the_js_adx_is_the_python_adx():
    """
    A PARITY TEST, because there are now two implementations and the JS one
    decides trades in the browser while the Python one decides them in the
    engine. Directional movement is the part people get wrong -- +DM and -DM are
    exclusive, and taking both turns a trend gauge into a volatility gauge that
    passes every smoke test.
    """
    rng = np.random.default_rng(4)
    n = 400
    c = 100 + np.cumsum(rng.normal(0, 0.5, n))
    h = c + np.abs(rng.normal(0, 0.4, n))
    lo = c - np.abs(rng.normal(0, 0.4, n))
    o = c + rng.normal(0, 0.1, n)
    want = py_adx(pd.DataFrame({'open': o, 'high': h, 'low': lo, 'close': c}), 14)

    rows = [{'t': i * 3600000, 'o': o[i], 'h': h[i], 'l': lo[i], 'c': c[i], 'v': 1}
            for i in range(n)]
    # Written to a file rather than inlined: 400 bars of JSON in an argv exceeds
    # the Windows command-line limit and fails as "filename too long", which
    # reads like a path bug rather than a size one.
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'bars.json').replace(os.sep, '/')
        with open(path, 'w') as fh:
            json.dump(rows, fh)
        got = run_node("""
        import fs from 'node:fs';
        import { adxSeries } from './js/chart/entryfilter.js';
        const b = JSON.parse(fs.readFileSync(%s, 'utf8'));
        console.log(JSON.stringify(adxSeries(b, 14)));
        """ % json.dumps(path))

    pairs = [(a, b) for a, b in zip(want, got)
             if a == a and b is not None]
    assert len(pairs) > 300, len(pairs)
    assert max(abs(a - b) for a, b in pairs) < 1e-9
    # and they must start in the same place: a shifted series is still "close"
    first_py = int(np.argmax(np.isfinite(want)))
    first_js = next(i for i, v in enumerate(got) if v is not None)
    assert first_py == first_js, (first_py, first_js)


def test_a_gate_can_only_suppress_entries_never_exits():
    """
    Driven by the bluntest possible gate: one that says no to everything. It
    must produce zero trades -- not trades that never close, and not the
    baseline's trades with their exits moved.
    """
    script = SYNTH + """
    import { runRule } from './js/chart/rules.js';
    import { donchianRule } from './js/chart/donchian.js';
    const b = bars(1600);
    const base = runRule(b, donchianRule, { tf: '1h' });
    const none = runRule(b, donchianRule, { tf: '1h', entryFilter: () => false });
    const all = runRule(b, donchianRule, { tf: '1h', entryFilter: () => true });
    /* WHAT THE GATE WAS SHOWN, over the series AND its mirror image. SYNTH
       drifts up and breaks out long only, so a one-series check would assert
       "never shown an exit" on a sample containing no shorts either -- and
       would pass just as happily if the gate were shown FLAT intents on the
       side it never saw. */
    const down = b.map((x) => ({ ...x, o: 200 - x.o, h: 200 - x.l,
                                 l: 200 - x.h, c: 200 - x.c }));
    const seen = [];
    for (const series of [b, down]) {
      runRule(series, donchianRule, { tf: '1h',
        entryFilter: (ctx) => { seen.push(ctx.side); return true; } });
    }
    console.log(JSON.stringify({
      base: base.trades.length,
      rejected: { trades: none.trades.length, open: none.position ? 1 : 0 },
      permitted: all.trades.length,
      identical: JSON.stringify(all.trades) === JSON.stringify(base.trades),
      sidesShown: [...new Set(seen)].sort(),
    }));
    """
    got = run_node(script)
    assert got['base'] > 0, got
    # rejecting everything leaves nothing -- no trades and no open position
    assert got['rejected'] == {'trades': 0, 'open': 0}, got
    # permitting everything must be byte-identical to having no gate at all
    assert got['identical'] is True, got
    # and the gate is never shown a FLAT (exit) intent
    assert got['sidesShown'] == [-1, 1], got
    assert 0 not in got['sidesShown'], got     # FLAT is an exit, never gated


def test_a_gate_that_throws_is_treated_as_no_gate():
    """
    A filter erroring on one bar must not quietly turn into "take no trades",
    which is a configuration that looks flat rather than broken.
    """
    script = SYNTH + """
    import { runRule } from './js/chart/rules.js';
    import { donchianRule } from './js/chart/donchian.js';
    const b = bars(1600);
    const base = runRule(b, donchianRule, { tf: '1h' });
    const bad = runRule(b, donchianRule, { tf: '1h',
      entryFilter: () => { throw new Error('boom'); } });
    console.log(JSON.stringify({
      base: base.trades.length, bad: bad.trades.length,
      identical: JSON.stringify(bad.trades) === JSON.stringify(base.trades),
    }));
    """
    got = run_node(script)
    assert got['base'] > 0 and got['identical'] is True, got


def test_a_gate_sees_nothing_past_its_own_bar():
    """
    The usual contract. Checked by handing the gate a series whose future has
    been replaced with garbage and demanding the same decisions.
    """
    script = SYNTH + """
    import { runRule } from './js/chart/rules.js';
    import { donchianRule } from './js/chart/donchian.js';
    import { makeFilter } from './js/chart/entryfilter.js';
    const b = bars(1600);
    const KEEP = 900;
    const wild = b.slice(0, KEEP).concat(b.slice(KEEP).map((x, i) => ({
      ...x, o: x.o * 3 + i, h: x.h * 3 + i + 5, l: x.l * 3 + i - 5, c: x.c * 3 + i,
    })));
    const out = {};
    for (const [kind, th] of [['room', 0.2], ['thrust', 0.4], ['adx', 25],
                              ['ema', 200], ['rand', 0.5]]) {
      const grab = (series) => {
        const seen = [];
        runRule(series, donchianRule, { tf: '1h',
          entryFilter: (ctx) => {
            const ok = makeFilter(kind, th, { tf: '1h', cell: kind + series.length })(ctx);
            if (ctx.i < KEEP) seen.push([ctx.i, ctx.side, ok]);
            return ok;
          } });
        return JSON.stringify(seen);
      };
      out[kind] = grab(b) === grab(wild);
    }
    console.log(JSON.stringify(out));
    """
    got = run_node(script)
    for kind, same in got.items():
        assert same is True, f'{kind} decisions changed when the future changed'


def test_an_unmeasurable_indicator_does_not_reject():
    """
    ADX is NaN through its warmup. If that counted as "not trending" the gate
    would drop every early trade and the sample would look improved when it had
    only been truncated.
    """
    script = SYNTH + """
    import { makeFilter, adxSeries } from './js/chart/entryfilter.js';
    const b = bars(400);
    const s = adxSeries(b, 14);
    const f = makeFilter('adx', 30, {});
    const warm = [];
    for (let i = 0; i < 20; i++) {
      if (!Number.isFinite(s[i])) warm.push(f({ i, side: 1, view: b }));
    }
    const emaF = makeFilter('ema', 200, {});
    const emaWarm = [];
    for (let i = 0; i < 20; i++) emaWarm.push(emaF({ i, side: 1, view: b,
                                                     signalPrice: b[i].c }));
    console.log(JSON.stringify({ n: warm.length, allTrue: warm.every(Boolean),
                                 emaAllTrue: emaWarm.every(Boolean) }));
    """
    got = run_node(script)
    assert got['n'] > 0, got
    assert got['allTrue'] is True, got
    assert got['emaAllTrue'] is True, got


def test_the_negative_control_keeps_what_it_claims_and_is_deterministic():
    """
    `rand` is the row the whole grid is read against: it is what "took fewer
    trades" looks like with no information in it. Its first version kept 75%
    while claiming 50%, because `^` returns a signed int32 and a negative
    modulo passed unconditionally.
    """
    script = """
    import { makeFilter } from './js/chart/entryfilter.js';
    const f = makeFilter('rand', 0.5, {});
    let pass = 0, n = 0;
    for (let i = 0; i < 40000; i++) {
      for (const side of [1, -1]) { n += 1; if (f({ i, side })) pass += 1; }
    }
    /* sparse indices too: real signals are not consecutive, and a hash can be
       uniform over a run and biased over a stride */
    let p2 = 0, n2 = 0;
    for (let i = 0; i < 40000; i += 37) { n2 += 1; if (f({ i, side: 1 })) p2 += 1; }
    const twice = f({ i: 12345, side: 1 }) === f({ i: 12345, side: 1 });
    console.log(JSON.stringify({ kept: pass / n, sparse: p2 / n2, twice }));
    """
    got = run_node(script)
    assert abs(got['kept'] - 0.5) < 0.02, got
    assert abs(got['sparse'] - 0.5) < 0.03, got
    # deterministic: a control that moves between looks cannot be compared
    assert got['twice'] is True, got


def test_the_bounded_detector_window_changes_nothing():
    """
    `levels.js` hands its detectors a 1,200-bar window ending at the signal bar
    rather than every bar from zero -- constant time instead of linear, which is
    what makes the `room` gate runnable over ten years of 1h bars.

    It is an optimisation, so it must be invisible. This is also the test that
    fails if any detector's own lookback is ever raised past the window, which
    is the real reason it exists.
    """
    script = SYNTH + """
    import { obstaclesAhead } from './js/chart/levels.js';
    const b = bars(6000);
    let same = 0, differ = 0;
    for (const at of [1500, 2500, 3500, 4500, 5500]) {
      for (const side of [1, -1]) {
        const key = (l) => JSON.stringify(
          l.map((o) => [Number(o.price.toFixed(8)), o.kind]));
        const win = obstaclesAhead(b, { side, from: b[at].c, upto: at, tf: '1h' });
        const full = obstaclesAhead(b, { side, from: b[at].c, upto: at, tf: '1h',
                                         params: { windowBars: Infinity } });
        if (key(win) === key(full)) same += 1; else differ += 1;
        if (!win.length) differ += 0;
      }
    }
    /* not vacuous: it has to actually be finding obstacles */
    const found = obstaclesAhead(b, { side: 1, from: b[5500].c, upto: 5500,
                                      tf: '1h' }).length;
    console.log(JSON.stringify({ same, differ, found }));
    """
    got = run_node(script)
    assert got['differ'] == 0, got
    assert got['same'] == 10, got
    assert got['found'] > 0, got


def test_every_grid_configuration_builds_and_is_actually_asked():
    """
    A gate that is never asked would sit in the report looking like a tested
    idea. Cheap to check, and the kind of thing that rots silently when a
    threshold is edited.

    WHAT THIS DELIBERATELY DOES NOT ASSERT is a retention rate. Two attempts to
    did, and both were really assertions about SYNTH: `room` at its loosest
    threshold passes everything, because smooth sine waves have little of the
    structure it looks for, and `thrust` at its tightest rejects everything,
    because those breakouts clear their channel by a hair. Both are honest
    behaviour on a fixture that is not a market. Retention is a property of real
    bars, it is measured by tools/entry_filter_calibrate.mjs, and it is printed
    in every row of the eval -- which is where a broken threshold would show up
    as a number a reader can see, rather than as a green test.
    """
    script = SYNTH + """
    import { runRule } from './js/chart/rules.js';
    import { donchianRule } from './js/chart/donchian.js';
    import { GRID, gridConfigs, makeFilter } from './js/chart/entryfilter.js';
    /* 4h, NOT 1h: `paramsFor` turns "N=20" into a 79-bar channel on 1h and a
       20-bar one on 4h, so the same fixture yields four signals on one and
       hundreds on the other. Both directions, so the short side is exercised. */
    const b = bars(4000);
    const down = b.map((x) => ({ ...x, o: 200 - x.o, h: 200 - x.l,
                                 l: 200 - x.h, c: 200 - x.c }));
    const out = {};
    for (const c of gridConfigs()) {
      const f = makeFilter(c.kind, c.threshold, { tf: '4h', cell: c.name });
      if (!f) { out[c.name] = null; continue; }
      let seen = 0, passed = 0, threw = 0;
      for (const series of [b, down]) {
        runRule(series, donchianRule, { tf: '4h', entryFilter: (ctx) => {
          seen += 1;
          let ok;
          try { ok = f(ctx); } catch { threw += 1; ok = true; }
          if (typeof ok !== 'boolean') threw += 1;
          if (ok) passed += 1;
          return ok;
        } });
      }
      out[c.name] = { seen, threw, kept: seen ? passed / seen : null };
    }
    let unknown = null;
    try { makeFilter('nonsense', 1, {}); } catch (e) { unknown = 'threw'; }
    /* Counted from GRID rather than written down here: a hardcoded total is a
       second place to edit when a threshold is added, and it was already wrong
       once. */
    const expected = Object.values(GRID).reduce((n, v) => n + v.length, 0);
    console.log(JSON.stringify({ out, unknown, expected }));
    """
    got = run_node(script)
    rows = got['out']
    assert rows['none'] is None, rows
    built = {k: v for k, v in rows.items() if v is not None}
    # the whole pre-committed grid, minus the baseline
    assert len(built) == got['expected'], (sorted(built), got['expected'])
    for name, row in built.items():
        assert row['seen'] > 20, (name, row)      # asked, not decoration
        assert row['threw'] == 0, (name, row)     # and answers with a boolean
    # an unrecognised gate is a programming error, not a silent pass-through
    assert got['unknown'] == 'threw', got

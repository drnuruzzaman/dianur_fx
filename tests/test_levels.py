"""
The structural levels: what is in the way of a trade, and what they may know.

`js/chart/levels.js` walks five detectors -- swing extremes, S/R zones,
supply/demand bases, trendlines, and market structure's unbroken swing plus its
past BOS/CHoCH levels -- and returns what stands in front of a trade, nearest
first. The Donchian panel lists them and the chart marks them on the price
scale.

THEY ARE NOT TARGETS. This module used to choose one and hand it to the walker
as a take-profit; that is gone, along with the walker's ability to execute one.
`logs/tp_struct_eval.txt` holds the run it went on the strength of: twelve cells
out of sample, and no target beat the trailing exit on net R. What survives is
the DESCRIPTION, because an open trade at +7 R means nothing without knowing
whether the next resistance is two pips away or two hundred.

So the tests here are about what the levels are NOT allowed to know, and what
they must not do to a reader:

  1. NOTHING PAST THE SIGNAL BAR. This is the whole ballgame. Structure
     detectors are exactly the tools that look clairvoyant when accidentally
     shown the future -- a supply zone fitted with tomorrow's bars in view stops
     today's rally with uncanny precision, and nothing about the resulting chart
     looks wrong.

  2. NOTHING BEHIND THE TRADE. A "level ahead" that price has already passed is
     not ahead.

  3. NEAREST FIRST, AND NO DUPLICATES. Four detectors over 600 bars routinely
     find a dozen levels within two risk-units, several of them the same price
     wearing different names -- a swing high, the S/R zone clustered on it, and
     the BOS level it defines are one line on a chart and three rows in a list.

    python -m pytest tests/test_levels.py -q
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
                         cwd=ROOT, capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout.strip().splitlines()[-1])


#: A series with swings big enough for the detectors to find, and drift, so the
#: two sides are not mirror images of each other.
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
/* The same bars with a completely different future after `keep`. */
function wildAfter(b, keep) {
  return b.slice(0, keep).concat(b.slice(keep).map((x, i) => ({
    ...x, o: x.o * 3 + i, h: x.h * 3 + i + 5, l: x.l * 3 + i - 5, c: x.c * 3 + i,
  })));
}
"""


def test_the_levels_cannot_see_past_the_signal_bar():
    """
    THE CAUSALITY GUARD. Every detector is handed `bars[0..upto]`, so the levels
    found at bar 800 must be byte-identical whether the 800 bars that follow are
    the real series or nonsense.

    Checked at several cursors rather than one, because a single sample would
    pass on any window where the garbage happened not to produce a zone in
    range.
    """
    script = SYNTH + """
    import { displayLevels, obstaclesAhead } from './js/chart/levels.js';
    const b = bars(1600);
    const key = (l) => JSON.stringify(
      l.map((o) => [Number(o.price.toFixed(8)), o.kind]));
    const out = { same: 0, differ: 0, found: 0 };
    for (const at of [400, 600, 800, 1000, 1200]) {
      for (const side of [1, -1]) {
        const opt = { side, from: b[at].c, upto: at, tf: '1h' };
        const real = obstaclesAhead(b, opt);
        const wild = obstaclesAhead(wildAfter(b, at + 1), opt);
        if (real.length) out.found += 1;
        if (key(real) === key(wild)) out.same += 1; else out.differ += 1;
      }
    }
    /* the display path too -- it re-slices for its own ATR */
    const d1 = displayLevels(b, { side: 1, from: b[800].c, upto: 800, tf: '1h' });
    const d2 = displayLevels(wildAfter(b, 801),
                             { side: 1, from: b[800].c, upto: 800, tf: '1h' });
    out.displaySame = key(d1) === key(d2);
    console.log(JSON.stringify(out));
    """
    got = run_node(script)
    assert got['differ'] == 0, got
    assert got['same'] == 10, got
    assert got['displaySame'] is True, got
    # and the guard is not vacuous: it has to actually be finding levels
    assert got['found'] >= 6, got


def test_every_level_is_ahead_of_the_trade():
    """
    A level behind price is not in the way of anything. Filtered by direction,
    so a long's levels are always above its anchor and a short's always below.
    """
    script = SYNTH + """
    import { displayLevels } from './js/chart/levels.js';
    const b = bars(1600);
    const bad = [];
    for (let at = 300; at < 1500; at += 37) {
      for (const side of [1, -1]) {
        const from = b[at].c;
        for (const lv of displayLevels(b, { side, from, upto: at, tf: '1h' })) {
          if ((lv.price - from) * side <= 0) bad.push({ at, side, p: lv.price });
        }
      }
    }
    console.log(JSON.stringify({ bad: bad.slice(0, 5), n: bad.length }));
    """
    assert run_node(script)['n'] == 0


def test_levels_come_back_nearest_first_and_deduplicated():
    """
    NEAREST FIRST because the obstacle price meets first is the one that decides
    whether a trade gets paid, however much more impressive the next one is.

    DEDUPLICATED because a swing high, the S/R zone clustered on it and the BOS
    level it defines are one line on a chart. The survivor keeps the STRONGEST
    name -- "supply zone, fresh" tells a reader more than "HH high" about the
    same price.
    """
    script = SYNTH + """
    import { displayLevels } from './js/chart/levels.js';
    const b = bars(1600);
    const out = { unordered: 0, tooClose: 0, checked: 0, maxReturned: 0 };
    for (let at = 400; at < 1500; at += 29) {
      for (const side of [1, -1]) {
        const from = b[at].c;
        const ls = displayLevels(b, { side, from, upto: at, tf: '1h', max: 4 });
        out.checked += 1;
        out.maxReturned = Math.max(out.maxReturned, ls.length);
        for (let k = 1; k < ls.length; k++) {
          const nearer = Math.abs(ls[k - 1].price - from);
          const further = Math.abs(ls[k].price - from);
          if (further < nearer) out.unordered += 1;
          if (further === nearer) out.tooClose += 1;
        }
      }
    }
    console.log(JSON.stringify(out));
    """
    got = run_node(script)
    assert got['unordered'] == 0, got
    assert got['tooClose'] == 0, got
    assert got['maxReturned'] > 1, got      # not vacuous
    assert got['maxReturned'] <= 4, got     # and `max` is honoured


def test_near_duplicate_levels_merge_without_swallowing_the_list():
    """
    HALF AN ATR IS THE SAME PRICE -- and the test guards BOTH failures, because
    this went wrong in each direction.

    Too tight (a third of an ATR) left 4331.61 and 4332.55, 0.39 ATR apart,
    stacked as `TP` and `TP1`: two rows for one decision. Too loose (a whole
    ATR) collapsed all four levels on that same chart into one, because they sat
    0.39, 0.82 and 0.62 ATR apart. A rule that merges everything a reader can
    see is not a tidier list, it is a missing one -- so this asserts a floor on
    what survives as well as a gap between what does.

    And the survivor must be the BETTER-EVIDENCED one, whole. Taking the
    stronger candidate's name while keeping the weaker one's price would report
    a level no detector found: an S/R zone's strength attached to a swing high's
    price.
    """
    script = SYNTH + """
    import { displayLevels, obstaclesAhead } from './js/chart/levels.js';
    const b = bars(1600);
    /* ATR(14) at the cursor, the same quantity the gap is measured in */
    const atr = (upto) => {
      let prev = 0;
      for (let i = upto - 13; i <= upto; i++) {
        const p = b[i - 1].c;
        prev += Math.max(b[i].h - b[i].l, Math.abs(b[i].h - p), Math.abs(b[i].l - p));
      }
      return prev / 14;
    };
    const out = { pairsTooClose: 0, checked: 0, mismatched: 0,
                   maxShown: 0, multiRowCases: 0 };
    for (let at = 400; at < 1500; at += 23) {
      for (const side of [1, -1]) {
        const from = b[at].c;
        const shown = displayLevels(b, { side, from, upto: at, tf: '1h' });
        const raw = obstaclesAhead(b, { side, from, upto: at, tf: '1h' });
        out.checked += 1;
        const a = atr(at);
        out.maxShown = Math.max(out.maxShown || 0, shown.length);
        if (shown.length > 1) out.multiRowCases = (out.multiRowCases || 0) + 1;
        for (let k = 1; k < shown.length; k++) {
          if (Math.abs(shown[k].price - shown[k - 1].price) <= a * 0.5) {
            out.pairsTooClose += 1;
          }
        }
        /* every survivor must be a level some detector actually returned --
           price AND kind together, not one from each */
        for (const lv of shown) {
          if (!raw.some((o) => o.price === lv.price && o.kind === lv.kind)) {
            out.mismatched += 1;
          }
        }
      }
    }
    console.log(JSON.stringify(out));
    """
    got = run_node(script)
    assert got['checked'] > 40, got
    assert got['pairsTooClose'] == 0, got
    assert got['mismatched'] == 0, got
    # AND THE LIST MUST SURVIVE. If merging routinely left one row, the gap
    # assertion above would pass on an empty result -- which is how the
    # whole-ATR version looked correct.
    assert got['maxShown'] >= 3, got
    assert got['multiRowCases'] > got['checked'] * 0.5, got
    # AND NEVER A FOURTH. The default cap went 4 -> 3 by request; a panel that
    # printed TP4 again would do it silently, since nothing downstream slices.
    assert got['maxShown'] == 3, got


def test_clear_air_returns_nothing_rather_than_an_invented_level():
    """
    A breakout into space is the situation a trend rule earns from, and the
    honest answer there is an empty list. Inventing a level would put a number
    in front of exactly the trades that have nothing in front of them.
    """
    script = """
    import { displayLevels, obstaclesAhead } from './js/chart/levels.js';
    const B = (o, h, l, c) => ({ t: 1700000000000, o, h, l, c, v: 1 });
    const bars = [];
    for (let i = 0; i < 300; i++) bars.push(B(100, 100.5, 99.5, 100));
    for (let i = 0; i < 60; i++) {
      const p = 100 + i * 2;
      bars.push({ t: 1700000000000 + (300 + i) * 3600000,
                  o: p, h: p + 1, l: p - 1, c: p, v: 1 });
    }
    const at = bars.length - 1;
    const opt = { side: 1, from: bars[at].c, upto: at, tf: '1h' };
    console.log(JSON.stringify({ ahead: obstaclesAhead(bars, opt).length,
                                 shown: displayLevels(bars, opt).length }));
    """
    got = run_node(script)
    assert got == {'ahead': 0, 'shown': 0}, got


def test_market_structure_levels_are_included():
    """
    BOS and CHoCH were asked for by name alongside trendlines and swing points,
    so their absence would be silent. The unbroken swing -- the level whose break
    would BE the next BOS -- is a different object from any confirmed swing:
    structure tracks the ONE that is currently in play.
    """
    script = SYNTH + """
    import { obstaclesAhead } from './js/chart/levels.js';
    const b = bars(2400);
    const kinds = new Set();
    for (let at = 400; at < 2300; at += 17) {
      for (const side of [1, -1]) {
        for (const o of obstaclesAhead(b, { side, from: b[at].c, upto: at, tf: '1h' })) {
          kinds.add(o.kind);
        }
      }
    }
    console.log(JSON.stringify({ kinds: [...kinds].sort() }));
    """
    got = run_node(script)['kinds']
    for want in ('structure', 'swing', 'trendline'):
        assert want in got, (want, got)
    # BOS or CHoCH must appear -- both are market-structure breaks
    assert ('bos' in got) or ('choch' in got), got

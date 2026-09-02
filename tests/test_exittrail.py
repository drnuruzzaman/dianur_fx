"""
The trailing exit: what it may do to a trade, and what it may not.

A TRAIL IS NOT A TAKE-PROFIT, and the distinction is the only reason this exists
at all. A target caps the winner -- it says "this is far enough" -- which is a
bet against the tail a trend rule is paid from, and twelve cells out of sample
said not to take it (logs/tp_struct_eval.txt). A trail says nothing about how
far a move may go; it only decides when one is over. So the tests here are
mostly about keeping it on the right side of that line:

  1. IT MAY ONLY TIGHTEN. The rule's own channel exit stays exactly as it is,
     and the trail can only sit inside it. That makes the effective exit
     monotone: adding a trail can never produce a looser exit than the rule
     already had, so any measured difference is the trail acting rather than the
     rule being weakened.

  2. IT MAY NEVER CAP THE UPSIDE. A trail that moved away from price -- or that
     could be reached in the direction of profit -- would be a target wearing
     another name.

  3. IT RATCHETS. Once moved toward the trade it never retreats, or it is not a
     trail, it is a level that wanders.

  4. IT MAY SEE NOTHING PAST ITS OWN BAR. The usual contract, and the usual
     reason to check it: structure detectors shown the future look uncanny and
     nothing about the resulting chart looks wrong.

  5. THE CONTROL MUST BE A REAL CONTROL. `atrTrail` is what the structural trail
     is scored against, and the comparison only means anything if the two sit at
     the same average distance from price. `trailDistance` is what matches them,
     and it must measure WITHOUT acting -- a control that changed the trades it
     was measuring on would be matched to the wrong thing.

    python -m pytest tests/test_exittrail.py -q
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
function wildAfter(b, keep) {
  return b.slice(0, keep).concat(b.slice(keep).map((x, i) => ({
    ...x, o: x.o * 3 + i, h: x.h * 3 + i + 5, l: x.l * 3 + i - 5, c: x.c * 3 + i,
  })));
}
"""


def test_no_trail_leaves_the_rule_exactly_as_it_was():
    """
    The baseline has to be the baseline. A trail that returns nothing, and no
    trail at all, must both produce the validated rule byte for byte.
    """
    script = SYNTH + """
    import { runRule } from './js/chart/rules.js';
    import { donchianRule } from './js/chart/donchian.js';
    const b = bars(3000);
    const base = runRule(b, donchianRule, { tf: '4h' });
    const nul = runRule(b, donchianRule, { tf: '4h', exitTrail: () => null });
    const nan = runRule(b, donchianRule, { tf: '4h', exitTrail: () => NaN });
    const thrown = runRule(b, donchianRule, { tf: '4h',
      exitTrail: () => { throw new Error('boom'); } });
    const j = (r) => JSON.stringify(r.trades);
    console.log(JSON.stringify({
      n: base.trades.length,
      nullSame: j(nul) === j(base),
      nanSame: j(nan) === j(base),
      throwSame: j(thrown) === j(base),
    }));
    """
    got = run_node(script)
    assert got['n'] > 0, got
    # a trail with no opinion, an unusable one, and a broken one are all "no trail"
    assert got['nullSame'] is True, got
    assert got['nanSame'] is True, got
    assert got['throwSame'] is True, got


def test_the_trail_only_ever_tightens_the_exit():
    """
    THE MONOTONICITY THAT MAKES THE MEASUREMENT INTERPRETABLE. Every trade a
    trailed run closes must be held no LONGER than the same entry would be
    without the trail -- the trail can bring an exit forward and never push it
    back, because the rule's own channel is untouched underneath it.

    Checked on shared entries: the two runs diverge after the first differing
    exit, so only entries both took can be compared.
    """
    script = SYNTH + """
    import { runRule } from './js/chart/rules.js';
    import { donchianRule } from './js/chart/donchian.js';
    const b = bars(3000);
    const base = runRule(b, donchianRule, { tf: '4h' });
    const trailed = runRule(b, donchianRule, { tf: '4h',
      exitTrail: (c) => c.close[c.i] - c.side * 2 * c.series.atr[c.i] });
    const byEntry = new Map(base.trades.map((t) => [t.entryI, t]));
    let shared = 0, longer = 0;
    for (const t of trailed.trades) {
      const o = byEntry.get(t.entryI);
      if (!o) continue;
      shared += 1;
      if (t.exitI > o.exitI) longer += 1;
    }
    console.log(JSON.stringify({ shared, longer,
      trailedExits: [...new Set(trailed.trades.map((t) => t.reason))].sort() }));
    """
    got = run_node(script)
    assert got['shared'] > 3, got
    assert got['longer'] == 0, got
    assert 'trail' in got['trailedExits'], got


def test_the_trail_is_always_behind_the_trade_never_ahead():
    """
    A level in FRONT of a trade that closes it is a take-profit. This one is
    always behind: below a long, above a short. If it could sit ahead, the
    exit would cap the winner and the whole distinction collapses.
    """
    script = SYNTH + """
    import { runRule } from './js/chart/rules.js';
    import { donchianRule } from './js/chart/donchian.js';
    import { makeTrail } from './js/chart/exittrail.js';
    const b = bars(3000);
    const fn = makeTrail('structure', { tf: '4h', cell: 'T' });
    const bad = [];
    let asked = 0, gave = 0;
    runRule(b, donchianRule, { tf: '4h', exitTrail: (c) => {
      asked += 1;
      const px = fn(c);
      if (px === null) return null;
      gave += 1;
      const close = c.close[c.i];
      /* behind means BELOW a long and ABOVE a short */
      if ((close - px) * c.side <= 0) bad.push({ i: c.i, side: c.side, px, close });
      return px;
    } });
    console.log(JSON.stringify({ asked, gave, bad: bad.slice(0, 5), nBad: bad.length }));
    """
    got = run_node(script)
    assert got['asked'] > 20, got
    assert got['gave'] > 0, got          # not vacuous
    assert got['nBad'] == 0, got


def test_the_trail_ratchets_and_never_retreats():
    """
    Once it has moved toward the trade it must not move away. A level that
    wanders is not a trail, and a trail that could loosen would give back the
    monotonicity the whole comparison rests on.
    """
    script = SYNTH + """
    import { runRule } from './js/chart/rules.js';
    import { donchianRule } from './js/chart/donchian.js';
    const b = bars(3000);
    /* a deliberately jittery trail: it proposes a level that jumps around, so
       only the walker's ratchet can keep it monotone */
    const retreats = [];
    const seen = new Map();
    runRule(b, donchianRule, { tf: '4h', exitTrail: (ctx) => {
      const key = ctx.entryI;
      if (ctx.trail !== undefined && ctx.trail !== null) {
        const prev = seen.get(key);
        if (prev !== undefined) {
          const moved = (ctx.trail - prev) * ctx.side;
          if (moved < -1e-9) retreats.push({ key, prev, now: ctx.trail });
        }
        seen.set(key, ctx.trail);
      }
      const a = ctx.series.atr[ctx.i];
      const wobble = (ctx.i % 5) - 2;
      return ctx.close[ctx.i] - ctx.side * (2 + wobble * 0.5) * a;
    } });
    console.log(JSON.stringify({ tracked: seen.size, retreats: retreats.length }));
    """
    got = run_node(script)
    assert got['tracked'] > 0, got
    assert got['retreats'] == 0, got


def test_the_structural_trail_cannot_see_past_its_own_bar():
    """
    The usual contract. Checked by rewriting the future and demanding the same
    decisions on every bar before the cut.
    """
    script = SYNTH + """
    import { runRule } from './js/chart/rules.js';
    import { donchianRule } from './js/chart/donchian.js';
    import { makeTrail } from './js/chart/exittrail.js';
    const b = bars(2400);
    const KEEP = 1400;
    const wild = wildAfter(b, KEEP);
    const grab = (series, tag) => {
      const fn = makeTrail('structure', { tf: '4h', cell: tag });
      const seen = [];
      runRule(series, donchianRule, { tf: '4h', exitTrail: (c) => {
        const px = fn(c);
        if (c.i < KEEP) seen.push([c.i, c.side, px === null ? null : +px.toFixed(8)]);
        return px;
      } });
      return JSON.stringify(seen);
    };
    const a = grab(b, 'real');
    const c = grab(wild, 'wild');
    console.log(JSON.stringify({ same: a === c, samples: JSON.parse(a).length }));
    """
    got = run_node(script)
    assert got['samples'] > 20, got
    assert got['same'] is True, got


def test_the_trail_never_lands_on_the_fill():
    """
    THE BREAK-EVEN FLOOR. `entryPrice` is the OPEN of the fill bar and that
    bar's CLOSE can be most of an ATR away, so a level half an ATR behind the
    close can sit exactly on the entry. It did: USDCAD 4h produced a trail 0.3
    pips from its fill on bar zero, GBPUSD 3.2 pips on bar three -- both clearing
    the price floor honestly, both break-even stops on trades that had not done
    anything yet.

    So the floor is measured from BOTH ends, and this checks both: every level
    the trail offers must be at least `minAtr` from the close AND from the entry.
    """
    script = SYNTH + """
    import { runRule } from './js/chart/rules.js';
    import { donchianRule } from './js/chart/donchian.js';
    import { makeTrail, DEFAULT_TRAIL_PARAMS } from './js/chart/exittrail.js';
    const b = bars(3000);
    const fn = makeTrail('structure', { tf: '4h', cell: 'F' });
    const floor = DEFAULT_TRAIL_PARAMS.minAtr;
    let offered = 0, nearPrice = 0, nearEntry = 0, worstEntry = Infinity;
    runRule(b, donchianRule, { tf: '4h', exitTrail: (ctx) => {
      const px = fn(ctx);
      if (px === null) return null;
      offered += 1;
      const a = ctx.series.atr[ctx.i];
      const dPrice = Math.abs(ctx.close[ctx.i] - px) / a;
      const dEntry = Math.abs(ctx.entryPrice - px) / a;
      if (dPrice < floor - 1e-9) nearPrice += 1;
      if (dEntry < floor - 1e-9) nearEntry += 1;
      if (dEntry < worstEntry) worstEntry = dEntry;
      return px;
    } });
    console.log(JSON.stringify({ offered, nearPrice, nearEntry, floor,
      worstEntryAtr: Number(worstEntry.toFixed(3)) }));
    """
    got = run_node(script)
    assert got['offered'] > 20, got          # not vacuous
    assert got['nearPrice'] == 0, got
    assert got['nearEntry'] == 0, got
    assert got['worstEntryAtr'] >= got['floor'], got


def test_the_matched_control_is_measured_without_acting():
    """
    `trailDistance` is what makes the control a control: it sets the ATR
    multiple so the dumb trail sits at the same average distance as the
    structural one. It must therefore measure on the BASELINE's trades -- if it
    let the trail act while measuring, it would be matching the control to a run
    that no longer exists.
    """
    script = SYNTH + """
    import { runRule } from './js/chart/rules.js';
    import { donchianRule } from './js/chart/donchian.js';
    import { makeTrail, trailDistance } from './js/chart/exittrail.js';
    const b = bars(3000);
    const base = runRule(b, donchianRule, { tf: '4h' });
    const fn = makeTrail('structure', { tf: '4h', cell: 'M' });
    const d = trailDistance(b, donchianRule, runRule, { tf: '4h' }, fn);
    /* measuring must not have changed anything: the same call again, and the
       plain baseline, must agree */
    const after = runRule(b, donchianRule, { tf: '4h' });
    console.log(JSON.stringify({
      n: d.n, meanAtr: d.meanAtr,
      baselineUntouched: JSON.stringify(base.trades) === JSON.stringify(after.trades),
    }));
    """
    got = run_node(script)
    assert got['n'] > 20, got
    assert got['meanAtr'] > 0, got
    assert got['baselineUntouched'] is True, got

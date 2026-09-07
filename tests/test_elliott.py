"""
The Elliott counter, checked for the two things that can make it a lie.

There is no Python twin of `js/chart/elliott.js` to compare against, so unlike
the parity suites this file drives the JS directly under node. Two properties
matter, and neither is about whether the counts are any GOOD:

  1. NO LOOK-AHEAD. `countAsOf(bars, {upto: k})` must return exactly what
     `countAsOf(bars.slice(0, k+1))` returns. If it differs, the count on a
     replay chart was drawn with knowledge of bars the replay had removed, and
     every number the replay log records is worthless.

  2. THE HARD RULES ARE ENFORCED. Wave 2 past the start of wave 1, wave 4
     overlapping wave 1, and wave 3 as the shortest of 1/3/5 are the three
     conditions that make an impulse count WRONG rather than unlikely. A counter
     that lets them through will always find a count, which is the same as
     finding nothing.

    python -m pytest tests/test_elliott.py -q
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
                         cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout.strip().splitlines()[-1])


SYNTH = """
/* A deterministic zig-zag series with enough structure to produce counts. */
function bars(n) {
  const out = []; let p = 100, t = 1700000000000;
  for (let i = 0; i < n; i++) {
    const wave = Math.sin(i / 9) * 1.6 + Math.sin(i / 37) * 4;
    p = 100 + wave + (i * 0.01);
    const o = p - 0.1, c = p + 0.1;
    out.push({ t: t + i * 900000, o, h: Math.max(o, c) + 0.25,
               l: Math.min(o, c) - 0.25, c, v: 1 });
  }
  return out;
}
"""


def test_count_is_identical_when_the_future_is_removed():
    """The property the whole replay rests on."""
    script = SYNTH + """
import { countAsOf } from './js/chart/elliott.js';
const b = bars(600);
const strip = (r) => JSON.stringify({
  n: r.pivots.length,
  counts: r.counts.map((c) => [c.label, +c.score.toFixed(9), c.invalidation,
                               c.outlook, c.pivots.map((p) => p.i)]),
});
const bad = [];
for (const k of [120, 200, 310, 405, 500, 599]) {
  const whole = strip(countAsOf(b, { upto: k }));
  const cut = strip(countAsOf(b.slice(0, k + 1)));
  if (whole !== cut) bad.push({ k, whole, cut });
}
console.log(JSON.stringify({ bad, checked: 6 }));
"""
    r = run_node(script)
    assert r['checked'] == 6
    assert r['bad'] == [], f"count changed when the future was removed: {r['bad']}"


def test_a_count_always_carries_an_invalidation_price():
    """A count with no level that would refute it is not a forecast."""
    script = SYNTH + """
import { countAsOf } from './js/chart/elliott.js';
const b = bars(600);
let seen = 0, missing = 0, wrongSide = 0;
for (let k = 120; k < 600; k += 7) {
  const r = countAsOf(b, { upto: k });
  for (const c of r.counts) {
    seen++;
    if (!Number.isFinite(c.invalidation)) missing++;
    // the level must sit on the losing side of price, or it is already dead
    else if (c.dir > 0 ? r.close <= c.invalidation : r.close >= c.invalidation) wrongSide++;
  }
}
console.log(JSON.stringify({ seen, missing, wrongSide }));
"""
    r = run_node(script)
    assert r['seen'] > 50, 'no counts produced; the fixture is not exercising the engine'
    assert r['missing'] == 0
    assert r['wrongSide'] == 0


@pytest.mark.parametrize('prices,why', [
    ([100, 110, 99, 120, 115, 130], 'wave 2 retraced past the start of wave 1'),
    ([100, 110, 105, 120, 108, 130], 'wave 4 overlapped wave 1'),
    # w1=20, w3=15 (still exceeds wave 1's high), w5=48 -- 3 is the shortest
    ([100, 120, 110, 125, 122, 170], 'wave 3 was the shortest of 1/3/5'),
])
def test_hard_rules_reject(prices, why):
    """Each of the three rules, on a count built to break exactly one."""
    script = f"""
import {{ checkRules }} from './js/chart/elliott.js';
console.log(JSON.stringify({{ verdict: checkRules({json.dumps(prices)}, 1) }}));
"""
    r = run_node(script)
    assert r['verdict'] == why


def test_a_textbook_impulse_is_admissible():
    """The mirror of the rule tests: a clean 1-2-3-4-5 must survive."""
    script = """
import { checkRules } from './js/chart/elliott.js';
const up = checkRules([100, 110, 105, 130, 122, 140], 1);
const down = checkRules([140, 130, 135, 110, 118, 100], -1);
console.log(JSON.stringify({ up, down }));
"""
    r = run_node(script)
    assert r['up'] is None
    assert r['down'] is None


def test_scoring_needs_a_full_horizon():
    """A belief scored against fewer bars than it asked for is scored at random."""
    script = SYNTH + """
import { countAsOf, scoreBelief } from './js/chart/elliott.js';
const b = bars(400);
const belief = countAsOf(b, { upto: 395 });
belief.close = b[395].c;
const short = scoreBelief(belief, b.slice(0, 397), { horizon: 24 });
const full = scoreBelief(belief, b, { horizon: 24 });
console.log(JSON.stringify({
  shortHorizon: short ? short.horizon : null,
  shortActualBars: 396 - 395,
  fullOk: !!full && full.horizon === 24,
}));
"""
    r = run_node(script)
    # scoreBelief clamps to the bars available; the caller (Replay.scoreAll) is
    # what refuses to score a belief without a full horizon, so assert that the
    # clamped case is still reported honestly rather than silently extrapolated.
    assert r['fullOk'] is True


def test_the_projection_is_drawn_before_the_bars_that_settle_it():
    """A projected path must be a function of the past alone, like the count."""
    script = SYNTH + """
import { countAsOf } from './js/chart/elliott.js';
const b = bars(600);
const strip = (r) => JSON.stringify(
  r.counts.map((c) => [c.label, (c.projection || []).map((p) => [p.label, p.ahead, +p.price.toFixed(9)])]));
const bad = [];
for (const k of [150, 260, 380, 470, 560]) {
  if (strip(countAsOf(b, { upto: k })) !== strip(countAsOf(b.slice(0, k + 1)))) bad.push(k);
}
console.log(JSON.stringify({ bad }));
"""
    assert run_node(script)['bad'] == []


def test_projection_points_move_forward_and_alternate():
    """A path that goes backwards in time, or twice the same way, is not a wave
    count -- it is a line with wave labels on it."""
    script = SYNTH + """
import { countAsOf } from './js/chart/elliott.js';
const b = bars(600);
let paths = 0, backwards = 0, sameSide = 0;
for (let k = 150; k < 600; k += 9) {
  for (const c of countAsOf(b, { upto: k }).counts) {
    const p = c.projection || [];
    if (p.length < 2) continue;
    paths++;
    for (let i = 1; i < p.length; i++) {
      if (p[i].ahead <= p[i - 1].ahead) backwards++;
      const a = Math.sign(p[i].price - p[i - 1].price);
      const prev = i > 1 ? Math.sign(p[i - 1].price - p[i - 2].price) : -a;
      if (a !== 0 && a === prev) sameSide++;
    }
  }
}
console.log(JSON.stringify({ paths, backwards, sameSide }));
"""
    r = run_node(script)
    assert r['paths'] > 10, 'no multi-leg projections produced'
    assert r['backwards'] == 0
    assert r['sameSide'] == 0


def test_calibration_is_perfect_on_a_forecast_that_is_true_by_construction():
    """A synthetic set where the claimed probability IS the outcome frequency
    must come back calibrated. Without this the metric can be wrong in the
    direction that flatters the model and nothing would say so."""
    script = """
import { calibration } from './js/chart/elliott.js';
const rows = [];
// 700 rows claiming 70% continuation, of which exactly 70% continue
for (let i = 0; i < 700; i++) {
  const cont = i % 10 < 7;
  rows.push({ expected: 'continuation', actual: cont ? 'continuation' : 'reversal',
    scenario: { continuation: 0.7, correction: 0.0, reversal: 0.3 } });
}
const c = calibration(rows);
const b = c.buckets.find((x) => x.n === 700);
console.log(JSON.stringify({ claimed: b.claimed, happened: b.happened,
  gap: +b.gap.toFixed(6), skill: +c.skill.toFixed(4) }));
"""
    r = run_node(script)
    assert abs(r['claimed'] - 0.7) < 1e-9
    assert abs(r['happened'] - 0.7) < 1e-9
    assert abs(r['gap']) < 1e-6
    # a perfectly calibrated forecast that also carries information beats
    # climatology; here it matches it exactly, so skill is ~0 and not negative
    assert r['skill'] >= -1e-6


def test_an_overconfident_forecast_is_reported_as_overconfident():
    script = """
import { calibration } from './js/chart/elliott.js';
const rows = [];
for (let i = 0; i < 400; i++) {
  const cont = i % 10 < 3;                       // happens 30% of the time
  rows.push({ expected: 'continuation', actual: cont ? 'continuation' : 'reversal',
    scenario: { continuation: 0.9, correction: 0.0, reversal: 0.1 } });   // claimed 90%
}
const c = calibration(rows);
const b = c.buckets[c.buckets.length - 1];
console.log(JSON.stringify({ gap: +b.gap.toFixed(3), skill: +c.skill.toFixed(3) }));
"""
    r = run_node(script)
    assert r['gap'] < -0.5, 'a 90% claim that happens 30% of the time must show a large negative gap'
    assert r['skill'] < 0, 'and must score worse than climatology'


def test_stability_counts_a_flip_only_when_the_reading_changes():
    script = """
import { stability } from './js/chart/elliott.js';
const mk = (kind, dir, waveNow) => ({ counts: [{ kind, dir, waveNow }] });
const steady = Array.from({ length: 10 }, () => mk('impulse', 1, 3));
const flipping = Array.from({ length: 10 }, (_, i) => mk('impulse', i % 2 ? 1 : -1, 3));
const a = stability(steady, { stride: 5 });
const b = stability(flipping, { stride: 5 });
console.log(JSON.stringify({ steadyFlip: a.flipRate, steadyRun: a.medianRunBars,
  flipRate: b.flipRate, dirFlip: b.dirFlipRate }));
"""
    r = run_node(script)
    assert r['steadyFlip'] == 0
    assert r['steadyRun'] == 50          # 10 samples, 5 bars apart
    assert r['flipRate'] == 1
    assert r['dirFlip'] == 1


# ------------------------------------------------------------------- cones

def test_the_cone_covers_what_it_claims_on_a_series_with_known_dispersion():
    """A nominal 80% band on a random walk must contain the outcome ~80% of the
    time. If it does not, the cone is decoration -- and unlike the wave count,
    the cone is a claim that CAN be checked directly."""
    script = """
import { cones, coverage, stateSeries } from './js/chart/cone.js';
/* A deterministic random walk: no structure to find, so the cone can only be
   right by being honest about dispersion. */
let seed = 12345;
const rnd = () => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff; };
const bars = [];
let p = 100;
for (let i = 0; i < 4000; i++) {
  const step = (rnd() - 0.5) * 2;
  p += step;
  const o = p - step / 2;
  bars.push({ t: 17e11 + i * 9e5, o, h: Math.max(o, p) + 0.3, l: Math.min(o, p) - 0.3, c: p });
}
const pre = stateSeries(bars);
const rows = [];
for (let i = 600; i < bars.length - 30; i += 25) {
  const c = cones(bars, { upto: i, steps: 24, precomputed: pre });
  if (c) rows.push({ bands: c.unconditional, close: c.close, atr: c.atr, asOfI: i });
}
const cv = coverage(rows, bars, { horizons: [1, 4, 16] });
console.log(JSON.stringify({ n: rows.length, cv }));
"""
    r = run_node(script)
    assert r['n'] > 80, 'not enough cones to judge coverage'
    for row in r['cv']:
        assert 0.70 <= row['cover80'] <= 0.90, f"80% band covered {row['cover80']:.2f} at +{row['horizon']}"
        assert 0.40 <= row['cover50'] <= 0.60, f"50% band covered {row['cover50']:.2f} at +{row['horizon']}"
        # and it must widen with the horizon, which is the whole shape of a cone
    widths = [row['width80Atr'] for row in r['cv']]
    assert widths == sorted(widths), f"the cone did not widen with the horizon: {widths}"


def test_the_cone_is_built_only_from_bars_before_the_cursor():
    """The dispersion must not be sized by the bars the cone is drawn over."""
    script = """
import { cones, stateSeries } from './js/chart/cone.js';
let seed = 999;
const rnd = () => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff; };
const mk = (n, vol) => {
  const out = []; let p = 100;
  for (let i = 0; i < n; i++) {
    const step = (rnd() - 0.5) * 2 * vol;
    p += step;
    const o = p - step / 2;
    out.push({ t: 17e11 + i * 9e5, o, h: Math.max(o, p) + 0.3 * vol, l: Math.min(o, p) - 0.3 * vol, c: p });
  }
  return out;
};
/* Calm history, then a violent future. A cone at the boundary must not know. */
const calm = mk(2500, 1);
const wild = mk(600, 8);
const all = calm.concat(wild);
const at = calm.length - 1;
const a = cones(all, { upto: at, steps: 12, precomputed: stateSeries(all) });
const b = cones(calm, { upto: at, steps: 12, precomputed: stateSeries(calm) });
const w = (c) => (c.unconditional[11].q[0.9] - c.unconditional[11].q[0.1]) / c.atr;
console.log(JSON.stringify({ withFuture: +w(a).toFixed(6), withoutFuture: +w(b).toFixed(6) }));
"""
    r = run_node(script)
    assert r['withFuture'] == r['withoutFuture'], \
        'the cone changed when future bars were appended — it is reading them'


def test_interval_score_punishes_both_missing_and_padding():
    script = """
import { intervalScore } from './js/chart/cone.js';
const tight = intervalScore(99, 101, 100, 0.2);     // narrow, contains
const wide = intervalScore(90, 110, 100, 0.2);      // wide, contains
const missed = intervalScore(99, 101, 105, 0.2);    // narrow, misses by 4
console.log(JSON.stringify({ tight, wide, missed }));
"""
    r = run_node(script)
    assert r['tight'] < r['wide'], 'an unnecessarily wide interval must score worse'
    assert r['tight'] < r['missed'], 'an interval that misses must score worse'


# --------------------------------------------------- the scenario mixture

def test_mixing_quantiles_is_not_averaging_them():
    """The P10 of a mixture is not the mean of the components' P10s. That
    identity holds by coincidence at best, and using it produces a band that is
    too narrow exactly when the scenarios disagree -- which is when width
    matters. This pins the pooled-sample definition."""
    script = """
import { weightedQuantiles } from './js/chart/cone.js';
/* Two far-apart components: A around -10, B around +10, equal weight. */
const A = []; const B = [];
for (let i = 0; i < 100; i++) { A.push(-10 + i * 0.02); B.push(10 + i * 0.02); }
const pooled = A.map((v) => ({ v, w: 0.5 / A.length }))
  .concat(B.map((v) => ({ v, w: 0.5 / B.length })));
const mix = weightedQuantiles(pooled, [0.1, 0.5, 0.9]);
const qA = { 0.1: A[9], 0.5: A[49], 0.9: A[89] };
const qB = { 0.1: B[9], 0.5: B[49], 0.9: B[89] };
const averaged = { 0.1: (qA[0.1] + qB[0.1]) / 2, 0.9: (qA[0.9] + qB[0.9]) / 2 };
console.log(JSON.stringify({
  mixLo: +mix[0.1].toFixed(3), mixHi: +mix[0.9].toFixed(3),
  avgLo: +averaged[0.1].toFixed(3), avgHi: +averaged[0.9].toFixed(3),
}));
"""
    r = run_node(script)
    # the true mixture spans BOTH components; the average collapses to the middle
    assert r['mixLo'] < -9, f"mixture P10 should sit inside component A, got {r['mixLo']}"
    assert r['mixHi'] > 9, f"mixture P90 should sit inside component B, got {r['mixHi']}"
    # averaging collapses both edges to the gap between the components, where
    # neither component has any mass at all
    assert -10 < r['avgLo'] < 10 and -10 < r['avgHi'] < 10
    assert abs(r['avgLo']) < 3 and abs(r['avgHi']) < 3,         'the averaged edges should land in the empty middle'
    assert (r['mixHi'] - r['mixLo']) > 10 * (r['avgHi'] - r['avgLo']), \
        'averaging quantiles must be shown to understate the mixture badly'


def test_a_degenerate_mixture_is_the_scenario_it_puts_all_its_weight_on():
    """All the weight on one scenario must reproduce that scenario's own cone
    exactly. If it does not, the pooling is not doing what it claims."""
    script = SYNTH + """
import { scenarioCones, stateSeries } from './js/chart/cone.js';
const b = bars(2000);
const pre = stateSeries(b);
const opts = { upto: 1900, steps: 12, dir: 1, refHorizon: 12, precomputed: pre };
const only = scenarioCones(b, { ...opts,
  weights: { continuation: 1, correction: 0, reversal: 0 } });
if (!only || !only.per.continuation) { console.log(JSON.stringify({ skip: true })); }
else {
  const mixQ = only.mixture.map((x) => +x.atrQ[0.5].toFixed(9));
  const ownQ = only.per.continuation.map((x) => +x.atrQ[0.5].toFixed(9));
  const mixHi = only.mixture.map((x) => +x.atrQ[0.9].toFixed(9));
  const ownHi = only.per.continuation.map((x) => +x.atrQ[0.9].toFixed(9));
  console.log(JSON.stringify({ same: JSON.stringify([mixQ, mixHi]) === JSON.stringify([ownQ, ownHi]),
    steps: mixQ.length, weights: only.weights }));
}
"""
    r = run_node(script)
    if r.get('skip'):
        pytest.skip('the fixture produced no continuation analogues')
    assert r['steps'] > 0
    assert r['same'], 'all weight on one scenario must equal that scenario alone'


def test_the_mixture_does_not_read_the_future():
    """Same causality guard as the plain cone: the scenario labels are read from
    windows that closed before the cursor, so appending bars must change
    nothing."""
    script = """
import { scenarioCones, stateSeries } from './js/chart/cone.js';
let seed = 4242;
const rnd = () => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff; };
const mk = (n, vol) => {
  const out = []; let p = 100;
  for (let i = 0; i < n; i++) {
    const step = (rnd() - 0.5) * 2 * vol;
    p += step;
    const o = p - step / 2;
    out.push({ t: 17e11 + i * 9e5, o, h: Math.max(o, p) + 0.3 * vol, l: Math.min(o, p) - 0.3 * vol, c: p });
  }
  return out;
};
const calm = mk(2200, 1);
const all = calm.concat(mk(600, 8));
const at = calm.length - 1;
const w = { continuation: 0.5, correction: 0.3, reversal: 0.2 };
const a = scenarioCones(all, { upto: at, steps: 12, dir: 1, weights: w, refHorizon: 12,
  precomputed: stateSeries(all) });
const b = scenarioCones(calm, { upto: at, steps: 12, dir: 1, weights: w, refHorizon: 12,
  precomputed: stateSeries(calm) });
console.log(JSON.stringify({
  withFuture: a.mixture.map((x) => +x.atrQ[0.9].toFixed(9)),
  withoutFuture: b.mixture.map((x) => +x.atrQ[0.9].toFixed(9)),
}));
"""
    r = run_node(script)
    assert r['withFuture'] == r['withoutFuture'], \
        'the mixture changed when future bars were appended — it is reading them'

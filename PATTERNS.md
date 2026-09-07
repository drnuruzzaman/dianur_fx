# Pattern recognition — design spec

**Status: proposal. Nothing here is built yet.** Read it, argue with it, and mark
up the parts you disagree with before any of it becomes code.

---

## The question, restated

"There are so many technical analysis methods, I don't know what to do."

You do not need to decide which ones work. That is the whole trap: every book
asserts its patterns work, none of them agree, and picking by reading is picking
by rhetoric. What you need is a **machine that decides for you** — a detector
that is honest about *when* it could have known something, and a research harness
that measures each pattern against a matched control and refuses to promote one
that cannot beat it.

So the order is inverted from how most people build this:

1. Build the **object model** and the **harness** first.
2. Build the **detectors** second — they are the cheap part.
3. Let the numbers pick the patterns.

If you build fifty detectors before the harness you will have fifty things you
believe in and no way to be wrong about any of them.

The second reframing: **you have already built a pattern recognition system.**
`sim/tl/` finds pivots, fits lines to them, tracks a lifecycle, scores quality,
and emits per-bar features that strategies consume. A head-and-shoulders is not a
different *kind* of thing from a trendline — it is four pivots with a shape
constraint instead of two pivots with a fit. This spec is mostly about
**generalising what `sim/tl/` already does**, not about starting something new.

---

## What you already have

Before adding anything, the inventory — because most of the hard parts are done:

| You have | In | Reused for patterns as |
|---|---|---|
| Fractal pivots with `confirmed_i` | `sim/tl/pivots.py` | the anchor stream every structural pattern is built from |
| Minimum-swing filtering | `engine._significant` | keeps a 0.1 ATR wiggle from becoming a shoulder |
| A lifecycle object with frozen-at-break scalars | `sim/tl/lines.py` | the model the `Pattern` object copies |
| Incremental causal walk → one snapshot per bar | `sim/tl/engine.py` | the model `PatternEngine` copies |
| Closed-bar-only MTF projection | `sim/tl/mtf.py` | patterns on 4H, usable on 15M, for free |
| Regime per timeframe | `sim/tl/regime.py` | the conditioning variable for every base-rate table |
| Confluence scored but never assumed | `mtf.confluence`, `base.gate` | the composite-pattern scoring model |
| ATR-relative everything | throughout | the only way a tolerance means the same on gold and yen |
| Look-ahead as a structural property | `BarView`, `confirmed_i`, `Snapshot` | non-negotiable; patterns inherit all four mechanisms |
| Time-shift control + bootstrap + sample floor | `sim/robust.py` | the statistical backbone of the pattern harness |
| Two-language parity with negative controls | `tests/test_parity.py`, `test_tl_parity.py` | the discipline that keeps chart == backtest |

**Nothing in `sim/patterns/` should re-implement any row of that table.**

---

## The one idea

> A pattern is an object with a lifecycle, a quality score, and **four distinct
> bar indexes** — not a boolean column.

Everything else in this document follows from that sentence.

A boolean `is_head_and_shoulders[i]` column is the wrong abstraction because it
cannot express the difference between the bar where the shape *exists*, the bar
where it *became visible*, the bar where it *became tradeable*, and the bar where
it *died*. Those are four different bars, they are often 20+ bars apart, and
collapsing them is the single most common way a pattern backtest fabricates edge.

---

## The four bars

Every pattern instance carries all four. This is the heart of the spec.

```
formed_i      where the shape geometrically SITS.
              The last anchor's own bar. This is where the chart DRAWS it.
              It is NOT tradeable and must never be used as an entry.

detected_i    the earliest bar an observer could have SEEN the shape.
              = max(confirmed_i of every anchor pivot)
              For a fractal pivot at bar p with strength s, that is p + s.
              A right shoulder at bar 400, strength 3 -> not visible until 403.

trigger_i     the bar where the ACTIVATION condition fired.
              H&S: the close through the neckline.
              Double top: the close through the intervening low.
              Flag: the close beyond the flag boundary.
              Engulfing: the close of the engulfing bar itself (= detected_i).
              This is the earliest LEGAL entry bar. The simulator then fills at
              the open of trigger_i + 1, as it already does.

invalid_i     the bar where the pattern died without triggering, or after
              triggering failed. A pattern with no invalidation rule is not a
              pattern, it is a decoration.
```

`formed_i <= detected_i <= trigger_i` is an **assertion in the engine**, not a
convention. Violating it raises, in the same spirit as `BarView`'s
`LookAheadError`.

Why this matters concretely: a head-and-shoulders drawn at the right shoulder and
"entered" there is reading roughly 3 bars of the future for the shoulder
confirmation, plus however many bars until the neckline actually breaks —
commonly 10 to 40 bars on 15M. A backtest that enters at `formed_i` for a
pattern whose real trigger is 25 bars later is not a good strategy; it is a time
machine. This is exactly the mistake `sim/divergence.py` already refuses to make,
and its docstring already says so:

> *"Drawing at `pivot_i` and trading at `confirmed_i` is the honest combination.
> A chart that draws the line and implies you could have acted at the swing low
> is the most common way divergence is oversold as a technique."*

Generalise that sentence to every pattern and you have this section.

---

## Taxonomy — seven tiers

Ordered by how much machinery each needs, not by popularity.

### T0 — Primitives (built)
Pivots, ATR, RSI, EMA, regime, trendlines. Not patterns; the substrate patterns
are made of.

### T1 — Candle patterns (1–3 bars)
Doji, spinning top, marubozu, hammer / hanging man, shooting star / inverted
hammer, bullish & bearish engulfing, harami (+cross), inside bar, outside bar,
tweezer top/bottom, piercing line, dark cloud cover, morning & evening star,
three white soldiers / three black crows, three inside/outside up/down.

**Detection cost: trivial.** All of them are predicates over normalised bar
geometry — body/range, upper wick/range, lower wick/range, body overlap with the
prior bar, gap size — all in ATR or in fractions of the bar's own range.

**Expected value: low on their own, and you should expect most to fail Gate 3
(cost survival).** Their real use is as a **trigger/timing layer** on top of a
location that came from somewhere else. "Pin bar" is not an edge; "pin bar
rejecting a CONFIRMED 4H support line while 1H and 4H agree" might be, and that
is a composite, not a candle pattern.

### T2 — Market structure (pivot sequences)
Higher-high / higher-low / lower-high / lower-low labelling, **break of structure
(BOS)**, **change of character (CHoCH)**, swing-failure / liquidity sweep
(a swing high taken out then closed back inside), equal highs/lows.

**This tier is the highest-value single piece of work in the whole document.**
Reasons: it consumes `find_pivots` output directly with no new geometry; it
produces a per-bar *state* rather than rare events, so the sample size is large
enough for real statistics; it is a strictly better trend filter than the EMA
filter currently in `base.ema_ok`; and it immediately improves `tl_bounce` and
`tl_breakout` even before any new strategy exists.

### T3 — Geometric / chart patterns (pivot sequences + fitted lines)
Double top/bottom, triple top/bottom, head & shoulders and inverse, ascending /
descending / symmetrical triangle, rising / falling wedge, bull & bear flag,
pennant, rectangle, channel, broadening formation, cup-and-handle,
rounding top/bottom.

Every one of these is: *k alternating pivots, plus constraints on the legs, plus
(sometimes) two fitted lines.* You already fit lines to pivot pairs in
`TrendlineEngine._form`. This tier is a predicate layer over machinery that
exists.

### T4 — Harmonic (pivot sequences + Fibonacci ratio constraints)
AB=CD, Gartley, Bat, Butterfly, Crab, Shark, Cypher, three-drives.

Structurally **identical to T3** — 4 or 5 alternating pivots — differing only in
that the leg constraints are Fibonacci ratios with tolerances instead of shape
rules. Once the sequence kernel exists these are a **table of numbers**, perhaps
80 lines of data total. Cheap to add, so add them; but hold them to the same
gates, and expect the ratio tolerances to be where the overfitting hides (a
0.786 ± 0.03 retracement is a much narrower claim than the sample size usually
supports).

### T5 — Volatility & range patterns (rolling scalars)
NR4 / NR7 (narrowest range in 4/7), inside-bar compression runs, Bollinger
squeeze, ATR contraction/expansion regimes, opening-range formation,
gap taxonomy (common / breakaway / runaway / exhaustion / island reversal).

Cheap, statistically well-behaved (frequent, so large samples), and they compose
well as a **conditioner** rather than a signal: "triangle breakout *out of a
squeeze*" is a different population from "triangle breakout".

**FX caveat:** true gaps barely exist except the weekend gap, so most of the gap
taxonomy is dead on your instruments. Build the squeeze/compression half.

### T6 — Indicator patterns
RSI divergence (**built**), *hidden* divergence (continuation, not reversal —
free to add, `sim/divergence.py` already has 90% of it), RSI failure swing,
MACD histogram divergence and zero-line cross, stochastic embedding, multiple
divergence classes across oscillators.

**Concrete refactor:** `sim/divergence.py` is currently RSI-specific. Make
`find_divergences(bars, oscillator, **opts)` take any causal array, and RSI
becomes one caller. Hidden divergence is then the same function with the price
and oscillator comparisons swapped. One generalisation, four new pattern kinds.

### T7 — Composite
See its own section below. This is the tier you actually care about, and it is
the one that needs the least new detection code and the most measurement.

---

## Three kernels, not sixty detectors

Naively, the seven tiers are ~60 named patterns. Writing 60 bespoke detectors
means 60 chances to leak the future, 60 things to port to JS, and 60 parity
tests. Don't.

They collapse to **three detection kernels plus declarative specification
tables.**

### Kernel A — `sim/patterns/candles.py`
Fixed small window, vectorised over the whole array, causal by construction (a
window ending at `i` reads only `i-k..i`).

Compute a normalised geometry table once — `body`, `upper`, `lower`, `range`,
each divided by ATR *and* by the bar's own range; overlap with the previous bar's
body; direction — then express every T1 pattern as a predicate over those
columns. A `SPECS` dict of `{name: (window, predicate, direction)}` in the same
spirit as `INDICATORS` in `js/chart/indicators.js`.

Roughly 20 patterns, ~150 lines of predicates, one kernel to test.

### Kernel B — `sim/patterns/sequence.py`
**The important one.** Covers T2, T3 and T4 entirely.

Input: the confirmed-pivot stream (alternating highs and lows, each with `i`,
`price`, `confirmed_i`).
Template: an alternation string, e.g. `'LHLHL'` for a head-and-shoulders
bottom, `'HLH'` for a double top.
Predicate: a function of the resulting legs, where a leg carries
`price_move_atr`, `bars`, `slope`, and each pair carries a `retracement` ratio.

Then:

```
DOUBLE_TOP   = Spec('HLH',   lambda g: abs(g.p[0]-g.p[2]) < tol*atr
                                      and g.leg[0].drop_atr > 1.5)
HEAD_SHLDRS  = Spec('HLHLH', lambda g: g.p[2] > g.p[0] and g.p[2] > g.p[4]
                                      and abs(g.p[0]-g.p[4]) < 0.5*g.height)
GARTLEY      = Spec('XABCD', ratios={'AB/XA': (0.618, 0.03),
                                     'BC/AB': (0.382, 0.886),
                                     'AD/XA': (0.786, 0.03)})
ASC_TRIANGLE = Spec('HLHLH', flat_highs=True, rising_lows=True)
```

Same matcher, same lifecycle, same look-ahead accounting, same parity test.
**Adding a new chart pattern becomes adding a row, not writing a module** —
exactly the property that makes `INDICATORS` in `indicators.js` pleasant to
extend, and the README already calls that out as a design goal.

The trigger condition is part of the spec, not the matcher: each spec declares
its `trigger` (a level and a direction — neckline, boundary line, D-point zone)
and its `invalidation` (a level and a bar budget).

### Kernel C — `sim/patterns/scalars.py`
Rolling-window scalar predicates for T5 and T6. Takes precomputed causal series
(you have all of them in `sim/indicators.py`) and a predicate over a trailing
window. Squeeze, NR7, compression runs, oscillator conditions.

---

## The Pattern object

Deliberately shaped like `Trendline` in `sim/tl/lines.py`, for the same reasons
and with the same discipline about frozen scalars.

```python
class Bias(str, Enum):
    BULLISH = 'bullish'
    BEARISH = 'bearish'


class PatternStatus(str, Enum):
    FORMING   = 'FORMING'      # partial match, not all anchors confirmed
    DETECTED  = 'DETECTED'     # shape complete and visible; not yet actionable
    TRIGGERED = 'TRIGGERED'    # activation condition fired -> tradeable
    COMPLETED = 'COMPLETED'    # measured-move target reached
    FAILED    = 'FAILED'       # invalidated after triggering
    EXPIRED   = 'EXPIRED'      # bar budget ran out without triggering
    ARCHIVED  = 'ARCHIVED'


@dataclass
class Pattern:
    id: str                     # '4h-HS-17', same scheme as Trendline ids
    kind: str                   # 'head_shoulders' | 'bull_engulfing' | ...
    tier: int                   # 1..6, so a study can slice by tier
    timeframe: str
    direction: Bias             # BULLISH | BEARISH — a separate enum from
                                # tl.lines.Direction (up/down/horizontal), which
                                # describes geometry, not a claim about price

    anchors: list               # [{i, t, price, role, confirmed_i}, ...]

    formed_i: int               # last anchor's own bar        -> DRAW here
    formed_at: int              # ms
    detected_i: int             # max(anchor.confirmed_i)      -> VISIBLE here
    trigger_i:  Optional[int]   # activation bar               -> TRADE here
    trigger_at: Optional[int]
    invalid_i:  Optional[int]

    trigger_level: float        # neckline / boundary price
    invalidation_level: float   # where the idea is wrong
    measured_target: float      # classical projection; a hypothesis, not a promise

    height_atr: float           # size in ATR at formation
    span_bars: int              # first anchor to last
    symmetry: float             # 0..1, time symmetry of the legs
    fit_error_atr: float        # how well the shape actually matches the template

    status: PatternStatus = PatternStatus.FORMING
    quality_score: float = 0.0
    quality_at_trigger: Optional[float] = None   # FROZEN, like quality_at_break

    def to_row(self) -> dict: ...
```

Two rules carried over from `sim/tl/`, and they are not optional:

1. **Frozen scalars.** A `Pattern` is mutable and keeps updating on later bars.
   Anything a feature row or a strategy reads must be frozen into the snapshot at
   that bar. This is *precisely* the bug `tests/test_lookahead.py` caught at
   87.13 vs 86.37 — in code that looked obviously fine. It will happen again here
   unless the same structure prevents it.
2. **Quality frozen at trigger.** `quality_at_trigger` mirrors
   `quality_at_break`, so a study can ask "how good did this pattern look at the
   moment it became tradeable" without reading what it eventually became.

### Quality score

Same shape as `Trendline.score` — 0..100, weighted so the things that make a
pattern *tradeable* dominate:

```
fit         30   how tightly the anchors match the template (fit_error in ATR)
size        20   height in ATR — a 0.3 ATR H&S is noise wearing a name
symmetry    15   leg time symmetry; asymmetric shapes are usually coincidence
context     15   formed at a CONFIRMED trendline / prior level, or in mid-air
clarity     10   anchor pivots' own swing magnitude (reuses min_swing_atr)
freshness   10   bars since trigger; a stale trigger is not a trade
penalty    -20   prior failed patterns of the same kind on the same level
```

The weights are a **starting hypothesis, to be fitted by the harness**, not a
belief. The harness should report edge conditioned on quality decile; if the
score has no monotone relationship to forward outcome, the score is wrong and
should be re-weighted or dropped. Do not ship a score that does not sort.

---

## What "composite pattern" actually means

You asked for composite patterns. The word covers four genuinely different
things, and conflating them is why composite pattern systems usually turn into
mush. Build them in this order.

### C1 — Confluence stack (co-occurrence) — **build first**
Several independent things true at the same bar: a pattern trigger, *at* a
CONFIRMED trendline, *in* a permitted regime, *with* MTF agreement, *during* a
given session.

This needs **no new detector at all.** It is a join on the feature table plus a
score — and you already have the pattern for it in `mtf.confluence` and
`base.gate`: score it, log it on every signal whether or not it gates entry, and
A/B `off` vs `require`. Your existing answer on that question ("helps 2 of 6
combinations and hurts 4") is exactly the kind of answer to expect here, and the
reason the logging must be unconditional.

Cheapest to build, most likely to pay, and it is what practitioners actually mean
99% of the time.

### C2 — Sequential composition (temporal) — build second
Pattern A *then* pattern B within k bars. `CHoCH → retest of the broken level →
bullish engulfing` is one composite object with a single trigger, not three
signals.

Implementation: a small finite-state matcher over the **pattern event stream**,
not over bars. It reuses Kernel B's matcher with pattern events as the alphabet
instead of pivots. The composite's `detected_i` is the last component's
`detected_i`; its `trigger_i` is the final component's trigger. Look-ahead
accounting composes automatically if each component is honest.

### C3 — Nested composition (structural) — build third
The pattern's parts are themselves patterns: a flag whose pole is an impulse leg,
an H&S whose right shoulder is a pin bar, a triangle formed *between* two
CONFIRMED trendlines. Also handled by Kernel B over the event stream, but with
containment rather than sequence semantics.

### C4 — Learned ensemble — build last, or never
A model over the pattern-occurrence feature vector (gradient boosting on
`pattern_kind`, quality, distances, regime, MTF, session → forward outcome).

Only worth it **after C1–C3 have shown that some individual patterns carry
signal**, because a model over sixty noise features will find a beautiful
in-sample story every time. If you get here: purged, embargoed walk-forward CV
only — ordinary k-fold on overlapping-horizon financial labels leaks, badly, and
will show you a Sharpe of 3 that does not exist. Your `test_lookahead.py`
philosophy applied to cross-validation.

---

## The research harness — the actual deliverable

`sim/patterns/research.py`. This is the part that makes the rest worth building.

The point: **a pattern is a claim about conditional distribution.** Measure the
distribution, conditioned and unconditioned, and compare.

### Forward labelling

From every instance's `trigger_i` (never `formed_i`, never `detected_i`),
entering at the **open of `trigger_i + 1`** to match the simulator:

```
mfe_atr[h]     max favourable excursion within h bars, in ATR at trigger
mae_atr[h]     max adverse excursion
ret_atr[h]     close-to-close return
h ∈ {4, 8, 16, 32, 64, 96}          (96 = your existing max_bars default)

barrier(k, h)  triple barrier: +k·ATR / -k·ATR / h bars, k ∈ {0.5, 1, 2}
               -> outcome ∈ {win, loss, timeout}, bars_to_outcome
hit_target     did the classical measured_move get reached, and how often
```

Ambiguous bars — where a bar's range contains both barriers — resolve as the
**adverse** one, and use `sim/intrabar.py` with the M1 proxy where available.
The README already establishes this rule for the simulator; the harness must not
be more optimistic than the thing it feeds.

### The control — the part everyone skips

Comparing a pattern's win rate to 50% is meaningless. Gold trended up over the
sample; *every* long has a good "win rate". You need a **matched control**:

- same number of instances,
- same side mix (long/short),
- same **regime** distribution,
- same **session/hour** distribution,
- same instrument and timeframe,
- drawn from bars where the pattern did **not** occur.

Then the statistic is the **lift**: `P(win | pattern) − P(win | matched
control)`, with a bootstrap CI on the difference.

You already invented the right form of this in `sim/robust.py`'s
`TimeShiftControl` — *"Trade count, side mix and risk geometry are identical;
only the ALIGNMENT with price is destroyed."* Reuse the reasoning, and reuse
`bootstrap()` and `percentile_of()` directly.

### Multiple comparisons — the part that kills projects

Ballpark the family size: ~50 pattern kinds × 2 directions × 3 symbols × 3
timeframes ≈ **900 hypotheses**. At α = 0.05 you get ~45 "significant" patterns
from pure noise. If you test them one at a time and keep the winners, you will
build a trading system entirely out of noise and it will look excellent.

Three mandatory defences:

1. **A holdout you do not look at.** Everything from 2024-01-01 forward is
   sealed. Not "mostly not looked at" — sealed, and touched exactly once, at the
   end, for the final set. The `data_hash` in `config.json` makes this auditable.
2. **Report the whole family in one table**, always, with Benjamini–Hochberg FDR
   control across it. Never report a single pattern's p-value in isolation.
3. **Best-of-N null.** The right null is not "this pattern's p-value" but "the
   maximum lift over 900 draws from the null" — generate it by permutation
   (shuffle the pattern occurrence times, preserving count and regime mix) and
   compare the observed best against that maximum distribution.

### Parameter sensitivity

For every pattern, sweep a small grid of its tolerances (`tol_atr`,
`min_height_atr`, `strength`, `max_span`) and report the **edge surface**. Require
a **plateau**, not a spike. An edge that exists at `tol_atr = 0.32` and vanishes
at `0.30` is a fit — and you already know from `tests/test_tl_parity.py` that a
0.01 change in that exact parameter is detectable, which is precisely why a
result that depends on it is not a result.

### Reporting

`runs/patterns/<id>/` mirroring `runs/<id>/`:
`instances.csv` (one row per pattern instance, `Pattern.to_row()` + all forward
labels), `by_kind.csv` (the family table with lift, CI, BH-adjusted q),
`by_quality_decile.csv` (does the score sort?), `sensitivity.csv`,
`config.json` with the input `data_hash`.

---

## Graduation gates

A pattern may not become a strategy until it passes all four. These sit *before*
your existing three simulator gates, which then apply unchanged.

**G0 — Existence.** ≥ 200 triggered instances in-sample (same floor as
`robust.MIN_TRADES`, and for the same reason: *"a 27-trade result in this project
reversed sign at 850 trades"*). Below the floor, report nothing at all. Not a
smaller number with a caveat — nothing.

**G1 — Lift.** Positive lift over the matched control, bootstrap CI excluding
zero, surviving BH correction across the whole family and the best-of-N null.

**G2 — Stability.** Sign-consistent across (a) the parameter plateau, (b) at
least two instruments or two timeframes, (c) yearly folds — no single year
carrying the result. USDJPY and EURUSD are dense from 1999; do the statistical
work there, where the sample is real.

**G3 — Cost survival.** The edge, in ATR, must exceed spread + slippage + the
swap band over the median hold time. Use the per-bar `spread` column already in
`data/bars/`, not a constant. **Expect this gate to kill most of T1.** A candle
pattern with a real, replicable 0.08 ATR edge over 4 bars is a genuine finding
and still not tradeable on a 15M gold bar. That is a correct outcome, not a
failure of the project.

Only then: build the strategy, run `tests/` gates 1 and 2, `deals_replay.py`
gate 3, and `sim/robust.py`'s time-shift control — as a *strategy*, on the
sealed holdout.

---

## Where it plugs into what exists

```
sim/patterns/
  __init__.py       build() — the public entry, mirroring sim/tl/__init__
  base.py           Pattern, Bias, PatternStatus, scoring
  candles.py        Kernel A + the T1 spec table
  sequence.py       Kernel B + the T2/T3/T4 spec tables
  scalars.py        Kernel C + the T5 spec table
  specs/            declarative pattern definitions, one module per tier
  engine.py         PatternEngine.walk(bars) -> [PatternSnapshot]   (mirrors tl/engine)
  composite.py      C1 stack scoring, C2 sequential FSM, C3 nesting
  features.py       per-bar feature emission
  research.py       labelling, matched control, lift, FDR, sensitivity
js/chart/patterns.js          the JS mirror (Phase 6)
tests/test_pattern_known_answers.py
tests/test_pattern_lookahead.py
tests/test_pattern_parity.py
tools/pattern_study.py        CLI: the family table
```

`PatternEngine.walk()` returns one `PatternSnapshot` per bar with **frozen
scalars only**, exactly like `tl.engine.Snapshot`. `sim/tl/features.build()`
gains a call into `sim/patterns/features.py`, and context timeframes ride the
existing `MTFContext` — so a 4H pattern is projected onto 15M through the same
closed-bar-only alignment, with no new look-ahead surface.

### Feature columns — keep it narrow

Resist one boolean column per pattern kind. Sixty booleans × four timeframes is
240 columns that are 99.8% zero, and it is an open invitation to C4 overfitting.
Emit per timeframe:

```
{tf}_pattern_id              str    joins to the pattern table
{tf}_pattern_kind            code   int, so it encodes like regime does
{tf}_pattern_tier            int
{tf}_pattern_dir             float  +1 bullish / -1 bearish
{tf}_pattern_quality         float  frozen at this bar
{tf}_pattern_triggered       0/1    fired on THIS bar
{tf}_pattern_trigger_level   float
{tf}_pattern_invalidation    float
{tf}_pattern_target          float
{tf}_pattern_age_bars        float  bars since detected_i
{tf}_pattern_stack_score     float  the C1 confluence stack
{tf}_pattern_active_mask     int    bitmask of tiers currently live
```

Detail matching `regime`'s treatment in `strategies/base._encode`: coded
categoricals, `None` → NaN, never a silent zero.

### Strategies

Subclass `MTFStrategy` as `tl_bounce` does. Extend `BASE_COLUMNS`, call
`self.gate(...)` so confluence stays measured rather than assumed, and register
in `FEATURE_STRATEGIES` in `sim/strategies/__init__.py`. `manage()`,
`ema_ok()`, `regime_code()`, sizing, costs and the signal log all come free.

The first pattern strategy should be **deliberately dumb**: trade the single
best-graduating pattern with a fixed ATR stop and your existing `risk_reward`.
Its purpose is to confirm the harness's prediction end-to-end in the simulator.
If the harness said +0.14 ATR lift and the simulator says −0.2R, something
between them is wrong, and finding that out on a one-pattern strategy is much
cheaper than on a twelve-pattern one.

---

## Tests

Four, in the established style.

**`test_pattern_known_answers.py`** — hand-built synthetic series containing
exactly one textbook instance each. Assert detection at the *exact* expected
`formed_i`, `detected_i` and `trigger_i`, and assert **non**-detection at every
earlier bar. Mirrors `test_known_answers.py`'s "hand-checkable results" idea.

**`test_pattern_lookahead.py`** — the truncation test, same shape as
`test_features_are_identical_when_the_future_is_removed`: rebuild the pattern
feature pipeline on truncated history and demand row *k* is identical. Plus two
structural assertions over every instance ever created:
`detected_i >= max(a.confirmed_i for a in anchors)` and
`trigger_i >= detected_i`.

**`test_pattern_parity.py`** — Python vs JS, instance for instance: same anchors,
same four bar indexes, same status transitions, same quality. With the
**negative control** you already use: perturb one tolerance in the JS alone and
demand the tests fail. A parity test that does not bite is decoration.

**Shuffle control** (in the known-answers file) — detection counts on
bar-shuffled data must fall to the random-geometry baseline. If your
head-and-shoulders count is unchanged on shuffled bars, the detector is finding
noise, and no amount of downstream statistics will fix that.

---

## Build order

**Phase 0 — foundation, no detectors.**
`base.py` (Pattern, Bias, PatternStatus), `engine.py` skeleton, `research.py`, the two test files.
**Validate the harness on something whose answer you already know:** run it over
`sim/divergence.py`'s existing instances. You know roughly what RSI divergence is
worth in this codebase; if the harness disagrees with the backtest, the harness
is wrong and you find that out before it has been used to make any decision.

**Phase 1 — Kernel B + T2 market structure.**
HH/HL/LH/LL, BOS, CHoCH, sweep. Largest sample, best statistics, and it
immediately gives `tl_bounce`/`tl_breakout` a better filter than `ema_ok`.

**Phase 2 — Kernel A + T1 candles.**
Two days of work, ~20 patterns, and the first real family table. Expect most to
die at G3. Publishing that table — *including* the failures — is itself
valuable output.

**Phase 3 — T3 geometric.**
Double/triple tops, H&S, triangles, wedges, flags, channels. The tier everyone
means by "chart patterns", built as spec rows on Kernel B.

**Phase 4 — Composites C1 then C2.**
The confluence stack and its A/B, then the sequential FSM. This is where the
project either earns its keep or tells you honestly that it does not.

**Phase 5 — T4 harmonic + T5 volatility.**
Ratio tables and rolling scalars on kernels that already exist. Cheap.

**Phase 6 — JS port + chart rendering.**
`js/chart/patterns.js`, parity test, and draw the shape at `formed_i` with a
marker at `trigger_i` — the same honest two-bar rendering `divergence.js`
already does for the RSI legs.

Optional, later: **unsupervised motif discovery** (matrix profile / `stumpy`)
over normalised windows, to find shapes nobody named. It is a legitimate
technique and it finds real repeated motifs — but it goes through the *same*
harness and the *same* four gates, and its multiple-comparisons burden is far
worse, because the search space is every window rather than sixty named shapes.
Do not start here.

---

## Traps specific to this repo

* **Gold intraday starts in 2016; 15M in 2018.** A "20-year pattern study" on
  XAUUSD 15M is fiction — this already cost you an out-of-sample conclusion once,
  per the README. Check bars-per-year before every study, and do the heavy
  statistical work on USDJPY/EURUSD, which are dense from 1999.
* **Server time, not UTC, with a shifting DST offset.** Any session-conditioned
  pattern (opening range, London/NY breakout, session-boundary sweeps) must go
  through the manifest offset. `tests/test_offset.py` exists; use it.
* **Swap dominates.** Keep pattern horizons under a day where possible, run
  `--carry-free`, and treat any result whose median hold crosses a rollover as a
  range rather than a number.
* **Spread is per bar and already in the data.** A 0.1 ATR pattern edge and a
  15M gold spread are the same order of magnitude. Use the column.
* **Tick history is ~18 months.** Anything needing intrabar resolution older than
  that uses the M1 proxy, resolved pessimistically — `sim/intrabar.py`.
* **Overlapping labels.** Two instances 3 bars apart with a 96-bar horizon share
  93 bars of outcome. They are not independent observations, and naive
  bootstrapping over them understates the variance badly. Either de-overlap
  (keep one instance per horizon window) or use a block bootstrap.

---

## What not to build

* **Image / CNN chart-pattern recognition.** It throws away the exact bar timing
  that your entire look-ahead architecture is built on, it cannot express
  `trigger_i`, it cannot be parity-tested against a chart, and it is
  unfalsifiable in the way that matters here. Everything it can find, Kernel B
  can find with an audit trail.
* **Sixty bespoke detector modules.** Three kernels and spec tables.
* **Any detector before `research.py` exists.** The temptation will be strong
  after Phase 0 and it should be resisted; the harness is the product.
* **Published win rates as priors.** Most candlestick statistics come from daily
  equity data, most have not replicated, and none of them account for your
  spread. Your own family table is the only number that should move you.
* **A composite score that was never A/B'd.** You already know how that question
  turns out when you actually ask it.

---

## Open questions for you

1. **Execution timeframe.** Everything above assumes 15M execution with 1H/4H/D1
   context, per `run_tl.py`. Patterns generally have better base rates on higher
   timeframes but far fewer instances, which fights G0. Worth running the first
   family table at 1H as well and comparing.
2. **Instrument set.** Gold, yen and euro behave very differently. Is a pattern
   allowed to graduate on one instrument, or must it hold on two? G2 above
   assumes two; that is a judgement call, and a strict one.
3. **Does quality need to sort?** I have written G1 as pass/fail on the whole
   population. The alternative — require the quality score to sort outcomes
   monotonically by decile — is stricter and more useful, but will fail many
   patterns whose edge is real and flat. My inclination is to report it always
   and gate on it only for T3/T4.

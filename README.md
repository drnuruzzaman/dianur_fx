# DiaNurFx

A TradingView-style charting terminal for your own MetaTrader 5 account. Runs
entirely on localhost: a read-only Python bridge talks to the MT5 terminal, and
a zero-dependency web front end draws the charts.

**Read only, by construction.** The bridge exposes no `order_send`, no modify,
no close — there is no endpoint that can move money or change a position, so
nothing in this app can trade. It binds `127.0.0.1` and restricts CORS to the
page's own origin, so no website you visit can read your account.

---

## Run it

Two processes, two terminals.

**1 — the bridge** (talks to MetaTrader 5; Windows only, like MT5 itself)

```bash
python bridge/mt5_bridge.py
```

It attaches to the terminal you already have logged in — no credentials in code
or chat. Add `--mock` to serve fabricated data with no terminal at all.

**2 — the page**

```bash
python serve.py
```

Then open <http://127.0.0.1:5173>. `serve.py` sends `no-store` on everything, so
an edited `.js` never runs from a stale cache.

If the bridge is not reachable the page still loads and clearly badges itself
`DEMO DATA`, serving a synthetic feed so the UI stays explorable. Click the
status pill (top right) to retry or point at a different bridge URL.

---

## What's in the UI

| Area | What it does |
|---|---|
| Watchlist | Live bid + daily change; click OPENS the symbol as a chart tab without disturbing the current one, hover `×` to remove (`u` undoes), `+` to search |
| Charts | 1 / 2 / 4 chart layouts, each with its own symbol, timeframe, type and studies |
| Chart tabs | MT5-style strip of every OPEN chart; click loads it into the focused cell (`Ctrl+W` closes) |
| Chart types | Candles, hollow candles, OHLC bars, line, area, Heikin Ashi, baseline |
| Studies | EMA, SMA, Bollinger, VWAP (overlay); Volume, RSI, MACD, ATR, Stochastic (panes) |
| Auto trendlines | Detected algorithmically per instrument × timeframe, with higher-timeframe lines projected onto lower-timeframe charts |
| Trend read | One verdict per instrument from three timeframes at once: regime per frame, swing structure, invalidation and R:R |
| Signal engine | Six independent component reads on the execution frame, with the composite's own walk-forward track record |
| Collapsible rails | Both side rails fold to a named spine (`\` left, `]` right); hover the spine to peek, click to pin |
| Account row | Balance, Equity, Floating, Margin free and **Margin level** in the footer, hidden behind `$` until asked for |
| Drawings | Horizontal line, trend line, ray, rectangle, Fib retracement — saved per symbol, shared across timeframes |
| Snapshot | `⤓ Snapshot` (or `s`) saves the chart as a 2x PNG on a white background, branded and timestamped, with positions excluded |
| Position lines | Open positions draw entry / SL / TP on the matching chart |
| Bottom tabs | Positions (with running total), Orders, History (7 days, net), Calendar |

Everything you arrange — layout, symbols, timeframes, studies, drawings,
watchlist, bridge URL — persists to `localStorage`, so a reload returns to the
same desk.

### Keyboard

| Key | Action |
|---|---|
| `/` | Symbol search |
| `1`–`8` | Timeframe M1 → W1 |
| `f` | Indicator menu |
| `a` | Fit all bars |
| `h` `t` `y` `r` `g` | Horizontal line, trend, ray, rectangle, Fib |
| `l` | Auto trendlines on/off |
| `u` | Undo the last watchlist removal |
| `s` | Save the chart as a PNG |
| `Ctrl+W` | Close the focused chart tab |
| `\` | Collapse / expand the Watchlist + Session rail |
| `]` | Collapse / expand the Trend read + Signal engine rail |
| `Delete` | Remove the selected drawing |
| `Esc` | Back to the cursor / close menu / clear the selection |

### Chart typography

Axis labels, the last-price tag, the countdown and both crosshair tags all share
one size (9.5px mono) because they sit on or against the axes -- shrinking the
axis alone would leave the price tag looking oversized among the ticks it is
supposed to line up with. Pane titles, study legends and structure labels are
sized separately, since they sit in the plot and compete with the candles rather
than with each other.

`AXIS_W` is 54px, sized from the widest label it must actually hold rather than
guessed: measured at the axis font, every instrument in the watchlist renders a
7-character price at 40px and the worst realistic case ("53478.90") at 46px,
drawn at `plot.r + 5`. 64px was inherited from a larger font and never
re-measured, so it was carrying ~13px of dead space that belonged to the
candles.

### Mouse

Drag to pan · wheel to zoom (shift+wheel to pan) · drag the price axis to
stretch it · drag the time axis to compress · double-click to reset the scale ·
click a drawing to select it, `Delete` to remove · right-click a drawing to
delete it under the pointer · hover a collapsed rail's spine to peek, click to
pin.

Charts open with **no indicators attached** — add what you need from `ƒ
Indicators`. The legend shows the **candle time**: the bar under the crosshair,
or the forming bar with a live `closes in mm:ss`, which also appears boxed under
the last-price tag on the axis.

---

## Layout of the code

```
index.html            shell: topbar, watchlist, chart grid, tape, bottom tabs
css/app.css           Ausloans brand tokens on a dark trading surface
js/api.js             bridge client; falls back to the demo feed when offline
js/demo.js            synthetic feed (only used when the bridge is unreachable)
js/util.js            formatting, DOM helpers, localStorage, FX sessions
js/chart/engine.js    the chart: canvas renderer, panes, scales, interaction
js/chart/indicators.js study library — pure functions of the bar array
js/chart/trendlines.js algorithmic support/resistance detection + projection
js/ui/menu.js         popup menu + toast
js/ui/search.js       symbol picker
js/ui/watchlist.js    quote rail + session strip
js/ui/panels.js       bottom tables, tape, contract spec
js/main.js            workspace: grid, toolbar, polling loops, persistence
bridge/mt5_bridge.py  read-only MT5 → localhost JSON bridge
tools/mt5_download.py bulk history exporter (bars + ticks) for backtesting
tools/dataset.py      loaders for the exported history
tools/capture_specs.py instrument spec snapshot -> data/instruments.json
tools/deals_replay.py  gate 3: reconcile the sim against real broker fills
sim/core.py           the simulator core: fills, sizing, costs, invariants
sim/fx.py             point-in-time currency conversion into AUD
sim/metrics.py        expectancy, drawdown, per-year breakdown, benchmark
sim/indicators.py     Python mirror of js/chart/indicators.js (parity-tested)
sim/divergence.py     RSI divergence detection (mirrored by js/chart/divergence.js)
sim/tl/               multi-timeframe trendline engine (strategy-agnostic)
sim/tl/swings.py      Layer B: swing detector as a state machine, two clocks
sim/tl/strategy.py    Layer E: the only layer that decides; every gate recorded
sim/tl/experiments.py frozen, versioned experiment definitions
js/chart/tlengine.js  the same engine, ported for the chart (parity-tested)
js/chart/structure.js HH/HL/LH/LL, ported (parity-tested)
js/chart/channels.js  channels, ported (parity-tested)
js/chart/zones.js     zones, ported (parity-tested)
js/chart/segments.js  regime episodes, ported (parity-tested)
js/chart/sensitivity.js per-instrument thresholds, ported (parity-tested)
js/chart/slopelines.js  one-pivot slope lines, ported (parity-tested)
js/chart/supplydemand.js impulse-origin zones, ported (parity-tested)
js/chart/marketstructure.js BOS / CHoCH, ported (parity-tested)
js/chart/slopelines.js  one-pivot slope lines — measured, NOT drawn
js/chart/regime.js    regime classification, ported (parity-tested)
js/chart/read.js      composition: conviction, invalidation, R:R (NOT mirrored)
js/chart/signals.js   six components + a walk-forward scorecard (NOT mirrored)
js/ui/trendread.js    the Trend read panel
js/ui/tips.js         shared explanatory tooltip for every panel row
js/ui/signalpanel.js  the Signal engine panel
tools/gold_breakout_wf.py  the gold 4h breakout, run through the real simulator
sim/strategies/       donchian, ema_cross (baselines); tl_bounce, tl_breakout,
                      rsi_divergence (candidates)
sim/run_tl.py         feature build + strategy runs + the confluence A/B
sim/run.py            run a backtest, write runs/<id>/
tests/                the three gates
serve.py              no-cache static dev server
```

No build step, no bundler, no npm — ES modules straight from disk. Two design
notes worth knowing before you edit:

* **The x-axis is bar index, not time.** Weekend and holiday gaps collapse the
  way a trading chart is expected to. `view.right` is a float index of the
  rightmost visible slot and may exceed the last bar — that's what produces the
  right-hand margin and lets you scroll into empty space.
* **Panes are derived from the study list.** Adding an oscillator restacks the
  chart with no layout bookkeeping anywhere else. Price keeps at least 45% of
  the height no matter how many oscillators you stack.

### Adding an indicator

Add one entry to `INDICATORS` in `js/chart/indicators.js`: a `label`, a `pane`
(`'main'`, `'volume'` or `'own'`), default `inputs`, and a `calc(bars, opts)`
returning plot descriptors (`line`, `histogram`, `band`, `level`, `zone`,
`range`). The renderer draws them blindly — no engine change needed. Then list
the key in the indicator menu in `js/main.js`.

---

## Bridge endpoints used

`/health` `/account` `/positions` `/orders` `/deals` `/symbols` `/spec`
`/quotes` `/ticks` `/bars` `/calendar` — all GET, all read-only.

Polling cadence: quotes 1s, tape 1.2s, account/positions 2s, health 5s,
history 30s, daily reference closes 60s, calendar 5min. Polling pauses while
the tab is hidden.

---

## Algorithmic trendlines

`js/chart/trendlines.js` derives support and resistance lines from the bars
themselves — nothing is hand-drawn and nothing is stored. Lines are computed
**separately for each instrument × timeframe**, from that series' own bars, and
the `⌁ Auto TL` menu controls them (`l` toggles them off and on).

How a line is found:

1. **Pivots** — fractal swing highs/lows, where a bar beats `strength` bars on
   each side. `strength` is the sensitivity control: 2 finds minor swings, 6
   finds structural ones.
2. **Candidates** — every pair of same-type pivots inside the lookback window,
   fitted in bar-index space (bars are evenly spaced there; time is not).
3. **Validation** — a resistance line stays alive only while no bar *closes*
   above it. Wicks may pierce; the tolerance is ATR-scaled, so a graze on gold
   isn't judged by the same absolute distance as one on EURUSD.
4. **Scoring** — distinct retests dominate (consecutive grazing bars count as
   one *event*, so counts compare across timeframes), then span, then recency,
   minus a penalty for distance from the last close: a line 5 ATR away is true
   but untradeable today.
5. **Dedupe and budget** — near-identical lines collapse to the best one, and
   "lines per side" is a budget for the whole chart, so a strong daily line can
   outrank a weak local one instead of every timeframe adding its own quota.

Lines are recomputed when a chart loads and again as each bar closes (debounced),
so they track the market rather than being a snapshot.

### Projection across timeframes

This is why a detected line carries a **per-millisecond slope** rather than a
per-bar one: bar index is local to one series, but time is shared. A line found
on H4 is drawn on an M15 chart by mapping its timestamps through that chart's
own index space, so it lands on the correct candles including across weekend
gaps.

Each chart shows its own timeframe's lines plus any higher timeframes you enable
(the menu only offers timeframes above the current one — projecting downwards
would be meaningless). Source is legible without reading the labels: dash length
grows with the source timeframe, own-timeframe lines are solid and show their
anchor pivots, and every line is labelled like `H4 R×5` — timeframe, side, and
number of retests. Support is green, resistance pink, as everywhere else.

Higher-timeframe series are fetched once per instrument+timeframe and cached for
a quarter of a bar interval, shared across every open cell. Detection costs
5–8ms per series on 2000 bars, so a four-chart layout each projecting three
higher timeframes recomputes in about 100ms on a bar close.

## Multi-timeframe trendline engine

`sim/tl/` describes market structure and knows nothing about trading. 15M is the
execution timeframe; 1H/4H/D1 are context.

```
sim/tl/pivots.py    swing highs/lows, each carrying confirmed_i
sim/tl/lines.py     the Trendline object: role, direction, lifecycle, quality
sim/tl/engine.py    per-timeframe incremental walk -> one Snapshot per bar
sim/tl/regime.py    trending_up / trending_down / sideways / transition
sim/tl/mtf.py       closed-bar-only alignment + the confluence score
sim/tl/features.py  the per-candle feature table
sim/tl/structure.py HH / HL / LH / LL swing-sequence classification
sim/tl/channels.py  parallel channels, paired and projected
sim/tl/zones.py     horizontal support/resistance BANDS
sim/tl/segments.py  regime history as a sequence of episodes
sim/tl/sensitivity.py per-instrument detection + validation thresholds
sim/tl/slope_lines.py one-pivot lines with a volatility-derived slope
sim/tl/supply_demand.py zones from impulse origins, not pivot clusters
sim/tl/market_structure.py BOS and CHoCH
sim/tl/fvg.py       fair value gaps: three-candle imbalances
sim/tl/clockguard.py one timestamp convention, enforced at the boundary
```

**Lifecycle**: `CANDIDATE` (two pivots) -> `CONFIRMED` (a third distinct touch)
-> `ACTIVE` (still being respected) -> `BROKEN` (a close through it) ->
`ARCHIVED`. Only a confirmed line is offered to a strategy, and only a confirmed
line breaking counts as a break event — a candidate breaking is a bad guess
expiring, not news.

**Quality score** (0-100) weights retests first, then span, then recency, minus
distance from price and any violations. It is frozen at the moment a line breaks,
so a break event reports what the line was worth *then*.

### Look-ahead protection is in the engine, not just in tests

Four mechanisms, each structural:

1. **`BarView`** raises `LookAheadError` on any negative index — `bars[i+1]`
   cannot be written.
2. **Pivots carry `confirmed_i`** (`i + strength`). A swing low is unusable until
   the bar it actually became visible, which is what makes divergence honest.
3. **MTF alignment matches on bar CLOSE times.** At 09:15 the 4H context is the
   candle that closed at 08:00, never the one still forming until 12:00. Strict
   mode re-verifies the map and raises.
4. **Snapshots freeze their scalars.** A `Trendline` is mutable and keeps being
   updated on later bars, so reading `line.quality_score` after a walk returns
   the score it *eventually* reached. Snapshots capture the value at that bar.

Mechanism 4 was found by `tests/test_lookahead.py`, which rebuilds the entire
feature pipeline on truncated history and demands the row at bar k be identical
to the row at bar k built from full history. It failed at 87.13 vs 86.37 — a real
leak, in code that looked obviously fine.

### Per-symbol chart settings

An instrument remembers how you look at it. Timeframe, chart type, studies and
zoom are stored under `sym.<SYMBOL>`, independently of any open tab, so closing
a chart no longer throws them away -- reopen XAUUSD and it comes back on H4 with
its EMA and Bollinger attached and the same span, whatever you were looking at
in the meantime.

    draw.<SYMBOL>   drawings          (already per-instrument)
    sym.<SYMBOL>    tf, type, studies, span

`span` is in there deliberately: how far you zoom out is part of how you read an
instrument, and daily gold wants a different span from 5m EURUSD.

WHICH SETTINGS WIN. `openSymbol()` restores a symbol's own settings when it has
any, and only inherits the focused chart for a symbol with no history -- "show
me GBPUSD" then has nothing better to mean than "the way I am looking at things
now".

THE ONE AMBIGUITY, resolved explicitly. The same symbol can be open in two cells
on different timeframes, and there is no single right answer for what to
remember. `persist()` writes every rendered chart but writes the ACTIVE one
LAST, so the chart you are looking at wins.

### Chart tabs

The strip under the chart lists every chart you have OPEN, MT5-style. The layout
decides how many render at once; the tabs decide which ones. Clicking a tab
loads it into the FOCUSED cell, so tabs and tiling coexist -- in a 2-chart
layout you can hold EURUSD on the left and cycle the right-hand cell through
four instruments without disturbing it.

    app.tabs    every open chart's state
    app.slots   which tab each visible cell is currently showing

Two states, drawn differently, because in a 4-chart layout four tabs are SHOWN
and only one is ACTIVE: a single highlight would lie about which cell has focus.

THE BUG THIS MODEL FIXES. The old code kept one array of VISIBLE charts and
persisted with `cells = charts.map(state)`, so anything past the layout count
was deleted on the next save -- which is why more charts than cells was not
possible before. `persist()` now writes each rendered chart back into ITS OWN
tab and leaves the rest alone.

Rebuilding the grid preserves the focused CELL. Switching a tab in the
right-hand cell rebuilds, and without that the focus jumps left, so the next tab
click would load into the wrong cell.

TABS ARE OPENED FROM THE WATCHLIST, not from a `+`. Picking an instrument used
to overwrite the focused chart, silently discarding its timeframe, studies and
view -- the chart you were reading was simply gone. `openSymbol()` now behaves
like MT5's Market Watch, and does one of three things:

    already visible          focus that cell, change nothing else
    open but off-screen      bring that tab into the focused cell
    not open                 new tab, inheriting the focused chart's
                             timeframe, type and studies

The inheritance is deliberate: "show me GBPUSD" nearly always means "the way I
am looking at things right now". Reselecting a symbol that already has a tab
must not create a second one, which is the case the first branch exists for.

Both entry points route through it -- the watchlist and the symbol search -- so
there is no path left that replaces a chart in place. `setSymbol()` and the
`Chart.setSymbol()` method it called are both deleted rather than left as dead
surface, and with the watchlist opening charts a separate `+` was redundant.

### Snapshot

`⤓ Snapshot` or `s` writes a PNG of the focused chart. Four decisions in it are
worth recording, because each was wrong in an earlier version:

  * **Positions are excluded.** A snapshot gets shared, and a share should not
    carry position size or where the stops sit.
  * **It paints synchronously.** `draw()` only schedules a repaint on the next
    animation frame, so `toDataURL` captured the frame from BEFORE export mode
    took effect -- the first version shipped images with the position lines
    still in them. `snapshot()` calls `_paint()` directly.
  * **The background is baked white, not transparent.** Transparency looked
    correct in a compositor and wrong everywhere else: viewers and chat clients
    paint their own backing behind a transparent PNG, usually dark, so a
    light-ink chart landed on a dark field and appeared unchanged.
  * **The ink is a palette swap, not a second renderer.** Every draw site reads
    `COL.<key>` at call time, so the export overwrites those keys, paints, and
    restores them in a `finally`. Brand hues are darkened where they had to be
    -- `#93C90F` green is a highlighter against paper.

Rendered at 2x by multiplying the backing store; layout is driven by
`this.w/this.h`, which do not change, so nothing reflows and text is simply
rasterised finer.

### The side rails collapse

Both rails fold to a zero-width grid track -- `\` for Watchlist + Session,
`]` for Trend read + Signal engine, or the handle at each edge. On a laptop the
two rails take 478px of a 1517px window, which is a third of the chart.

They collapse the TRACK rather than hiding the panels. `display:none` would tear
the panels down and lose their scroll position and any in-progress render;
a zero-width column keeps them mounted and simply gives them no room, so they
come back instantly and still holding their state. The canvases are resized
after the transition settles, or they would keep the old backing-store width and
render blurred into the space they just gained.

A collapsed rail leaves a 26px SPINE carrying its name -- "Watchlist · Session",
"Trend read · Signal engine" -- because a rail you cannot see is a rail you
forget exists. Hovering the spine PEEKS the panel open; clicking it PINS it.

The open-state handle is VERTICALLY CENTRED on the rail edge. Pinned to the top
it sat straight on the chart legend, covering the instrument name; the middle of
a rail edge is empty by construction. It also sits at 55% opacity until hovered,
since it is a control you reach for rarely and read past constantly.

The peek OVERLAYS the chart rather than reflowing it. Widening the grid track on
hover would resize every canvas on every mouse-over, a full re-render for a
glance; sliding the panel over the chart costs one transform and leaves the
backing store alone. Only a pin changes the track, and only a pin triggers the
canvas resize.

The spine and the peeked panel are one hover region, but leaving the spine to
enter the panel fires `mouseleave` before `mouseenter`. A 140ms grace window
bridges that, or the panel snaps shut under the cursor on its way in.

ONE TRAP WORTH KNOWING. A collapsed rail is `position:absolute`, which takes it
out of the grid FLOW -- and auto-placement then slides everything after it one
column left, putting the chart section in the 26px stub track and blanking it.
Each area is pinned with an explicit `grid-column`, so the layout no longer
depends on which rails happen to be in flow.

State persists per rail.

### Margin level

Sits next to Margin free in the footer account row, because it answers a
question Margin free does not: `equity / margin used`, the number a stop-out is
actually measured against. Free margin can read healthy on a large account while
the level sits near the broker's threshold.

Coloured on the usual bands -- below 100% red, below 200% amber, above green --
and rendered as an em dash when nothing is open, since MT5 reports 0 there and
`0%` would look like a catastrophe rather than an empty account.

The row stays hidden behind the `$` button until asked for. A balance is the one
number on screen that is nobody else's business, and this app is often on a
shared or recorded screen.

### Trend read and Signal engine

Two panels in the right rail. Both follow the FOCUSED chart in both axes --
instrument and timeframe -- because they describe the chart you are looking at.
The headings are also the timeframe picker, and picking there moves the chart:
two timeframes on screen, one the chart's and one the panel's, is how a reader
ends up trading a signal computed on a series they were not watching.

**Trend read** answers "is this trending, do the frames agree, and where would I
be wrong?" It reads the chart's own frame plus the two above it -- M15 is read
as 15m/1h/4h, H1 as 1h/4h/1d. The ladder deliberately skips 30m: context earns a
row only if it can disagree with the row below it, and a 30m read beside a 15m
read is close to the same series sampled twice. Near the top there is nothing
above to add, so the read extends DOWNWARD (a D1 chart reads 1h/4h/1d) rather
than silently shrinking to two frames and quietly weakening every headline.

Two readings per frame, from two different definitions of "trend":

* **Regime** (`js/chart/regime.js`) -- EMA separation in ATR units, position
  inside the recent range, and whether that range is expanding or contracting:
  `trending up / trending down / range-bound / transition`.
* **Structure** (`js/chart/structure.js`) -- the literal swing sequence. Each
  confirmed pivot is labelled against the previous pivot of the same kind:
  `HH`, `HL`, `LH`, `LL`. `HH + HL` is up, `LH + LL` is down, and the two mixed
  cases are reported as what they are -- broadening or contracting -- rather
  than flattened into "sideways". Structure is taken from the HIGHEST frame
  read, because an `HH + HL` on 5m inside an H1 downtrend is a retracement.

They can disagree, and the disagreement is the useful part: EMAs can point up
while the market prints a lower high.

Then the two numbers that decide whether the sentence is worth acting on.
**Invalidation** is the nearest level on the wrong side -- for a bull read, the
most recent higher low or the nearest support beneath price, whichever is nearer,
since the one that breaks first is the one that matters. **R:R to first zone** is
reward to the first opposing line over risk to invalidation: not to a target you
picked, to the first thing in the way.

**The geometry cap is the point.** Three frames can agree perfectly and the trade
still be unplaceable, because price has already run to just under resistance and
the first zone is nearer than the stop. When R:R is below 1 the conviction score
is capped and the panel SAYS `capped by 0.41:1 geometry` -- a reader who sees 35
with no explanation assumes a weak read, when in fact the read was strong and the
trade is simply badly placed. Agreement is not an edge if the geometry has
already spent it.

Two rules keep the headline honest, both added after the panel got them wrong:

* **Geometry modifies a directional read; it does not create one.** R:R first
  contributed up to 30 points on its own, and USDCAD printed `BEAR 75` on a
  5.62:1 setup while its M15 frame was trending UP. A stop far away and a target
  further away is evidence about room, not about direction. The bonus is now 15
  points and is only paid once the direction has earned 30 on its own.
* **A frame trending AGAINST the call demotes it to WATCH**, however good the
  rest looks, with the theme `Unconfirmed trend`.

**Signal engine** scores six components on the execution frame, deliberately
different in kind rather than six flavours of momentum -- six agreeing trend
indicators is one opinion repeated. `meanrev` is INVERTED so it can fight the
other five at extremes; a composite where everything points the same way in a
stretched market cannot warn you.

The composite is not a prediction, and the panel refuses to show it without its
own track record beside it: walk-forward accuracy, the **majority baseline** that
accuracy has to beat, how strong signals actually resolved five bars forward, and
the average move that followed. Accuracy is coloured against the baseline, never
against 50 -- a model that is 56% accurate where the market printed 57% up bars
has learned the drift and nothing else. The walk-forward is genuine: every
prediction at bar i comes from a model trained only on bars before i.

`regime.js` and `structure.js` are ports of `sim/tl/*.py` compared bar-for-bar in
`tests/test_structure_parity.py`, so those words are the words a backtest reads.
A negative control confirms the test bites: moving the JS equality band from 0.10
to 0.11 ATR, or the regime slope threshold from 0.35 to 0.36, fails 3 of the 4
parity tests. **`read.js` and `signals.js` have NO Python mirror and are not
parity-tested** -- they read parity-tested inputs, but their weights are a stated
opinion, not a measurement. Do not backtest against those scores.

### Drawing a line is a lower bar than trading one

`min_quality` is 90, and that is a measured threshold (see the trendline results
below). At 90 a EURUSD 4h chart draws ZERO lines, and a structure tool that draws
nothing is not being rigorous, it is being useless.

So the two thresholds are separate. `minDraw` (70) decides what APPEARS;
`min_quality` (90) decides what is flagged `offered`, and the renderer draws the
rest at 45% alpha and thinner. You see the structure and can still tell at a
glance which lines the engine would stand behind. Anchor pivots are drawn for
projected lines too, not just own-timeframe ones -- the anchors of an H4 line are
exactly what a 15m chart cannot otherwise show you. The second anchor is hollow:
it is the later of the two, so the line's direction reads without following it.

`Snapshot.tradeable` deliberately keeps reporting every confirmed line regardless
of quality. It is the diagnostics' measurement surface, and filtering it would
make the low-quality population -- the one carrying the strongest measured effect
in this project -- impossible to measure at all.

### Zone detection: what a fixed ATR budget got wrong

`max_distance_atr = 12` sounded instrument-neutral and was not. Measured over
the same 500-bar window it covered 19% of the actual price range on USDJPY 1h
and 54% on XAUUSD 4h -- so the same number meant three different things
depending on what you were looking at, and on gold H1 it cut 8 surviving
clusters down to 2.

The allowance is now `max(12 x ATR, 0.75 x lookback range)`. The range term
normally binds and self-scales with the instrument; the ATR term is a floor for
an unusually quiet window, where the recent range can collapse to nothing. The
SAME allowance drives the proximity score, so a zone that barely survives the
cut also scores near zero for closeness -- previously the filter and the score
used different yardsticks.

`min_touches` went 3 -> 2. A clean double top is a level a trader draws, and the
scorer already scores it lower (9 touch points against 28 for four touches), so
a weak zone competes and loses rather than being excluded before it can compete.
Hard-gating at 3 removed 23 of 33 clusters on gold H1. Note that "loses on
merit" is a claim about the SCORE, and the score turned out not to predict
whether a zone holds -- see the ranking result below. What min_touches=2 really
buys is that the population is not pre-filtered on a criterion that was never
shown to matter.

RE-MEASURED, because these change a detector whose structural result was already
on record. Old and new params, identical bars, three eras:

| era | old | new |
|---|---|---|
| 1999-2010 | +10.76 pp | **+11.83** |
| 2011-2020 | +10.23 pp | **+11.25** |
| 2021-2026 | +10.23 pp | +8.82 |
| pooled | +10.45 pp | **+10.99** |

More zones (4.2 -> 5.6 per bar), 25% more approaches, and the levels hold
marginally BETTER. No trade-off to weigh. This is a statement about WHICH LEVELS
ARE ZONES, which is the part of the detector that carries the finding -- not
about their order, which carries nothing. These absolute numbers are not
comparable to the +5.5/+5.0/+5.1 recorded elsewhere -- that used matched-candle
stratification and this does not -- but old-vs-new is a fair comparison.

### Counting the reaction, not just the touches

A touch count says price ARRIVED at a level. It does not say the level did
anything. Eight touches that each drifted 0.3 ATR away describe a level price
walks through; two touches that each threw price 7 ATR away describe a level
price respects. The old scorer ranked the first above the second, because
touches carried 35 of its 100 points and nothing measured what happened next.

`reaction_atr` is the MEDIAN excursion away from the zone's own pivots, in ATR:
for a swing low, how far price rose afterwards; for a swing high, how far it
fell. Median rather than mean, so one violent bounce cannot carry a level that
otherwise did nothing. It earns up to 17 points, full marks at
`reaction_full_atr = 2.0`, and the other weights were cut to pay for it:

| term | before | after | what it asks |
|---|---|---|---|
| touches | 35 | 28 | how often did price come back? |
| tightness | 25 | 22 | is this a level or a region? |
| span | 20 | 15 | has it held across time? |
| proximity | 20 | 18 | does it matter from here? |
| **reaction** | -- | **17** | did price actually LEAVE? |

CAUSAL, and deliberately pessimistic about fresh levels. Every bar read lies
between the pivot and the bar being computed, and the window is clipped at that
bar -- so a pivot three bars old is scored on the three bars that exist, not
credited with an excursion that has not happened yet. That makes a brand-new
pivot report a small reaction and drag the median down. The alternative -- wait
for the full 20-bar window before scoring -- would be look-ahead wearing a
plausible face, so the bias stays.

The effect on live gold H1, where the intent shows plainly:

    RES  4428.90 - 4441.44   4 touches   4.09 ATR reaction   strength 71
    SUP  4449.73 - 4450.62   2 touches   7.04 ATR reaction   strength 64
    RES  4396.43 - 4416.43   8 touches   2.43 ATR reaction   strength 62

The two-touch level now ranks ABOVE the eight-touch one. Under the old weights
that ordering was impossible: 8 touches beat 2 by 24 points and nothing else
could close the gap.

The tooltip reports the figure alongside the touch count, so a zone that scores
well on reaction and badly on touches can be read as such rather than taken on
the strength number alone.

THE TOOLTIP HAD TO BE CORRECTED once the ranking result below came in. It read
`RATING 74 / 100 - strong`, which is a claim about what the zone will do. The
row is now `SHAPE`, the words run textbook / clean / rough / marginal rather
than strong / solid / moderate / weak, and a footnote states the measurement
outright: a high score holds no more often than a low one. The number describes
how cleanly the zone is drawn, which is a real property and the wrong one to
read as confidence.

RE-MEASURED, and **the pooled hold rate did not move**: +11.05 pp with the term
off against +11.25 pp with it on, over 21,900 approaches and three eras, and
2011-2020 came out slightly WORSE (+11.17 -> +10.80). A 0.2 pp swing that
changes sign by era is noise, and it is not evidence the change helped.

That measurement was also the wrong question, which is worth saying rather than
quietly running a better one. Reaction is a SCORING term: it cannot make a level
hold more often, only change which levels the detector calls good. Averaging the
hold rate over every surviving zone is blind to that by construction -- reorder
the same six zones and the average is unchanged. The only thing the pooled
number can detect is the second-order effect of a different top six surviving
the `max_zones` cut, which is exactly the 0.2 pp of nothing it found.

The question the score actually makes is whether it RANKS: sorted by strength,
does the top of the ranking hold more often than the bottom, and is that spread
wider with reaction than without?

**It does not rank. Not with reaction, and not without it.**

    arm    quartile        n    strength    hold%
    off    Q1 worst     5488        47.4    60.95
    off    Q4 best      5479        67.6    60.49
    on     Q1 worst     5460        64.1    61.37
    on     Q4 best      5451        84.5    60.72

    best minus worst:   reaction off  -0.47 pp
                        reaction on   -0.65 pp
                        change        -0.19 pp   CI [-2.73, +2.44]

The best-scoring quartile holds very slightly LESS often than the worst. By
rank, the top-ranked zone holds 60.44% and the sixth 62.88%. The correlations
put it beyond rescue:

    strength vs held    -0.0097
    reaction vs held    +0.0077
    touches  vs held    -0.0105

So it is not that reaction is a weak term in a good score. **No component of
the score ranks** -- not touches, not tightness, not reaction. The reweighting
was rearranging terms that were all measuring the same nothing, which is why
the pooled number never moved.

WHAT SURVIVES THIS is the thing that was actually measured: zones as a CLASS
carry +11 pp over a matched placebo, replicated across three eras. Being a zone
is informative. Being a *better* zone is not. The detector's SELECTION carries
the finding; its RANKING carries none of it, and the two were easy to conflate
because they come out of the same function.

The score still decides which six zones survive `max_zones`, so it is not
useless -- it is a tidiness criterion doing a tidiness job. It was the word
"strength" that made it look like more than that.

### Why the 15m chart looked like a cobweb

The complaint was "so many trendlines". The trendline engine was not the cause,
and counting first is what found that: on a 15m gold chart the budget was doing
its job and drawing exactly 4 lines. The canvas held 22 diagonals.

    autoLines   4      <- the budget, working
    channels    3      <- 6 rails
    segments   12      <- 12 sloped runs
                ---
                22 diagonal lines

**`app.auto.channels` and `app.auto.segments` existed in saved state, were both
`false`, and were never read by anything.** Neither had a menu entry either, so
there was no way to turn off two overlays that had been drawing on every chart
since they were added. The line budget exists precisely to stop this happening,
and it was being defeated by two sources that never passed through it.

Both are now gated on their flag, both default OFF, and both have a menu entry.
That alone took the 15m chart from 22 diagonals to 4.

#### Scrolling back shows what the engine WOULD have drawn

The AUTO stack ran on the full series no matter where the chart was scrolled, so
panning into history drew TODAY's lines over year-old bars. That is
unfalsifiable by construction: those lines were fitted knowing what the bars to
their right did, so of course they sat well. The chart could not be used to
check the detector, only to admire it.

The visible right edge is now treated as the present. The series is cut there
and every detector re-runs on the slice -- trendlines, channels, zones,
supply/demand, swings, BOS/CHoCH -- so the chart draws what the engine would
have drawn standing on that bar with no knowledge of anything after it. That is
the same rule the backtests run under, which makes scrolling a genuine test
rather than a tour of hindsight.

Verified by round trip on 15m gold: at the live edge, four lines from 1h and
15m; 600 bars back, an entirely different set anchored on 4h and 1d; 1200 bars
back, different again; and returning to the live edge restores the original four
lines, three channels and six zones exactly. No state leaks between positions.

#### The analysis holds still between grid points

Re-detecting as-of the right edge made the chart honest and unusable to look at.
Dragging 100 bars left changed the channel set at every sample:

    back   0   J-1200-1446, J-1200-1402
    back  20   J-1205-1451, J-1205-1407     same corridors, anchors moved 5 bars
    back  40   J-1375-1439                  a different corridor entirely
    back  60   P-1165-1382, P-1165-1382, J-1276-1459
    back  80   J-1415-1479
    back 100+  none

The corridor you were examining moved while you examined it. And this is not
noise in the DRAWING: the detector is genuinely re-estimating from a sliding
window, and every one of those answers is correct at its own instant.

The as-of point now snaps to a 25-bar grid. 25 is not a taste: it is the cadence
the MEASUREMENTS use -- `zone_remeasure.py` and `zone_rank.py` both re-detect
every 25 bars -- so a chart refitting on every bar was showing something no
backtest here has ever tested. Snapping to the same grid makes the picture hold
still while you read it AND makes it the picture that was measured.

Sampled every 6 bars afterwards, the set is identical within each grid cell and
changes only at the boundaries:

    back  0-12   0 channels
    back 18-36   1   J-1374-1438                              (4 samples, identical)
    back 42-60   3   P-1169-1386, J-1280-1463, J-1339-1463    (4 samples, identical)
    back 66-72   1   J-1424-1488

The live edge is exempt and stays exact. "Now" is a real bar, not a grid point,
and rounding it would stop the newest bars from updating the read.

#### Channel ids were not unique

Visible in the table above: two corridors at `back 60` both calling themselves
`15m-CH-P-1165-1382`, with upper rails 29 points apart. The overlap window
`i0..i1` does not identify a channel -- two different rail PAIRS can share one.
Nothing keys on the id today, so it cost nothing and showed nothing; the first
`Map` keyed by it would have silently dropped a corridor. The id now carries the
rails' own anchors, in both languages. Verified over 21 scroll positions and 29
drawn channels: no duplicates.

#### The whole Auto TL menu belongs to the instrument AND the frame

It began as one global. Splitting out sensitivity alone left the menu half
per-symbol and half not, which is worse than either -- so every entry is now a
claim about ONE instrument: how big a swing has to be, how many lines are worth
drawing, which frames are worth projecting from, which overlays help. Gold at 42
ATR and EURUSD at 0.0008 do not answer any of those the same way, which is the
same reason `max_distance_atr`, `dedupeAtr` and the draw-distance budget all had
to stop being flat numbers.

KEYING ON THE INSTRUMENT ALONE WAS ALSO WRONG, for the same reason keying on
nothing was. Strength 3 is the sensible read on H1 and says nothing about what
M15 or D1 want -- measured on XAUUSD H1, `major` turns HALF its BOS marks into
sub-quarter-ATR closes while `normal` is the strength every backtest on record
was run at, and neither fact transfers to another frame. Overrides are a map of
timeframe -> settings.

    XAUUSD.a   { 1h: normal, 15m: major }     <- chosen, per frame
    USDJPY.a   (defaults)
    dnfx.auto  sens normal, maxLines 3         <- global default, untouched

`app.auto` stays the DEFAULT for anything never configured, so nothing moves
until you choose. The menu reads `activeAuto()` -- symbol AND frame -- and
writes back to both, so a toggle flips exactly what the tick just showed.
Switching frames re-ticks it: H1 shows `✓Normal`, M15 `✓Major structure`, and
H4, never touched, falls through to `✓Normal` from the global.

A LEGACY FLAT `auto` OBJECT is read as belonging to `record.tf` -- the frame the
symbol was on when it was written, which is the frame the reader was looking at
when they chose it. Spreading it across every frame would turn one decision into
eight.

A BLANKET RENAME IS NOT A REFACTOR, and this one cost two rounds. Replacing
`app.auto.` with `auto.` across `runAuto` needed a `const auto` binding, and the
insertion anchored on `asOfCut(chart)` -- which appears in `loadTrendRead`
first, so the declaration landed in the wrong function and every overlay
silently vanished (`lines 0, zones 0, swings 0`). Moving it into `runAuto` then
put it AFTER the `if (!auto.on)` guard that reads it: `Cannot access 'auto'
before initialization`. It is the first line of the function now. Both failures
were only visible in the console; the chart just drew nothing.

#### The workspace lives in the project, not the browser

localStorage is scoped to a browser profile. Clear the site data, switch
browser, or open the app from another host and every setting is gone -- which
makes it the wrong home for something that belongs to the PROJECT.

`serve.py` gained one endpoint and the workspace now has a file:

    GET  /workspace   ->  data/workspace.json, or {} when there is none
    PUT  /workspace   <-  {set: {...}, del: [...]}, merged into the file

localStorage stays the WORKING store -- every `load()` in the app reads it
synchronously and not one of them changed. The file is a durable copy: written
after each change (debounced 600ms, because a single drag fires dozens of
saves) and read once before `boot()`.

THE BROWSER WINS where it already holds a key. localStorage is never STALER
than the file -- the file trails it by the debounce and nothing else -- so the
file's job is to fill what the browser is MISSING: a fresh profile, cleared
site data, another machine. Taking the file first would be wrong in the one
case that matters, where a browser mid-session holds changes the file has not
received yet and a reload would undo the last thing you did. Written through a temp file and
`os.replace`, so a crash mid-write cannot leave half-parsed JSON where the
settings used to be.

Verified by clearing localStorage completely and reloading: 30 keys came back
from the file, drawings and watchlist intact, and `XAUUSD.a: major` -- a
per-instrument sensitivity set minutes earlier -- survived a browser with no
memory at all.

#### THE PUT MERGES, and the first version did not

The first attempt sent the whole snapshot and refused to write only when it was
EMPTY. That guard has the wrong shape, and the test that proved it was
destructive: `localStorage.clear()` followed by a single `save()` produces a
snapshot of ONE key, which is not empty, so it passed the guard and took the
file from 30 keys to 1. The durable copy was destroyed by the browser losing its
own copy -- exactly the failure the file exists to prevent.

A client may send what it HAS; it does not get to assert what it lacks. The PUT
merges into the file, and removal is explicit via `del`, populated by `drop()`.
Re-tested against the same hazard:

    file before                          13 keys
    localStorage.clear() + save()        13 keys   (was 1 before the fix)
    explicit drop('probe')               12 keys, probe gone

Two smaller guards sit alongside it. Nothing flushes before the file has been
READ, or a save during module init would push a pre-hydration snapshot over the
durable copy. And `hydrateWorkspace` runs before `boot()`, so `BOOT_LOCKS` is
rebuilt from the hydrated store rather than the empty one it saw at module load.

#### What survives a reload, audited rather than assumed

Everything lives in `localStorage` under `dnfx.`, so a SERVER restart is
irrelevant -- it is client-side and origin-scoped. The question worth asking is
what the app itself fails to write. Setting a distinctive state and diffing it
across a reload found two gaps and confirmed the rest:

    survived   symbol, timeframe, span, chart type, studies, drawings,
               tab list, slot layout, both rails, watchlist, every Auto TL
               toggle, the account-value switch, the broker clock offset
    lost       the bottom panel tab, and a hand-set price scale

**The bottom tab** reset to Positions on every reload. Now saved under
`panelTab`, and applied to the BUTTONS as well as to `this.tab` -- index.html
marks Positions active, so restoring only the state rendered History while the
footer highlighted Positions.

**A hand-set price scale** was treated as transient. It is a setting: it is now
in `chart.state()`, written when a Y-scale drag ENDS (the only way to set one),
and restored per symbol -- guarded on the timeframe, since a scale set on M15
means nothing on D1.

THE RESTORE HAD TO READ A SNAPSHOT TAKEN AT MODULE LOAD, which is the part that
took two attempts. Reading the saved lock at restore time returned null every
time, and the reason is that `persist()` runs while the workspace is being
built -- before any bars arrive -- and writes the fresh chart's EMPTY lock over
the saved one. The app was erasing the value it was about to ask for. A
`BOOT_LOCKS` map captured once at module load is the only point guaranteed to be
before that; the lock is applied after `setData` (which calls `resetView`, which
clears locks), then written straight back to disk.

Verified end to end: drag the scale on USDJPY H4 to 149.91-169.30, reload, and
the chart comes back on 149.91-169.30 with the History tab still selected.

#### One candle width, every timeframe

It took three goes to name the right invariant, which is worth recording
because the first two are both defensible:

    fixed 160 bars     ->  a different WINDOW per frame
                           (2.5 hours of M1, three years of D1)
    fixed five days    ->  a different CANDLE WIDTH per frame
                           (M1 drew 4800 hair-thin bars, D1 drew 12 fat ones)
    fixed 350 bars     ->  one candle width, and the PERIOD varies

Candle width is plot width divided by span, so only a constant span holds it
constant. Same period with unrecognisable rendering was the worse trade.

Holding the SPAN constant is what holds the candle width constant. **350 bars,
everywhere**:

| frame | span | candle width | period on screen |
|---|---|---|---|
| M5 | 350 | 3.65px | 1.1 days |
| M15 | 350 | 3.65px | 5.4 days |
| M30 | 350 | 3.65px | 10.8 days |
| H1 | 350 | 3.65px | 19.7 days |
| H4 | 350 | 3.65px | 76.7 days |
| D1 | 350 | 3.65px | 461 days |

The period on screen is now the thing that varies, which is the right way round:
a timeframe switch changes how much history one screen holds, not how the
candles look.

ONE GOTCHA, found while verifying and initially misread as a bug: resetting
DURING a load is overridden by the arriving data. A sweep that waited 4.2s after
a timeframe switch caught M15 mid-load and read span 691; `setData` re-positions
when the payload lands, after `resetScale` has run. With the load settled it
reads 350 before, immediately after, and four seconds later. A second click
fixes it if it is ever seen.

`dblclick` calls `resetScale` like the button. Two gestures called reset doing
two different things -- one keeping the zoom, one not -- was a distinction
nobody asked for.

CHANGING TIMEFRAME RE-FRAMES. The span used to carry over, so fitting all bars
on H1 and switching to M15 kept a 1590-bar window and opened onto empty space.
`setTimeframe` now sets the default and flags `right = 0` for `setData` to
re-position.

#### Dragging the chart moves it both ways

Holding the chart only panned left and right; the price axis was reachable only
by dragging the axis itself. A drag now moves the view vertically too.

The arithmetic is one line, and getting its SIGN right is the whole feature --
the content has to follow the cursor. From

    price(y) = max - (y / h) * (max - min)

holding a bar's price under a cursor that moved by `dy` needs the window shifted
by `dy * (max - min) / h`, applied to BOTH edges so the scale is untouched and
only the offset moves. Drag down, the bars move down, the price window moves up.
Measured: an 80px drag down shifted 4428.46-4719.04 to 4466.75-4757.34, a span
of 290.58 against 290.59 -- the same window, moved.

The range is captured at grab time rather than read live each frame, or every
frame would pan relative to the range the previous frame just set and the chart
would accelerate away from the cursor.

A VERTICAL PAN SETS `priceLock`, because without it the next repaint re-fits the
range to the bars and the drag springs back. That means panning vertically turns
auto-scaling off -- which is what the reset gestures are for, and both still
clear it. It is also a saved setting now, so the drag is persisted per symbol
like any other scale choice.

#### `Reset scale` resets the scale

It called `resetView`, which returns to the live edge and clears the price lock
but KEEPS the current span -- so after fitting 1273 bars it left the reader at
1273 bars wide, which is not what a button called Reset says it does.

It could not simply be changed in place. `setData` calls `resetView` on the
first payload, and main.js applies the symbol's REMEMBERED span before the bars
arrive, so forcing a default span in there would discard the saved view every
time a chart opened. `resetScale` is therefore its own method: default zoom,
live edge, no price lock, and persisted so the symbol reopens that way.

    before   span 1273, right 480, AS OF banner showing
    after    span  160, live edge, banner cleared, last bar 2026-08-25 on screen

Returning to the live edge also takes the panels off the as-of read, which is
the "current price for that symbol" half of it -- the banner clears because the
chart is genuinely live again, not because it was hidden.

`DEFAULT_SPAN` is exported from engine.js and used by main.js for restoring
saved views, so the open-at span and the reset-to span cannot drift apart.
`dblclick` still calls the lighter `resetView`.

#### Scrolling past the oldest bar, and "fit all" not fitting all

Two separate faults met at the same place: scroll left far enough and the chart
went blank.

**`fitAll` cut off the oldest bars.** It set `span` to the bar count and `right`
to the last bar PLUS the right pad, so the left edge landed at `pad` -- 72 bars
in on a 1201-bar 4h chart, because the pad is 6% of the span. The span has to
carry the pad too. The pad is now derived from the intended span rather than
read back from `rightPad()`, which would use the span still being computed.

**And there was nothing older to show.** `BAR_COUNT` fetches once at open --
1200 bars on 4h is nine months -- and no path existed to more, so panning past
the first bar showed empty canvas rather than history. Those counts stay small
because they decide how fast a chart OPENS; history now doubles on demand when
you scroll into the old edge, up to a 20,000-bar ceiling. Measured on XAUUSD 4h:
1201 -> 2401 -> 4802 -> 9605, each round ~64ms on a primed terminal.

Prepending shifts every bar INDEX and the view is expressed in indices, so
`view.right` moves by the same delta -- otherwise the chart jumps to a different
era the moment the data arrives.

THE TRIGGER IS KEYED ON THE RIGHT EDGE, and getting there took two attempts.
Fitting everything puts `i0` at -1 by design, which looks exactly like being
scrolled to the old edge, so the first version fired the fetch and moved the
view out from under a reader who had just asked to see the whole series. The
second version compared span to bar count, which stopped that -- and also
stopped the fetch when the reader then panned LEFT from the fitted view, which
was the entire purpose of the feature. One fix, one regression.

What separates the two cases is the RIGHT edge, not the left. `fitAll` leaves it
at the live bar; panning left moves it back, and that is the gesture meaning
"show me earlier". Verified from a fitted view: 1201 bars, then pan left once ->
9609 bars, oldest 2025-11-13 -> 2020-06-09, 1272 bars on screen instead of
blank.

`MAX_SPAN` went 4000 -> 4800 so `fitAll` can still frame a chart that has
extended twice. Beyond that a bar is under half a pixel and candles stop being
candles, so the cap is a rendering limit rather than a data one: scrolling still
reaches everything loaded.

#### Zoom preserves the right edge

Tying the AUTO stack to the right edge had a consequence I did not see coming:
the wheel zoom anchored on the CURSOR, which moves that edge, so magnifying the
chart silently changed WHEN you were standing. Parked 700 bars back, four
notches in moved the edge from 1301 to 1260 and the channel count from 1 to 3;
four notches back out landed on 1306, not 1301, so the original picture could
not be recovered by zooming. Channels appeared and vanished as you scrolled the
wheel.

Freezing the as-of point and keeping the cursor anchor would have been worse.
The chart would draw lines fitted to bar 1301 while showing only up to 1260 --
41 bars of hindsight on screen, which is the exact thing the as-of cut exists to
remove.

So panning chooses WHEN, and zoom chooses how much of it you see. The right edge
does not move on a wheel zoom; at the live edge it is re-pinned, because
`rightPad` scales with span and staying live means re-deriving it. Verified:
parked at 1300, four notches in and four back out leaves the edge at 1300 and
the span at exactly 250, with channels, lines and zones unchanged throughout.

The cost is cursor-anchored zoom, which is a genuine convenience -- panning
after the zoom recovers it, and an analysis that changes when you magnify it is
the worse trade.

Higher timeframes are cut by TIME, not by index. 500 bars back on 15m is not 500
bars back on 4h, and slicing both by the same count would project a 4h line
built from bars the 15m chart has not reached yet -- look-ahead arriving through
the back door of a unit mismatch.

The trigger is one `onView` notifier in the paint path, fired when the rounded
right edge changes, rather than a call at each of the six places that mutate the
view -- a new pan, zoom or keyboard path cannot forget to announce itself. It
debounces at 200ms because one refit is ~19ms per source and there can be five,
so a drag recomputes when it settles rather than on every frame.

THE PANELS FOLLOW IT TOO. They read the live edge at first while the chart read
as-of, which put history and today side by side on one screen. The cut is now a
shared `asOfCut(chart)` used by both, so the two cannot disagree about what
"now" means -- that disagreement was not a display bug so much as two callers
answering the same question differently.

    AS OF  13 Aug 15:00
    WATCH ↑                                    35
    Structure       HH + HL
    Invalidation    4382.97

A HISTORICAL READ THAT DOES NOT SAY SO IS WORSE THAN NO PANEL AT ALL. `BULL 76`
looks identical whether it is today's call or one from three weeks ago, so a
banner sits at the top of both panels whenever the chart is parked, in brand
orange -- pink is `--down` here, and a bearish-coloured banner over a BULL read
would fight the number it is qualifying.

Live ticks are suppressed while parked. A tick repaint reads the current bar by
definition, so it has nothing to say about a chart in history, and letting it
run would quietly restore today's numbers, banner and all. Verified: parked 700
bars back, nine seconds of live ticks changed nothing.

Round-tripped end to end -- live `BULL 58, invalidation 4625.26`; 700 bars back
`WATCH ↑ 35, 4382.97`; 1400 back `NEUTRAL 13, 4053.93`; and back to the edge,
`BULL, 4625.26` again.

#### Channel history: the corridor that already ended

`liveChannels` detects at ONE bar, the last, so it answers "what corridors are
visible now". A channel that formed and died 400 bars ago is not returned at
all -- which is why the older half of a chart is bare while the right edge
carries bands. Seeing a finished ascending channel beside the descending one now
forming was only possible by accident, when both happened to still be alive.

`Channel history` in the `⌁ Auto TL` menu repeats detection back across the
visible window on the same 25-bar grid the as-of cut uses, and keeps every
corridor that was live at any of those bars.

THE MERGE IS STRUCTURAL, which matters because the last attempt to merge
corridors was reverted for guessing at a price threshold. A corridor re-detected
as it ages reappears with the SAME end bar and an ever-later start, so the raw
sweep holds the same band three or four times. Two rules, neither of which needs
a price:

    same direction + same tEnd     ->  keep the longest span
    spans overlapping >= 60%       ->  keep the longer

Measured on 15m gold over 600 bars, 33 raw detections collapse to 9. **The
overlap fraction is not delicate**: 0.5 and 0.7 return the identical nine bands,
so there is no edge for the number to balance on -- the same property that made
the 0.35 ATR line dedupe trustworthy.

On a 420-bar window it takes 3 corridors to 7, laid out in sequence rather than
stacked:

    down        1436-1622
    up          1590-1622
    down        1669-1739
    up          1714-1871      a finished ascending channel
    horizontal  1815-1985
    up          1876-1919
    down        1925-1985      the one forming now

Off by default, and it costs ~15ms per detection with the sweep capped at 30 of
them. The clipping already in `_channels` does the rest: each band is drawn over
its own span and stops 25% past its own end, so a corridor from 300 bars ago
cannot pretend to reach current price.

#### A channel that stops having data should stop being drawn

The renderer drew every corridor from the view's left edge to the view's right
edge. The left was clipped to `c.tStart`, so that end was honest. The right was
not clipped to anything -- `t1` was simply the right edge of the pane, which
quietly turned every channel into an infinite one. A channel whose bars ran out
148 of them ago was still drawn all the way to current price, at full weight, as
though it were live.

Measured on a 15m gold chart, in bar index space:

| span | data ended | drawn past its data | as % of its own span |
|---|---|---|---|
| 98 bars | 106 bars ago | 106 bars + pad | **108%** |
| 198 bars | 106 bars ago | 106 bars + pad | 54% |
| 258 bars | 148 bars ago | 148 bars + pad | 57% |

Every one of them was extrapolated further than the evidence it was fitted to.

### Most BOS marks are not the event the backtests trade

`marketstructure.js` labels a break when price CLOSES through the last swing --
wick-only breaks never qualify, which is the usual first objection and it does
not apply here. What it does not ask is HOW FAR. `bufferAtr` defaults to 0, and
the comment beside it already knew: "a close a tenth of a pip through a level
counting as structure, which on a 15m chart happens constantly."

Measured on XAUUSD, close-beyond-level in ATR:

| frame | strength | BOS | median | under 0.25 ATR | over 1.0 ATR |
|---|---|---|---|---|---|
| M15 | 2 | 84 | 0.38 | 29.8% | 10.7% |
| M15 | 3 | 63 | 0.40 | 33.3% | 11.1% |
| M15 | 6 | 34 | 0.49 | 32.4% | 11.8% |
| H1 | 2 | 70 | 0.49 | 31.4% | 20.0% |
| H1 | 3 | 60 | 0.42 | 36.7% | 21.7% |
| H1 | 6 | 28 | 0.35 | **50.0%** | 14.3% |

About a third of BOS labels are closes less than a quarter-ATR through. It is
not an M15 noise problem -- H1 is the same shape.

RAISING SENSITIVITY MAKES IT WORSE, which is the part worth understanding.
Strength 6 on H1 is 50% marginal against 31% at strength 2, because rarer swings
sit further apart: price has usually travelled a long way before it reaches the
next one and has less left when it gets there. Strength selects WHICH LEVELS
COUNT and says nothing about HOW CONVINCINGLY THEY BREAK. Two independent axes,
and only one of them was ever visible.

`DISPLACEMENT_V1` requires the close to clear the level by **1.0 ATR**, and that
spec is behind the only positive-expectancy cell this project has measured. So
roughly 80-90% of what the chart labels BOS would never have triggered a trade.
The label was not wrong; it was a far weaker claim than the word implies.

DISPLACEMENT NOW DECIDES THE WEIGHT. Every event carries `dispAtr`, and marks
clearing `Chart.DISPLACEMENT_ATR` draw at full strength with a bolder label
while marginal ones stay at 45%. Nothing is hidden and nothing is filtered --
they ARE structure, just weak structure, and a reader who has been watching a
level for weeks still finds its mark where it was. What changes is that the ones
worth acting on stop looking identical to the ones that are not.

`Chart.DISPLACEMENT_ATR` is 1.0 because `DISPLACEMENT_V1.displacement_atr` is
1.0. Not a taste, and deliberately the same constant: a chart that drew its
"real" breaks at a different threshold from the one the backtests trade would be
the two disagreeing in the one place a reader cannot see.

Live check across frames, twelve most recent events each: M15 0 strong (max
0.83), H1 0 strong (max 0.90), H4 1 strong (1.10), D1 1 strong (1.31). That
distribution is the finding, not a bug -- genuine displacement is rare, and it
gets rarer as the timeframe drops.

#### Three segments, and only the future one is dashed

A corridor makes three different claims across its length, so it is drawn in
three pieces:

    measured    the bars it was fitted to              solid, full weight
    projected   past the fit, over bars that EXIST     solid, ~half alpha
    future      past the LAST BAR, no data at all      dashed

Only the third is dashed. Dashing the second read as a different KIND of line
rather than the same rail with less behind it, and this chart already spends
dashes on higher-timeframe lines and on the median -- a third pattern over real
bars is a vocabulary, not a hint. Beyond the last bar the dash says something no
weight can: there is nothing here yet.

A STILL-FORMING corridor runs to the right edge; a finished one stops at the 25%
cap. The cap exists so a dead channel cannot pretend to reach current price, and
it has no business shortening a live one -- those rails are where the next bars
would sit if the corridor holds, which is the only forward statement a channel
makes.

`c.live` is set by the detector, not inferred by the renderer, and the first
attempt to infer it failed instructively. A bar-count test on `tEnd` cannot
answer "is this still forming", because `tEnd` is the last PIVOT and pivots
confirm several bars late: on 15m gold, live corridors were ending 22 bars back,
so a two-bar tolerance called every one of them finished and nothing extended.
What actually means live is being returned by the CURRENT detection rather than
by the history sweep, which the detector knows for free. Measured after: three
live corridors, each drawing 21 bars past the last bar into the right pad.

There are now THREE times per channel rather than two: where drawing starts,
where its DATA ends (`c.tEnd`), and where drawing stops. The measured span is
drawn as before; past `tEnd` the corridor continues at roughly half alpha and
stops at 25% of its own length.

#### One corridor drawn twice: merged, then un-merged

Two channels on 15m gold, from the SAME anchor bar, differing only in where they
ended:

    15m-CH-J-1202-1448   q 53   lo 4603.6  hi 4700.1   width 6.07 ATR
    15m-CH-J-1202-1404   q 48   lo 4593.5  hi 4688.6   width 5.99 ATR
                                       rails 0.64 and 0.72 ATR apart

One corridor refit to a different end bar, drawn as two -- which is what makes a
channel look like four rails instead of two.

`dedupeAtr` is 0.5, so 0.64 clears it by a hair. The diagnosis was that the
number is the wrong SHAPE rather than the wrong size: a flat ATR budget is most
of a 1 ATR corridor and a tenth of a 6 ATR one, so it means two different things
depending on what it is looking at -- the same mistake `max_distance_atr` made
in zones.py. The fix was a width-relative tolerance,
`max(0.5 ATR, 25% of width)`, in both languages.

**IT WAS REVERTED, deliberately, and the tolerance is a flat 0.5 ATR again.**
Merging is not the only answer to two corridors on the same prices, and it is
the lossy one: it picks a winner and throws the other corridor away. Telling
them APART keeps both and costs nothing, which is what the section below does.
The diagnosis above is left standing because it is still true of the parameter
-- if these ever need merging rather than colouring, that is the shape the
threshold should take.

#### Two corridors on the same prices get told apart by colour

Dedupe removes only corridors whose rails sit within a flat 0.5 ATR of each
other. Everything else that overlaps -- including the near-copies above -- stays
on the chart and has to be READABLE, and colour was no help: it encodes DIRECTION --
ascending green, descending pink -- and overlapping channels are usually
overlapping precisely BECAUSE they are the same shape, so both came out the same
hue. The reader saw four rails of one colour with no way to pair them up.

Inside an overlapping group, colour switches to POSITION: upper corridor green,
lower corridor red. Outside a group, direction still rules -- a lone channel has
nothing to be confused with.

That makes green mean two things depending on context, which is the sort of
quiet ambiguity worth refusing outright. So the label carries the reading: it
appends `· UPPER` or `· LOWER` whenever position took over, and a channel whose
label says neither is showing a direction colour.

Three or more in one group: only the outermost two are coloured. Anything
between them is neither upper nor lower, and naming it either would be a lie
told in colour -- it draws in neutral text grey and labels itself `· MIDDLE`.

Bands are compared over the VISIBLE window rather than at the last bar. Two
corridors converging off-screen right are not overlapping anywhere the reader
can see, and two that cross mid-screen are, even if they are a hair apart at the
edge.

OVERLAP MEANS SHARING A PRICE AT THE SAME X, and the first version did not test
that. It compared bounding boxes -- the min and max of each corridor's rails
across the whole window -- which is the classic AABB false positive: two steep
parallel bands each sweep hundreds of points over a screen, so their boxes
intersect while the bands stay far apart at every single x. Synthetic control:
two bands 300 apart with identical slope have boxes `[4000, 4650]` and
`[4300, 4950]`, overlapping on paper and never touching in fact. They were
coloured as a stack; they now keep their direction colour.

ORDER IS READ AT THE RIGHT EDGE, not from a window average. Corridors cross --
measured on 15m gold, a pair 700 bars back swapped vertical order once
mid-window -- and an average can put one on top when it is below for half the
chart, agreeing with neither end. The right edge is where the label sits and
where the reader is standing, so that is the x that UPPER and LOWER describe. A
pair that crosses inside the view is still labelled from the right edge; the
crossing needs no annotation because the bands visibly swap.

Checked against an independent 200-sample same-x test across four views (live,
700 back, 1400 back, and a 900-bar span): grouping agreed everywhere, and UPPER
sat above LOWER at the right edge in every case.

    two overlapping     upper #93C90F UPPER   lower #E31C79 LOWER
    disjoint            both keep the direction colour, no role
    three overlapping   green UPPER · grey MIDDLE · red LOWER
    single              direction colour, no role

#### Dashes, and what they were encoding

Every auto line is solid now, whatever frame it came from. Dash length used to
grow with the source timeframe -- 15m at `[8,4]`, 1h at `[12,5]`, 1d at
`[20,7]` -- so a projected line read as "from higher up" without reading its
label. But the line already carries its frame IN its label (`M30 S x3`), so the
pattern was a second encoding of something already on screen, and it cost the
legibility of the line itself. Frame shows in weight and alpha alone: a
projected line is dimmer and slightly heavier.

The projection was dashed at first, and that was wrong. This chart already
spends dashes on two other things -- higher-timeframe trendlines carry a
per-frame dash pattern, and every channel median is dashed by convention -- so a
third pattern reads as a different KIND of line rather than as the same rail
with less behind it. The rails stay solid and recede by WEIGHT alone: same
colour, same width, less alpha. A corridor is a claim about the bars
it was fitted to -- beyond them it is a guess, and the guess should not outgrow
the evidence. The label moves to wherever the drawing actually ends, which is no
longer the edge of the pane.

The cap is counted in BARS, and getting that wrong first is instructive. Applied
to wall-clock milliseconds, a 98-bar channel projected 74 bars instead of 25:
gold stops over the weekend, so those 98 bars span 298 bars' worth of clock, and
a quarter of the clock span is three times too far. It is the same index-versus-
time trap the slope conversion in `detectTrendlines` documents, in a new place.

WHAT THIS DOES NOT FIX is that a stale channel is still drawn at all. The one
whose data ended 148 bars back now stops 83 bars short of the right edge instead
of reaching it, which states its age honestly rather than hiding it -- but
whether a corridor that old should be returned by the detector is a different
question, and not one a renderer should answer.

#### A weekly line may be fifty-two chart-ATRs away and still pass

An H4 gold chart carried twelve trendlines, the nearest 6.3 ATR from price and
the furthest 13.4 -- a fan crowding the axis, none of them reachable. It reads
as the detector being broken. It is the UNITS.

The engine drops a line the market has walked away from at `maxDistanceAtr`, and
measures that distance in the ATR of the series it detected on. For the chart's
own frame that is the same thing. For a projected line it is not:

| source | its own ATR | its allowance, in CHART ATR |
|---|---|---|
| 4h | 42.4 | 10.0 |
| 1d | 100.2 | 23.6 |
| 1w | 223.6 | **52.7** |

A weekly line may sit fifty-two chart-ATRs from price and still pass its own
proximity test. Third instance of one mistake: `max_distance_atr` in zones.py,
`dedupeAtr` in channels.js, and now this -- a fixed ATR budget meaning different
things at different scales.

The draw stage now cuts on distance in the CHART's units, at the engine's own
detection threshold, so every source obeys the rule the chart's own frame
already obeys:

At `major` sensitivity, which is what the chart above was set to:

| frame | lines | furthest drawn | channels |
|---|---|---|---|
| M15 | 7 | 4.99 ATR | 0 |
| H1 | 4 | 2.80 ATR | 1 |
| H4 | **0** | -- | 2 |
| D1 | 2 | 3.89 ATR | 1 |

ONE LINE OF THAT FILTER WAS NOT CUT, and it broke scrolling. The distance test
compared a line valued at the as-of time against `chart.bars[last].c` -- the
LIVE close. Everything around it already used the cut (`tNow`, `atrNow`); this
one reference did not. Scrolled back 1000 M15 bars on gold the as-of close is
4344 and the live close 4646, a gap of **44 ATR** against a 5 ATR budget, so
every line failed the test and the chart emptied. Measured on the four lines
that should have been drawn there:

| source | distance from the as-of close | distance from the live close |
|---|---|---|
| 30m | 2.57 ATR | 41.61 ATR |
| 4h | 2.00 ATR | 42.17 ATR |
| 15m | 2.36 ATR | 46.53 ATR |
| 15m | 0.14 ATR | 44.04 ATR |

Four survive; none would have. It is exactly the class of mistake the shared
`asOfCut` was introduced to prevent -- one caller reaching past the cut for
"now" -- and it reappeared the moment a new filter was added beside it. Fixed by
taking spot from `ownBars`. Verified by scrolling 0 -> 1500 bars and back:
lines hold at every position and the round trip is exact.

H4 DRAWS NO TRENDLINE AT ALL **at that preset**, and that is the correct answer
rather than a failure -- the next section shows the same chart at `fine`, where
one line qualifies. Measured across the sensitivity presets on that window, of 10 to 35
candidate lines, the nearest to price was 6.3 ATR at `major` and 5.9 at
`normal` -- gold had just run ~700 points into open space and no line with
enough touches exists up there yet. Only `fine` found one, at 2.5 ATR. The
detector was not ranking good lines out; there were none to rank. Twelve
unreachable lines said "here is structure"; an empty chart says "not here", and
the zones and channels still draw.

#### The same chart at `fine`, across every frame

`SENS` is one number: the fractal strength a pivot must clear. `fine` is 2,
`normal` 3, `major` 6. It is not a cosmetic density dial -- it decides which
extremes are pivots at all, and therefore which lines can exist.

XAUUSD, every timeframe, at `fine`:

| frame | bars | lines | drawn from | touches | distance (ATR) | channels | zones | S/D |
|---|---|---|---|---|---|---|---|---|
| M1 | 3000 | 4 | 1m | 5,4,4,3 | 1.09-2.16 | 3 | 6 | 8 |
| M5 | 2500 | 4 | 5m, 30m, 1h | 4,3,3,4 | 0.96-4.92 | 0 | 6 | 3 |
| M15 | 2000 | 4 | 1h | 5,5,3,3 | 1.51-4.35 | 3 | 6 | 2 |
| M30 | 2000 | 3 | 1h | 5,6,3 | 1.22-3.11 | 1 | 6 | 0 |
| H1 | 1500 | 3 | 1h | 5,6,3 | 0.95-2.34 | 3 | 6 | 1 |
| H4 | 1200 | **1** | 1w | 5 | 2.41 | 0 | 6 | 5 |
| D1 | 1001 | 2 | 1w | 5,5 | 2.97-3.35 | 0 | 6 | 4 |
| W1 | 800 | 4 | 1w | 4,6,5,5 | 0.44-2.74 | 3 | 5 | 8 |

**H4 gains a line** -- a weekly resistance, 5 touches, 2.41 ATR away. The same
detector and the same proximity rule that drew nothing at `major` draw one here,
because at strength 6 the nearest candidate was 6.3 ATR out and at strength 2 it
is 2.4. The pivot definition is the whole difference.

**Every line sits within 5 ATR, and none of them is junk.** Touch counts run 3
to 6 across all eight frames, so the proximity cut is not keeping weak lines to
fill a budget -- the budget simply goes unfilled when nothing qualifies, which
is what M30, H4 and D1 show.

**Lower frames borrow from higher ones, and that is the design working.** M15
and M30 draw ONLY `1h` lines; H4 and D1 draw only `1w`. Their own frame
contributed nothing -- its recent pivots are too new to have accumulated
touches -- so the projection carries the read. M1 and W1 are the exceptions,
drawing entirely from themselves, which is what you would expect at the two ends
of the ladder where there is no frame below or above to borrow from.

#### Which swings are the MAJOR ones

At `fine` every turn of three bars is a swing. M15 gold carries 454 of them, 51
visible in a 220-bar window, and every dot looked identical -- so the chart
could say WHERE the pivots were and not which ones the market actually turned
on.

The app already owned the word. `major` sensitivity is strength 6, so a swing is
MAJOR when it also survives that window. No new parameter, no new threshold to
justify, and the ring means exactly what picking `Major structure` from the menu
would have shown -- the two cannot drift apart because they read the same
constant.

    strength 2 (fine)     454 swings on M15
    also strength 6       168 of them, 37%

Around 38% qualify on every frame measured (M15 37.0%, H1 38.5%, H4 38.6%),
which in a normal window is 15 majors among 51 swings -- a hierarchy you can
read rather than a second chart's worth of marks.

A major gets a filled dot, a hollow ring around it, and a bold label; a minor
gets a small dot at 45% alpha. The RING is doing the work rather than size
alone: a slightly larger dot is hard to judge against a candle wick, and a
hollow centre survives being drawn on top of one.

MAJORS ARE DRAWN FIRST, and that ordering is the point rather than an
implementation detail. Labels are rationed by horizontal spacing, first come
first served, so drawing in index order let a minor pivot three bars earlier
take the slot its major neighbour needed. Sorting majors to the front means they
claim their labels before minors are considered.

The second detection pass runs on the same `ownBars` as the first, so it
inherits the as-of cut and cannot see past the bar being drawn. At `major`
itself the two passes coincide and everything is marked major -- the honest
reading of that setting rather than a bug.

#### The ladder had two rungs missing

M1 drew almost nothing while 87 candidate lines existed and 14 of them sat
within 5 ATR -- and every one of those near candidates was a `15m` line. The
projection list was `['4h','1d','1h','30m','1w']`: **M5 and M15 were never in
it**, so a one-minute chart jumped straight from its own lines to M30 and never
consulted the two frames directly above it.

The gap bites hardest at the bottom of the ladder, because the proximity rule is
in CHART ATR. M1's ATR is 1.9 points, so 5 ATR is a 9.6-point window, and an H1
or H4 line is almost never that close in absolute terms. The high frames were
structurally excluded by distance and the two that would have qualified were not
enabled, so the answer was zero.

With `Project from M5` and `Project from M15` switched on:

| frame | lines | drawn from | distances (ATR) | channels |
|---|---|---|---|---|
| M1 | 3 | 1m, **5m** | 0.67, 1.69, 2.36 | 3 |
| M5 | 2 | 30m | 3.43, 3.86 | 1 |
| M15 | 4 | 1h, 15m | 3.31-4.67 | 3 |
| M30 | 3 | 1h | 1.83-2.33 | 1 |
| H1 | 3 | 1h | 1.50-2.13 | 3 |
| H4 | 1 | 1w | 2.14 | 0 |
| D1 | 2 | 1w | 2.87, 3.24 | 0 |
| W1 | 4 | 1w | 0.39-2.79 | 3 |

M1 goes from 0 lines to 3, the nearest 0.67 ATR away, and now draws from `5m` --
the rung that was missing. Every frame on the ladder has lines, and everything
drawn is still inside the 5 ATR budget.

Channels stay patchy on purpose: 3 on M1/M15/H1/W1, 1 on M5/M30, none on H4/D1.
A corridor needs a qualifying parallel pair with containment, and gold's V does
not offer one on every frame. Zones fill in regardless -- 6 on nearly every
frame -- which is why no chart is bare even where lines and channels are thin.

#### Two lines that arrive at the same price are one line

What remained was subtler. On gold H1 the four surviving lines were two
near-identical PAIRS -- `r×5` scoring 93 and 92, `s×6` scoring 89 and 88:

    resistance x5 (93) vs x5 (92)    0.21 ATR apart NOW,  2.96 ATR apart 250 bars back
    support    x6 (89) vs x6 (88)    0.15 ATR apart NOW,  2.82 ATR apart 250 bars back

They converge on today from different angles. The engine's own dedupe misses
this twice over: it asks whether two candidates agree at their endpoint AND at
the midpoint of their own span, which a converging pair passes, and it runs per
SOURCE, so lines projected from different timeframes are never compared at all.

The last filter before drawing is therefore about NOW. Whatever a line did in
the past, its job at the right-hand edge is to say where the level is, and two
lines saying the same thing there are one signal drawn twice. Same kind, within
0.35 ATR at the current bar, keep the better score.

It runs BEFORE the budget, not after, so a dropped duplicate frees its slot for
a genuinely different line rather than leaving the chart emptier. On gold H1
that is exactly what happened -- the 92 and 88 duplicates were replaced by
distinct lines at 91 and 82, and the closest same-kind pair went from 0.15 ATR
to 0.45.

THE THRESHOLD IS NOT DELICATE, which is the part worth trusting. Across 12
symbol/timeframe cells, 0.25 ATR and 0.75 ATR removed the same lines in all but
one: duplicates sit far inside the band and real neighbours far outside it, so
there is no edge for the number to balance on. Half the cells had exactly one
duplicate; none had more.

WHAT IT DISCARDS is the convergence itself -- a wedge closing into current price
now reads as a single line. That is a real loss and it is the price of a legible
chart; the geometry is still in the engine for anything that wants to measure
it.

**This lives in `main.js`, not in the detector.** `detectTrendlines` is
parity-tested and feeds every backtest in this project, so changing what it
finds would move measured results as a side effect of a display fix. The chart's
budget stage is already the layer that merges sources and decides what fits on
screen; "and two lines at the same place are one line" is the same kind of
decision. `tl_lines=1681` in the parity fixture is unchanged.

### Does a channel's direction carry information?

The chart draws ASCENDING, DESCENDING and RANGE corridors and the Trend read
ignores all three -- `read.js` has no reference to channels at all. Before
wiring direction into a verdict it had to earn the place. 29,208 samples, every
25th bar, 4 instruments x 2 timeframes x 3 eras, channels detected causally
through the engine walk so each one is what an observer standing on that bar
could see.

**It carries nothing.**

| horizon | up - down | CI (clustered) | cells positive |
|---|---|---|---|
| h=12 | +0.0389 ATR | [-0.0491, +0.1160] | 14/22 |
| h=24 | -0.0261 ATR | [-0.1520, +0.0744] | 8/22 |
| h=48 | -0.0691 ATR | [-0.2858, +0.1112] | 8/22 |

Every interval spans zero, the sign flips with horizon, and per-cell results run
from -0.92 to +0.51 with no pattern. By timeframe: 1h +0.007 (5 of 11 cells),
4h -0.141 (3 of 11). By era: +0.089, -0.068, -0.159, all spanning zero.

The version that needs no statistics:

    P(price higher after 24 bars)
      up channel      50.5%
      down channel    51.6%
      horizontal      50.8%
      NO channel      50.8%

Knowing the corridor's direction tells you nothing about which way price goes
next, and "down" is if anything marginally more often followed by a rise.

#### The first answer was wrong, and the reason generalises

The first pass pooled every cell and differenced `mean(up) - mean(down)`,
reasoning that drift common to the series would cancel. **It does not**, and the
correction came from the observation that trendlines and channels are detected
PER INSTRUMENT PER TIMEFRAME, so their populations are not interchangeable.
Pooling cancels drift only when the two arms are balanced INSIDE every cell:

    pooled = sum_c w_up,c (d_c + e) - sum_c w_dn,c d_c

which equals the effect `e` only where `w_up,c == w_dn,c`. It never does. A cell
in an uptrend produces mostly ascending corridors AND carries the highest drift,
so the up arm is weighted toward high-drift cells. Measured:

    corr(cell up-share, cell drift) = +0.784

    2021-2026 USDJPY 4h   up-share 0.75   drift +0.4613
    2021-2026 XAUUSD 4h   up-share 0.73   drift +0.4661
    2021-2026 EURUSD 4h   up-share 0.41   drift -0.0239

The pooled contrast reported **+0.0562 ATR at h=12** and that number was the
market. Computing inside each cell, where `d_c` is common to both arms and
cancels exactly, gives +0.0389 with an interval covering zero.

#### And the correction to the correction

Channels persist, and are sampled every 25 bars for as long as they survive, so
one corridor contributes many rows and the h=48 windows of adjacent rows overlap
by 23 bars. Treating those as independent shrinks the interval on a sample size
that was never there. The first attempt to fix it **changed the estimator**
rather than the resampling unit -- averaging episode-means equally -- which
upweighted 1-sample fragments (median episode length is 1 to 2) and inverted the
sign to `-0.80 ATR, 1 of 22 cells positive`. That number was an artefact of the
fix, not a finding, and was thrown away.

A cluster bootstrap resamples the CLUSTER and keeps the estimator. Corrected,
the point estimates match the naive version exactly and only the intervals
differ:

| horizon | naive CI width | clustered CI width |
|---|---|---|
| h=12 | 0.163 | 0.165 |
| h=24 | 0.236 | 0.226 |
| h=48 | 0.321 | **0.397** |

Clustering mattered only at the longest horizon, which is where window overlap
is worst -- the honest report is that the correction was right to make and
changed little.

#### The rails do not turn price either

Same data answers the other claim a channel makes. If a corridor is mechanical,
sitting in its lower third should beat sitting in its upper third:

    up channels     lower minus upper  +0.1427  CI [-0.0292, +0.3304]   9/19 cells
    down channels   lower minus upper  +0.0329  CI [-0.1669, +0.2458]  12/18 cells

The up-channel figure is the closest thing to a signal anywhere in this study
and it still spans zero, with 9 of 19 cells positive -- a coin flip on which
cells agree.

**Channels stay out of the Trend read.** They are a description of where price
has been, drawn because it is legible, and this is now measured rather than
assumed.

### Pushing on the one cell that cleared the gate

`displacement_v1_4h` was the only positive net-expectancy result in this
project: **+0.102 R over 307 trades**. Everything else replicated structurally
and died on friction. So it was worth finding out whether that number was a
finding or a small-sample accident wearing one.

**It does not survive.**

#### The 307 was six cells of twelve

Re-running the frozen spec reproduced the recorded CSV EXACTLY on every cell it
contains -- 69/53/58/50/40/37 trades, per-cell R matching to the digit. The
arithmetic was never wrong. The POPULATION was partial:

| era | symbol | n | net R | in the recorded baseline? |
|---|---|---|---|---|
| 1999-2010 | AUDUSD | 47 | **-0.310** | no |
| 1999-2010 | EURUSD | 69 | +0.282 | yes |
| 1999-2010 | USDJPY | 53 | -0.163 | yes |
| 1999-2010 | XAUUSD | 6 | -0.063 | no |
| 2011-2020 | AUDUSD | 23 | +0.485 | no |
| 2011-2020 | EURUSD | 58 | +0.012 | yes |
| 2011-2020 | USDJPY | 50 | +0.118 | yes |
| 2011-2020 | XAUUSD | 19 | +0.355 | no |
| 2021-2026 | AUDUSD | 20 | +0.261 | no |
| 2021-2026 | EURUSD | 40 | +0.194 | yes |
| 2021-2026 | USDJPY | 27 | -0.180 | no |
| 2021-2026 | XAUUSD | 37 | +0.162 | yes |

Nobody hand-picked those six. Five exclusions are the `len(trades) < 30`
reporting filter in `metrics()`. The sixth is that **AUDUSD produced 1h cells
only in that run** -- its 4h bars were not loadable, and the loader's
`except Exception: continue` said nothing about it. Two mechanical causes, one
consequence: the single largest excluded cell is the worst in the study.

    recorded 6 cells (2026-08-24 data)     307 trades   +0.1015
    same >=30 filter, TODAY's data         354 trades   +0.0469
    every cell, no filter                  449 trades   +0.0767

The headline more than halved when one dataset arrived. That is what a silent
`except` costs.

#### And 449 trades cannot tell it from zero

    net mean  +0.0767 R   CI [-0.0570, +0.2108]   n=449

Every era individually spans zero too -- 1999-2010 is actually negative:

    1999-2010   175  -0.0237   [-0.2295, +0.1990]
    2011-2020   150  +0.1634   [-0.0763, +0.4040]
    2021-2026   124  +0.1136   [-0.1513, +0.3799]

Drop the single best cell and what remains is +0.0395, CI [-0.110, +0.189].

#### The parameter choice does as much work as the signal

Moving one parameter at a time from the frozen point:

| point | n | net R |
|---|---|---|
| **frozen 1.0 / 1.0 / 2.0** | 449 | **+0.0767** |
| stop 0.75 | 449 | +0.1038 |
| atr pct 10-90 | 370 | +0.1032 |
| target 1.50 | 449 | +0.0745 |
| displacement 1.25 | 270 | +0.0722 |
| stop 1.25 | 449 | +0.0191 |
| atr pct 20-80 | 268 | +0.0138 |
| stop 1.50 | 449 | +0.0014 |
| displacement 1.50 | 144 | -0.0517 |
| displacement 2.00 | 49 | -0.2758 |

This is the one encouraging part: it is a PLATEAU, not a spike -- two
neighbours beat the frozen point, so nothing was tuned to a knife edge. But the
neighbourhood ranges from +0.001 to +0.104, a spread the size of the effect
itself. When moving a stop by a quarter of an ATR matters as much as the signal
does, the signal is not what is being measured.

Friction break-even sits at **2.06x** the modelled cost: double the spread and
the cell is gone.

#### The sample is exhausted, and that is the real finding

CI half-width is 0.134 at n=449. Clearing zero at this effect size needs roughly
**1368 trades -- about 3x what exists**, which at ~112 trades per instrument
means about **12 instruments**. Four have bars on disk, and the history already
runs 1999-2026.

So this question cannot be settled with more HISTORY. Only with more
INSTRUMENTS. Until then `displacement_v1_4h` is not an edge that needs
defending, it is a hypothesis that has never had the sample to be tested. The
frozen spec keeps its original numbers -- that is what freezing is for -- and
carries a `revision` field with all of the above beside them.

### R:R was quoting a number it was built to suppress

`geometry()` has a noise floor: an invalidation sitting a hair from price is not
a tight stop, it is a stop inside the noise, and a ratio computed off it is
fiction. A tenth of ATR is the floor.

    if (!(risk > 0) || (atr > 0 && risk < atr * 0.1)) return { rr: NaN };

**The floor never fired. Not once, in any read.** The panel fed it
`execRead.regime.atr`, and `regime.latest()` returns `regime`, `direction`,
`rangePos`, `energy` and `emaSepAtr` -- there is no `atr` key. The value was
`undefined`, `undefined > 0` is false, and the whole guard short-circuits away.
A missing property, not a wrong threshold, which is why nothing ever looked
broken.

What it produced were spectacular ratios off invalidations touching spot. On
XAUUSD 4h: invalidation 2.24 away, ATR 41.05 -- a stop **0.05 ATR** wide --
reported as `18.2:1`. Not an opportunity the engine found, an artefact of
dividing by almost nothing, and precisely the case the floor was written for.

The panel now computes ATR from the execution bars it already holds. Unit-tested
at the boundary:

| stop width | rr |
|---|---|
| 0.055 ATR | suppressed |
| 0.09 ATR | suppressed |
| 0.11 ATR | 9.04 |
| 1.0 ATR | 1.00 |
| 0.055 ATR, `atr` undefined | **18.24** -- the bug, reproduced |

That last row is the regression test: pass the guard nothing and the artefact
comes straight back. Live, the H4 read that quoted `18.2:1` now shows `—`.

#### The cap applies, but stops announcing itself

`capped by 1:0.16 risk-to-reward` is gone from the note line. It said the same
thing as the `Risk : reward` row three lines below it, in the detector's
arithmetic rather than in words, on the one line whose job is the plain-English
WHY.

The CAP itself is untouched -- `rr < 1` still forces conviction to 35, verified
directly: identical timeframe agreement scores 79 with the first level 2.25x the
stop away, and 35 with it at 0.15x. Only the announcement moved.

It moved into the conviction tooltip, next to the number it explains, and that
fixed a second thing on the way. The tooltip's note read `35 - under 45. The
read exists, but the evidence behind it is thin.` For a CAPPED 35 that is the
opposite of the truth: the evidence is often excellent and it is the PLACEMENT
that is weak. Capped reads now get their own sentence saying so, and pointing at
the row that carries the number.

#### Risk first, because that is how the ratio is spoken

`0.16:1` was arithmetically correct and read backwards. A trader seeing it says
"risking 0.16 to make 1" -- the flattering reading -- when the truth is the
reverse. The row is now labelled `Risk : reward` and reads `1 : 0.16`. The 1 is
always what you put up, so there is nothing left to invert. The capped note
follows: `capped by 1:0.16 risk-to-reward`.

### The side panels explain themselves

`Majority baseline 52.7%`. `R:R to first zone 0.31:1`. `Meanrev -18`. Every one
of those is precise, and every one is opaque to anyone who has not already met
the term: they name a quantity without saying what it measures, what a good
value looks like, or what it should change about the reader's view of the chart.
The panels carried `title=` attributes with a one-line gloss, which is unstyled,
slow, plain-text-only and invisible on touch.

`js/ui/tips.js` generalises the zone tooltip into one shared element, in three
parts, because the three questions are always the same:

    TITLE   what this row is called
    BODY    what it means, in a sentence, with no jargon
    NOTE    how to read the number that is on screen right now

The NOTE is the part a static `title=` could never do, because it is written
against the live value:

    HOW OFTEN IT HAS BEEN RIGHT
    Over the last 1735 bars, the share of times the model called the
    next bar correctly. Every one of those calls was made before its
    own bar existed, so this is not the model being tested on what it
    was taught.
    ----------------------------------------------------------------
    50.4% against a 52.6% baseline - NOT ahead. Compare it to the
    baseline below, never to 50%.

A reader who sees `50.4%` next to nothing concludes "about a coin flip, fine".
The subtraction that turns it into a verdict is the whole point of the panel,
and leaving it to the reader is how a scorecard built to prevent
overconfidence ends up producing it.

TWENTY-ONE rows carry one: the verdict badge and conviction score, each
timeframe row, structure, invalidation, R:R, the signal composite, all six
components, and the five scorecard statistics. The component glosses avoid
indicator names entirely -- `MACD` is described as "whether upward pressure is
building or fading", not as a difference of exponential averages.

Mean reversion needed its sign spelled out. It is the one component that points
the OTHER way to the rest, so a reader seeing `Meanrev +64` under a body that
says "stretched moves snap back" has been handed a contradiction. The body now
says which way each sign runs and what a positive number therefore means.

The note is NOT coloured with `--accent`. Pink is `--down` in this palette, and
a bearish-coloured line under a number would read as a verdict on it.

Delegated from `document` once at startup rather than bound per row: the Trend
read re-renders on every tick, and per-row listeners would be built and thrown
away thousands of times a session. The tooltip anchors to the ELEMENT, not the
cursor -- these rows are thin and the panels are narrow, so a cursor-following
tooltip covers the very number it is explaining.

TWO INDICATORS, answering two different questions. An ARROW on the tooltip says
which way it points; the source row is highlighted to say what it points AT.
The second is not redundant: the Signal engine stacks six near-identical rows
14px apart, and an arrow alone leaves the reader counting rows to find which one
it came from.

The arrow is a rotated square with two of its four borders removed, so the
corner poking out carries the same 1px edge as the tooltip body. The classic
CSS triangle -- a box with zero width and thick borders -- has no border of its
own and would read as a floating blob against the panel behind it.

Its vertical position is set from JS per row, not pinned at 50%, because the
tooltip's centre and the row's centre are the same point only while the tooltip
is unclamped. Near the top or bottom of the window the tooltip is pushed back
inside the viewport and the two separate.

AND WHEN IT CANNOT REACH, IT IS DROPPED. On a short window the tooltip can be
taller than the room beside its row -- measured at 542px of separation on a
420px-high viewport, where 14 of 21 rows could not be reached. Pinning the arrow
to the nearest edge would leave it pointing confidently at the wrong row, which
is worse than no arrow at all. It hides instead, and the row highlight carries
the job alone. At a normal 909px window all 21 rows reach, every one within 1px
of the row's centre.

### Two zone detectors, two vocabularies

The chart draws two kinds of band from two unrelated detectors, and they were
BOTH labelled "DEMAND" -- which made the chart look like it was reporting the
same thing twice.

| label | detector | the question it answers |
|---|---|---|
| `SUPPORT ×3` / `RESISTANCE ×3` | `zones.js` pivot clusters | where has price TURNED AT the same level, repeatedly? |
| `DEMAND ●` / `SUPPLY ×2` | `supplydemand.js` | where did price LEAVE IN A HURRY? |

The suffix disambiguates as well as the noun: `×N` is a touch count, `●` marks a
supply/demand zone price has not returned to yet.

They are genuinely independent -- measured pairwise overlap is low, which is why
both are drawn. Where they land on the same region that is two unrelated methods
agreeing; where they do not, they are answering different questions.

HOVER A BAND and a tooltip explains the zone in words before it quotes any
numbers. The scoring vocabulary -- `0.64 ATR wide`, `2.0 ATR reaction`,
`strength 74` -- is the DETECTOR's, not a reader's: it is only meaningful to
someone who already knows what ATR is worth on this instrument. So each row
carries a label, a value in the instrument's own units, and a plain-word
reading, with the raw ratio kept in parentheses:

    SUPPORT x6
    Price has come back to this level 6 times and turned back up
    each time. Buyers keep showing up here.

    ZONE            4655.43 - 4664.15
    THICKNESS       8.7 pts - normal            (0.82 ATR)
    TYPICAL BOUNCE  20 pts away                 (1.9x a normal bar)
    RATING          71 / 100 - solid

`pts` becomes `pips` on FX, scaled off the quote's digits. The opening line is a
sentence about THIS zone rather than a definition of the zone type, because the
first question a chart raises is "what is this band telling me", not "what is a
supply zone". A supply/demand tooltip says in the same place whether the zone is
untested and what that implies -- that the orders behind the move may still be
waiting there -- rather than printing the word `untested` and leaving the reader
to look it up.

It also names the DIRECTION, which the first version dropped. `kind` IS the
direction in `supplydemand.js` -- DEMAND is set when the impulse out of the base
was up, SUPPLY when it was down -- so "price left in a hurry" threw away half of
what the detector knew. It now reads "price paused here, then ran UP hard", and
the departure row is signed to match.

A label tells you the zone exists; this lets you judge it.

Each band type has its own switch in the `⌁ Auto TL` menu --
`Support / resistance zones` and `Supply / demand zones` -- and both persist.
They answer different questions, so being able to see one without the other is
the point: with both drawn, a region where they overlap is easy to mistake for
one strong zone rather than two methods agreeing.

Supply/demand is hit-tested FIRST. Its bands are narrower and usually sit inside
the broader pivot clusters, so testing the wide one first would always win and
the more specific object would be unreachable. The tooltip is a positioned DIV
rather than canvas -- a canvas tooltip would redraw every frame and clip at the
cell edge -- and carries `pointer-events:none`, without which the div sitting
under the cursor would swallow every mouse-move and the chart would stop
tracking.

### Zone prices on the axis

Every visible zone gets ONE tag on the price axis, coloured by whether it sits
above price or below. The bands alone said WHERE a level was but not WHAT, so
reading a price off one meant hovering the crosshair.

THE TAG SITS AT THE NEAR EDGE, not the midpoint. The mid is an average of pivot
prices and nothing ever turned there; the edges are the extremes that formed the
cluster, and the near edge -- the one price meets first -- is the level the
detector itself uses for every approach it measures. Labelling both edges also
read as two separate levels, which a zone is not.

Price INSIDE a zone is the exception: it can leave either way, so both edges are
live and both are labelled.

Three collisions had to be resolved for it to be readable rather than noisy:

  * The LAST-PRICE tag wins. It is drawn later and would paint over a zone tag
    anyway, and a half-covered number reads as a bug. Overlap is tested as BOX
    against BOX -- comparing centres let a tag whose centre cleared the reserved
    region still have its top half clipped by the countdown.
  * A level within ~15px of the current price cannot be labelled at its true
    height. It is DROPPED rather than nudged: a number drawn beside the wrong
    line is worse than no number.
  * The AXIS yields to zone tags. A zone tag is a specific, named level; a
    round-number tick is scenery, so the tick is skipped when they collide.

### Structure the chart draws: channels, zones, episodes, breaks

The line engine finds one line at a time. Four objects sit on top of it, each
with a Python source of truth, a JS port, and a parity test comparing them
object-for-object with a negative control proving the test bites.

**Channels** (`sim/tl/channels.py`) pair two roughly parallel rails into the
corridor a trader actually reads. Two kinds: `paired`, where both rails are
independently confirmed, and `projected`, where one confirmed rail is copied out
to the furthest opposite extreme in its span -- how a channel is drawn by hand,
and a weaker claim, so it carries an 18-point penalty and is drawn fainter with
`(proj)` in its label. CONTAINMENT is what stops this finding channels
everywhere: two parallel lines can always be drawn, and what makes them a
channel is that price stayed between them, so the fraction of closes inside the
corridor is measured and anything under 75% is discarded. Both rails must also
have been TOUCHED -- without that gate the detector happily returns a corridor
drawn wide enough to contain everything.

**Zones** (`sim/tl/zones.py`) are BANDS, not lines. Price does not turn at
1.16847; it turns somewhere in a band a few tenths of an ATR wide. Confirmed
pivots within `cluster_atr` of each other agglomerate into one zone, touches
closer than `min_separation` bars collapse to one event, and strength weights
TIGHTNESS heavily -- four touches inside 0.15 ATR is a level, the same four
spread over a full ATR is a neighbourhood. **Role is derived from price, not
from the pivots**: a zone built from swing highs is resistance below and support
above, so the band flips colour the moment price closes through it. Hard-coding
the role from its origin would throw away the most-taught idea in S/R.

**Segments** (`sim/tl/segments.py`) turn the per-bar regime into a SEQUENCE with
boundaries -- "downtrend, then range, then uptrend" -- which is what an
annotated chart shows and what a per-bar label cannot give you. Two guards stop
it producing confetti: runs shorter than `min_bars` are absorbed into their
longer neighbour, and a regime must hold `confirm_bars` consecutive bars before
a new episode opens. Only the final segment is open-ended, because you never
know the current episode has ended until it has. Drawn as a thin strip along the
top of the price pane rather than a full-height tint, so the BOUNDARIES read as
a sequence instead of competing with the zones and channels behind the candles.

**Break markers** come from `breakEvents()`, which reads the engine's own break
events and their `qualityAtBreak` -- frozen at the break for exactly this
reason, since reading `qualityScore` afterwards reports what the line decayed
to. Triangles point the way PRICE went, which is the opposite of the rail that
failed, and size scales with the quality the line had when it broke. Two
reductions were necessary: several near-parallel lines commonly fail on the same
impulse candle, which is one event to a reader, so only the strongest per bar
survives; and 1200 bars of EURUSD 4h produce 562 raw breaks -- a texture, not a
set of events -- so the most recent 30 are kept.

Draw order is deliberate and is the design: segments and zones UNDER the
candles, because a band is the region price moved through and covering the wick
that tested it would hide the evidence; channels and lines above, because they
are annotations on the bars; break markers last, because an event should be
findable at a glance.

### Sensitivity: per-instrument thresholds, and what an ablation found

    sensitivity = f(pivot_window, ATR_prominence, volatility_regime)

`sim/tl/sensitivity.py` calibrates the detector to the instrument in front of
it instead of using one constant everywhere. It is **opt-in**: pass
`sensitivity=` to `TrendlineEngine` and the per-side values override the flat
ones in `Params`; leave it out and nothing changes.

**Prominence is a percentile of the instrument's own distribution.** The old
`min_swing_atr` was a fixed constant and it was mis-scaled -- prominence is
measured over a +/-strength window, and a 7-bar high-to-low range is ~2.4 ATR
by construction, so the documented "useful" values of 0.5 and 1.0 filtered
**0.03%** and **1.6%** of pivots. It did nothing. `prominence_pct = 40` now
means "drop the least prominent 40% of swings on THIS instrument at THIS
timeframe", which means the same thing on gold and on yen.

**Volatility regime widens the WINDOW, not the threshold.** `strength` rises by
one at the 75th ATR percentile and two at the 90th: widening the window asks
price to travel further in TIME, which is what separates a turn from a spike.

#### The ablation

The three components were switched on one at a time across three disjoint eras
(1999-2010, 2011-2020, 2021-2026), calibrated causally on the earliest 20% of
each cell. Pooled placebo-adjusted edge:

| arm | n | edge | z |
|---|---|---|---|
| default (strength 3, no prominence) | 70,245 | -0.17 pp | -0.70 |
| wider window ALONE | 62,944 | **-0.60 pp** | -2.31 |
| prominence ALONE | 57,604 | **-0.32 pp** | -1.17 |
| window + prominence | 45,468 | +0.64 pp | +2.12 |
| **+ regime  (shipped)** | 35,665 | **+0.85 pp** | **+2.48** |
| + per-side asymmetry | 34,292 | +0.93 pp | +2.68 |

**Neither the wider window nor the prominence bar helps alone -- both are worse
than doing nothing.** They only work together, and the reason is mechanical:
prominence is measured over +/-strength, so at strength 3 the depth measure
spans 7 bars and barely discriminates. The wider window is what makes the
prominence measurement mean something. Changing one without the other is a
regression, not a partial improvement.

#### Per-side asymmetry is measured, real, and NOT acted on

Support and resistance genuinely behave differently. Paired line-vs-placebo,
71k approaches:

    support      +2.37 pp   (z = +6.96)
    resistance   +0.20 pp   (z = +0.57)

But acting on it does not replicate. The asymmetric arm contributed **+1.10 pp**
in one era, **-0.85** in the next and **-0.13** in the third, and it was the
only variant that turned a positive era negative. Knowing the two sides differ
is not the same as knowing how to exploit it, so `resistance_quality_bonus` and
`resistance_tol_scale` ship at 0 and 1.0. Set them to 6.0 and 0.90 to re-enable.
`tests/test_sensitivity_parity.py` asserts the defaults stay off.

Detection is symmetric on purpose: swing-high and swing-low prominence
distributions were measured to be the same to within 2% (2.407 vs 2.396 on
EURUSD 1h, 2.370 vs 2.413 on XAUUSD 4h), so splitting the DETECTION stage would
be an assumption dressed as a calibration.

#### Rolling recalibration measured worse, and is Python-only

`rolling()` returns a callable `i -> Sensitivity`, recalibrated from a trailing
window, and the Python engine caches pivots per distinct strength so the window
can change mid-walk. It was built to test a hypothesis -- that freezing a
momentary regime for a decade was what damaged USDJPY 4h in 2011-2020 -- and
the hypothesis was **wrong**: rolling moved that cell from -3.66 to -3.49, and
was worse than the static calibration in all three eras (+0.91 vs +1.91,
-0.71 vs -0.37, +0.50 vs +1.31). It is kept for further study and is not
ported: the chart calibrates once at the bar it draws, so there is nothing for
a rolling calibration to vary over.

#### The `⌁ Auto TL` menu, and a debug handle

Every overlay the AUTO stack draws now has its own switch: support/resistance
zones, supply/demand zones, swing points, BOS/CHoCH, channels, regime segments,
plus sensitivity, lines-per-side and which timeframes to project from.

`Recompute now` was removed. It matched no branch in the menu handler and fell
through to the same `recomputeAll()` that every other menu action ends with, so
toggling anything already did the work and the item could only ever repeat what
had just happened. It did FUNCTION -- wiping every overlay to zero and clicking
it restored all four exactly, and it correctly kept an as-of scroll position
rather than snapping the analysis to the live edge -- it simply had no job that
another control was not already doing. `recomputeAll()` itself stays: the `l`
shortcut and every toggle call it.

`window.dnfx` exposes the workspace. The chart's contents were otherwise
unreachable from a console, which makes "what is actually ON this canvas"
unanswerable -- and that is the first question whenever the chart looks wrong.
It is what turned "so many trendlines" from an opinion into `autoLines 4,
channels 3, segments 12` in one line. Read-only by convention; nothing in the
app reads it back.

#### On the chart: `Auto TL -> Adaptive sensitivity`

Off by default; the toggle is in the `⌁ Auto TL` menu and persists.

**Turning it on never removes a line.** Detection is untouched -- the engine
still walks on the preset window with no prominence bar, so the chart draws
exactly what it drew before. That is deliberate, and it is measured: applying
the calibrated window to DRAWING cost XAUUSD 1h all three of its lines and
USDJPY 4h a quarter of them. A threshold that is right for what a strategy acts
on empties a chart when it also decides what the chart shows, which is the same
lesson `min_quality = 90` taught.

What the toggle changes is the `offered` FLAG. A line is flagged only if the
measured calibration would have built it at all -- both anchors clearing its
prominence bar at its window -- and cleared its quality bar. Unflagged lines
still draw at half weight. Live, that tightens USDJPY 4h from 4 offered lines to
2 while all 12 stay on screen.

The anchor check has no Python counterpart and nothing to keep in parity: the
engine filters at the pivot stage, so only the chart needs to ask this question
of a line that already exists.

#### The honest ceiling

+0.85 pp makes the detector **less bad, not good**. The default arm is negative
(-0.17 pp) and this cancels that. It is a defect repair, not an edge, and the
arms filter different approach sets (n falls 70k -> 34k) so the comparison is
indicative rather than controlled.

### Two more detectors, both measured before being drawn

Everything below is off by default in `⌁ Auto TL` and generates no signals. Both
passed a structural placebo test across three disjoint eras and both fail the
economic gate, like every other candidate here.

#### Slope lines — one pivot, volatility for the rest

`sim/tl/slope_lines.py` implements the method published as **Trendlines with
Breaks** by LuxAlgo (TradingView, 2022, open source). `engine.py` builds a line
from TWO pivots, so position and slope are both measured and no line exists
until a second pivot arrives. This uses ONE pivot for position and takes the
slope from volatility:

    upper = (pivot high just visible) ? that price : upper - slope
    lower = (pivot low  just visible) ? that price : lower + slope

The upper line decays down toward price, the lower rises up, until the next
pivot resets them.

**Why it is here: a line always exists.** XAUUSD M15 has ZERO confirmed lines
under the two-pivot engine; this holds ~96% coverage on the same series. That
is the "chart draws nothing" failure the rest of this file keeps running into.

Measured against matched control candles (same body, close position and
direction, non-breaking), three eras:

| slope method | 1999-2010 | 2011-2020 | 2021-2026 |
|---|---|---|---|
| **atr** | +3.38 pp (z 5.97) | +3.22 pp (z 5.08) | +2.70 pp (z 3.18) |
| stdev | +3.00 (z 5.11) | +3.92 (z 6.02) | +2.62 (z 3.02) |
| linreg | +4.17 (z 7.23) | +3.83 (z 6.02) | +1.70 (z 2.01) |

All nine cells positive; ATR is the most stable, linreg decays hardest. That is
at least as much information per break as the two-pivot engine's
(+2.43 / +2.97 / +2.80), from a third the events.

Two deviations from the published script, both deliberate:

* **`backpaint` defaults OFF.** The original can reset the line at the bar the
  pivot OCCURRED, which is `length` bars before anyone could know it. The
  default here resets when the pivot became VISIBLE. A test asserts the
  backpainted variant both matches across languages AND differs from the causal
  one -- otherwise the guard would be silently inert.
* **`max_distance_atr`** (0 = published behaviour; the chart uses 6). A decaying
  line keeps decaying until the next pivot resets it, so after a break it can
  run a long way from price -- measured up to 13.5 ATR on gold 1h, with 14-24%
  of bars beyond 6. Those stretches are arithmetic, not structure.

A real numerical bug was fixed in BOTH languages on the way: the published slope
formula uses `mean(x*y) - mean(x)*mean(y)` for covariance, which suffers
catastrophic cancellation when bar index (~1900) times price (~2400) gives
products around 4.5e6 whose difference is small. Parity failed at 1.004e-9
against a 1e-9 bound. Both sides now use the centred form, which is more
accurate than the original rather than merely agreeing.

#### Supply/demand — zones from impulse origins

`sim/tl/supply_demand.py` asks a different question from `zones.py`: not "where
has price turned repeatedly?" but "where did price leave from in a hurry?" An
impulse of >= 2.5 ATR within <= 6 bars, with >= 60% of its bars agreeing in
direction, and the quiet base it departed from becomes the zone.

**The two detectors barely overlap.** A pivot cluster needs price to have
visited a level SEVERAL times; an impulse origin can be a level price visited
ONCE and ran from. Live across four cells: six impulse-origin zones, exactly
ONE of which coincides with a pivot-cluster zone.

And they agree on the answer:

| era | pivot-cluster | impulse-origin |
|---|---|---|
| 1999-2010 | +5.50 pp (z 7.05) | +5.55 pp (z 3.91) |
| 2011-2020 | +5.00 pp (z 6.21) | +6.87 pp (z 5.21) |
| 2021-2026 | +5.09 pp (z 4.68) | +3.83 pp (z 2.21) |

Two nearly disjoint detectors converging on ~5 pp is the strongest argument in
this project that zones are structural rather than fitted.

**Freshness is the property pivot clustering cannot express** -- a pivot-cluster
zone is DEFINED by repeated touches, so "untested" is meaningless there. Fresh
beat tested in all three eras (+6.16 vs +4.93, +7.21 vs +6.53, +4.28 vs +3.39):
the direction replicates, the gap is about one percentage point, and none of the
differences would clear significance alone. So a fresh zone draws solid and a
used one dashed, and nothing is weighted by it.

**Impulse SIZE does not replicate** and is not used for ranking: big impulses
won in two eras and lost badly in the third (+4.60 vs +9.14).

Zones are drawn from the bar they were CONFIRMED rightward, never across earlier
bars -- the zone did not exist before its impulse finished, and spanning the
whole chart would imply it was tradeable then.

### BOS and CHoCH, and the label that does not replicate

`sim/tl/market_structure.py` implements the two events every market-structure
course is built around:

    BOS    price closes through the last swing in the SAME direction as the
           prevailing bias -- the trend made another leg
    CHoCH  price closes through the last swing AGAINST the bias -- the first
           evidence the trend may be over, and it flips the bias

The break is identical; only the state it arrives in differs. Closing above the
last swing high is a BOS while the bias is already bullish and a CHoCH while it
is bearish, which is why this is a state machine rather than a per-bar rule --
and why the port had to reproduce the STATE, not just the comparison.

Definitions follow "Market Structure CHoCH/BOS (Fractal)" by LuxAlgo
(TradingView, 2023, open source).

Two rules that matter more than they look:

* **Closes, not wicks**, exactly as `engine.py` breaks a trendline.
* **A level is consumed when it breaks.** It cannot break twice, so no further
  bullish event fires until a NEW swing high confirms. Without this one strong
  trend prints a BOS on every bar that makes a new high.

#### Measured against matched candles

| era | CHoCH | BOS |
|---|---|---|
| 1999-2010 | +4.02 pp (z 5.58) | +2.99 pp (z 4.25) |
| 2011-2020 | +5.03 pp (z 6.45) | +3.31 pp (z 4.38) |
| 2021-2026 | +3.02 pp (z 2.98) | **+5.57 pp** (z 5.57) |

All six cells positive and significant. CHoCH at ~+4 pp is the second-strongest
structural finding in this project, behind zones.

**But the BOS/CHoCH distinction does not replicate.** CHoCH beats BOS in the two
older eras and BOS wins decisively in the most recent one. A close through the
last confirmed swing carries about +4 pp WHICHEVER LABEL it gets; the state
machine that decides which is bookkeeping, not information. Every video and
indicator on this topic presents that distinction as the insight, and it is the
part that does not survive.

Note also the gap between raw and matched: raw differences are +0.6 to +3.5 pp,
matched are +3.0 to +5.6. These events fire on big directional candles, so
without controlling for candle shape you would credit structure for momentum.

### Divergence: regular AND hidden both fail

`sim/divergence.py` gained HIDDEN divergence -- the mirror comparison, where
regular says the trend is failing and hidden says a pullback inside it is
ending:

    bullish_hidden   price makes a HIGHER low while RSI makes a LOWER low
    bearish_hidden   price makes a LOWER high while RSI makes a HIGHER high

It is **opt-in** (`include_hidden=True`) and a test enforces that the default
output stays byte-identical: `rsi_divergence` and the parity fixtures both
depend on it, and quietly adding rows would move a backtest without touching a
strategy file. Hidden gets a mid-range RSI band (30-70) rather than the
overbought/oversold filter, because it lives in the middle of a trend where the
extreme filter would reject nearly all of it.

Measured at the CONFIRMATION bar -- never the pivot bar -- against the same
matched-candle control:

| | 1999-2010 | 2011-2020 | 2021-2026 |
|---|---|---|---|
| regular | +0.12 pp (z 0.09) | +0.71 pp (z 0.47) | +4.79 pp (z 2.29) |
| hidden | -1.51 pp (z -1.47) | -0.24 pp (z -0.22) | +0.07 pp (z 0.05) |

**Neither carries information.** Regular is flat out of sample and significant
only in the era everything here was developed on. Hidden averages -0.6 pp. Per
kind the signs flip: `bearish_hidden` is -2.97 pp (z -2.06) in the oldest era
and positive in both others; regular `bearish` carries the whole 2021-2026
result at +7.22 pp and is negative before that.

This is not a power problem -- hidden has 1,300-2,800 events per era against a
control of 74k-159k, ample to resolve the +3 to +5 pp the other detectors show.
It is simply not there. Divergence joins trendline-holds and channels as a
detector that fails the STRUCTURAL gate, not merely the economic one.

The detector stays: it is drawn, it is parity-tested, and knowing it does not
predict is worth more than not knowing.

### The lifecycle is not a ranking, and RECLAIMED is the good state

`BROKEN` used to be terminal. That buried lines the market was still using: on
gold H1 the engine held a rising support with five touches sitting ONE POINT
under price, marked BROKEN because price had left it 29 bars earlier -- and
seven of the ten bars since had closed back above it. The break was real; the
death sentence was not.

`reclaim_confirm_bars` (default 3) revives a broken line after that many
CONSECUTIVE closes back on its working side. Deliberately harder than breaking
(one close, by default), because a line should not resurrect because price
brushed past it, and bounded by `archive_after` since the line is gone after
that. **The violation is never forgiven** -- `violations` stays, the -12 quality
penalty stays, `quality_at_break` stays. A reclaimed line is a line with a scar.

#### What the lifecycle is actually worth

Paired line-vs-placebo, split by status at the approach bar:

| status | 1999-2010 | 2011-2020 | 2021-2026 |
|---|---|---|---|
| **CONFIRMED** | **-5.10 pp** (z -4.88) | **-4.12 pp** (z -3.94) | +0.24 pp |
| ACTIVE | +0.64 | -1.18 | +0.08 |
| RECLAIMED (gated) | **+4.76** (z 5.75) | **+3.10** (z 3.47) | **+4.75** (z 4.06) |

The ordering is the reverse of what the names imply. A line that broke and was
then respected again beats one that never broke; the state the engine treats as
its promotion target is the one you would least want to act on.

`CONFIRMED` is definitionally 3 touches -- a line is CONFIRMED only between its
third touch and its fourth -- so status and touch count are collinear and cannot
be fully separated. But the sequence reads monotonic:

    3 touches  -2.71 pp (z -4.1)      5 touches  +1.13 pp (z 2.1)
    4 touches  -0.44 pp (z -1.2)      6+         +0.03 pp

so it is HOW TESTED the line is, not the label. Most of it is already handled:
CONFIRMED lines have median quality 78.7 against a `min_quality` of 90, so under
a quarter were ever offered to a strategy. It is a drawing-level effect.

What DOES separate cleanly is reclaim: at the SAME touch count, RECLAIMED beats
ACTIVE (+5.50 vs +1.13 at five touches, +3.86 vs +0.03 at six or more).

#### The gate, and the bug that only re-verification caught

`reclaim_min_quality` is 70, read off the data rather than chosen. Paired edge
over 42,273 reclaimed approaches, three eras pooled:

| quality >= | n | edge | z |
|---|---|---|---|
| 0 | 42273 | +1.12 pp | 3.51 |
| 50 | 40134 | +1.37 | 4.20 |
| 60 | 34266 | +1.95 | 5.52 |
| **70** | **21229** | **+3.41** | **7.59** |
| 75 | 10554 | +3.97 | 6.23 |

70 is where it is both large and STABLE: +3.02 / +3.61 / +3.85 pp per era, all
three positive, while the half below is negative in two (-2.71, -2.73).

**The first version of this gate was wrong, and it looked right.** It checked
quality inside `register_reclaim` -- but a BROKEN line is not rescored, so the
score there is the one FROZEN at the break: high, because the line was tradeable
before it failed. That filtered 9% of reclaims and left the edge unchanged
(+1.80 / +0.42 / +1.87). Moved to run AFTER rescoring -- which is the quantity
the measurement bucketed on -- it filters 64% and delivers the measured result.
Same threshold, same field name, different quantity. Only re-verifying the
shipped gate against the original measurement caught it.

#### Pooled effect of shipping it on

| era | reclaim off | reclaim on |
|---|---|---|
| 1999-2010 | +0.21 pp (z 0.57) | **+1.02 pp** (z 2.95) |
| 2011-2020 | **-1.43 pp** (z -3.50) | -0.40 pp (z -1.06) |
| 2021-2026 | +1.19 pp (z 2.21) | +0.90 pp (z 1.80) |

Better in two eras of three, and the gain lands where it was most needed. Note
this moves BACKTESTS as well as the chart: `snap.tradeable` feeds the
diagnostics and `_best()` feeds `features.py`, which feeds every strategy.

### Fair value gaps are the strongest thing measured here

`sim/tl/fvg.py` -- a three-candle imbalance, and the only object in this
codebase with no judgement in it at all:

    BULLISH   high[i-2] < low[i]     zone = (high[i-2], low[i])
    BEARISH   low[i-2]  > high[i]    zone = (high[i],  low[i-2])

No pivots, no lookback, no confirmation window. It is also the only detector
here with NO fractal lag: `confirmed_i = i`, because all three candles have
closed. Everything else in `sim/tl/` carries a confirmation bar later than the
bar it describes.

Against the same placebo as zones:

| era | FVG | pivot-cluster zones |
|---|---|---|
| 1999-2010 | **+8.97 pp** (z 5.98) | +5.50 pp |
| 2011-2020 | **+7.82 pp** (z 5.62) | +5.00 pp |
| 2021-2026 | **+7.25 pp** (z 3.98) | +5.09 pp |

Larger gaps score better (+10.2 vs +7.8). And the claim the technique is usually
sold on survives too: **96.4% of gaps fill against 86.6% for a same-width band
1.5 ATR away** -- so gaps do attract price, not merely get touched the way any
level eventually is.

### Confluence does not stack

Six detectors, each individually worth +2.4 to +9 pp and each individually short
of friction. If they were independent, agreement should compound.

| agreement | 1999-2010 | 2011-2020 | 2021-2026 |
|---|---|---|---|
| 1 voter | +3.43 (z 7.85) | +2.67 (z 5.48) | +2.99 (z 4.81) |
| 2 voters | +2.78 | +2.62 | +2.36 |
| 3+ voters | +4.49 (z 3.86) | +4.92 (z 3.87) | +3.92 (z 2.31) |

Three-plus agreement beats one -- +4.4 pp against +3.0 -- and replicates. But it
is NOT additive, TWO voters is consistently worse than one in all three eras,
and 3+ agreement occurs on about 4% of signal bars.

The independence check says why: `bos_choch` and `line_break` co-occur on
**73% / 73% / 71%** of BOS/CHoCH firings. They are near enough the same detector
wearing two names -- both fire when price closes through a prior swing. Only
FVG-vs-zone is genuinely independent (~12% overlap), and FVG alone already
carries more than any pair.

Confluence was the one direction the evidence pointed at for closing the 1-2.5
pp gap to friction. It does not close it.

### One clock, enforced rather than assumed

Bars carry broker **server time** as a timezone-naive index (`tools/dataset.py`),
and that is deliberate: the broker's UTC offset moves with its own DST, so
correcting twenty years with a single constant would be wrong for half of them.

The danger is not the convention, it is a **mixture**. A tz-aware frame reaching
the engine would not raise anywhere downstream -- `mtf.py` matches on bar close
times, so an hour of drift silently serves the wrong context bar, which is
look-ahead wearing a plausible face. `sim/tl/clockguard.py` rejects a tz-aware
index at `engine.walk`, `structure.classify` and `regime.compute`.

It **rejects rather than converts**, on purpose: a silent `tz_localize(None)`
would reintroduce exactly the ambiguity the guard exists to prevent. Convert at
the loader, deliberately, or not at all.

### RSI divergence, drawn and traded from one definition

`ƒ Indicators -> RSI + divergence` adds an RSI pane that draws each divergence as
two legs: the RSI leg in the oscillator, and the corresponding PRICE leg on the
candles, because the whole point is that the two disagree. Bullish is green,
bearish pink, with a dot on each of the two swings being compared.

The line is drawn to the swing, but a small triangle marks the bar where the
pivot was **confirmed** — three bars later. That is the earliest bar the
divergence could have been acted on, and the backtester uses exactly that bar.
Drawing to the swing while implying you could have traded it is how divergence
gets oversold as a technique.

Detection lives in **one place per language**: `sim/divergence.py` and its mirror
`js/chart/divergence.js`, compared divergence-for-divergence in
`tests/test_parity.py` — same swings, same confirmation bars, same RSI values.
`sim/strategies/rsi_divergence.py` consumes the Python function directly, so a
divergence you can see on the chart is a divergence the backtest traded.

Rendering support for this is generic: plots may be of type `segment` (two
endpoints anchored in time) or `mark`, and a study in its own pane may target the
main pane via `pane: 'main'` — which is what lets one object draw in two panes.

### One detector, chart and backtest

`js/chart/tlengine.js` is a port of `sim/tl/engine.py` — same parameter defaults,
same iteration order, same scoring arithmetic, same archive reasons. The chart
draws from it, and `tests/test_tl_parity.py` compares every line either
implementation ever creates: same anchors, same lifecycle transitions, same
touch/violation counts, same quality, same break bars.

This replaced a real problem rather than a hypothetical one. The page used to
draw lines from the batch scorer in `trendlines.js` while the simulator used the
lifecycle engine — two different algorithms, so **the lines you could see were
not the lines the backtest traded**, which quietly undermined every visual
sanity check. Verified with a negative control: changing `tolAtr` from 0.32 to
0.33 in the JS alone fails 7 of the 10 parity tests.

Lifecycle state is now visible on the chart: a line's label carries `○` for
CONFIRMED and `●` for ACTIVE (retested since confirming), and ACTIVE lines draw
brighter. Only CONFIRMED/ACTIVE lines are drawn — a candidate is a guess.

`trendlines.js` remains for its `findPivots`, which both engines share and which
is parity-tested at strengths 2/3/6.

### Parity with the live chart

`tools/parity_export.mjs` runs the actual `js/chart/indicators.js` and
`js/chart/trendlines.js` under node and dumps their output;
`tests/test_parity.py` compares the Python mirror value by value at 1e-9.

```bash
node tools/parity_export.mjs        # regenerate when the JS changes on purpose
python -m pytest tests/test_parity.py -q
```

Verified bit-exact for pivots (strength 2/3/6), EMA, SMA, Bollinger, RSI, MACD,
ATR, Stochastic, VWAP and Heikin Ashi. Negative controls confirm the test bites:
pandas' default `ewm` seeding differs from the JS by 1.9 in price, which is
exactly the trap the explicit SMA seeding in `sim/indicators.py` avoids.

## Strategies and the confluence question

```bash
python -m sim.run_tl --ab --carry-free --start 2021-01-01
```

`tl_bounce` trades lines holding, `tl_breakout` trades them failing (running both
over the same lines answers whether the detector finds levels that hold or levels
that break), `rsi_divergence` trades price/momentum disagreement off confirmed
pivots only.

**Confluence is measured, never assumed.** Every signal is logged with its
confluence score whether or not the filter gated it, so `--ab` runs
`confluence_mode=off` against `require` over identical features and reports which
way it went. Current answer on 2021-2026: it helps 2 of 6 combinations and hurts
4. It is not a free win.

**Carry-free mode** (`--carry-free`, `Config.flat_by_hour`) flattens before
rollover so swap never applies — necessary here because swap on these
instruments is large and only knowable at today's rate.

## What the trendline detector is actually worth

This is the part to read before building anything on these lines. Every number
below is out-of-sample on frozen parameters, and the summary is uncomfortable.

### The bounce hypothesis is closed

`sim/tl/diagnostics.py` asks the cleanest available question: when price
approaches a CONFIRMED line, is what happens next different from what happens at
the same line shifted 1.5 ATR sideways? Symmetric barriers, so the null is 50/50,
and the placebo arm carries identical slope and approach dynamics.

Pooled across EURUSD / USDJPY / XAUUSD and 15m / 1h / 4h:

| era | edge vs placebo | z |
|---|---|---|
| 2021-2026 (in-sample) | +0.10 pp | 0.35 |
| 1999-2010 | +0.40 pp | 0.94 |
| 2011-2020 | **-1.40 pp** | **-3.30** |

Three disjoint eras, ~73k paired approaches, roughly 27 years. **A confirmed
trendline is not a place where price holds more often than a nearby parallel
line.** The one cell that looked real in-sample -- EURUSD 1h at +3.5 pp, z 4.12 --
collapsed to +0.3 pp, z 0.42 on 1999-2010.

Sweeping `min_swing_atr` from 0.0 to 3.0 does not rescue it (+0.10, -0.20, -0.40,
+0.00, +0.30, +0.50, +0.60; no |z| above 1.8). That sweep did expose a real bug:
the parameter is mis-scaled. `_significant()` measures prominence over only
+/- `strength` bars, and a 7-bar high-to-low range is ~1 ATR by construction, so
at 0.5 it filters 0.03% of pivots and at 1.0 it filters 1.6%.

### What DID replicate: quality predicts harm

Paired line-vs-placebo hold rates by quality bucket, two disjoint out-of-sample
eras:

| quality | 1999-2010 | z | 2011-2020 | z |
|---|---|---|---|---|
| <= 65 | -7.79 pp | -1.18 | -12.07 pp | -2.25 |
| 65-80 | **-5.13 pp** | **-4.29** | **-5.06 pp** | **-4.84** |
| 80-90 | -1.21 pp | -1.67 | -2.72 pp | -3.84 |
| > 90 | +2.47 pp | +4.34 | +0.55 pp | +0.96 |

The 65-80 band is -5 pp in BOTH eras at |z| beyond 4 -- near-identical magnitude
across two decades. That is the most robust finding here, and it is a negative
one: **a sub-80 line is a level price respects measurably LESS than a random
parallel level.** The positive tail did not replicate (+2.47 then +0.55).

So `min_quality` is 90 not because it buys an edge, but because it stops paying
for a measured, repeatable harm. Expect roughly zero, which beats negative.

### The gold breakout, and why the approximation lied

The break side looked different from the hold side for a long time.
`tools/r_conversion.py` swept 25 geometries per era and 15 passed in each, seven
passed in both, and net R correlated 0.623 across eras -- the same surface shape
in both decades.

`tools/gold_breakout_wf.py` then ran the SAME geometry (stop 0.4, RR 4.0, frozen,
not re-swept) through the real simulator: next-bar-open fills, stop-before-target
on ambiguous bars, equity-scaled sizing, real spread, swap both ways.

| min_quality | 2016-2020 | 2021-2026 |
|---|---|---|
| 90 | +3.87% | **-10.43%** |
| 40 | -16.07% | **+24.91%** |

The sign flips between eras AND between quality settings. r_conversion reported
+0.576 net R where the simulator reports -0.308 avg R on the same span and
geometry, and the gap is the approximation: r_conversion enters at the break
bar's CLOSE and resolves barriers on CLOSES. With a 0.4 ATR stop and a 4:1
target, many bars contain both, and the simulator resolves those pessimistically
as the stop -- which is the honest rule. **The edge was the approximation.**

`r_conversion.py` prints `PASS -- but note these are IN-SAMPLE and the geometry
was chosen by looking` for exactly this reason. It is a cheap sweep, not a
backtest. Believe `gold_breakout_wf.py`.

### Everything measured, in one table

| candidate | structural gate | economic gate |
|---|---|---|
| trendline HOLDS | fail: +0.10 / +0.40 / -1.40 pp | -- |
| channels | fail: -1.21 pp (z -0.95) | -- |
| pivot-cluster zones | **pass**: +5.5 / +5.0 / +5.1 | fail: 2 of 100, t < 0.6 |
| impulse-origin zones | **pass**: +5.6 / +6.9 / +3.8 | **fail**: 0 of 25 geometries |
| two-pivot line BREAKS | **pass**: +2.4 / +3.0 / +2.8 | fail: negative at every geometry |
| slope-line BREAKS (LuxAlgo method) | **pass**: +3.4 / +3.2 / +2.7 | fail: negative at every geometry |
| gold 4h breakout | passed an r-sweep | fail: -10.4% in the real simulator |
| fair value gaps | **pass**: +9.0 / +7.8 / +7.3 | **fail**: 14 of 300 |
| reclaimed lines (gated) | **pass**: +4.8 / +3.1 / +4.8 | **fail**: 0 of 25 geometries |
| CHoCH | **pass**: +4.0 / +5.0 / +3.0 | fail: negative at every geometry |
| BOS | **pass**: +3.0 / +3.3 / +5.6 | fail: negative at every geometry |
| **full A-E stack** (Layer E strategy) | n/a — this is a strategy, not a detector | **fail**: net -0.059 R; see displacement_v1 below |
| **displacement_v1** (structural BOS + 1 ATR displacement) | gross +0.078 R, direction call real (vs flip, CI excludes 0) | **fail**: +0.049 R over a random bar (CI straddles 0) vs 0.136 R real friction |
| **area rejection** (wick into an area, close back out) | **fail**: -0.013 R vs a plain touch at the same areas (CI excludes 0) | **fail**: -0.138 R net; indistinguishable from a random bar |
| confluence, 3+ detectors agreeing | **pass**: +4.5 / +4.9 / +3.9 | not run — but see below |
| RSI divergence (regular) | fail: +0.1 / +0.7 / +4.8, only in-sample | -- |
| RSI divergence (hidden) | fail: -1.5 / -0.2 / +0.1 | -- |

Eight detectors replicate structurally across 27 years (confluence is a
combination of them, not a ninth). Nothing has passed an
economic gate. The break rows are the sharpest statement of it: `net_vs_control`
is positive at EVERY geometry while `net_R` is negative at EVERY geometry --
breaks consistently beat matched candles and consistently lose money.

### Distinctions that did NOT replicate

Worth its own list, because each of these is presented somewhere as the key
insight, and each failed the same way -- winning in some eras and losing in
others:

| claimed distinction | verdict |
|---|---|
| BOS vs CHoCH | CHoCH wins 2 eras, BOS wins the third decisively |
| support vs resistance asymmetry | +1.10 / -0.85 / -0.13 pp; flips sign |
| big vs small impulse (zones) | wins 2 eras, loses the third (+4.60 vs +9.14) |
| fresh vs tested zones | direction holds all 3 eras, but only ~1 pp |
| hidden vs regular divergence | neither carries information at all |
| CONFIRMED vs ACTIVE lifecycle | collinear with touch count; it is testedness, not the label |
| more confluence = more edge | 2 voters is worse than 1 in all three eras |

Only the fresh/tested one survived, and barely. The pattern is consistent
enough to be worth stating as a prior: an A-vs-B refinement on top of a real
effect usually turns out to be noise, and should be measured before it is
weighted.

### Why nothing converts: the arithmetic

Every economic failure has the same shape, and it is worth stating as a number
rather than a mood. At 1:1 with a 3 pp edge, expectancy is `2p - 1` = **+0.03 R
gross**. Friction on these instruments runs **0.03 to 0.23 R** depending on stop
distance. The structural edges are real and roughly **1 to 2.5 percentage points
short** of covering costs.

That is why the break detectors all produce the same table: `net_vs_control`
positive at EVERY geometry while `net_R` is negative at EVERY geometry. They
beat matched candles consistently and lose money consistently.

The geometry sweep made it vivid. Sweeping stop x target over 36 combinations,
the hit rate runs from **13%** (stop 0.5, target 4.0) to **86%** (stop 3.0,
target 0.5) -- a 6x spread -- and net R peaks at 1.0/2.0, in the middle, falling
away on BOTH sides. Every gain in accuracy is bought with a
worse payoff, at almost exactly the rate that cancels it. A high win rate is not
an edge, and a setup that specifies its own target and stop usually specifies
the bad ones.

Three routes remain untested, in the order the evidence favours them: exits
(everything here used a fixed symmetric bracket, which nobody trades), friction
reduction (0.03 R at 3 ATR stops on 4h versus 0.23 R at 0.4 ATR -- a 7x spread,
and D1 has never been run), and portfolio effects (a 3-5 pp edge sized across
uncorrelated instruments is different arithmetic from per-trade expectancy).

Confluence was the fourth, and it has now been run. It does not close the gap.

### The conclusion to carry forward

Nothing in this project currently survives a real out-of-sample backtest. The
detector describes structure without predicting it. That makes it genuinely
useful as a READING tool -- which is what the Trend read panel is for -- and it
is a reason to be sceptical of any automated strategy built on these lines.

The infrastructure is what has value here: look-ahead is structurally
impossible, the chart and the backtest run one engine, and the diagnostics are
honest enough to keep returning answers nobody wanted. That is what makes a
negative result trustworthy rather than merely disappointing.

### Reproducing the trendline results

```bash
python tools/tl_diagnostics.py --symbols EURUSD.a,USDJPY.a --tfs 1h,4h     --start 2011-01-01 --end 2020-12-31 --out runs/oos/era_b.csv
python tools/gold_breakout_wf.py --min-quality 90
```

`tl_diagnostics` writes one row per approach with its arm (`line` / `placebo` /
`random`), quality and outcome, so the paired bucket analysis above is a groupby
away. `gold_breakout_wf` runs one pre-registered geometry through the real
simulator on both spans; it does not sweep, on purpose.

## The A-E layer stack, and displacement

The detectors above were each tested by wiring them straight to a bracket:
level touched -> enter. That is a DETECTOR pretending to be a strategy, and
eleven of them failed that way. These modules separate the layers so the
question "what job is each part doing?" can be asked at all.

    sim/tl/swings.py       Layer B  swing detector, as a state machine
    sim/tl/structure.py    Layer C  HH / HL / LH / LL
    sim/tl/engine.py       Layer D  trendlines
    sim/tl/strategy.py     Layer E  the only layer that decides
    sim/tl/experiments.py           frozen, versioned experiment definitions

### Layer B: two clocks, three states

`pivots.py` answers "is bar i a fractal extreme" over a completed array and can
only say yes or no. `swings.py` asks the live question, which produces a third
state:

    CANDIDATE    the wick has printed and beats everything to its LEFT
    CONFIRMED    `strength` bars passed and none took the level out
    INVALIDATED  a bar inside the window exceeded it -- tried, and failed

INVALIDATED is not bookkeeping. Under `pivots.py` a failed candidate and a
boring bar are both simply absent, so nothing could ask how often a market kills
its own attempts. On EURUSD 1h that is 1,328 invalidated against 604 confirmed:
most candidates fail, and that was previously invisible.

Every swing carries BOTH clocks -- `t_event` (when the market made the extreme)
and `t_known` (when an observer could know). A backtest may plot on the first
and may only ACT on the second. The CONFIRMED set matches `find_pivots` exactly,
which is the correctness anchor: same rule, two shapes.

### Layer E: eleven gates, every rejection attributed

`strategy.evaluate` runs regime, swing, structure, trendline, retest, momentum,
htf, risk, spread, session and exposure IN ORDER, and records the FIRST gate
that said no. Without that, a strategy taking four trades a year is
indistinguishable from one that is broken.

That histogram immediately paid for itself. The stack as first specified took
0-2 trades per five-year cell:

    structure   45.0%   |  trendline   20.2%   |  momentum   0.8%
    regime      30.9%   |  retest       3.0%   |  swing      0.1%

The gates are individually sensible and their conjunction is very nearly empty.
Requiring Layer C alone cuts the sample by 78% and makes net expectancy WORSE.

### displacement_v1 -- the only thing that ever worked

`experiments.py` freezes it. A structural BOS may fire only if the breaking bar
CLOSES at least 1.0 ATR beyond the broken level -- price proving the break
rather than merely touching through it.

    gate 0.00   avg R -0.148   win 31.69%
    gate 0.25         -0.117       32.37%
    gate 0.50         -0.103       33.01%
    gate 1.00         -0.027       36.07%

Monotone in all three eras and every timeframe. And by timeframe, at gate 1.00:

| tf | trades | win% | avg R | friction |
|---|---|---|---|---|
| 5m | 11,366 | 30.09 | -0.365 | 0.243 |
| 15m | 3,774 | 33.32 | -0.169 | 0.161 |
| 1h | 1,154 | 35.20 | -0.047 | 0.102 |
| **4h** | **307** | **39.08** | **+0.102** | **0.071** |

4h is the only positive cell in the entire project. Both the gross edge AND the
friction improve with timeframe, so this is not purely a cost story.

### What killed it anyway

  * MFE/MAE: 55% reach +1R, 37% reach +2R. The 2R target is DEFENSIBLE -- it is
    not being imposed on a setup that cannot produce it.
  * Real friction is 0.136 R, not the 0.104 R the frozen model assumed: spread
    0.064 + slippage 0.040 + commission 0.014 + swap 0.002. Session matters --
    London 0.122, Tokyo 0.178, Sydney 0.209.
  * Controls: the direction call is REAL (signal vs flipped = +0.149 R, CI
    [+0.042, +0.252], excludes zero). The magnitude is not: signal vs a random
    bar at matched volatility = +0.049 R, CI [-0.062, +0.156], straddles zero.
  * Walk-forward on the best entry variant (B_retrace, a 0.5 ATR pullback):
    in-sample +0.0001 R, out-of-sample -0.032 R. Selecting the best of four
    variants on training data added NOTHING (selected - always_B = -0.039 R,
    CI straddles zero), and B is indistinguishable from the baseline variant
    out of sample (+0.056 R, CI [-0.058, +0.169]).

A real signal with a magnitude smaller than its costs. Outcome: kept as context,
not traded.

## The mean-reversion dividing line

REJECTION was the last structural hypothesis tested: price entering a zone and
leaving the way it came. It is the behavioural form of the question -- not
"where is price" but "what did price do at the level" -- which matters because
that is the family the one partial success (displacement) belongs to.

The comparison was deliberately rejection vs TOUCH. Both are price at the same
zone; only one is price refusing it, so the difference isolates behaviour from
location. Plus the two controls that survived scrutiny on displacement: a
non-signal bar at matched ATR percentile, and the same bar traded the other way.

| arm | n | win% | gross R | net R |
|---|---|---|---|---|
| rejection | 356,680 | 32.70 | -0.0190 | -0.1377 |
| touch | 284,124 | 33.12 | -0.0063 | -0.1334 |
| random_bar | 356,644 | 32.82 | -0.0154 | -0.1341 |
| flip | 356,653 | 33.01 | -0.0097 | -0.1283 |

    rejection - touch       -0.0128 R   CI [-0.019, -0.006]   excludes zero
    rejection - random_bar  -0.0036 R   CI [-0.010, +0.003]   straddles zero
    rejection - flip        -0.0094 R   CI [-0.016, -0.003]   excludes zero

Worse than the location it stands at, no better than an ordinary bar, worse than
its own inverse. Consistent across all three zone detectors (fvg -0.140, zones
-0.138, supply_demand -0.123) and all three eras. Trade table:
`runs/struct/rejection_test.csv`.

READ THE MAGNITUDES, NOT THE STARS. Every arm sits at 32.6-33.1% against a 33.3%
breakeven, and the differences are ~0.01 R against 0.128 R of friction. At
n=356,680 that resolves as "significant"; it is not tradeable, and "fade
rejections" is NOT what this says. It says all four arms are the same thing.

### Widening a level into an area does not rescue it

Expectancy by distance from a trendline, n=126,571: -0.342 / -0.337 / -0.327 /
-0.336 across the 0-0.25, 0.25-0.50, 0.50-1.00 and >1.00 ATR bands. Flat. A
corridor is only a choice of which band counts as a touch, so no width can help
-- and `Params.tol_atr` (0.32) already made every trendline a corridor anyway.

Zones were never single prices to begin with: `zones.py`, `supply_demand.py` and
`fvg.py` all carry `low`/`high` and an ATR-relative `width_atr`. They beat lines
STRUCTURALLY -- FVG at +9.0 pp is the largest structural result in this project
-- and all three still failed the economic gate.

### The line the measurements draw

    continuation after a break     carries information
                                   (displacement: direction vs flip,
                                    CI [+0.042, +0.252])
    reversal at a level            does not

Every mean-reversion hypothesis here has failed: trendline holds, zone holds,
supply/demand holds, FVG holds, reclaimed lines, and now rejection. The only
positive cell ever produced was momentum continuation on 4h.

## Backtest simulator

```bash
python -m pytest tests/ -q                  # gates 1 and 2
python tools/deals_replay.py --days 365     # gate 3: reconcile against the broker
python -m sim.run --all                     # both baselines x symbols x timeframes
python -m sim.run --symbol XAUUSD.a --tf 4h --strategy donchian --no-swap
```

`sim/core.py` is a referee, not a strategy. It knows nothing about indicators:
a strategy receives a `BarView` and returns an `Intent`, and the engine handles
fills, sizing, costs and accounting. What it enforces:

* Look-ahead is **structurally impossible** — `BarView` raises on any attempt to
  read past the current bar, so `bars[i+1]` cannot be written.
* A signal on the close of bar i fills at the **open of bar i+1**.
* A bar containing both stop and target resolves as the **stop**; a gap past the
  stop fills at the **open**.
* Size comes from a risk fraction of live equity, rounded **down** to
  `volume_step`; too small to place means not placed.
* A wiped account stops trading (`ruin_pct`), rather than compounding fiction.
* Every instrument-specific number is read from `data/instruments.json`.

Invariants are asserted every bar — equity identity, P/L reconciliation, no
overlapping trades, every trade has an exit reason. A violation raises instead of
returning a plausible wrong number.

### The three gates

1. **Known answers** (`tests/test_known_answers.py`) — synthetic series with
   hand-checkable results: a straight line returns exactly the price difference,
   a flat line returns exactly minus the spread, fills land on the next open.
2. **Invariants and causality** (`tests/test_invariants.py`) — books reconcile on
   random walks for both baselines and both spec shapes. The causality audit
   recomputes every precomputed indicator on *truncated* history and demands the
   value at that index be unchanged, so look-ahead inside an indicator fails a
   test rather than inflating a result.
3. **Deals replay** (`tools/deals_replay.py`) — recomputes real closed positions
   from the live account with the simulator's own formula and compares against
   what the broker actually paid. Currently: 108 round trips, median difference
   0.004 AUD, total within 0.3%. The residual is broker FX conversion
   timing/markup, not the P/L formula.

Gate 3 is the one that earns trust, and it found real bugs: deals must be paired
by `position_id` (this is a hedging account, so FIFO matching pairs an entry with
somebody else's exit), and daily conversion rates were too coarse — hourly cut
the worst error from 1.27 to 0.59 AUD.

### Reading a result

`runs/<id>/` holds `trades.csv`, `equity.csv`, `by_year.csv`, `metrics.json` and
`config.json` — the last with a hash of the input files, so a number can always
be traced to the history that produced it. `runs/summary.csv` is one row per run.

**Swap dominates these instruments.** Holding gold long costs ~83 points per lot
per night and MT5 exposes only *today's* rate, so a 20-year overnight backtest is
largely a statement about assumed carry. Always run both `--no-swap` and the
default and treat the pair as a range, not a number.

## History for backtesting

`tools/mt5_download.py` exports bulk history straight from the terminal (read
only — `copy_rates_*` and `copy_ticks_*`, nothing else). It talks to MT5
directly rather than through the web bridge, because the bridge serves a live UI
(60-second tick windows, capped bar counts) and holding its lock for a
multi-gigabyte pull would stall the page.

```bash
python tools/mt5_download.py --probe                       # what history exists
python tools/mt5_download.py --bars --years 20 --tf 15m,1h,4h,1d
python tools/mt5_download.py --ticks --days 600
python tools/dataset.py                                    # what is on disk
```

Everything is resumable: one file per chunk, existing chunks are skipped, and a
partial write lands on `.part` before being renamed.

```
data/bars/<SYMBOL>/<TF>/<YYYY>.csv.gz      ts,open,high,low,close,tick_volume,real_volume,spread
data/ticks/<SYMBOL>/<YYYY-MM>/<DD>.csv.gz  ts_ms,bid,ask,last,volume,flags
data/manifest.json                         coverage, server offset, terminal build
```

`data/` is git-ignored — it is large, and it is broker data.

### Reading it back

```python
from tools.dataset import load_bars, load_ticks, resample

bars = load_bars('XAUUSD.a', '15m', '2019-01-01', '2024-12-31')   # DataFrame
h4   = resample(load_bars('USDJPY.a', '1h'), '4h')                 # aggregate up
for day, ticks in load_ticks('XAUUSD.a', '2026-08-01', '2026-08-31'):
    ...            # a generator: a year of gold ticks is ~100M rows
```

### Two things to know before you trust a backtest built on this

* **Timestamps are broker SERVER time, not UTC.** The offset shifts with the
  broker's DST, so correcting 20 years with a single constant would be wrong for
  half of it. The raw server time is stored, the offset measured at download is
  in the manifest, and loaders return a `server_time` index so nothing quietly
  pretends otherwise.
* **A bar file covering a year does not mean that year has bars at that
  timeframe.** MetaTrader answers a 4h request for a period it has no 4h history
  for by returning what it does have, so `XAUUSD.a/4h/2010.csv.gz` exists and
  looks ordinary while holding 260 rows -- one per trading day, not six. Gold
  intraday on this feed begins in **2016** (15m in 2018); USDJPY and EURUSD are
  dense from **1999** at every timeframe. This cost a whole out-of-sample
  conclusion once: a gold 4h result "validated" over 2007-2020 had in fact run
  Donchian-20 as a 20-DAY channel for eight of those years. Check bars-per-year
  before trusting a span:

      python -c "import sys; sys.path.insert(0,'.'); from sim.instruments import load; \
        b=load('XAUUSD.a','4h','1990-01-01',None); print(b.groupby(b.index.year).size())"

  A full year is roughly 1500 bars at 4h, 6000 at 1h, 24000 at 15m, 260 at 1d.
* **Tick history is far shallower than bar history.** Bars go back to 2001 on
  this feed; ticks only exist for roughly the last 18 months. For anything older
  than that, use M1 bars as the intrabar proxy (`--bars --tf 1m`) and resolve
  ambiguous bars pessimistically — a bar whose range contains both stop and
  target counts as the stop.

## Broker tickers

Brokers suffix their symbols — Pepperstone serves `EURUSD.a`, not `EURUSD`. The
bridge resolves a plain name by prefix, so either works, but the page adopts the
broker's real ticker as soon as it learns it: the watchlist is reconciled against
`/symbols` at startup (shortest match wins, so `EURUSD` → `EURUSD.a`), and a
chart renames itself from the ticker the bridge returns. Drawings therefore end
up saved under the broker's name, matching your terminal.

## Known limits

* MetaTrader only serves history it has already downloaded. If a chart reports
  no history, open that symbol and timeframe once in the terminal.
* A first deep request for a symbol/timeframe makes the terminal download that
  history, which can block for a minute — measured on a live feed, 38 years of
  weeklies took over 60s cold and 312ms once primed. Bar requests therefore get
  a 90s timeout, the chart says what it is waiting for, and bar counts are
  capped per timeframe (`BAR_COUNT` in `js/main.js`) so nothing asks for decades
  of weeklies it will never draw.
* `/calendar` and `/cot` reach out to public sources through the bridge and are
  empty in `--mock` mode.
* The forming bar is advanced from `/quotes` (bid/ask mid), so its volume only
  counts ticks seen since the page opened.
* Sync with MT5 is POLLED, not pushed: quotes every 1s, account every 2s, panels
  every 4s, deals every 30s, pausing while the tab is hidden and catching up on
  `visibilitychange`. There is no WebSocket, and adding one would not remove the
  polling -- MT5's Python API has no event callback, so `symbol_info_tick()` is
  a poll whatever wraps it. A server-side stream would move the polling off the
  browser and cut latency to whatever interval the bridge chose; it would matter
  for tick-level work and is invisible at the 15m-and-above resolutions
  everything here was measured on.
* A snapshot exports what is RENDERED. Studies in separate panes are included
  because they are on the same canvas; the side rails and footer are not.

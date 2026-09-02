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
| Donchian rule | The validated rule's plan on the focused chart: entry, the 2-ATR risk block, the levels in the way, and the exit that fires |
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

**AND THE LEFT EDGE WAS NEVER BOUNDED BY THE DATA.** The two fixes above made
the fetch work, but the pan clamp underneath them read

    clamp(right, span * 0.4, bars.length + span * 0.6)

-- a lower bound expressed in SCREENS, which says "you may always scroll 60% of
a screen past the oldest bar". At the default 350-bar span that is 210 empty
columns with real candles still beside them, so it passed for headroom. After
`fitAll` a screen IS the whole dataset, and the same rule bought 60% of the
entire history in blank space. Measured on 15m gold: pan left from a fitted view
and `i0` reached **-360** with 2120 columns on screen; on a wider fit it passes
-1200. The fetch was firing correctly the whole time and simply could not
outrun a drag.

The bound now comes from the data. `_clampRight` puts the oldest bar at the left
edge and stops there; when the span is wider than the history it stops
immediately, which is right -- everything is already on screen.

That clamp would have made `fitAll` a dead end on its own, because the fetch
trigger needs the right edge to move back and at the wall it cannot. So pushing
INTO the wall is recorded as `wantsHistory` and main.js treats it as the request
it is, and `_paint` reports it even though the edge did not move. Verified on
15m/1h/4h/1d and on XAUUSD and EURUSD: `i0` never goes below 0, and at the
20,000-bar ceiling the view simply stops on the oldest bar.

`MAX_SPAN` went 4000 -> 4800 so `fitAll` can still frame a chart that has
extended twice. Beyond that a bar is under half a pixel and candles stop being
candles, so the cap is a rendering limit rather than a data one: scrolling still
reaches everything loaded.

### The frozen cost model was wrong about gold

`tools/session_screen.py` re-ran DISPLACEMENT_V1 on XAUUSD charging each trade
its OWN spread -- the `spread` column has been in every bar file since the
download and nothing had ever read it. The experiments charge a frozen constant
instead.

    XAUUSD 1h        frozen friction  0.100 R
                   measured friction  0.051 R

The frozen model is `spread/stop_price + 2*slippage_atr/stop_atr`, and it is not
wrong as a formula -- it was calibrated where the project's other instruments
live. Gold's spread is 5-8 points against a 1 ATR stop of several dollars, so its
spread term is 0.011 R where a major's is several times that. Charging gold a
major's costs overstated its friction by roughly **two to one**.

With real costs the same trades read differently:

    XAUUSD 1h, 687 trades, 2011-2026
        gross                          +0.074 R
        friction (measured, per trade)  0.051 R
        net                            +0.023 R   CI [-0.086, +0.132]
        net, entering at the next open +0.053 R

Still not distinguishable from zero, and the caveat stands. But the project's
standing summary -- "gross 0.05-0.17 R against friction 0.07-0.24 R" -- was
charging this instrument twice what it costs, and every conclusion about gold
that leaned on the gap was leaning on the wrong number.

### Sessions: an hour effect that survives every test I could put to it

The reason for the re-run was a cost argument: gold's spread barely moves across
the day while its bar range varies 3.7x, so the same trade costs 5.3x more per
unit of move at 23:00 than at 16:00, and ATR normalisation smooths exactly that
cycle. The prediction was that London/NY entries would clear costs and the
off-hours would not.

**That prediction was wrong, and so was the artefact hypothesis I replaced it
with.**

    session      n     net R      CI                win%
    asia       132    -0.071   [-0.320, +0.179]     33
    london     121    -0.137   [-0.385, +0.112]     31
    overlap    261    +0.028   [-0.145, +0.191]     36
    ny-late    131    +0.048   [-0.182, +0.298]     37
    off (21-24) 42    +0.666   [+0.236, +1.095]     57

The cheap hours are flat. The EXPENSIVE window is the one that pays, and 29 of
those 42 trades sit in the single hour 21:00 UTC at +1.019 R.

That is the shape of a fluke, so it was tested four ways:

  MULTIPLE COMPARISONS. A permutation test that shuffles the hour labels and
  records the best BUCKET each time -- so the null distribution is of the maximum,
  which is what selection actually produces. Observed best hour +1.019 against a
  null mean of +0.475 and a 95th percentile of +0.752: **p = 0.004**. Best
  session +0.666 against a null 95th of +0.378: **p = 0.002**.

  ERA REPLICATION. 2011-2020: +0.682 on n=26, CI [+0.105, +1.259]. 2021-2026:
  +0.641 on n=16, CI [-0.109, +1.389]. Same sign, same magnitude, independent
  windows; the second era's interval includes zero on sixteen trades.

  ROLLOVER ARTEFACT. My first suspicion, and it is wrong: the rollover is at
  23:00, not 21:00. Hour 23 is followed by a non-3600-second gap 100% of the time
  and a 0.126 ATR jump to the next open. Hours 20, 21 and 22 are ordinary bars --
  1.4% irregular spacing and a 0.003 ATR gap -- and hour 23 contributes 2 trades.

  ENTRY REALISM. Re-run entering at the NEXT bar's open rather than the signal
  bar's close, so the price is one anyone could have traded. Hour 21 is unchanged
  at +1.019, and the whole book improves from +0.023 to +0.053.

WHAT IT IS WORTH ANYWAY: 29 trades in eight years, about three and a half a year.
Every test above is computed on those same 29. A p-value cannot manufacture
sample size, and an effect that passes four checks on 29 observations is a lead,
not a result. It needs either more instruments -- which is the axis this
instrument-specific scoping deliberately gave up -- or a mechanism that explains
why the hour before the New York close should behave differently, and no such
mechanism has been found here.

### Cone-based trade screening: it does not rescue the edge

The cone is calibrated, so the obvious use is a filter: `DISPLACEMENT_V1` misses
its costs by a hair, and if some of its trades ask price to travel further than
it normally travels in the holding period, removing them should lift the gross
side without touching the detector. `tools/cone_screen.py` tests that on XAUUSD,
per timeframe, per era, with one threshold sweep applied to every cell rather
than the best threshold per cell.

THE FIRST VERSION ASKED THE WRONG QUESTION, and the answer was worth having. A
cone says where price is likely to BE in 96 bars; a trade with two barriers is
decided by which one it TOUCHES first. Screening on the endpoint kept 77-100% of
trades at every threshold and moved nothing -- because the 2.0 ATR target sits
around the **20-35th percentile** of the 96-bar move. The targets were never
ambitious. These trades do not fail by asking too much; they fail on the path.

So the screen was rebuilt as a first-passage race: for every bar, would this
geometry have touched its target before its stop, measured over bars whose own
race had already finished. Screening on that historical rate:

    cell               all trades           path >= 0.30
    1h 2011-2020   n=326 net +0.032   ->  n=280 net +0.057  CI [-0.114, +0.229]
    1h 2021-2026   n=340 net -0.085   ->  n=274 net -0.060  CI [-0.224, +0.104]
    4h 2011-2020   n= 76 net +0.034   ->  n= 61 net -0.038  CI [-0.382, +0.306]
    4h 2021-2026   n=105 net -0.100   ->  n= 83 net -0.095  CI [-0.420, +0.230]

**No threshold improves all four cells, and every interval spans zero.** Tighter
thresholds look spectacular and are not: `path >= 0.38` on 1h 2021-2026 reports
+0.580 R on 25 trades with a CI of [-0.020, +1.180]. That is the shape of a
finding that would not survive being asked twice, which is why every row here
carries a bootstrap interval -- without one, `+0.750` reads as a discovery rather
than as twelve trades.

XAUUSD'S THIRD ERA IS THIN. 1999-2010 has 3,165 4h bars against 8,851 and 8,723
in the later two, and produces 20 trades -- under the reporting floor. On this
instrument the replication axis is two eras, not three, and saying so is part of
the result.

### Limit at the line: settled, and negative

Killed twice by earlier sessions, finished here on XAUUSD across all three eras.
The geometry fix is real and insufficient:

    arm          rows     bars      net_R              CI (by bar)
    near        22643    10228    -0.2122   [-0.2320, -0.1917]
    limit       18553    10228    -0.1283   [-0.1567, -0.1046]
    per_op      22643    10228    -0.1051   [-0.1279, -0.0855]

    by era (per_op)   1999-2010  -0.1304   2011-2020  -0.1283   2021-2026  -0.0811
    by timeframe      1h -0.1095            4h -0.0917

Resting the entry AT the line instead of 0.20 ATR off it halves the loss --
-0.2122 to -0.1051 -- exactly as the geometry argument predicted. It is still
decisively negative, in every era and both timeframes, with intervals that
exclude zero everywhere.

And the adverse selection was as predicted, to the point. Breaks fill 90.3% of
the time, holds only 74.2%: the limit keeps every loser and misses a quarter of
the winners, so the hold rate among FILLED approaches drops from 52.2% to 47.3%.
Those 4.9 points are what the geometry gain is spent on.

### Elliott Wave Replay

`Backtest -> Elliott Replay`. Step a chart through history one bar at a time,
record what the counter believed BEFORE each bar existed, then reveal what
followed and score it.

IT IS A SANDBOX, NOT A MODE ON THE LIVE CHART, and that is the most important
thing about it. The first build drove the live chart directly -- sliced its bar
array to the cursor, moved the view, released the price lock, restored
everything on exit. It worked. It was still the wrong place: a tool for deciding
whether a strategy is worth anything must not be able to disturb the chart you
trade from, and "it restores correctly" is a promise that has to be re-earned
after every future edit. The cost of it being broken once is your working
layout.

The panels sit behind ONE MENU rather than a row of segments -- labels across the
top ate the width the panels underneath need, and the row would only grow (it has:
there are five now, `Strategy Replay` being the newest). `Components` is now `Download market data`, named for what it is used for;
the view key is unchanged.

A HOVER-OPENED MENU HAS TO CLOSE ON HOVER-OUT, which the shared `openMenu` does
not do: it closes on a pick or a pointerdown elsewhere, right for a menu you
clicked open and wrong for one that appeared under the pointer -- leaving the tab
left it standing over the chart until you clicked something. The tab and the menu
are one hover region with a gap between them (the menu is positioned 4px below
its anchor, so crossing that gap fires mouseleave before mouseenter), bridged by
the same 160ms grace the rail peek uses.

The menu hangs off the BACKTEST TAB, on hover, and THE TAB TAKES THE NAME of the
panel it is showing -- `Runs`, `Elliott Replay`, `Strategy Replay`, `Download
market data` -- so the strip says which of the five is up. It falls back to `Backtest` whenever that
panel is not what you are looking at: another tab selected, or the panel
collapsed and nothing being shown at all.

That is the whole navigation now. The panel's own toolbar is gone: it held a
duplicate view picker sitting directly under the tab that offers the same list,
a line of index metadata, and a ⟳ button. The button was a manual step for
something the page can see for itself, so `runs/index.json` re-reads on a timer
and re-renders only when `generated_utc` or the run count has actually changed
-- a periodic re-render would reset scroll and drop a half-typed symbol every
fifteen seconds. It skips the re-render entirely while the replay is open, which
owns a chart and a cursor that a remount would throw away.

One routing note that keeps the label honest: the tab-click handler used to
toggle the `active` class inline. It goes through `paintTabs` now, because that
is also what writes the label -- a second place that repaints the strip is a
second place that can forget to.

THE TRANSPORT IS PLAY / BACK / STOP, not a row of nudge buttons. Watching a count
evolve is what this view is for, and clicking `next bar` two hundred times is not
watching it -- play runs the cursor and you read the panel as it changes, at
0.5x to 4x. Single stepping stays on `,` and `.` (space toggles play), which is
where it belongs: the fine adjustment, not the main gesture.

Two bugs the transport turned up, both from the panel REMOUNTING on every render
-- the Backtest tab rebuilds its DOM, and the same instance is mounted into the
new host so the cursor and the beliefs survive. Anything bound to the document
has to be released first: without that the key handler stacked one listener per
render, so `.` stepped two bars and space played then immediately stopped itself.
And `e.target?.matches?.()` is not defensive noise -- a key event delivered to
the document has the document as its target, `document.matches` does not exist,
and the handler threw where it looked like it was doing nothing.

The belief panel sits beside the chart at 244px, which leaves the chart at 1277px
against the live chart's 1249px -- the tab spans the full window while the live chart
gives up both rails, so the two come out within a few pixels of each other
without arranging it. That matters: a wave in 1231px of canvas and the same wave
in 940px are different pictures, and the point of the sandbox is to judge what
the live chart would show.

So the replay owns a `Chart` of its own, built into the Backtest tab and never
added to `app.charts`, with its own bars fetched separately. Its `onChange` is a
no-op -- and since `_persist()` is nothing but `this.onChange(this)`, that one
line is what makes a bare Chart genuinely inert: it writes no workspace key,
touches no saved span, no timeframe, no drawing. `js/main.js` and `index.html`
do not know the file exists.

THE FUTURE IS REMOVED, NOT HIDDEN. Stepping re-slices the series to the cursor
and hands the slice to `setData`, so the overlays and the counter see an array
whose last bar IS the cursor. A masking flag would have meant every consumer had
to remember to honour it, and the one that forgot would leak the answer quietly,
in the direction that flatters the model.

Two things the sandbox still has to get right, both found by testing the first
build rather than by reading it. The price lock is released on every step: a
dragged price axis pins the vertical range and walking the cursor back takes the
bars straight out of it -- the lock held 4569-4714 while the replay bar closed at
4365 and the chart drew nothing at all. And `Reveal` parks the right edge just
past the scoring horizon rather than at the live edge, which is the one place the
comparison cannot be made -- the count, its invalidation line and its target zone
were all a thousand bars off screen.

#### One chart, two halves — and the structure by degree

The chart holds the WHOLE series. Left of the dashed marker is what the engine
could see; right of it, washed, is what happened. The count, its projection and
its invalidation line are drawn from the left half only.

The separation that matters is not the visual one. The belief is computed from
`full.slice(0, cursor + 1)` and never from the array the chart is holding, and
those two lines sit next to each other in `_apply` so they cannot drift.
Drawing the future costs nothing as long as nothing that produces a CLAIM can
read it. This was briefly two stacked charts, which bought the same guarantee at
the price of half the height and two price scales to reconcile by eye.

STRUCTURE BY DEGREE. Elliott is self-similar, so a count is only a claim once you
say at what degree -- the same three bars are wave 5 of a 15m impulse and noise
inside a D1 wave 2. Four rows read the same instrument at 1D, 4H, 1H and 15M,
each cut to the cursor's INSTANT:

    tf    reads                  bias   weight
    1D    wave 4 correction      down     37%
    4H    wave 5 continuation    down     48%
    1H    wave 5 continuation    up       52%
    15M   wave 5 continuation    up       46%
    1 of 4 degrees read up — divided

Cut by time, not by index: with the cursor at 2026-06-12 19:00 the 1D row ends
on 06-11 21:00, the 4H row on 06-12 17:00 and the 1H row on 06-12 19:00. Slicing
all four by the same bar COUNT would have let the macro row read tomorrow.

The fetch depth is derived rather than fixed. 3000 bars is 31 days of 15m and
twelve years of D1, so a cursor two months back had a full daily series and an
EMPTY 15m one -- and the execution row read "no count", which looks like the
counter having nothing to say rather than the data not going back far enough.
Each degree now asks for the distance from the cursor to now in its own bars,
plus a warm-up, and deepens itself if a row still comes up short.

THE % IN THE PANEL IS NOW THE TARGET'S REACH RATE, not the count's share. The
share was one scoring function's weight with nothing behind it -- measured, its
confidence was inverted, and weighting a cone by it cost 33 points of coverage.
The reach rate is the same visual slot filled with something checkable: how often
price actually travelled that far, in that direction, within the horizon,
measured on windows that closed before this bar. Each degree measures against its
OWN series and horizon, because a 4h target is not a 15m target expressed in
different bars.

WHAT THIS PANEL DELIBERATELY DOES NOT PRINT is a line like `Next 1H -> bullish
67%`. That number has been measured on this instrument at these timeframes and
it has no skill: fitted walk-forward and honestly calibrated it collapses onto
the base rate and still scores a shade worse than ignoring the chart. So each
row shows what the count IS -- direction, wave, the level that refutes it -- and
the weight is labelled as a weight. The direction is worth reading because it is
falsifiable at the invalidation price. The percentage is not.

#### Recording a replay, and snapshotting one

`⏺ Rec` records the replay as VIDEO. Press it, play, press it again, and the
file lands in **`data/replays/<symbol>_<tf>_<stamp>.mp4`** through the dev
server. A browser download would go wherever the browser puts downloads, which
is not the project, and the point of a recorded replay is that it sits beside the
runs it will be compared with.

`MediaRecorder` records a canvas, and half this view is HTML -- so a COMPOSITE
canvas is drawn on every animation frame: the chart's own canvas copied in, the
belief panel painted beside it by the same routine the PNG snapshot uses, in the
app's dark palette rather than the PNG's light one. Recording the chart canvas
alone would have been one line and would have produced a video of some candles
with no count beside them.

EVERY FRAME CARRIES THE BRAND HEADER -- the mark, `DiaNurFx`, the instrument and
timeframe, and the as-of bar and its timestamp right-aligned. A recording gets
shared, and a shared frame with a wave count on it and no header is a chart from
anywhere. The mark is drawn from the same 32-unit artboard as the favicon and the
chart's own export stamp, so the three cannot drift. It sits in a band ABOVE the
chart rather than over it: the top-left of the plot is where the count's labels
appear, and a logo there covers the thing the video exists to show.

The composite is forced to EVEN dimensions. H.264 subsamples chroma 2x2, and an
odd width or height makes the encoder pad or refuse -- a 1579px composite fails
silently at `start()`.

#### Which video format

Measured here, not assumed: the same 8-second take of this view (1578x618),
recorded through `MediaRecorder` at a 2500 kbps cap.

    codec                  fps   bytes      note
    H.264  avc1  (mp4)     15    606,556    under-runs the cap on static content
    AV1    av01  (mp4)     15    568,215    6% smaller, CPU-heavy to encode
    VP9          (webm)    15  1,114,765    spends the whole budget
    VP8          (webm)    15  1,304,871
    H.264  avc1  (mp4)     25    922,143    +52% for frames that are duplicates
    H.264  avc1  (mp4)     10    323,557    at a 1200 kbps cap

Two things fall out of that. THE FRAME RATE IS THE REAL LEVER, not the codec:
this is a screencast of a chart that only changes when the cursor steps, so most
frames are duplicates, and 25 -> 15fps saves a third with nothing visibly
different. And the codec comparison at a fixed cap measures how each encoder
SPENDS its budget rather than its efficiency -- VP9 is not worse than H.264 in
general, it simply took the bitrate it was offered.

**The recommendation is H.264 in MP4, which is what this records by default.** It
is the only format that is genuinely universal -- browsers, phones, VLC, Office,
Slack, WhatsApp, every editor -- and on this content it is also the second
smallest of the five. AV1 is 6% smaller and much better at matched quality in
principle, but it will not open in most editors or on older phones, so it is not
the universal answer; VP9/WebM is neither smaller here nor as portable. If a file
needs to be smaller, drop the frame rate before changing the codec.

Defaults are `FPS = 15` and `BITRATE = 2.5 Mbps`, which is ~75 KB/s -- about 9 MB
for a two-minute replay.

MP4 IF THE BROWSER WILL, WEBM OTHERWISE. H.264 in MediaRecorder is recent and not
universal, and asking for a container the browser cannot make throws at
`start()`. The type comes from `isTypeSupported` and THE EXTENSION FOLLOWS WHAT
WAS NEGOTIATED, not what was asked for: a file named `.mp4` holding a WebM stream
is worse than a `.webm`. Chromium here produces a fragmented MP4 (`ftyp`/`moov`
then `moof`+`mdat` pairs), which plays and seeks in browsers and in VLC; a few
older editors want a flat MP4 and will ask you to remux.

Data arrives in one-second chunks so a crash costs a second rather than the take,
and `rec` is held until `onstop` fires -- the last chunks arrive after `stop()`
returns, and clearing it earlier drops the tail.

A small JSON sidecar is written beside the video with the same basename: the
ordered list of beliefs, each trimmed to what a later analysis reads, with the
score included so the file is self-contained. It is ~40KB against the video's
megabytes and it is the half that can be queried -- a video cannot tell you what
the count claimed at bar 1804.

`⤓ PNG` saves the chart AND the panel as one image. The live chart's snapshot is
canvas-only, which is right there -- the whole picture is on the canvas. Here
half the picture is HTML, and an image of a wave count without the count that
produced it is a picture of some candles. The panel is REDRAWN onto the canvas
rather than screenshotted: there is no DOM-to-image in this project, adding one
to satisfy a caption would be a dependency, and the panel's content is three
arrays this file already holds. What the image says is therefore what the code
believes, not what a rasteriser made of the stylesheet. The uncalibrated-share
caveat is drawn INTO the image, because a screenshot outlives the session that
made it and a percentage in a picture reads as a probability unless the picture
says otherwise.

    POST /record?name=x.mp4  ->  raw bytes, written byte for byte
    POST /record             ->  {name, payload}, written as JSON
    GET  /records            ->  what is on disk

Video is streamed to disk in 1MB reads rather than held whole in memory, and is
never decoded: the browser has already produced a finished container, and
re-encoding it here would be work with no purpose and one more thing to get
wrong.

The name is sanitised even though this is a dev server on 127.0.0.1: a write
endpoint that accepts `../` can leave the folder it was pointed at, and there is
no reason to allow it. `os.path.basename` alone would not do it on Windows,
where a backslash is also a separator.

#### The forecast cone, and the one number on this chart that is honest

A projected path is a claim about where price will be to the tick, and nothing
here supports that. A CONE is a different kind of object: at this bar, over the
history available at this bar, how far did price actually get 1, 2, ... n bars
later? That is a fact about the instrument, and unlike everything else in this
panel it can be CHECKED -- a nominal 80% band should contain the outcome about
80% of the time.

Measured on XAUUSD 4H, 510 cones, 2018-2026:

    horizon   coverage 80%   coverage 50%   width 80%   interval score
       +1         79.0%          48.4%       1.49 ATR       2.65
       +2         78.6%          48.0%       2.22 ATR       3.81
       +4         80.8%          50.4%       3.31 ATR       5.32
       +8         78.2%          47.8%       4.88 ATR       7.85
      +16         81.4%          47.8%       6.95 ATR      10.93
      +24         77.1%          48.6%       8.57 ATR      13.93

Nominal 80 delivers 77-81, nominal 50 delivers 48-50, at every horizon out to a
day and a half. **This is the first forecast object in the project that says
what it means.** The wave count's shares miss by up to 65 points; the cone misses
by two.

Two rules make that possible. Every sample runs to `upto - ahead`, so the widest
step is built from returns that had already finished at the cursor -- sizing a
cone from the dispersion of the bars it is drawn over is the future leaking in
through the one number nobody would check, and `tests/test_elliott.py` appends a
violent future to a calm history and demands the cone not move. And returns are
divided by the ATR at each HISTORICAL bar and multiplied by the ATR now, so a
quiet decade and a violent one contribute the same shape.

Coverage is reported with WIDTH beside it, and the interval score
(Gneiting-Raftery) is what the two are judged on together. A band from -10 to
+10 ATR has perfect coverage and says nothing; the interval score charges for
width and adds a penalty for how far outside the band the outcome fell, so it
cannot be bought by widening.

#### Conditioning on state did not earn its keep

The obvious next step was to condition the cone on the current state -- trend,
where price sits in its recent range, how wide that range is, momentum, and the
volatility regime -- and take the quantiles of the nearest 400 historical
analogues instead of all 1,716 candidates.

    horizon   conditional cov80 / width   unconditional cov80 / width
       +1        78.6%  /  1.48 ATR          79.0%  /  1.49 ATR
       +4        80.6%  /  3.29 ATR          80.8%  /  3.31 ATR
       +8        79.0%  /  4.88 ATR          78.2%  /  4.88 ATR
      +24        77.6%  /  8.63 ATR          77.1%  /  8.57 ATR

Within a point on coverage and within 0.06 ATR on width at every horizon. Those
state features carry no information about DISPERSION on this cell -- the same
answer from a quarter of the samples, which is the worse of the two. So the
unconditional band is the one drawn, and that is a measurement rather than a
preference. The conditional path is kept in `js/chart/cone.js` so a better
feature set can be dropped in and re-scored rather than re-argued.

#### The scenario mixture, and what a bad classifier costs

The idea is the right one to test: a wave-3 continuation and a wave-4 correction
are different futures, so blending them into one band before looking throws away
the only thing the count claims to know. So `scenarioCones` builds a cone per
scenario from the historical bars that RESOLVED into that scenario -- read in the
current count's directional frame, bucketed at the same +/-0.75 ATR the scorer
uses -- and combines them by the count's own weights.

MIXING QUANTILES IS NOT AVERAGING THEM. The P10 of a mixture is not the mean of
the components' P10s; that identity holds by coincidence at best, and using it
gives a band that is too narrow exactly when the scenarios disagree. The mixture
is built by pooling the samples at per-class weights and taking WEIGHTED
quantiles of the pool, which is the mixture distribution by construction. A test
pins it: two components at -10 and +10 mix to a band spanning both, while
averaging their quantiles collapses to the empty middle, a band **thirteen times
narrower** than the truth.

Measured on XAUUSD 4H, 510 forecasts:

    horizon   plain cone           mixture (count weights)   mixture (equal weights)
       +1     79.0% / 1.49 / 2.65    77.5% / 1.46 /  2.70      79.6% / 1.48 /  2.65
       +4     80.8% / 3.31 / 5.32    76.1% / 3.10 /  5.57      80.2% / 3.24 /  5.30
       +8     78.2% / 4.88 / 7.85    66.7% / 4.29 /  8.55      77.6% / 4.69 /  7.82
      +16     81.4% / 6.95 /10.93    59.6% / 5.20 / 13.21      78.0% / 6.49 / 10.88
      +24     77.1% / 8.57 /13.93    43.5% / 5.02 / 20.52      73.9% / 7.85 / 14.01

                        coverage 80% / width in ATR / interval score

**The mixture is worse, and worse the further out it looks.** At 24 bars its 80%
band holds the outcome 43.5% of the time -- it narrowed from 8.57 to 5.02 ATR and
bought nothing but confidence. The interval score, which charges for exactly that
trade, goes from 13.93 to 20.52.

The equal-weight column is why this is a finding about the WEIGHTS rather than a
bug in the mixing. Give the three scenarios 1/3 each and the mixture lands back on
the plain cone at every horizon, within a point of coverage. The machinery is
correct; the damage arrives entirely with the count's probabilities.

What it costs is now quantified. Conditioning on a class and then weighting the
classes with a classifier that has no skill inherits the SHARPNESS of the
conditioning without the accuracy -- a textbook failure, and at 24 bars it is
worth **-33.6 points of coverage**. That is the strongest argument yet for not
shipping the count's shares as probabilities anywhere.

So the drawn band stays the plain one. `scenarioCones` is kept, tested and ready:
the day the counter earns positive skill, the mixture is one weight vector away
from being the better cone.

#### The projected path, drawn before the bars exist

A wave LABEL cannot be scored. "Wave 3" is a name, not a forecast. What can be
scored is a PATH: at bar +8 the count expects this price, at +14 this one, at
+20 this one. So each count projects its remaining legs forward into the empty
space right of the cursor, dashed and labelled, and the view is widened to hold
them -- parked on the last bar the forecast would be off screen, which is the
one thing it exists not to be. Step forward, or press `Reveal`, and the actual
bars arrive on top of it.

PRICE comes from the fib relationships the guidelines are already stated in, so
nothing new is invented. Wave 3 is projected to 1.618 of wave 1 rather than the
middle of its 1.618-2.618 zone: the lower bound is the one the guideline names,
and projecting to the middle of a range is quietly optimistic.

TIME is the weak half and is labelled as such. It comes from the median duration
of this count's own completed legs, because Elliott says a great deal about
proportion in price and almost nothing testable about proportion in time. The
scoring reports PRICE error against the path and does not pretend the timing is
a claim.

The path is scored separately from the direction, because they are separate
questions -- a count can call the direction and still be nowhere near the prices
it projected. Over the same 556-belief sweep on XAUUSD 1H the projected path
misses by a MEDIAN of 2.78 ATR (median, not mean: a few counts project into a
gap and miss by twenty, and an average would report on those rather than on the
typical case).

#### What the counter does, and what the numbers are not

Three HARD RULES drop a count rather than penalise it -- wave 2 past the start of
wave 1, wave 4 overlapping wave 1, wave 3 the shortest of 1/3/5. Everything else
(fib retracements, the wave-3 extension, alternation between 2 and 4) is
evidence, shown as ticks and crosses beside the primary count so the panel says
WHY it prefers a reading rather than only that it does.

Each surviving count carries an INVALIDATION price. That is what makes it a
claim: a count with no level that would refute it is a description of the past,
and `tests/test_elliott.py` fails the build if one appears without it.

**`share` is not a probability and the panel says so.** It is one scoring
function's weight, normalised across the surviving counts, with nothing behind it
yet. Printing `62%` from a softmax and calling it a probability is exactly the
failure this codebase exists to avoid. Turning it into one means measuring how
often a count with share s was the one that survived -- which is what the log is
for.

#### Calibration and repainting

Accuracy answers "how often is the top pick right", and a model can score well on
it while its numbers mean nothing -- always saying 99% and being right 60% of the
time is 60% accurate and badly wrong about itself. `Score whole series` therefore
also reports the two questions that decide whether the shares may EVER be printed
as probabilities.

CALIBRATION buckets each forecast by the confidence it claimed for the class it
named, against how often that class then happened. On XAUUSD 1H, 556 forecasts,
24-bar horizon:

    claimed      n    said   happened    gap
    0-35%        4     35%        0%    -35pp
    35-45%      89     40%       24%    -17pp
    45-55%      84     49%       21%    -28pp
    55-65%      12     63%       50%    -13pp
    65-75%     249     70%       34%    -37pp
    75-100%    118     90%       25%    -65pp

    Brier 1.0548 vs climatology 0.6353 · skill -66.0%

Every bucket is overconfident and the overconfidence GROWS with the claim: the
75-100% bucket says 90% and delivers 25%. The Brier score is quoted against
climatology -- the same score for a forecast that ignores the chart and always
predicts this sample's base rates -- and loses to it by 66%. A model that cannot
beat counting outcomes has told you nothing you did not already know. This is
the measurement that keeps the word "probability" off the panel.

REPAINTING is the other thing a replay can see and a finished chart cannot. A
finished chart always looks right, because the count was fitted to it; what
matters is whether the count you were shown at the time kept being replaced.

    primary reading changed on 37% of steps (sampled every 5 bars)
    changed DIRECTION on 13%
    median run 10 bars · longest 80 bars

The direction flips are the ones that matter: relabelling wave 3 as wave 5 is
bookkeeping, but reversing the direction is the change that would have reversed a
trade. Thirteen percent of five-bar steps is a reading that turns over roughly
every 38 bars.

Both metrics are tested against forecasts that are true by construction --
`tests/test_elliott.py` builds a set where the claimed probability IS the outcome
frequency and demands a zero gap, and an overconfident set that must report as
overconfident. A calibration metric that is wrong in the flattering direction
would otherwise be indistinguishable from a model that works.

#### Fitting the weights, and why that settles it

The flat guideline weights were the obvious suspect: the scorer counts satisfied
guidelines and weights them equally because there was no evidence for any other
weighting. So `tools/elliott_fit.mjs` learns them instead -- XAUUSD only, per
timeframe, walk-forward, on the app's own `countFrom` rather than a
reimplementation.

Three things had to be right for the answer to mean anything. Weights that
predict bar i are fitted only on samples whose outcome was already KNOWN at i,
which is `i - horizon`, not i. Each timeframe is fitted alone -- pooling was the
confound that produced a +0.784 correlation out of nothing earlier in this
project. And the temperature is fitted on a held-out slice the weights never saw,
so the model is not calibrating itself against its own training error.

XAUUSD 1H, 2015-2026, 12,146 settled forecasts, 24-bar horizon:

    scorer                              acc    baseline   Brier   climatology   skill
    flat guideline weights             0.309     0.404   1.0178      0.6373    -59.7%
    fitted, per-count head             0.373     0.404   1.0068      0.6373    -58.0%
    fitted, softmax over classes       0.372     0.404   0.8883      0.6373    -39.4%
    fitted softmax + temperature       0.382     0.404   0.6604      0.6373     -3.6%

Each row fixes a real defect in the row above. The per-count head learns which
guidelines matter and lifts accuracy from 0.309 to 0.373. The softmax removes the
inflation caused by normalising three independent probabilities into a
distribution. Temperature scaling -- T came out at **5.75**, so the raw model was
nearly six times overconfident -- removes the exaggeration.

AND THE CALIBRATION BECOMES HONEST AND EMPTY. After scaling, 9,613 of the 12,146
forecasts fall in one narrow band that says 38.7% and delivers 39.3%. That is a
+0.6pp gap, which is excellent calibration, and it is calibration onto the BASE
RATE. The model has learnt what fraction of bars continue and stopped there.
Brier skill is -3.6%: still, marginally, worse than a forecast that ignores the
chart entirely.

All three timeframes land in the same place, which is what makes it a finding
rather than an artefact of one cell:

    cell           n        flat skill    fitted+temperature    acc / baseline      T
    XAUUSD 15m   31,087       -56.8%              -2.6%        0.372 / 0.396      5.65
    XAUUSD 1H    12,146       -59.7%              -3.6%        0.382 / 0.404      5.75
    XAUUSD 4H     2,993       -57.7%              -5.2%        0.379 / 0.414     11.15

Every cell: the flat scorer is catastrophically miscalibrated, fitting and
scaling repairs the calibration almost exactly onto the base rate, and the
repaired model is still a shade WORSE than ignoring the chart. Accuracy sits
2-4pp under the majority-class baseline in all three. The temperatures say the
raw model was six to eleven times overconfident depending on the timeframe.

**So the counter cannot be fixed by fitting its weights.** That is a stronger
statement than the first measurement was: the guidelines have now been given
learned weights, walk-forward, a proper multiclass head and a fitted temperature,
and the best any of it manages is to reproduce the base rate. Two readings
follow, and only the first is supported here -- these features, at this horizon,
on this instrument, carry no information about the next 24 bars. Whether a
different feature set, a different horizon, or a human reading the same chart
would do better is not something this measures.

#### The first measurement, and it is negative

The replay log makes the counter falsifiable, so the first thing to do with it
was falsify it. `Score whole series` walks every 5th bar rather than only the
bars you stepped through -- clicking `Next bar` two hundred times is a fine way
to watch a count evolve and a poor way to measure it, because the sample ends up
wherever you happened to stop. Primary count only, 24-bar horizon, outcome
bucketed at ±0.75 ATR:

    symbol      tf     n     accuracy   baseline   edge     invalidated
    XAUUSD      15m    556   32.0%      44.4%      -12.4pp  47%
    XAUUSD      1h     556   26.3%      44.1%      -17.8pp  57%
    XAUUSD      4h     556   35.4%      41.9%       -6.5pp  53%
    EURUSD      1h     556   30.2%      44.4%      -14.2pp  55%
    USDJPY      1h     556   25.7%      39.6%      -13.9pp  58%

The baseline is naming the most common outcome every time. The primary count is
BELOW it in all five cells, and roughly half its counts are invalidated by their
own stated level within 24 bars. Consistent sign across two instruments and three
timeframes, which is what makes it worth reporting rather than noise.

What that measures is THIS counter -- a rule-checked enumeration over confirmed
pivots with flat guideline weights. It is not a verdict on Elliott, and a human
analyst reading the same chart is doing something this does not model. It does
say that the count on the screen should not be traded as it stands, and that the
honest next step is calibration against the log rather than more label styling.

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

#### Channel history: removed

`liveChannels` detects at ONE bar, the last, so it answers "what corridors are
visible now"; a channel that formed and died 400 bars ago is not returned at
all. A `Channel history` toggle used to repeat detection back across the visible
window on the as-of grid and merge the duplicates structurally -- same direction
plus same `tEnd`, or spans overlapping >= 60%, which took 33 raw detections down
to 9 on 15m gold.

It is gone, along with its flag, its menu entry and the sweep. The merge was
sound and the cost was bearable (~15ms per detection, capped at 30), but a
corridor that ended 400 bars ago is a fact about the past that the chart was
being asked to carry forward, and none of the channel work replicated as a
signal in the first place -- direction carries no information about what price
does next over 29,208 samples. Drawing more of something that measures at zero
is not a feature. Live corridors only now.

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

### The status bar, and the Ask panel

The account numbers -- balance, equity, floating, margin free, margin level --
sat behind a `$` toggle in the tab row, default OFF. That made the one thing you
must never lose track of, how much room the account has left, the one thing
hidden by default. They now live in a permanent status bar in its own grid row
at the bottom edge, so it can never overlap the panel it reports on.

    Balance AUD 6,066.61 · Equity AUD 2,497.29 · Floating -3,569.32
    · Margin free -AUD 112.75 · Margin level 95.7%

COLLAPSED, THE TWO BARS BECOME ONE. A collapsed panel was a 34px strip of tab
buttons sitting directly on a 26px strip of account numbers -- sixty pixels of
chrome saying almost nothing, with two horizontal rules where the eye expects
the bottom of the app. Collapsing now MOVES the account cells into the tab row
at the right end and takes the status bar to zero height, so the tab strip
becomes the bottom edge.

The cells are RELOCATED, not duplicated. Two copies of a live number is two
things to keep in sync, and the id lookups that write them (`#acBal` and
friends) would silently feed whichever came first in the document. Verified:
one copy of `#acBal` in both states, same value, and the cells return to the
status bar on restore.

#### No handle for the bottom panel

It went from two glyph buttons to one chevron to none. The panel had `–` and
`□`, which made two independent-looking controls out of one three-state thing
and neither said which way a press would go; then a single chevron; and now the
gesture every docked panel already has:

    double-click the tab strip        collapse / restore
    ALT + double-click                expand / restore

Zero chrome. The strip takes the HAND cursor, not the two-headed resize arrow
it first had: nothing there is dragged, and an arrow promising a drag is a worse
lie than no cursor at all. Its `title` says which way the next double-click will
go -- set through `setSize` at startup rather than by assignment, because a hint
that only appears after you have already found the gesture is no hint.

DOUBLE-CLICKS ON THE TAB BUTTONS ARE IGNORED. A quick second click while
choosing a tab would otherwise collapse the panel out from under the choice --
the gesture and the tabs share a surface, so one of them has to yield, and it is
the one that did not ask for the click.

#### Ask: grounded, and it refuses

`js/ui/chat.js` answers two kinds of question and declines a third, and the
declining is the design rather than a limitation to apologise for.

    TERMINOLOGY     from a glossary written in this project, the same wording
                    the tooltips use, so the chat and the chart cannot disagree
    CURRENT STATE   by READING the live panels -- every number it quotes is one
                    already on screen
    NEWS, SENTIMENT declined

The panel sits on the status bar, beside the button that opens it, and GROWS
UPWARD. `bottom` is the fixed edge with `top` left auto, so increasing the
height moves the top edge up -- which is also what makes the drag extend it
upward instead of down through the status bar.

It opens exactly as tall as the footer, so its top edge lands on the tab strip
and nothing above is covered: measured 246x210, bottom 883 on a status bar at
883, top 673 against a tab strip at 674.

THE GRIP IS HAND-BUILT AND SITS TOP-LEFT, because `resize: vertical` can only
put its handle in the bottom-right corner -- which on a bottom-anchored panel is
the one corner that never moves. The grip belongs on the edge that travels, and
the width stays locked to the right rail rather than being a free choice.

    drag up 250px     210 -> 460, bottom unmoved
    drag up 2000px    clamped at 837, top edge on the top bar
    drag down 2000px  clamped at the 180px floor

The CEILING IS CAPTURED AT POINTERDOWN, from the bottom edge, and the first
version got that wrong in a way worth keeping: it derived the limit from
`offsetTop` inside the move handler, and `offsetTop` IS the top edge -- the
thing the drag is moving. The limit shrank as fast as the panel grew, so a
250px drag yielded 26.

`min-height` is 180px because `--bottom-h` drops to 34 when the panel is
collapsed, and a 34px chat box is not a chat box.

THE CHAT STOPS ABOVE THE ASK BUTTON IN BOTH STATES. Open, `bottom:var(--status-h)`
rests it on the status bar. Collapsed, the status bar is `display:none` and that
variable is 0 -- which first hid the button (measured 0x0, inside the hidden bar),
then, once the button had been rescued into the tab row, buried it under the
panel instead. Collapsed the chat rests on the tab row: a control you cannot see
is a control you cannot press to close the thing covering it.

THE ACCOUNT NUMBERS ARE RIGHT-ALIGNED IN BOTH STRIPS, so collapsing the panel
does not slide them across the screen. Their position is the one thing that
should not move when the layout changes underneath them.

HOVER A TAB PEEKS, CLICK PINS -- the same contract the two side rails use,
because the tab row IS this panel's stub: the part that stays on screen when it
is collapsed, carrying the names of what is inside, already where the pointer
goes.

THE TAB BUTTONS ARE THE HOVER TARGET, not the strip. Peeking asks a question --
"what is in History?" -- and the empty half of the strip does not ask it. Bound
to the whole strip, sweeping the pointer along the bottom of the window flashed
the panel up over the chart for no reason. The footer bar does nothing on hover
for the same reason; it still takes the click.

The peeked panel OVERLAYS the chart instead of growing the grid row, for the same
reason the rails overlay: widening a track on hover re-renders every canvas under
it, and a glance should cost a transform, not a repaint. It fades rather than
sliding off an edge -- this panel has no screen edge to slide behind, so a
translated copy would sit on top of the tab strip on its way out. The 140ms grace
window is the rails' again: leaving the strip to enter the panel fires mouseleave
before mouseenter, and without it the panel shuts under the cursor on the way in.

HOVERING A TAB PREVIEWS THAT TAB -- History shows history, Calendar shows the
calendar. A glance at a five-tab strip has five possible answers, so the button
under the pointer decides which one. The selection is NOT
changed: `peekTab` remembers what to go back to, nothing is written to storage,
and leaving restores it. A hover that silently re-pointed the panel would make
the pointer a mode switch. Clicking a tab commits the preview AND pins the panel
open, because a gesture that reads as choosing something should choose it.

One thing the preview must not do is resize the page. Backtest asks for the
expanded panel, and running that from a hover threw the layout to 72vh and back
as the pointer moved on -- and with Backtest as the pinned tab, the RESTORE
re-rendered it and un-collapsed a panel that had just been closed. The expand is
now skipped while previewing and while collapsed.

EITHER STRIP TAKES THE CLICK -- the tab row or the footer. They are the top and
bottom edges of the same panel, and collapsed they are stacked together, so
binding only one means guessing which band the pointer is in. Clicks landing on a
button are ignored, or choosing a tab would collapse the panel out from under the
choice. Alt-click still reaches the expanded state.

It started 380x520 and arrived ALREADY SCROLLED: the state read-back runs to a
dozen lines on its own, so the answer scrolled itself out of view the moment it
appeared.

There is no model behind the box and no news feed in this project. A chat that
answered "what is the sentiment, should I buy" would be inventing a market
narrative, which is precisely the failure this codebase spends 2,900 lines
guarding against. So it says it cannot, in its own orange style rather than in
the voice it uses for answers, and points at the Calendar -- the only
forward-looking data the app actually holds.

Refusals are matched BEFORE definitions, so "what is the sentiment and should I
buy?" gets the refusal rather than a definition of a term it happens to contain.

Verified live:

    "what is displacement?"    -> the glossary entry, bot style
    "what is the current read?" -> BULL 53/100, the three frames, structure,
                                   invalidation, the scorecard, the account
    "sentiment / should i buy"  -> refusal, orange
    "capital of France"         -> says it has no grounded answer

If an LLM is wired in later, the honest shape is a server proxy receiving this
same grounded state as context. Until then, being unable to answer is the
correct behaviour rather than a gap to paper over.

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

## The Donchian rule on screen

The one validated rule, drawn on the live chart and stepped in the sandbox. The
measurements behind every choice below sit beside the code they justify --
`js/chart/trailmode.js`, `js/chart/levels.js`, `logs/tp_struct_eval.txt`,
`logs/exit_trail_eval.txt` -- and what follows says what is drawn and why, not
what was hoped for.

**The rule.** Enter at the next open after a close through the 20-bar channel;
leave when a close crosses the 10-bar channel the other way, or on a 2-ATR stop
measured from the signal close. `paramsForTf` holds the horizon fixed in TIME
rather than in bars, so N=20 on 4h becomes 317 on 15m and 950 on 5m -- the same
3.3 days of history on every frame.

### There is no take-profit, and that is a measurement

Two separate measurements say so, and they say different things.

`tools/tp_sweep.py` asked what a FIXED cap costs on the cell the rule was
validated on: a 1R take-profit lifts the win rate from 36% to 49% and turns
**+43.7 net R into -2.1**. A trend rule is paid from the tail, and a cap is a bet
against the tail.

A second study then asked whether a STRUCTURAL target -- one placed at the next
real level rather than at a multiple of risk -- escapes that. Twelve cells out of
sample, four target variants against the plain trailing exit, pooled in
`logs/tp_struct_eval.txt`: every variant lands below the trail (structural -40.3
net R, fitted-R -9.9, the half-position versions -11.7 and -13.4) and every
interval spans zero. Nothing was demonstrated in the target's favour anywhere, so
the machinery came out of the walker and off every surface -- and the eval script
went with it, which is why that log is the record and there is nothing left to
re-run. `tools/tp_sweep.py` survives; it measures the fixed cap above.

So nothing on either chart is a price the rule exits at, except the stop and the
trailing exit.

`TP1 / TP2 / TP3` are named for what a reader recognises and captioned for what
they are -- **"in the way -- not targets"**. They are the levels price has to get
through: swing points, S/R zones, supply/demand bases, trendlines, and the
unbroken swing whose break would be the next BOS (`js/chart/levels.js`). They are
chosen ONCE, at the signal bar, from bars that existed then; levels price has
since touched are dropped as spent, and a trade that has run through all of them
is drawn as clear air rather than as an empty list. Three are shown, not four --
the fourth was the furthest, the least likely to be reached and the first to go
stale.

### The exit moves; the stop does not

Two things can end a trade, and the chart draws the one that will. The rule's own
channel exit is a window fixed in TIME -- on XAUUSD 5m it is 39.6 hours, so after
a fast move it sits wherever the high was a day and a half ago and cannot
compress faster than the clock. The structural trail puts the exit behind the
nearest S/R or swing instead, and the walker takes whichever of the two is
TIGHTER, so the trail can never loosen the rule's own exit. One violet dashed
line traces that effective level.

**The honest verdict on the trail is NOT DEMONSTRATED.** Against the channel it
looks strong in the recent era (+85.9 R) and indistinguishable in the earlier one
(+0.2 R). Against the control that decides it -- an ATR trail matched to the same
average distance from price, knowing nothing about the chart -- it is -49.1 R in
2016-2020 and +38.9 R in 2021-2026, both spanning zero. Every refinement that
made it more sensible as a stop made it harder to distinguish from a dumb one.
Nothing on screen says this: the verdict chip is earned on the channel exit, and
`js/chart/trailmode.js` is the only place that records the difference.

### Entry filters: eleven gates, none of them real

Eleven entry gates were measured across eight cells and two eras -- headroom to
the next level, thrust, ADX, EMA regime, and combinations -- each against a
random gate held to the SAME retention rate. None beat its matched coin flip. The
finding that did replicate is about WHERE, not WHEN: XAUUSD is positive in both
eras (+51.9 / +72.2 and +57.4 / +49.5), USDJPY positive in both, EURUSD negative
in both, GBPUSD flips sign. **The edge is the cell, not the signal.**

### Why 5m looks empty, and what a sweep said about it

A 5m gold chart is full of moves and the rule sits flat through nearly all of
them. That is the horizon, not a detection failure: at N=950 the channel measured
349 points wide while the whole preceding day spanned 105, so nothing on the
screen came close to the edge of it.

`tools/horizon_5m_eval.py` re-asked the question properly -- eight durations from
the literal 20/10 (100 minutes) to the shipped 3.3 days, four symbols, both eras,
about 740k 5m bars per symbol, paired on calendar blocks. Pooled net R against
the shipped horizon, full output in `logs/horizon_5m_eval.txt`:

| horizon | N | 2016-2020 | 2021-2026 | verdict |
|---|---|---|---|---|
| native 20/10 | 20 | -3616 R [-4493, -2669] | -3672 R [-4506, -2788] | worse in both |
| 2h | 24 | -3345 R | -3381 R | worse in both |
| 4h | 48 | -2117 R | -1804 R | worse in both |
| 8h | 96 | -846 R | -597 R | not demonstrated |
| 12h | 144 | -581 R | -285 R | not demonstrated |
| 24h | 288 | -29 R | +246 R | not demonstrated |
| 48h | 576 | +156 R | +63 R | not demonstrated |
| **79.2h (3.3d)** | **950** | baseline | baseline | **ships** |

**The row a reader's eye asks for is the worst one.** Native 20/10 takes 21,071
trades on gold alone and loses by more than three thousand R in both eras, with
intervals nowhere near zero. Nothing beats 3.3 days in both eras, and neither row
that leads on a point estimate survives being picked in one era and read in the
other: 48h is +156 R where it was chosen and +63 R [-207, +341] where it was not;
24h is +246 R where chosen and -29 R [-486, +395] where not.

And all of it is gross. On XAUUSD 5m the 18-point spread is **0.122 R per trade**
in 2016-2020 and **0.061 R** in 2021-2026 -- median ATR 0.74 and 1.48, so a 2-ATR
stop is 1.47 and 2.96 wide. The short-horizon rows take 10-20x the baseline's
trades and owe hundreds of R these numbers never pay. Even the best row on gold,
24h, is only about +0.09 R per trade gross before roughly half of that goes to
the spread on its extra trades.

So 5m keeps the 3.3-day channel and keeps being quiet. The silence is the rule
working, not the rule failing to see.

### And the same question on every other frame

`tools/horizon_eval.py` generalises that sweep: a ladder of **multiples of the
shipped duration** -- 0.1x, 0.25x, 0.5x, 2x, 4x -- plus the literal 20/10, on
5m / 15m / 30m / 1h / 4h / 1d / 1w, four symbols, both eras
(`logs/horizon_eval.txt`). Multiples rather than absolute hours because an hour
ladder cannot span the frames: 2h is a 5-bar channel on 4h and a 24-bar one on
5m. On 4h and above the shipped rule IS 20/10, so those rows are one run.

Pooled net R against what ships:

| TF | ships | native 20/10 | 0.5x | 2x | 4x |
|---|---|---|---|---|---|
| 4h | 20 (3.3d) | *is* shipped | +15.4 / +36.7 | -111.3 / -17.9 | -146.1 / -10.9 |
| 1h | 79 | +93.8 / +196.6 | +49.1 / +115.7 | -18.2 / +73.0 | -143.6 / +19.7 |
| 30m | 158 | -32.9 / +6.7 | +131.3 / +174.8 | -19.9 / +181.7 | -173.2 / +10.7 |
| 15m | 317 | -438.6 / -185.9 | +117.7 / +98.3 | -32.5 / +208.8 | -257.1 / +34.6 |
| 5m | 950 | **-3616 / -3672** | +47.5 / +43.2 | -225.1 / +315.9 | -490.7 / +64.9 |
| 1d | 20 | *is* shipped | -22.2 / +8.7 | +5.5 / +7.2 | +3.7 / +53.0 |
| 1w | 20 | *is* shipped | +10.7 / -2.4 | +3.2 / -27.6 | — |

**Not one row on any timeframe is better in both eras.** Every cell above is
"not demonstrated" except native 20/10 on 5m, which is decisively worse in both.

The one thing that recurred was **0.5x**: positive in both eras on 4h, 30m, 15m
and 5m, with every interval spanning zero. Four frames leaning the same way is
not four observations -- the same instruments over the same decade, the same
moves resampled at different resolutions -- so it went to the only test that is
not selected on itself: the same sweep on **AUDUSD, AUDJPY and USDCAD**, which
fed none of it (`logs/horizon_holdout.txt`).

It did not repeat. 0.5x came back -2.2 / -17.4 R on 4h, -5.7 / +39.1 on 1h, and
**-314.1 R on 15m in 2016-2020 with an interval excluding zero**. The lean was
the sample.

That hold-out also re-derived the reason the horizon map exists, on symbols that
never fed it: native 20/10 on 15m is worse in BOTH eras there (-1230 R, -470 R).
So 3.3 days stands on every frame and nothing changed.

**4h reproduces its own history** -- going longer is significantly worse (2x:
-111.3 R early, interval excluding zero) and only the halving leans positive --
which is a check on the sweep rather than a finding, since 4h is the cell N=20
was validated on.

### The 1h pool was the wrong pool

1h came out of that sweep as the weakest case for the shipped horizon anywhere:
-59.8 R in the recent era, with the literal 20/10 leading both. That is either
3.3 days being wrong on the frame or the POOL containing instruments the rule
does not work on, and a pooled row cannot tell those apart.
`tools/horizon_1h_look.py` splits it, reading runs already on disk
(`logs/horizon_1h_look.txt`).

Per cell, the shipped horizon on 1h:

| symbol | 2016-2020 | 2021-2026 | |
|---|---|---|---|
| XAUUSD | +51.9 | +72.2 | positive both |
| USDJPY | +23.3 | +15.8 | positive both |
| GBPUSD | +13.2 | -76.9 | flips |
| AUDJPY | +58.9 | -20.5 | flips (hold-out) |
| USDCAD | +17.2 | -35.6 | flips (hold-out) |
| EURUSD | -20.0 | -70.9 | negative both |
| AUDUSD | -18.7 | -110.8 | negative both (hold-out) |

Re-pooled on **XAUUSD + USDJPY** -- the two cells `tools/entry_filter_eval.py`
had already called positive in both eras, on its own data, so this is a prior
applied rather than a selection made here -- 1h is **+75.2 R and +87.9 R**. The
dropped pair is -6.8 and -147.7. There was never a 1h horizon problem; there was
a pool containing four instruments this rule should not be run on.

The same restriction holds everywhere it can be applied: 4h +72.3 / +57.3, 15m
+239.1 / +244.2, 5m +511.5 / +444.3 -- the shipped horizon positive in both eras
on every intraday frame, once the cells are the ones the rule works on.

And the "shorter is better on 1h" impression is an era, not a horizon. Tallied
across all seven 1h cells, native 20/10 beats the shipped horizon in 3 of 7 cells
in 2016-2020 (-10.5 R in total) and 6 of 7 in 2021-2026 (+348.9 R). A change that
only helps after 2021 is a bet on the regime, which is exactly what the two-era
rule exists to catch.

### What the chart draws, and in what colour

| Object | Drawn as |
|---|---|
| Entry | Orange dashed line, opaque `ENTRY <price> <pips>` tag with an orange hairline |
| Risk | Light-red block from entry to the 2-ATR stop, dashed border, `SL` tag with a red hairline |
| Room ahead | Light teal band from entry to TP1 -- solid, see-through, one solid line on the TP1 edge |
| Levels | Teal `TP1 / TP2 / TP3` tags at the plot edge, plus a chip on the price scale |
| Exit | Violet dashed line at the tighter of channel and trail |

**The plan and the market are coloured from different palettes, deliberately.**
Structure is coloured by direction -- lime where it should hold price up, magenta
where it should hold price down -- and the plan used to borrow those same two
colours, so the stop was magenta beside every resistance band and the
room-to-TP1 block was lime beside every demand base. The plan now owns orange,
red, teal and violet, none of which structure uses: nothing on the chart can be
mistaken for the rule's plan, and the plan cannot be mistaken for structure.

The `ENTRY` and `SL` tags are bold on an opaque chip because they are the two
prices a reader acts on, and they were the faintest text on the chart.

### Units: a pip is ten points, everywhere

A point is the last digit the broker prints; a pip is the digit traders count in.
One rule covers every instrument this app quotes, checked against each symbol's
own `/spec`:

| Instrument | digits | point | pip |
|---|---|---|---|
| 5-digit FX (EURUSD, GBPUSD, AUDUSD, USDCAD) | 5 | 0.00001 | 0.0001 |
| JPY crosses (USDJPY, AUDJPY) | 3 | 0.001 | 0.01 |
| Gold (XAUUSD), BTCUSD | 2 | 0.01 | 0.1 |
| Indices (NAS100, US30) | 1 | 0.1 | 1.0 |

So a $1.00 move in gold is 10 pips, and a 342-point move on US30 is 342 pips. Two
things follow from applying one rule everywhere: on an index "pips" means index
points, where the zone tooltips say "pts" for the same distance; and on BTCUSD a
pip is $0.10, which makes routine moves print five-figure pip counts. Both are
naming rather than arithmetic -- `_pips` is one line in `js/chart/engine.js` and
one in `js/ui/rulepanel.js`, and the two agree.

Money beside a level is quoted at the broker minimum, 0.01 lots, from
`tick_value / tick_size` -- a property of the CONTRACT, needing no equity and no
FX rate, which is why it is a safe fallback where inventing a position size would
not be.

### The strategy replay draws the same object

`Backtest -> Strategy Replay` steps the rule bar by bar through history under the
same causality contract as the Elliott sandbox: the signal is computed from the
slice up to the cursor, never from the whole series, and it owns its own `Chart`
so it cannot disturb the one you trade from.

It draws what the live chart draws, from the same two calls -- `setRuleZone` for
the plan and `setRuleTargets` for the levels. Two things were removed to get
there. It had been drawing its simulated trade through `setPositions`, the
renderer for REAL BROKER ROWS, which put a fabricated position through the one
code path that is supposed to mean "you hold this". And it kept a fixed
2R / 3.5R / 5R reference ladder from a `js/chart/targets.js` that no other
surface still used -- three numbers computed from the stop alone, the same shape
on every symbol, blind to what was actually in front of the trade. That file and
its parity test are gone. `sim/targets.py` stays, because `/signal` still quotes
reference R multiples in text, where they are labelled as such.

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
* **The Islamic admin fee is modelled as ZERO, by decision.** Pepperstone
  charges $100 per lot on positions held past 5 days on this swap-free account,
  and 25-27% of XAUUSD 4h Donchian trades cross that grace period. Measured, it
  costs 0.011-0.023 R per trade read as a one-off and 0.059-0.088 R read as
  per-night -- so the per-night reading takes gold 4h from +0.219 to +0.132 R,
  about a third of the edge. It is deliberately NOT in the cost model: the
  decision was to focus on whether a strategy exists at all rather than on
  brokerage arithmetic. Every expectancy in runs/ is therefore optimistic by
  that amount, and capping the hold to dodge the fee was measured to cost MORE
  than the fee (the two eras disagree on the sign, and drawdown worsens). If a
  result is ever taken to a funding decision, this is the first line to revisit.
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

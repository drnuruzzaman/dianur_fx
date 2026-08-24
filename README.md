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
| Account row | Balance, Equity, Floating, Margin free and **Margin level** in the footer, hidden behind `◧` until asked for |
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

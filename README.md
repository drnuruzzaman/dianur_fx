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
| Watchlist | Live bid + daily change; click to load, hover `×` to remove (`u` undoes), `+` to search |
| Charts | 1 / 2 / 4 chart layouts, each with its own symbol, timeframe, type and studies |
| Chart types | Candles, hollow candles, OHLC bars, line, area, Heikin Ashi, baseline |
| Studies | EMA, SMA, Bollinger, VWAP (overlay); Volume, RSI, MACD, ATR, Stochastic (panes) |
| Auto trendlines | Detected algorithmically per instrument × timeframe, with higher-timeframe lines projected onto lower-timeframe charts |
| Drawings | Horizontal line, trend line, ray, rectangle, Fib retracement — saved per symbol+timeframe |
| Position lines | Open positions draw entry / SL / TP on the matching chart |
| Time & Sales | Real broker ticks with bid/ask, uptick/downtick coloured |
| Contract | Digits, point, live spread in points, lot limits, margin per lot |
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
| `Esc` | Back to the cursor / close menu |

### Mouse

Drag to pan · wheel to zoom (shift+wheel to pan) · drag the price axis to
stretch it · drag the time axis to compress · double-click to reset the scale ·
right-click a drawing to delete it.

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
sim/tl/events.py      the fact event store (TOUCH/BREAK/RETEST/...)
tools/tl_events.py    build the event store, print the funnel and cell sizes
js/chart/tlengine.js  the same engine, ported for the chart (parity-tested)
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
sim/tl/events.py    the FACT layer: typed events with occurred_at / known_at
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

A fifth mechanism was added after a silent, version-dependent failure. Every
timestamp in `sim/tl/` is milliseconds, and the conversion used to be
`index.astype('int64') // 1_000_000` — correct only while a DatetimeIndex was
always nanosecond-resolution. Since pandas 2.0 it is not: `tools/dataset.py`
builds bar indexes with `to_datetime(..., unit='s')`, which yields
`datetime64[s]`, and that expression then returns **seconds**. Every trendline
slope came out 1000x wrong, MTF close times were compared against millisecond
`TF_MS` constants, and the strict alignment guard checked a condition that could
no longer be true, so it raised nothing. Nothing crashed; the lines were simply
wrong, and only on pandas >= 2.

`sim/tl/mtf.to_ms()` converts the unit before the cast, so the result no longer
depends on how the bars happened to be loaded. The symptom that exposed it was
the event funnel reporting 1021 breaks and **zero** retests — a number too
absurd to explain away, which is the argument for reports that count facts.

### The fact layer: three questions, three modules

A trendline system fails when the three questions get answered in one place:

    Detector   what happened?          sim/tl/engine.py   -> lines, lifecycle
    Signal     is this an opportunity? sim/tl/events.py   -> typed events
    Strategy   should I trade it?      sim/strategies/    -> side, stop, target

`sim/tl/events.py` is the middle one, and the discipline is that it contains no
trading decision at all — no side, no stop, no target, no size. A test asserts
those column names are absent, because the value of an event store is that it
can be replayed against a hypothesis you have not written yet, and it stops
being replayable the moment a strategy's opinion is baked into it.

```bash
python tools/tl_events.py --fixtures            # no data/ needed
python tools/tl_events.py --tfs 15m,1h,4h --tols 0.32,0.10
```

**Two clocks on every row.** `occurred_at` is the bar's open — when the thing
happened. `known_at` is the bar's close — the first moment it could have been
acted on. They differ by exactly one bar interval, a test asserts it on every
row, and every consumer filters on `known_at`. Carrying only one timestamp is
how a trendline backtest quietly trades on information it did not have.

**Parameter-free, and where that stops being true.** TOUCH, BREAK, CONFIRMED
and INVALIDATED carry one parameter, the engine's `tol_atr`, which is a property
of the detector rather than of a trading idea. RETEST and REJECTION are *not*
parameter-free — "came back" needs a distance and a patience, "rejected" needs a
wick threshold — so their parameters are written into every row they produce and
`param_free` is False. A sweep over them can then never be mistaken for a fact.

BREAKOUT is deliberately **not** an event. "The break followed through by k ATR"
is a hypothesis with a free parameter and a forward-looking window; it belongs
in the strategy layer, built from a BREAK plus bars. Emitting it here would
smuggle a strategy into the fact store.

**The funnel is what saves the time.** Counting facts before writing a strategy
answers the only question that matters first — is there a sample at all?

```
instrument timeframe  tol_atr  confirmed  touch  touch_rejected  break  retested  retest%  retest_rejected
  USDJPY.a       15m     0.32       2352   1400             389   1433       574     40.1               56
  XAUUSD.a        1h     0.32       1465    911             278   1021       392     38.4               40
```

Read the last column. A breakout-plus-retest-plus-rejection strategy has **40 to
56 events** in these spans against a sample floor of 200. That strategy cannot
be evaluated on this data no matter how it is coded, and finding that out costs
one minute here instead of a week of tuning a curve fitted to fifty trades.

Causality is enforced the same way the feature pipeline enforces it: the store
is rebuilt on truncated history and every event must be identical to the one the
full history produced, with the forward-looking retest window excluded rather
than excused.

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
python tools/tl_diagnostics.py                  # gate 1+2.1, both tolerances
python -m sim.run_tl --ab --carry-free --start 2021-01-01
```

`tools/tl_diagnostics.py` runs **both registered tolerances** and keeps them as
separate cells — 0.32 and 0.10 are two different detectors and a result at one
says nothing about the other, so they are never pooled. It writes the
per-approach rows, not just a summary: instrument, timeframe, trendline id,
tolerance, both clocks, the distance at the approach, the real result, its
placebo result, the effect and the z. A summary is a claim; the rows are the
evidence, and `tests/test_events.py` recomputes the headline effect from the
stored rows to prove they are sufficient.

One correctness note that the paired export forced into the open: only the
**approach** phase is a true paired comparison, where the same approach is
measured at the real level and at a placebo 1.5 ATR away. Breakout and retest
rows exist only for the arm whose approach broke, and a line can hold where its
placebo breaks — so those arms are conditioned on different subsets and are
reported with the wider unpaired error. Applying McNemar's paired error there
(which uses only discordant pairs, and is roughly 40% tighter on this data)
would manufacture significance out of bookkeeping.

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

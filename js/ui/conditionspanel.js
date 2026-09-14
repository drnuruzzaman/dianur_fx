/* conditionspanel.js — the FEAR AND GREED INDEX block in the right rail.
 *
 * THE NAME IS BORROWED; THE COMPUTATION IS NOT CNN'S. Five of that index's
 * seven inputs need a universe -- breadth, 52-week highs and lows, put/call,
 * stocks against bonds, junk spreads -- and none of them exist for a single FX
 * pair. What this shows is one instrument's own price pressure, volatility and
 * participation, each ranked against that same instrument's recent history.
 * Named by request; recorded here so nobody later reads the label as a claim
 * that the arithmetic matches CNN's.
 *
 * The file, the class and the element id still say `conditions`; only the
 * displayed name changed. Renaming the identifiers too would touch main.js,
 * index.html and the snapshot registry for no behavioural gain, and the id is
 * what `SNAP_PANELS` keys on.
 *
 * A DIAL FOR THE ONE SCALAR, BARS FOR THE THREE AXES. See js/ui/gauge.js for
 * why that split exists rather than a single CNN-style composite: the price
 * transforms that would have made up such a composite correlate 0.84-0.95 with
 * each other, so averaging them produces a direction indicator with
 * decorations and hides the two axes that are actually independent.
 *
 * THE DIAL IS DIRECTION, NAMED AS DIRECTION. Calling it Fear & Greed would
 * borrow CNN's credibility for a different computation on a different kind of
 * market -- five of that index's seven inputs need a universe of stocks, an
 * options chain or a credit market, and none of those exist for one FX pair.
 * What is left is honest enough on its own: which way this instrument is
 * pushing, relative to how hard it usually pushes.
 */

import { conditions, MIN_BARS } from '../chart/conditions.js';
import { zoneFor } from './gauge.js';

/* ONE PLACE DECIDES WHAT COLOUR A DIRECTION READING IS, so the rail bar, the
   dial zone and the exported caption cannot disagree. Fear is pink, greed is
   green, and the neutral band in the middle is neither -- read off the zone
   itself rather than sniffed out of its name. */
export function directionTone(v) {
  return Number.isFinite(v) ? (zoneFor(v).tone || null) : null;
}

const NL = String.fromCharCode(10);

/* Plain words for the two axes that have no good end and no bad end. A bare
   "64" in an exported image, with no bar beside it and no hover, says nothing;
   the reader of a PNG has only the text. */
const BANDS = {
  Volatility: ['very calm', 'calm', 'ordinary', 'lively', 'violent'],
  Participation: ['very thin', 'thin', 'ordinary', 'busy', 'very busy'],
};
const bandFor = (name, v) => BANDS[name][Math.min(4, Math.floor(v / 20))];
import { axes, dial, dialForExport } from './gauge.js';

export class ConditionsPanel {
  constructor(root, tfRoot) {
    this.root = root;
    this.tfRoot = tfRoot;
  }

  update(symbol, tf, bars) {
    this.symbol = symbol;
    this.tf = tf;
    this.bars = bars;
    this.render();
  }

  /**
   * Same reading against a newer live bar, on the panel tick.
   *
   * SEPARATE FROM `update` so a tick cannot quietly change the symbol or the
   * frame: a repaint is about one thing moving, and the panel that repaints
   * under a stale label is the one that shows an XAUUSD reading over a yen
   * chart for four seconds.
   */
  repaint(bars) {
    if (!bars || !this.symbol) return;
    this.bars = bars;
    this.render();
  }

  render() {
    const r = this.root;
    if (!r) return;
    r.innerHTML = '';
    if (this.tfRoot) this.tfRoot.textContent = this.tf || '—';

    const c = conditions(this.bars);
    /* KEPT FOR THE SNAPSHOT. The exporter reads text out of the live panels,
       and this one is mostly SVG -- that walk returns the dial's axis ticks as
       orphan numbers ("0", "25", "50", "75", "100") and pairs them into
       nonsense. The panel formats its own export instead. */
    this.last = c;
    if (!c.n) {
      r.append(Object.assign(document.createElement('div'), {
        className: 'sc-empty',
        textContent: `needs ${MIN_BARS} bars — load more history`,
      }));
      return;
    }

    const zone = Number.isFinite(c.direction) ? zoneFor(c.direction).name : null;
    r.append(dial({
      value: c.direction,
      title: 'Direction',
      tip: {
        title: 'Fear & Greed Index',
        body: 'How far price sits from its own EMA50, measured in ATR, then '
            + 'ranked against its own recent history on this instrument and '
            + 'frame. The ranking is what lets gold and the yen share one '
            + 'scale. The name is borrowed from CNN; the arithmetic is not '
            + 'theirs — five of their seven inputs need a whole market, and '
            + 'this is one instrument.',
        note: zone
          ? `${Math.round(c.direction)} — ${zone}, ranked over the last `
            + `${c.window} bars. Moves with the price; volatility and `
            + 'participation step on the bar close, because a part-formed bar '
            + 'has only part of its range and its ticks.'
          : 'No reading yet — not enough bars on this frame.',
      },
      /* THE RANKING WINDOW, NOT THE LOADED ONE. This said "of 1999 bars" --
         the size of the chart's window -- while the percentile was computed
         against the last 500, overstating the comparison fourfold. The number
         printed here is now the sample the rank was actually taken over. */
      sub: `vs last ${c.window} bars`,
    }));

    r.append(axes([
      { name: 'Direction', pct: c.direction, low: 'selling', high: 'buying',
        polar: true, tone: directionTone(c.direction), reading: zone,
        note: 'How far price sits from its own EMA50, measured in ATR, then '
            + 'ranked against the last 500 readings on this cell.'
            + `${NL}0 = more selling pressure than almost any recent bar; `
            + '100 = more buying. 50 means as pushed as this market usually '
            + 'is, NOT flat.' },
      { name: 'Volatility', pct: c.volatility, low: 'calm', high: 'violent',
        reading: Number.isFinite(c.volatility)
          ? bandFor('Volatility', c.volatility) : null,
        note: 'ATR(14), ranked against its own last 500 readings -- how '
            + 'violent this market is by its own standards, not against a '
            + 'fixed threshold.'
            + `${NL}Independent of direction (correlation -0.02 on XAU 1h), `
            + 'which is why it gets its own bar instead of being folded into '
            + 'one score.' },
      { name: 'Participation', pct: c.participation, low: 'thin', high: 'busy',
        reading: Number.isFinite(c.participation)
          ? bandFor('Participation', c.participation) : null,
        note: 'Tick volume, ranked against its own last 500 readings -- how '
            + 'busy the market is by its own standards.'
            + `${NL}Thin and violent at once is the combination worth `
            + 'noticing: the same move on fewer ticks is easier to slip on '
            + 'and harder to fill.' },
    ]));

    const foot = document.createElement('div');
    foot.className = 'cond-foot';
    foot.textContent = 'Context, not a call — percentiles against this cell\u2019s own past.';
    r.append(foot);
  }

  /**
   * Rows for the snapshot caption, in the shape `panelLines` returns.
   *
   * MONOSPACE IS WHY THIS ALIGNS. The exporter draws the caption in Roboto
   * Mono, so padding with spaces produces real columns -- the same trick that
   * does NOT work in a native tooltip, which is why the Live hint on the
   * Signal Board had to become a DOM card instead.
   */
  /**
   * The dial, as an SVG the exporter can rasterise. Null when there is
   * nothing to draw, so the caption simply has no picture rather than a
   * needle resting on a value nobody measured.
   */
  snapshotArt() {
    const c = this.last;
    if (!c || !c.n || !Number.isFinite(c.direction)) return null;
    return {
      svg: dialForExport({
        value: c.direction,
        title: '',
        /* NO TITLE INSIDE THE ART: the caption already prints the panel name
           as its heading, and the sample belongs with the prose line. */
      }),
      w: 200,
      h: 132,
    };
  }

  snapshotLines() {
    const c = this.last;
    if (!c || !c.n) {
      return [{ t: `not enough bars for a reading (needs ${MIN_BARS})`, prose: true }];
    }
    /* THE BAR GOES IN THE IMAGE TOO. A percentile is a position on a scale and
       the bar IS that position; reducing it to a number in the export made the
       reader do in their head what the rail does for them on screen. */
    const row = (name, v, tone, note, low, high) => ({
      k: name,
      v: Number.isFinite(v) ? String(Math.round(v)) : '\u2014',
      tone: Number.isFinite(v) ? tone : 'dim',
      note: Number.isFinite(v) ? note : 'no reading',
      bar: Number.isFinite(v)
        ? { pct: Math.max(0, Math.min(100, v)), anchor: 'left', tone } : null,
      ends: [low, high],
    });
    const d = c.direction;
    return [
      /* DIRECTION IS THE ONLY COLOURED ROW, for the same reason it is the only
         coloured bar on screen: volatility and participation have no good end
         and no bad end, and inking them green would be an opinion the data
         does not contain. */
      row('Direction', d, directionTone(d),
          Number.isFinite(d) ? zoneFor(d).name : '', 'selling', 'buying'),
      row('Volatility', c.volatility, null,
          Number.isFinite(c.volatility) ? bandFor('Volatility', c.volatility) : '',
          'calm', 'violent'),
      row('Participation', c.participation, null,
          Number.isFinite(c.participation)
            ? bandFor('Participation', c.participation) : '',
          'thin', 'busy'),
      {
        t: `Percentiles against this cell's own past — ranked over the last `
           + `${c.window} bars of ${this.symbol || ''} ${this.tf || ''}. `
           + 'Context, not a call: no reading here gates a trade.',
        prose: true,
      },
    ];
  }
}

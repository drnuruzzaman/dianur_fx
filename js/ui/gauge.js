/* gauge.js — the dial and the axis bars. Presentation only.
 *
 * NOTHING IS MEASURED HERE. These take numbers and draw them; what the numbers
 * mean is decided by whoever passes them. Keeping the drawing separate is what
 * lets the same dial show a per-cell reading today and a cross-instrument one
 * later without either surface inheriting the other's assumptions.
 *
 * WHY A DIAL SHOWS ONE NUMBER AND THE BARS SHOW THREE. The obvious build was a
 * CNN-style composite: average six or seven inputs into a single 0-100 and
 * point a needle at it. Measured on XAU 1h, four of those candidate inputs --
 * momentum, position in range, drawdown from the high, EMA separation --
 * correlate 0.84 to 0.95 with each other. They are one number in four
 * costumes, and a "composite" of them is a direction indicator with
 * decorations: the first principal component alone carries 59% of the
 * variance. Only volatility and participation are independent of that cluster.
 *
 * So the dial gets the one thing a dial is good at -- a single scalar with a
 * position on a scale -- and the three genuinely separate axes get three bars.
 * Averaging orthogonal axes into one needle would destroy the only information
 * they carry: a quiet uptrend and a violent selloff both land near 50.
 *
 * IT IS DECORATION, AND THAT IS A DESIGN CONSTRAINT, NOT A DISCLAIMER. Nine
 * signal-time scores have been tested against outcome in this project and none
 * passed a pre-registered bar. So these components must read as CONTEXT and
 * never as a call: no buy/sell words, no green-means-go, and the numbers stay
 * off the ticket itself.
 */

import { tip } from './tips.js';

const NS = 'http://www.w3.org/2000/svg';

function n(tag, attrs = {}, ...kids) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    e.setAttribute(k, String(v));
  }
  for (const kid of kids.flat()) {
    if (kid) e.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return e;
}

/** 0 on the left, 100 on the right, over a half turn. */
const angleFor = (v) => Math.PI * (1 - Math.max(0, Math.min(100, v)) / 100);
const pt = (cx, cy, r, a) => [cx + r * Math.cos(a), cy - r * Math.sin(a)];

/** A filled band between two radii and two values, as one path. */
function band(cx, cy, rOut, rIn, v0, v1) {
  const a0 = angleFor(v0);
  const a1 = angleFor(v1);
  const [x0, y0] = pt(cx, cy, rOut, a0);
  const [x1, y1] = pt(cx, cy, rOut, a1);
  const [x2, y2] = pt(cx, cy, rIn, a1);
  const [x3, y3] = pt(cx, cy, rIn, a0);
  return [`M ${x0} ${y0}`, `A ${rOut} ${rOut} 0 0 1 ${x1} ${y1}`,
          `L ${x2} ${y2}`, `A ${rIn} ${rIn} 0 0 0 ${x3} ${y3}`, 'Z'].join(' ');
}

/**
 * The five zones, low to high. `cls` picks the colour from CSS.
 *
 * `tone` IS A FIELD, NOT A SUBSTRING TEST. The colour used to be decided by
 * looking for "selling" or "buying" inside the zone's NAME, which worked right
 * up until the names changed -- renaming these to fear/greed would have turned
 * every reading neutral, silently and everywhere, because neither word was
 * there any more. A label is for reading; the tone is data.
 */
export const ZONES = [
  { to: 20, name: 'extreme fear', cls: 'g-z4', tone: 'down' },
  { to: 40, name: 'fear', cls: 'g-z3', tone: 'down' },
  { to: 60, name: 'neutral', cls: 'g-z0', tone: null },
  { to: 80, name: 'greed', cls: 'g-z1', tone: 'up' },
  { to: 100, name: 'extreme greed', cls: 'g-z2', tone: 'up' },
];

export function zoneFor(v, zones = ZONES) {
  return zones.find((z) => v <= z.to) || zones[zones.length - 1];
}

/**
 * A half-dial for ONE value on a 0-100 scale.
 *
 * `value` may be null -- "not enough history yet" is a real state and drawing a
 * needle at 50 would present ignorance as balance.
 */
export function dial(opts = {}) {
  const { value, title = '', sub = '', zones = ZONES } = opts;
  const cx = 100;
  const cy = 96;
  /* THE BAND CARRIES THE LABELS NOW, so it has to be deep enough for two
     lines of 7px type -- "EXTREME FEAR" does not fit on one line at the arc
     length an end zone gets. 30 units deep against the old 24. */
  const rOut = 88;
  const rIn = 58;
  const known = Number.isFinite(value);
  const v = known ? Math.max(0, Math.min(100, value)) : 50;
  const zone = known ? zoneFor(v, zones) : null;

  const g = n('svg', {
    class: 'gauge', viewBox: '0 0 200 132', role: 'img',
    'aria-label': `${title}: ${known ? Math.round(v) : 'no reading'}`,
  });
  /* The dial explains itself too -- it is the first thing a reader looks at
     and the least self-evident, since 50 is "usual", not "flat". */
  if (opts.tip) tip(g, opts.tip.title, opts.tip.body, opts.tip.note);

  let from = 0;
  for (const z of zones) {
    g.append(n('path', {
      d: band(cx, cy, rOut, rIn, from, z.to),
      class: `g-band ${z.cls}${zone === z ? ' on' : ''}`,
    }));
    from = z.to;
  }

  /* THE ZONE NAMES SIT IN THEIR OWN BANDS, rotated to the tangent, which is
     what makes a dial readable without a legend: the label is where the needle
     points rather than in a key somewhere else.

     The tangent in SCREEN space, where y grows downward, is (sin a, cos a) --
     not the (-sin a, cos a) you get by differentiating the maths-convention
     circle and forgetting the flip. At the top (a = 90 degrees) that is
     (1, 0), so NEUTRAL sits horizontal; at the left end it stands up on its
     side, exactly as the outer zones do on CNN's. */
  let from0 = 0;
  for (const z of zones) {
    const mid = (from0 + z.to) / 2;
    from0 = z.to;
    const a = angleFor(mid);
    const [lx, ly] = pt(cx, cy, (rOut + rIn) / 2, a);
    const deg = (Math.atan2(Math.cos(a), Math.sin(a)) * 180) / Math.PI;
    const words = z.name.toUpperCase().split(' ');
    const t = n('text', {
      x: lx, y: ly, class: 'g-name' + (zone === z ? ' on' : ''),
      transform: `rotate(${deg.toFixed(2)} ${lx.toFixed(2)} ${ly.toFixed(2)})`,
    });
    /* Two words stack; one word centres. `dy` is in the ROTATED frame, so the
       stack always runs across the band rather than down the page. */
    words.forEach((w, i) => {
      t.append(n('tspan', {
        x: lx, dy: i === 0 ? (words.length > 1 ? -1.5 : 2.6) : 8.4,
      }, w));
    });
    g.append(t);
  }

  // Ticks at the quarters, inside the band, so the scale is readable at 220px.
  for (const t of [0, 25, 50, 75, 100]) {
    const [tx, ty] = pt(cx, cy, rIn - 8, angleFor(t));
    g.append(n('text', { x: tx, y: ty + 3, class: 'g-tick' }, String(t)));
  }

  if (known) {
    const a = angleFor(v);
    /* THE NEEDLE STOPS AT THE BAND, it does not cross it. Running to `rOut`
       drew the pointer straight through the name of the zone it was pointing
       at, which is the one label a reader wants. */
    const [nx, ny] = pt(cx, cy, rIn + 4, a);
    g.append(n('line', { x1: cx, y1: cy, x2: nx, y2: ny, class: 'g-needle' }));
  }
  g.append(n('circle', { cx, cy, r: 5, class: 'g-hub' }));
  g.append(n('text', {
    x: cx, y: cy + 28, class: 'g-val' + (known ? '' : ' g-val-none'),
  }, known ? String(Math.round(v)) : '—'));
  /* NO ZONE CAPTION UNDER THE HUB ANY MORE -- it is written in the band the
     needle points at. Printing it twice made the centre of the dial busier
     than the reading it was there to give. The "no reading" case keeps a
     caption, because there is no lit band to carry it. */
  if (!known) {
    g.append(n('text', { x: cx, y: cy + 42, class: 'g-zone' }, 'NO READING'));
  }
  const wrap = document.createElement('div');
  wrap.className = 'gauge-wrap';
  if (title) {
    const h = document.createElement('div');
    h.className = 'gauge-title';
    h.textContent = title;
    if (sub) {
      const s = document.createElement('span');
      s.className = 'dim';
      s.textContent = sub;
      h.append(' ', s);
    }
    wrap.append(h);
  }
  wrap.append(g);
  return wrap;
}

/**
 * The same dial, built for the EXPORTED image instead of the rail.
 *
 * TWO REASONS IT CANNOT JUST BE THE LIVE ONE, CLONED. A serialised SVG is
 * loaded by the browser as an isolated document: it never sees the page's
 * stylesheet, so every class on it resolves to nothing and the dial rasterises
 * as black shapes. And the rail's palette is built for a near-black rail --
 * `--text` is almost white, which is invisible on the export's #F4F6F8 card.
 *
 * So the export gets its own `<style>`, carried inside the SVG, with literal
 * colours and GENERIC font families. Roboto Condensed is loaded by the page,
 * not by the isolated image, and naming it there would silently fall back to
 * whatever the renderer picks -- which is how exported type ends up a
 * different size from the type it was laid out for.
 */
export function dialForExport(opts) {
  const wrap = dial(opts);
  const svg = wrap.querySelector('svg');
  const st = document.createElementNS(NS, 'style');
  st.textContent = `
    .g-band{stroke:#F4F6F8;stroke-width:1}
    .g-z0{fill:#8d99a6;opacity:.22}
    .g-z1,.g-z3{opacity:.32}
    .g-z2,.g-z4{opacity:.58}
    .g-z1,.g-z2{fill:#2F6B0A}
    .g-z3,.g-z4{fill:#A81059}
    .g-band.on{opacity:.92}
    .g-needle{stroke:#0B1A2B;stroke-width:2.5;stroke-linecap:round}
    .g-hub{fill:#0B1A2B;stroke:#F4F6F8;stroke-width:2}
    .g-tick{fill:#7b8a99;font:400 7.5px sans-serif;text-anchor:middle}
    .g-name{fill:#5a6672;font:700 7.5px sans-serif;text-anchor:middle;
      letter-spacing:.05em}
    .g-name.on{fill:#0B1A2B}
    .g-val{fill:#0B1A2B;font:700 26px sans-serif;text-anchor:middle}
    .g-val-none{fill:#8093a6}
    .g-zone{fill:#5F6C77;font:700 8.5px sans-serif;text-anchor:middle}
  `;
  svg.insertBefore(st, svg.firstChild);
  return svg;
}

/**
 * One bar per axis: `[{ name, pct, low, high, note }]`.
 *
 * `pct` is a PERCENTILE against the same cell's own history, which is the only
 * normalisation that makes gold and the yen comparable on one scale -- and the
 * part of CNN's method that does port even though its components do not.
 *
 * `low`/`high` name the ends, because a bare 0-100 on "volatility" does not say
 * which end is which and the reader should not have to guess.
 *
 * THE HINT IS A CARD, NOT A `title`. Every other side panel explains itself
 * through js/ui/tips.js -- one shared node, three parts (what the row is
 * called, what it measures, how to read the number in front of you right
 * now). A native tooltip here was slower to appear, unstyled, invisible on
 * touch, and looked like it belonged to a different application than the
 * Signal engine two headings below it.
 *
 * ONLY `polar` ROWS GET RED AND GREEN. Direction has a good end and a bad end
 * in the ordinary sense, so the bipolar palette carries meaning there. Volatility
 * and participation do NOT: high volatility is not success and thin trade is
 * not failure, they are just conditions. Colouring them on the same scale said
 * exactly that -- a calm market rendered as a red 12, a busy one as a green 77 --
 * which is an opinion the data does not contain. Non-polar rows are neutral.
 */
export function axes(rows) {
  const box = document.createElement('div');
  box.className = 'axes';
  for (const r of rows) {
    const known = Number.isFinite(r.pct);
    const row = document.createElement('div');
    row.className = 'axis';
    row.innerHTML = '';

    const k = document.createElement('div');
    k.className = 'axis-k';
    k.textContent = r.name;

    /* THE HINT IS ON THE WHOLE ROW. It used to sit on the bar alone, so
       hovering the word "Volatility" -- the thing a reader points at when they
       want to know what it means -- explained nothing. */
    if (r.name) {
      tip(row, r.name, r.note || '',
          known
            ? `${Math.round(r.pct)}th percentile${r.reading ? ' — ' + r.reading : ''}`
              + `${r.low && r.high ? `. 0 is ${r.low}, 100 is ${r.high}.` : ''}`
            : 'No reading yet — not enough bars on this frame.');
    }
    const track = document.createElement('div');
    track.className = 'axis-track';
    /* THE CALLER PICKS THE TONE. Splitting at 50 here painted a reading of 48
       pink while the snapshot caption -- which colours by ZONE -- called the
       same number neutral (the middle band) and inked it grey. Two surfaces
       disagreeing about one value is worse than either convention on its
       own, so the decision moved out of the drawing code. */
    const fill = document.createElement('div');
    fill.className = 'axis-fill ' + (r.tone === 'up' ? 'hi'
      : r.tone === 'down' ? 'lo' : 'neutral');
    fill.style.width = `${known ? Math.max(2, Math.min(100, r.pct)) : 0}%`;
    const mid = document.createElement('div');
    mid.className = 'axis-mid';
    track.append(fill, mid);

    const v = document.createElement('div');
    v.className = 'axis-v' + (known ? '' : ' dim');
    v.textContent = known ? String(Math.round(r.pct)) : '—';

    row.append(k, track, v);
    box.append(row);

    if (r.low || r.high) {
      const ends = document.createElement('div');
      ends.className = 'axis-ends';
      ends.innerHTML = '';
      const a = document.createElement('span');
      a.textContent = r.low || '';
      const b = document.createElement('span');
      b.textContent = r.high || '';
      ends.append(a, b);
      box.append(ends);
    }
  }
  return box;
}

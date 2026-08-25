/* signalpanel.js — the Signal engine panel.
 *
 * Six component bars, a composite, and the composite's own track record.
 *
 * A VIEWER over js/chart/signals.js. The scorecard is not decoration and is not
 * optional: the composite is a weighted average of six opinions, and this
 * project has already measured that its trendlines carry no placebo-adjusted
 * edge out of sample. A confident-looking number with nothing underneath it is
 * exactly the failure mode to design against, so accuracy is always shown
 * against the majority baseline it has to beat, and the realised hit rate and
 * average move are shown whether or not they flatter the model.
 *
 * Bars diverge from a centre line because the sign is the information — six
 * left-anchored bars would read as six magnitudes and hide the disagreement
 * that makes the composite worth computing at all.
 */

import { el } from '../util.js';
import { tip } from './tips.js';
import { COMPONENTS, LABEL, latest } from '../chart/signals.js';
import { asOfBanner, shortSymbol } from './trendread.js';

/* What each component actually looks at, in words rather than indicator names.
   "Macd -12" tells a reader nothing unless they already know what MACD is; the
   panel is the wrong place to assume that. */
const COMP_TIP = {
  trend: ['Trend', 'Compares fast and slow moving averages of price. Positive '
    + 'when the recent average is above the longer one, which is the plainest '
    + 'definition of "price has been going up".'],
  momentum: ['Momentum', 'How fast price has moved recently, regardless of '
    + 'direction of the longer trend. Positive means the last stretch of bars '
    + 'pushed upward harder than they pushed down.'],
  macd: ['MACD', 'Whether upward pressure is building or fading. It watches the '
    + 'GAP between a fast and a slow average: a widening gap means the move is '
    + 'accelerating, a narrowing one means it is running out of steam.'],
  meanrev: ['Mean reversion', 'How stretched price is from its own average. '
    + 'This one deliberately points the OTHER way to the rest, because '
    + 'stretched moves tend to snap back: price far ABOVE normal reads '
    + 'negative, price far BELOW normal reads positive. So a positive number '
    + 'here means price looks cheap and this component expects a bounce up.'],
  breakout: ['Breakout', 'Whether price has pushed clear of its recent range. '
    + 'Positive when it has broken out of the top of where it has been sitting, '
    + 'negative out of the bottom.'],
  flow: ['Flow', 'Where price closes inside each bar, weighted by volume. '
    + 'Closing near the highs on heavy volume says buyers were in control of '
    + 'the bar, not just that the price ended higher.'],
};

function compNote(v) {
  if (!Number.isFinite(v)) return 'Not enough bars yet.';
  const m = Math.abs(v);
  const dir = v > 0 ? 'up' : 'down';
  if (m < 15) return `${Math.round(v)} — near zero. This one has no opinion.`;
  if (m < 45) return `${Math.round(v)} — leaning ${dir}, mildly.`;
  return `${Math.round(v)} — strongly ${dir}.`;
}

const BADGE_CLS = { BULLISH: 'bull', BEARISH: 'bear', NEUTRAL: 'neutral' };

export class SignalPanel {
  constructor(root, headRoot) {
    this.root = root;
    this.headRoot = headRoot;
    this.symbol = null;
    this.tf = null;
    this.data = null;
  }

  update(symbol, tf, bars, asOf = null) {
    this.symbol = symbol;
    this.tf = tf;
    this.asOf = asOf;
    this.data = (bars && bars.length >= 120) ? latest(bars) : null;
    this.render();
  }

  /**
   * Recompute from bars already in hand. `latest()` includes the walk-forward
   * scorecard, which is the expensive part (~53ms on 2000 bars), so main.js
   * calls this on a slower cadence than the Trend read.
   */
  repaint(bars) {
    if (!this.symbol || !bars || bars.length < 120) return;
    this.data = latest(bars);
    this.render();
  }

  render() {
    const r = this.root;
    if (!r) return;
    r.textContent = '';
    if (this.headRoot) {
      this.headRoot.textContent = this.symbol
        ? `${shortSymbol(this.symbol)} · ${this.tf}` : '—';
    }
    if (!this.data) {
      r.appendChild(el('div', { class: 'sig-empty dim' },
                       this.symbol ? 'needs 120+ bars' : '—'));
      return;
    }
    const d = this.data;

    if (this.asOf) r.appendChild(asOfBanner(this.asOf));
    const head = el('div', { class: 'sig-head' });
    head.appendChild(tip(
      el('b', { class: `sig-badge sig-${BADGE_CLS[d.badge]}` }, d.badge),
      `Signal engine: ${d.badge}`,
      'Six independent measurements of the same bars, averaged. This is the '
      + 'verdict of that average. It is a summary of what price HAS done, not '
      + 'a forecast — the scorecard at the bottom is where you find out how '
      + 'often it has been right.',
      d.badge === 'NEUTRAL'
        ? 'The six components disagree, or all six are quiet.'
        : 'Most of the six point the same way. Check which ones below.'));
    head.appendChild(tip(
      el('span', {
        class: `sig-score mono ${d.score > 0 ? 'up' : d.score < 0 ? 'down' : 'dim'}`,
      }, (d.score > 0 ? '+' : '') + Math.round(d.score)),
      'Composite score',
      'The six components below, averaged with equal weight, on a scale of '
      + '-100 to +100. Equal weight is deliberate: weighting one higher would '
      + 'be a claim that it predicts better, and nothing here has measured '
      + 'that.',
      Math.abs(d.score) >= 40
        ? `${Math.round(d.score)} — a strong reading. These are the ones the `
          + '"Signals hit" line below is scored on.'
        : `${Math.round(d.score)} — below the 40 that counts as a strong `
          + 'reading.'));
    r.appendChild(head);

    const bars = el('div', { class: 'sig-bars' });
    for (const k of COMPONENTS) {
      const v = d.scores[k];
      const row = el('div', { class: 'sig-row' });
      const ct = COMP_TIP[k] || [LABEL[k], ''];
      tip(row, ct[0], ct[1], compNote(v));
      row.appendChild(el('span', { class: 'sig-label dim' }, LABEL[k]));

      const track = el('div', { class: 'sig-track' });
      track.appendChild(el('i', { class: 'sig-zero' }));
      if (Number.isFinite(v)) {
        const pct = Math.min(50, Math.abs(v) / 2);        // half-width max
        const fill = el('i', { class: `sig-fill ${v >= 0 ? 'pos' : 'neg'}` });
        fill.style.width = `${pct}%`;
        if (v >= 0) fill.style.left = '50%'; else fill.style.right = '50%';
        track.appendChild(fill);
      }
      row.appendChild(track);
      row.appendChild(el('span', {
        class: `sig-num mono ${Number.isFinite(v) ? (v >= 0 ? 'up' : 'down') : 'dim'}`,
      }, Number.isFinite(v) ? String(Math.round(v)) : '—'));
      bars.appendChild(row);
    }
    r.appendChild(bars);

    /* ---- the scorecard ------------------------------------------------- */
    const c = d.card;
    const pct = (x, dp = 1) => (Number.isFinite(x) ? `${x.toFixed(dp)}%` : '—');
    const stats = el('div', { class: 'sig-stats' });

    stats.appendChild(this._stat('Model P(next bar up)', pct(c.pUp),
      c.pUp > 55 ? 'up' : c.pUp < 45 ? 'down' : 'dim',
      'Chance the next bar closes up',
      'A small statistical model reads the six components and estimates the '
      + 'odds the NEXT bar finishes higher than this one. It is retrained as it '
      + 'goes and only ever sees bars that had already happened, so it is not '
      + 'grading its own homework.',
      Number.isFinite(c.pUp)
        ? (Math.abs(c.pUp - 50) < 3
          ? `${pct(c.pUp)} — that is a coin flip. No opinion.`
          : `${pct(c.pUp)} — a genuine lean, but one bar is one bar.`)
        : 'Not enough history yet.'));

    /* Accuracy is coloured against the BASELINE, never against 50: a model that
       is 56% accurate where the market printed 57% up bars has learned the
       drift and nothing else. */
    const beats = Number.isFinite(c.accuracy) && Number.isFinite(c.baseline)
      && c.accuracy > c.baseline;
    stats.appendChild(this._stat(`Accuracy (walk-forward, n=${c.n || 0})`,
      pct(c.accuracy), beats ? 'up' : 'down',
      'How often it has been right',
      'Over the last ' + (c.n || 0) + ' bars, the share of times the model '
      + 'called the next bar correctly. Every one of those calls was made '
      + 'before its own bar existed, so this is not the model being tested on '
      + 'what it was taught.',
      Number.isFinite(c.accuracy) && Number.isFinite(c.baseline)
        ? (beats
          ? `${pct(c.accuracy)} against a ${pct(c.baseline)} baseline — ahead, `
            + 'by the only comparison that counts.'
          : `${pct(c.accuracy)} against a ${pct(c.baseline)} baseline — NOT `
            + 'ahead. Compare it to the baseline below, never to 50%.')
        : 'Not enough history yet.'));

    stats.appendChild(this._stat('Majority baseline', pct(c.baseline), 'dim',
      'The score to beat',
      'What you would get by ignoring the model entirely and always guessing '
      + 'whichever direction has been more common. If the market printed 57% up '
      + 'bars, guessing "up" every single time scores 57% — so a model on 56% '
      + 'has learned nothing except the drift.',
      Number.isFinite(c.baseline)
        ? `Beat ${pct(c.baseline)} or the model is not adding anything.`
        : 'Not enough history yet.'));

    stats.appendChild(this._stat('Signals hit (5 bars fwd)',
      Number.isFinite(c.hit) ? `${Math.round(c.hit)}% of ${c.hitN}` : `— of ${c.hitN || 0}`,
      !Number.isFinite(c.hit) ? 'dim' : c.hit >= 50 ? 'up' : 'down',
      'Did the strong calls work out?',
      'Ignores the quiet readings entirely. Of the ' + (c.hitN || 0) + ' times '
      + 'the composite went past 40 in either direction, this is how often '
      + 'price was on the predicted side five bars later.',
      Number.isFinite(c.hit)
        ? `${Math.round(c.hit)}% of ${c.hitN}. A small count here is normal — `
          + 'strong readings are rare, so treat it as a hint, not a track record.'
        : 'No strong readings in this window yet.'));

    stats.appendChild(this._stat('Avg move per signal',
      Number.isFinite(c.avgBps) ? `${c.avgBps > 0 ? '+' : ''}${c.avgBps.toFixed(1)} bps` : '—',
      !Number.isFinite(c.avgBps) ? 'dim' : c.avgBps > 0 ? 'up' : 'down',
      'How far price actually moved',
      'Being right is not the same as being paid. This is the average move '
      + 'after a strong signal, measured in basis points (1 bp = 0.01%) and '
      + 'signed so that a correct call is positive. It is measured before '
      + 'spread and commission, which come out of it.',
      Number.isFinite(c.avgBps)
        ? (c.avgBps > 0
          ? `${c.avgBps.toFixed(1)} bps before costs. Compare that against the `
            + 'spread you actually pay.'
          : `${c.avgBps.toFixed(1)} bps — negative. High accuracy with a `
            + 'negative average move means it is right on bars that go nowhere '
            + 'and wrong on the ones that move.')
        : 'No strong readings in this window yet.'));

    r.appendChild(stats);

    /* One line of prose, because a panel of five statistics invites the reader
       to pick the flattering one. */
    if (Number.isFinite(c.accuracy) && Number.isFinite(c.baseline)) {
      const edge = c.accuracy - c.baseline;
      /* Two caveats ride along with any positive number here, and they are
         stated rather than left for the reader to remember:

         IN-SAMPLE-ADJACENT. The walk-forward is honest bar by bar, but the six
         components, their weights and the |score|>=40 threshold were all chosen
         while looking at series like this one. That is not the same as a fresh
         instrument on a fresh decade, and this project has already watched a
         z=4.3 effect decay to z=0.96 on a third era.

         COSTS. Accuracy is measured on mid prices. Spread and commission are
         subtracted from whatever this edge is, not from something else. */
      r.appendChild(el('div', { class: `sig-verdict ${edge > 1 ? 'up' : 'dim'}` },
        edge > 1
          ? `Model is ${edge.toFixed(1)}pp above baseline on this window. `
            + 'Still in-sample-adjacent; spread and commission come out of that.'
          : `No edge over baseline (${edge >= 0 ? '+' : ''}${edge.toFixed(1)}pp) `
            + 'on this window.'));
    }
  }

  _stat(label, value, cls, tipTitle, tipBody, tipNote) {
    const row = el('div', { class: 'sig-stat' });
    row.appendChild(el('span', { class: 'dim' }, label));
    row.appendChild(el('span', { class: `sig-val mono ${cls || ''}` }, value));
    return tipTitle ? tip(row, tipTitle, tipBody, tipNote) : row;
  }
}

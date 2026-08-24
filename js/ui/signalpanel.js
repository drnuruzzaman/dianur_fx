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
import { COMPONENTS, LABEL, latest } from '../chart/signals.js';
import { shortSymbol } from './trendread.js';

const BADGE_CLS = { BULLISH: 'bull', BEARISH: 'bear', NEUTRAL: 'neutral' };

export class SignalPanel {
  constructor(root, headRoot) {
    this.root = root;
    this.headRoot = headRoot;
    this.symbol = null;
    this.tf = null;
    this.data = null;
  }

  update(symbol, tf, bars) {
    this.symbol = symbol;
    this.tf = tf;
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

    const head = el('div', { class: 'sig-head' });
    head.appendChild(el('b', { class: `sig-badge sig-${BADGE_CLS[d.badge]}` }, d.badge));
    head.appendChild(el('span', {
      class: `sig-score mono ${d.score > 0 ? 'up' : d.score < 0 ? 'down' : 'dim'}`,
      title: 'equal-weighted composite of the six components, -100..+100',
    }, (d.score > 0 ? '+' : '') + Math.round(d.score)));
    r.appendChild(head);

    const bars = el('div', { class: 'sig-bars' });
    for (const k of COMPONENTS) {
      const v = d.scores[k];
      const row = el('div', { class: 'sig-row' });
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
      'walk-forward logistic on the six components; trained only on bars before this one'));

    /* Accuracy is coloured against the BASELINE, never against 50: a model that
       is 56% accurate where the market printed 57% up bars has learned the
       drift and nothing else. */
    const beats = Number.isFinite(c.accuracy) && Number.isFinite(c.baseline)
      && c.accuracy > c.baseline;
    stats.appendChild(this._stat(`Accuracy (walk-forward, n=${c.n || 0})`,
      pct(c.accuracy), beats ? 'up' : 'down',
      'out-of-sample by construction — each prediction made before its own bar'));

    stats.appendChild(this._stat('Majority baseline', pct(c.baseline), 'dim',
      'accuracy of always predicting the more common direction — the bar to beat'));

    stats.appendChild(this._stat('Signals hit (5 bars fwd)',
      Number.isFinite(c.hit) ? `${Math.round(c.hit)}% of ${c.hitN}` : `— of ${c.hitN || 0}`,
      !Number.isFinite(c.hit) ? 'dim' : c.hit >= 50 ? 'up' : 'down',
      'how strong composite readings (|score| >= 40) actually resolved'));

    stats.appendChild(this._stat('Avg move per signal',
      Number.isFinite(c.avgBps) ? `${c.avgBps > 0 ? '+' : ''}${c.avgBps.toFixed(1)} bps` : '—',
      !Number.isFinite(c.avgBps) ? 'dim' : c.avgBps > 0 ? 'up' : 'down',
      'signed by the signal direction, before spread — a positive model with a '
      + 'negative average move is picking direction on bars that do not move'));

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

  _stat(label, value, cls, title) {
    const row = el('div', { class: 'sig-stat', title: title || '' });
    row.appendChild(el('span', { class: 'dim' }, label));
    row.appendChild(el('span', { class: `sig-val mono ${cls || ''}` }, value));
    return row;
  }
}

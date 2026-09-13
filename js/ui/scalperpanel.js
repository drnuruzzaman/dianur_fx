/* scalperpanel.js — the scalper ticket on the right rail.
 *
 * THE TICKET AND ITS EXPECTATION, ALWAYS TOGETHER. The format is the one a
 * signal room posts -- side, order type, entry, stop, three targets -- and the
 * whole reason that format is persuasive is that it looks like a decision
 * somebody has already made for you. So the measured net sits directly under
 * it, in the same panel, in the same weight: gold 5m break mode is -0.0922 R
 * per fill after costs, and a reader who sees the ladder without that number
 * is being shown half of what was measured.
 *
 * IT DRAWS WHATEVER FRAME THE CHART IS ON, and says so. The measurement behind
 * the expectation was made on 5m; on any other frame the ticket is still the
 * rule's own output but the number under it was not measured there, and the
 * panel marks it rather than quietly reusing it.
 */

import { el, px } from '../util.js';

const NL = String.fromCharCode(10);
import { MEASURED, SESSION, UNMEASURED_NOTE, brokerHour, orderPhrase, ticket }
  from '../chart/scalper.js';

/* The frames the surviving settings were measured on. */
const MEASURED_TF = '30m';

export class ScalperPanel {
  constructor(root, headRoot) {
    this.root = root;
    this.headRoot = headRoot;
    this.mode = 'break';
    this.symbol = null;
    this.tf = null;
    this.bars = null;
    this.spreadPx = 0;
    if (this.headRoot) {
      this.headRoot.addEventListener('click', () => {
        this.mode = this.mode === 'break' ? 'fade' : 'break';
        this.render();
      });
      this.headRoot.title = 'Click to switch break / fade';
    }
  }

  update(symbol, tf, bars, spec, rawTf) {
    this.symbol = symbol;
    this.tf = tf;
    /* THE RAW FRAME, not the label. The panel compared `M5` against `5m` and
       announced "NOT measured on M5" on the one frame it WAS measured on. */
    this.rawTf = rawTf || tf;
    this.bars = bars;
    if (spec && spec.point) {
      const pts = spec.spread_points_now ?? spec.spread_current ?? 0;
      if (pts) this.spreadPx = spec.point * pts;
    }
    this.render();
  }

  render() {
    const r = this.root;
    if (!r) return;
    r.innerHTML = '';
    if (this.headRoot) this.headRoot.textContent = this.mode.toUpperCase();

    const t = this.bars && ticket(this.bars, { mode: this.mode });
    /* OFF-SESSION THERE IS NO TICKET, and the panel says which it is: "no
       ticket" and "not enough bars" are different facts.

       READ OFF THE SHARED CONSTANT, not off hours written here. `SESSION` is
       null now -- the rule trades 24 hours -- so this branch is dead and the
       panel simply says "no ticket". Hard-coding 7-21 here would have left it
       announcing a closed session the rule no longer keeps, which is the same
       class of bug as before: the panel disagreeing with the rule it displays.
       Once it was the wrong CLOCK (wall-clock UTC against broker hours read off
       the decision bar); it would now be the wrong WINDOW.

       ASKED OF THE DECISION BAR, and on the broker's clock, for the same reason
       ticket() is: the relevant hour is that bar's, not this moment's. */
    let offSession = false;
    if (SESSION) {
      const decideAt = this.bars && this.bars.length >= 2
        ? this.bars[this.bars.length - 2].t : Date.now();
      const bh = brokerHour(decideAt);
      offSession = !(bh >= SESSION[0] && bh < SESSION[1]);
    }
    if (!t) {
      r.append(el('div', { class: 'sc-empty',
        text: offSession
          ? `outside the measured session (broker ${String(SESSION[0]).padStart(2, '0')}:00-`
            + `${String(SESSION[1]).padStart(2, '0')}:00) — the rule does not trade here`
          : 'no ticket' }));
      return;
    }
    const d = /JPY/i.test(this.symbol || '') ? 3
      : /XAU|XAG/i.test(this.symbol || '') ? 2 : 5;
    const buy = t.side === 'buy';

    /* THE ORDER TYPE IN WORDS, ON HOVER. The rationale below already says
       what the ticket does, but the BADGE is what gets read first and it is
       the part that misleads: SELL LIMIT rests above the market and so looks
       like a buy to anyone who has not memorised the vocabulary. Shared with
       the Signal Board so the two cannot drift. */
    r.append(el('div', { class: 'sc-head' },
      el('span', { class: buy ? 'sc-side up' : 'sc-side down',
                   title: orderPhrase(t, (v) => px(v, d)) },
         `${t.side.toUpperCase()} ${t.order.toUpperCase()}`),
      el('b', { class: 'sc-entry', text: px(t.entry, d) })));

    const row = (k, v, cls) => el('div', { class: 'sc-row' },
      el('span', { class: 'sc-k', text: k }),
      el('span', { class: 'sc-v' + (cls ? ' ' + cls : ''), text: v }));

    r.append(row('SL', px(t.stop, d), 'down'));
    t.tp.forEach((x, i) => r.append(
      row(`TP${i + 1}`, px(x, d), i === 0 ? 'dim' : 'up')));
    r.append(row('risk', `${px(t.risk, d)}  (4 ATR)`, 'dim'));

    /* WHY THIS TICKET EXISTS, in the rule's own terms. Which condition fired,
       off which level, and what has to happen for it to fill at all.

       WRITTEN TRUTHFULLY, NOT PERSUASIVELY. The measured net is -0.0922 R per
       fill, so a rationale that only listed reasons to click would be the
       marketing panel this was built as a corrective to. The last line is the
       arithmetic that decides it, and the full measurement is on hover. */
    const tfKey = String(this.rawTf || '').toLowerCase();
    const m = MEASURED[tfKey] || null;
    const away = t.entry - (this.lastClose ?? t.entry);
    const why = el('div', { class: 'sc-why-take' });

    /* TREND AGE ON THE FACE OF THE TICKET. Of nine signal-time scores tested
       for a relationship to outcome, this was the only one that showed a
       consistent one -- fresh trends beat old ones in 15 of 16 era-cells. It is
       REPORTED AND NOT ACTED ON, because it then held up on USDJPY across both
       halves of history and three EMA pairs while failing about half of those
       checks on XAUUSD, and a gate that works on one instrument is not a rule.
       The reader gets the number and decides. */
    const fresh = t.age <= 20;
    why.append(el('div', { class: 'snap-line' },
      el('b', { class: t.trend === 'up' ? 'up' : 'down',
                text: `Trend ${t.trend}` }),
      ` — EMA20 ${t.trend === 'up' ? 'above' : 'below'} EMA50 on ${this.tf}, `,
      el('b', { class: fresh ? 'up' : 'dim',
                text: `${t.age} bar${t.age === 1 ? '' : 's'}` }),
      ` since the cross (${fresh ? 'fresh' : 'stale'}).`));

    if (t.mode === 'break') {
      why.append(el('div', { class: 'snap-line' },
        `Joins a break of the 20-bar ${t.trend === 'up' ? 'high' : 'low'} `,
        el('b', { text: px(t.level, d) }),
        `. Fills only if price trades ${t.trend === 'up' ? 'up through' : 'down through'} `
        + `${px(t.entry, d)} within ${t.expire} bars, else it is cancelled.`));
    } else {
      /* t.entry, NOT t.level. `level` is `H if up else L` -- the swing the
         TREND was read off -- and fade rests the order at the OPPOSITE one, so
         this line named the right swing at the wrong price: on a down trend it
         said "the 20-bar high" and then printed the low. In fade mode the
         entry IS the swing being faded. */
      why.append(el('div', { class: 'snap-line' },
        /* THE VERB FOLLOWS THE SIDE. Hard-coded "Buys" here described a
           SELL LIMIT as a buy, which is the exact confusion the order-type
           tooltip exists to clear up. */
        `${buy ? 'Buys' : 'Sells'} the pullback to the 20-bar `
        + `${t.trend === 'up' ? 'low' : 'high'} `,
        el('b', { text: px(t.entry, d) }),
        `. Fills only if price comes back to ${px(t.entry, d)} within `
        + `${t.expire} bars, else it is cancelled.`));
    }

    why.append(el('div', { class: 'snap-line' },
      `Stop 4 ATR (${px(t.risk, d)}) beyond the entry — that is 1R. `
      + `Targets sit at 0.9, 1.5 and 2.4R.`));

    /* THE EXPECTATION IS NOT ON THE FACE OF THIS PANEL, by request. It lives
       on the hover below and in tools/scalper.py, which is where the number
       came from. Recorded here so the omission reads as a decision rather than
       as something nobody got round to: measured net is -0.0922 R per fill. */

    /* NO MEASUREMENT LINE ON THE FACE OF THIS PANEL, by request -- neither the
       expectation on a measured frame nor the warning on an unmeasured one.
       Both live on the hover below, and the full table is in
       sim/strategies/rayo.py. Recorded so the omission reads as a decision:
       stop 4 ATR / exit TP1 measured +0.043 R on 30m and +0.218 R on 1h, and
       was NOT measured on any faster frame. */

    r.append(why);
    /* The full measurement, one hover away rather than four lines on the rail. */
    r.title = (m
      ? `${this.mode} / stop 4 ATR / exit TP1 on ${this.tf}:`
        + ` net ${m.net >= 0 ? '+' : ''}${m.net.toFixed(4)} R per fill,`
        + ` ${m.n} fills, ${m.win}% win`
        + `${NL}worst sub-period ${m.worst.toFixed(3)} R (2017-19, when gold ranged)`
        + `${NL}${m.eras}; USDJPY was a holdout for the parameter choice`
      : `not measured on ${this.tf} - ${UNMEASURED_NOTE}`)
      + `${NL}spread costs ${t.costR(this.spreadPx).toFixed(3)} R of this ticket`
      + `${NL}exit at 0.9R needs a 52.6% hit rate`;
  }
}

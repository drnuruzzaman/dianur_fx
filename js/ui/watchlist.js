/* watchlist.js — left rail: quote list, price flash, session strip. */

import { $, SESSIONS, el, hhmmss, load, save, sessionClock, sessionOpen, withZone, zoneLabel } from '../util.js';

const DEFAULT = ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'XAUUSD', 'US30', 'NAS100'];

export class Watchlist {
  constructor({ onPick, onRemove }) {
    this.host = $('#watchlist');
    this.onPick = onPick;
    this.onRemove = onRemove || (() => {});
    this.symbols = load('watchlist', DEFAULT);
    this.quotes = {};
    this.prev = {};
    this.refs = new Map();
    this.active = null;
    this.build();
    this.startSessions();
  }

  add(sym) {
    if (this.symbols.includes(sym)) return;
    this.symbols.push(sym);
    save('watchlist', this.symbols);
    this.build();
  }

  remove(sym) {
    const at = this.symbols.indexOf(sym);
    this.symbols = this.symbols.filter((s) => s !== sym);
    save('watchlist', this.symbols);
    this.build();
    this.lastRemoved = { sym, at };
  }

  /** Put back whatever remove() last took out, in its original position. */
  undoRemove() {
    const undo = this.lastRemoved;
    if (!undo || this.symbols.includes(undo.sym)) return null;
    this.symbols.splice(Math.max(0, Math.min(undo.at, this.symbols.length)), 0, undo.sym);
    save('watchlist', this.symbols);
    this.build();
    this.lastRemoved = null;
    return undo.sym;
  }

  /** Swap a plain name for the broker's suffixed one, keeping list order. */
  rename(from, to) {
    const i = this.symbols.indexOf(from);
    if (i < 0 || from === to) return;
    if (this.symbols.includes(to)) this.symbols.splice(i, 1);
    else this.symbols[i] = to;
    if (this.active === from) this.active = to;
    save('watchlist', this.symbols);
    this.build();
  }

  setActive(sym) {
    this.active = sym;
    for (const [name, row] of this.refs) row.root.classList.toggle('active', name === sym);
  }

  build() {
    this.host.innerHTML = '';
    this.refs.clear();
    for (const sym of this.symbols) {
      const px = el('span', { class: 'px', text: '—' });
      const chg = el('span', { class: 'chg', text: '—' });
      // Removal is an explicit button, not a right-click: a stray context-menu
      // click used to silently delete a row with no undo.
      const kill = el('button', {
        class: 'wl-x', title: `Remove ${sym}`,
        onclick: (e) => { e.stopPropagation(); this.remove(sym); this.onRemove(sym); },
      }, '×');
      const root = el('div', {
        class: 'wl-row' + (sym === this.active ? ' active' : ''),
        title: 'Click to load',
        onclick: () => this.onPick(sym),
      }, el('span', { class: 'sym', text: sym }), px, chg, kill);
      this.refs.set(sym, { root, px, chg });
      this.host.append(root);
    }
    this.paint();
  }

  /** quotes: {SYM:{bid,digits,...}}, day: {SYM: previousClose} for the % column. */
  update(quotes, refCloses = {}) {
    this.quotes = { ...this.quotes, ...quotes };
    this.refCloses = { ...(this.refCloses || {}), ...refCloses };
    this.paint(quotes);
  }

  paint(changed = null) {
    for (const [sym, row] of this.refs) {
      const q = this.quotes[sym];
      if (!q) continue;
      const d = q.digits ?? 5;
      row.px.textContent = Number(q.bid).toFixed(d);
      const ref = (this.refCloses || {})[sym];
      if (ref) {
        const pct = ((q.bid - ref) / ref) * 100;
        row.chg.textContent = (pct >= 0 ? '+' : '') + pct.toFixed(2) + '%';
        row.chg.className = 'chg ' + (pct >= 0 ? 'up' : 'down');
        row.px.className = 'px ' + (pct >= 0 ? 'up' : 'down');
      }
      const before = this.prev[sym];
      if (changed && changed[sym] && before !== undefined && q.bid !== before) {
        const cls = q.bid > before ? 'flash-up' : 'flash-down';
        row.root.classList.remove('flash-up', 'flash-down');
        void row.root.offsetWidth;                 // restart the animation
        row.root.classList.add(cls);
      }
      this.prev[sym] = q.bid;
    }
  }

  startSessions() {
    const host = $('#sessions');

    /* Two clocks at two rates. The session rows only change on the hour, so
       they are rebuilt every 30s; the running clock changes every second and
       lives in its own node, updated in place. Rebuilding four rows and a
       title string 60 times a minute to move one digit would be silly. */
    const rows = el('div', { class: 'sess-rows' });
    const clock = el('span', { class: 'clock' });
    host.append(rows, el('div', { class: 'sess' }, clock));

    /* This strip is the ONE surface that stays on the viewer's LOCAL clock,
       against the app's broker-time default. It answers a personal question --
       "what time do I need to be at my desk for the London open" -- and broker
       time cannot answer it. The tooltip still names the canonical UTC window,
       which is the only frame where the four windows keep fixed hours.

       withZone is used rather than a separate formatter so this cannot drift
       out of step with the shared clock: same code path, one argument. */
    const paintLocal = () => {
      rows.innerHTML = '';
      const now = new Date();
      for (const s of SESSIONS) {
        const open = sessionOpen(s, now);
        rows.append(el('div', {
          class: 'sess' + (open ? ' open' : ''),
          title: `${s.name} ${sessionClock(s.open, now)}\u2013${sessionClock(s.close, now)} `
               + `${zoneLabel()}`
               // Name the canonical UTC window too \u2014 but not twice, on the day
               // a viewer is actually sitting on UTC.
               + (zoneLabel() === 'UTC' ? ''
                 : ` \u00b7 ${String(s.open).padStart(2, '0')}:00\u2013`
                   + `${String(s.close).padStart(2, '0')}:00 UTC`),
        },
          el('i'), el('span', { text: s.name }),
          el('span', { class: 'clock', text: sessionClock(s.open, now) })));
      }
    };

    const paint = () => withZone('local', paintLocal);

    /* The running clock is local too, so the strip reads in one zone. It names
       that zone, because the rest of the app is on broker time and an unlabelled
       clock three hours off the chart would look like a bug. */
    const tick = () => withZone('local', () => {
      clock.textContent = `${zoneLabel()} \u00b7 ${hhmmss(Date.now())}`;
    });

    paint();
    tick();
    setInterval(paint, 30000);
    // land the tick just after each whole second so the display never sits on
    // a stale digit for most of its life
    setTimeout(() => { tick(); setInterval(tick, 1000); }, 1000 - (Date.now() % 1000));
  }
}

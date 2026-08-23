/* panels.js — bottom tabs (positions / orders / history / calendar), the
   right-hand tape and the contract spec block. All read-only views. */

import { $, el, hhmmss, load, money, num, px, relTime, save, stamp,
         stampTz, tzLabel, TZ_MODES } from '../util.js';
import { Backtest } from './backtest.js';

const table = (cols, rows) => {
  const t = el('table', { class: 'grid' });
  t.append(el('thead', {}, el('tr', {}, cols.map((c) => el('th', { text: c })))));
  t.append(el('tbody', {}, rows));
  return t;
};

const empty = (msg) => el('div', { class: 'empty', text: msg });

const plCell = (v) => el('td', { class: v >= 0 ? 'up' : 'down', text: (v >= 0 ? '+' : '') + num(v) });

export class Panels {
  constructor() {
    this.host = $('#panel');
    this.tab = 'positions';
    this.data = { positions: [], orders: [], deals: [], calendar: [] };
    this.currency = '';
    // the Backtest tab is a viewer over runs/index.json; see js/ui/backtest.js
    this.backtest = new Backtest();
    this.tz = load('calendarTz', 'local');
    this.brokerOffsetMs = 0;          // handed over from /health by main.js

    $('#tabs').addEventListener('click', (e) => {
      const b = e.target.closest('.tab');
      if (!b) return;
      if (this.tab === 'backtest' && b.dataset.tab !== 'backtest') this.backtest.hide();
      if (b.dataset.tab === 'calendar') this.calScrolled = false;   // land on now again
      this.tab = b.dataset.tab;
      [...$('#tabs').querySelectorAll('.tab')].forEach((x) => x.classList.toggle('active', x === b));
      this.render();
    });

    /* Three panel sizes, one source of truth. The buttons previously toggled two
       independent classes, so expanded+collapsed could both be set and the
       glyphs stopped describing the actual state. */
    const setSize = (size) => {
      document.body.classList.toggle('bottom-collapsed', size === 'collapsed');
      document.body.classList.toggle('bottom-expanded', size === 'expanded');
      const collapse = $('#bottomToggle');
      const expand = $('#bottomExpand');
      collapse.textContent = size === 'collapsed' ? '\u25b4' : '\u2013';
      collapse.title = size === 'collapsed' ? 'Restore panel' : 'Collapse panel';
      collapse.classList.toggle('on', size === 'collapsed');
      expand.textContent = size === 'expanded' ? '\u2750' : '\u25a1';
      expand.title = size === 'expanded' ? 'Restore panel' : 'Expand panel';
      expand.classList.toggle('on', size === 'expanded');
      this.size = size;
    };
    this.setSize = setSize;
    this.size = 'normal';

    $('#bottomExpand').addEventListener('click', () =>
      setSize(this.size === 'expanded' ? 'normal' : 'expanded'));
    $('#bottomToggle').addEventListener('click', () =>
      setSize(this.size === 'collapsed' ? 'normal' : 'collapsed'));
  }

  set(kind, rows) {
    this.data[kind] = rows || [];
    if (kind === 'positions') $('#posCount').textContent = this.data.positions.length;
    if (kind === 'orders') $('#ordCount').textContent = this.data.orders.length;
    if (this.tab === kind) this.render();
  }

  render() {
    const h = this.host;
    document.body.classList.toggle('bt-active', this.tab === 'backtest');
    document.body.classList.toggle('cal-active', this.tab === 'calendar');
    if (this.tab === 'backtest') {
      if (this.size !== 'expanded') this.setSize('expanded');   // it needs room
      this.backtest.show(h);
      return;
    }
    h.innerHTML = '';
    const d = this.data;

    if (this.tab === 'positions') {
      if (!d.positions.length) return void h.append(empty('no open positions'));
      const rows = d.positions.map((p) => el('tr', {},
        el('td', { class: 'sym', text: p.symbol }),
        el('td', { class: p.side === 'buy' ? 'up' : 'down', text: p.side.toUpperCase() }),
        el('td', { text: num(p.volume, 2) }),
        el('td', { text: px(p.price_open, 5) }),
        el('td', { text: px(p.price_current, 5) }),
        el('td', { text: p.sl ? px(p.sl, 5) : '—' }),
        el('td', { text: p.tp ? px(p.tp, 5) : '—' }),
        el('td', { text: num(p.swap) }),
        plCell(p.profit),
        el('td', { text: stamp(p.time_ms) }),
        el('td', { text: p.ticket })));
      const total = d.positions.reduce((a, p) => a + (p.profit || 0), 0);
      rows.push(el('tr', {},
        el('td', { class: 'sym', text: 'TOTAL' }),
        ...Array.from({ length: 7 }, () => el('td', { text: '' })),
        plCell(total), el('td', { text: '' }), el('td', { text: '' })));
      h.append(table(['Symbol', 'Side', 'Lots', 'Open', 'Current', 'SL', 'TP', 'Swap',
        `P/L ${this.currency}`, 'Opened', 'Ticket'], rows));
      return;
    }

    if (this.tab === 'orders') {
      if (!d.orders.length) return void h.append(empty('no pending orders'));
      h.append(table(['Symbol', 'Type', 'Lots', 'Price', 'SL', 'TP', 'Placed', 'Ticket'],
        d.orders.map((o) => el('tr', {},
          el('td', { class: 'sym', text: o.symbol }),
          el('td', { text: String(o.type || o.side || '').toUpperCase() }),
          el('td', { text: num(o.volume, 2) }),
          el('td', { text: px(o.price_open ?? o.price, 5) }),
          el('td', { text: o.sl ? px(o.sl, 5) : '—' }),
          el('td', { text: o.tp ? px(o.tp, 5) : '—' }),
          el('td', { text: stamp(o.time_ms) }),
          el('td', { text: o.ticket })))));
      return;
    }

    if (this.tab === 'deals') {
      if (!d.deals.length) return void h.append(empty('no deals in the window'));
      const net = d.deals.reduce((a, x) => a + (x.profit || 0) + (x.commission || 0) + (x.swap || 0), 0);
      const rows = d.deals.map((x) => el('tr', {},
        el('td', { class: 'sym', text: x.symbol || '—' }),
        el('td', { class: x.side === 'buy' ? 'up' : 'down', text: String(x.side || x.type || '').toUpperCase() }),
        el('td', { text: num(x.volume, 2) }),
        el('td', { text: px(x.price, 5) }),
        el('td', { text: num(x.commission || 0) }),
        el('td', { text: num(x.swap || 0) }),
        plCell(x.profit || 0),
        el('td', { text: stamp(x.time_ms) })));
      rows.push(el('tr', {},
        el('td', { class: 'sym', text: 'NET' }),
        ...Array.from({ length: 5 }, () => el('td', { text: '' })),
        plCell(net), el('td', { text: '' })));
      h.append(table(['Symbol', 'Side', 'Lots', 'Price', 'Comm', 'Swap', 'Profit', 'Time'], rows));
      return;
    }

    if (this.tab === 'calendar') {
      clearInterval(this.calTimer);
      if (!d.calendar.length) return void h.append(empty('no calendar data from the bridge'));

      /* The bridge returns the whole trading week, so the question worth
         answering is not "what is on" but "what is next". Hence the relative
         column, the NOW divider and the pinned next-high-impact line \u2014 all
         repainted on a timer, because "in 45m" goes stale while you read it. */
      const dots = { high: '\u25cf\u25cf\u25cf', medium: '\u25cf\u25cf', low: '\u25cf' };
      const impactClass = { high: 'down', medium: 'sym', low: 'dim' };
      const SOON_MS = 60 * 60 * 1000;
      const events = d.calendar
        .map((e) => ({ ...e, ts: e.ts ?? e.time_ms ?? e.time }))
        .filter((e) => e.ts)
        .sort((a, b) => a.ts - b.ts);

      const head = el('div', { class: 'cal-head' });
      const wrap = el('div', { class: 'cal-wrap' });
      h.append(head, wrap);

      const paint = () => {
        const now = Date.now();
        const off = this.brokerOffsetMs || 0;
        head.innerHTML = '';
        wrap.innerHTML = '';

        // ---- pinned: the next high-impact event, or just the next one ----
        const next = events.find((e) => e.ts > now && String(e.impact).toLowerCase() === 'high')
                  || events.find((e) => e.ts > now);
        const nimp = next ? String(next.impact || '').toLowerCase() : '';
        head.append(next
          ? el('div', { class: 'cal-next' },
              el('span', { class: 'cal-next-k', text: 'next' }),
              el('b', { class: 'sym', text: next.currency || '' }),
              el('span', { class: impactClass[nimp] || 'dim', text: dots[nimp] || '' }),
              el('span', { text: next.title || next.event || '' }),
              el('span', { class: 'cal-in', text: relTime(next.ts, now) }),
              el('span', { class: 'dim', text: stampTz(next.ts, this.tz, off) }))
          : el('div', { class: 'cal-next dim', text: 'nothing further this week' }));

        head.append(el('div', { class: 'cal-tz' },
          el('span', { class: 'cal-next-k', text: 'times in' }),
          ...TZ_MODES.map((mode) => el('button', {
            class: 'cal-tzb' + (this.tz === mode ? ' on' : ''),
            onclick: () => { this.tz = mode; save('calendarTz', mode); paint(); },
          }, tzLabel(mode, off)))));

        // ---- the week, split by now ----
        let divider = null;
        const rows = [];
        for (const e of events) {
          const imp = String(e.impact || '').toLowerCase();
          const past = e.ts <= now;
          const soon = !past && e.ts - now <= SOON_MS;
          if (!past && !divider) {
            divider = el('tr', { class: 'cal-now' }, el('td', { colspan: '7',
              text: 'now \u00b7 ' + stampTz(now, this.tz, off) }));
            rows.push(divider);
          }
          const row = el('tr', { class: past ? 'cal-past' : soon ? 'cal-soon' : '' },
            el('td', { text: stampTz(e.ts, this.tz, off) }),
            el('td', { class: 'cal-rel' + (soon ? ' hot' : ''), text: relTime(e.ts, now) }),
            el('td', { class: 'sym', text: e.currency || '' }),
            el('td', { class: impactClass[imp] || '',
                       text: dots[imp] || (e.importance ? '\u25cf'.repeat(Number(e.importance)) : '\u2014') }),
            el('td', { text: e.title || e.event || '' }),
            el('td', { text: e.forecast || '\u2014' }),
            el('td', { text: e.previous || '\u2014' }));
          rows.push(row);
        }
        if (!divider) {
          // the published week has run out — normal on a weekend
          divider = el('tr', { class: 'cal-now' }, el('td', { colspan: '7',
            text: 'now \u00b7 ' + stampTz(now, this.tz, off) +
                  ' \u00b7 nothing further in this week\u2019s calendar' }));
          rows.push(divider);
        }
        wrap.append(table(['Time', 'When', 'Ccy', 'Impact', 'Event', 'Forecast', 'Previous'], rows));

        if (!this.calScrolled) {
          this.calScrolled = true;      // open on now, not on Monday morning
          requestAnimationFrame(() => divider.scrollIntoView({ block: 'center' }));
        }
      };

      paint();
      this.calTimer = setInterval(() => {
        if (this.tab === 'calendar' && wrap.isConnected) paint();
        else clearInterval(this.calTimer);
      }, 30000);
      return;
    }
  }
}

export class Tape {
  constructor() {
    this.host = $('#tape');
    this.sym = $('#tapeSym');
    this.rows = [];
    this.lastTs = 0;
  }

  reset(symbol) {
    this.rows = [];
    this.lastTs = 0;
    this.sym.textContent = symbol;
    this.host.innerHTML = '';
  }

  push(payload) {
    const ticks = (payload && payload.ticks) || [];
    if (!ticks.length) return;
    const d = payload.digits ?? 5;
    for (const t of ticks) {
      if (t.ts <= this.lastTs) continue;
      this.lastTs = t.ts;
      this.rows.unshift({ ...t, d });
    }
    this.rows = this.rows.slice(0, 120);
    this.host.innerHTML = '';
    for (const t of this.rows) {
      this.host.append(el('div', { class: 'tape-row' },
        el('span', { class: 't', text: hhmmss(t.ts) }),
        el('span', { class: t.side === 'buy' ? 'up' : 'down', text: Number(t.price).toFixed(t.d) }),
        el('span', { class: 'dim', text: `${Number(t.bid).toFixed(t.d)} / ${Number(t.ask).toFixed(t.d)}` })));
    }
  }
}

export function renderSpec(spec, quote) {
  const host = $('#spec');
  host.innerHTML = '';
  if (!spec || !spec.symbol) { host.append(empty('no contract data')); return; }
  const d = spec.digits ?? 5;
  const spread = quote && quote.ask && quote.bid ? (quote.ask - quote.bid) : spec.spread_current;
  const points = spread && spec.point ? spread / spec.point : null;
  const rows = [
    ['Digits', spec.digits],
    ['Point', spec.point ? spec.point.toFixed(Math.min(8, d + 1)) : '—'],
    ['Spread', points !== null ? `${points.toFixed(1)} pts` : '—'],
    ['Contract', num(spec.contract_size, 0)],
    ['Lot min/step', `${spec.volume_min ?? '—'} / ${spec.volume_step ?? '—'}`],
    ['Lot max', spec.volume_max ?? '—'],
    ['Tick value', spec.tick_value != null ? num(spec.tick_value, 4) : '—'],
    ['Margin/lot', spec.margin_per_lot ? money(spec.margin_per_lot, spec.currency_margin || '') : '—'],
    ['Profit ccy', spec.currency_profit || '—'],
    ['Stops level', spec.stops_level_points ?? 0],
  ];
  for (const [k, v] of rows) {
    host.append(el('dt', { text: k }), el('dd', { text: String(v) }));
  }
}

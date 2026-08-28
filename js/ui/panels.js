/* panels.js — bottom tabs (positions / orders / history / calendar), the
   right-hand tape and the contract spec block. All read-only views. */

import { $, el, hhmmss, load, money, num, px, relTime, save, stamp,
         stampTz, tzLabel, TZ_MODES } from '../util.js';
import { Backtest } from './backtest.js';
import { closeMenu } from './menu.js';

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
    /* Which bottom tab you were on is a UI setting like any other. It was the
       one thing in the footer that did not survive a reload -- open History,
       come back, and you are looking at Positions again. */
    this.tab = load('panelTab', 'positions');
    /* What to return to when a hover-preview ends; null when nothing is being
       previewed, so it doubles as the "is this a preview" flag. */
    this.peekTab = null;
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
      /* Clicking a tab COMMITS whatever was being previewed: there is no longer
         a tab to fall back to when the pointer leaves. */
      this.peekTab = null;
      /* Clicking a tab on a collapsed strip pins the panel open on it, which is
         what the click meant. Peeking History and having to click twice -- once
         for the tab, once for the strip -- would be a gesture that reads as
         choosing something and does not choose it. */
      if (this.size === 'collapsed') this.setSize('normal');
      save('panelTab', this.tab);
      /* Through paintTabs, not an inline toggle: it also writes the BACKTEST
         tab's label, and a second place that repaints the strip is a second
         place that can forget to. */
      this.paintTabs();
      this.render();
    });

    /* index.html marks Positions active, so a restored tab has to be applied to
       the buttons as well as to `this.tab` -- otherwise the panel renders
       History while the footer highlights Positions. */
    this.paintTabs();

    /* Three panel sizes, one source of truth. The buttons previously toggled two
       independent classes, so expanded+collapsed could both be set and the
       glyphs stopped describing the actual state. */
    const setSize = (size) => {
      document.body.classList.toggle('bottom-collapsed', size === 'collapsed');
      document.body.classList.toggle('bottom-expanded', size === 'expanded');
      /* Nothing to repaint: there is no button. The panel's own height is the
         indicator, and the tab strip carries the affordance in its title. */
      const hint = size === 'collapsed'
        ? 'Hover a tab to peek · click to pin the panel open'
        : 'Click to collapse the panel';
      $('#tabs').title = hint;
      $('#statusbar').title = hint;
      /* A pinned panel is never also peeking, or leaving the strip afterwards
         would run the fade-out on a panel that is now part of the layout. */
      if (size !== 'collapsed') document.body.classList.remove('bottom-peek');
      /* `size` is read by paintTabs, and it is not assigned until the end of
         this function -- so set it before repainting rather than showing the
         label for the state we are leaving. */
      this.size = size;
      this.paintTabs();

      /* COLLAPSED, THE TWO BARS BECOME ONE.
       *
       * A collapsed panel is a 34px strip of tab buttons sitting directly on
       * top of a 26px strip of account numbers -- sixty pixels of chrome to say
       * almost nothing, and two horizontal rules where the eye expects the
       * bottom of the app. So the account cells MOVE into the tab row, at the
       * right end, and the status bar collapses to zero height.
       *
       * The nodes are relocated rather than duplicated: two copies of a live
       * number is two things to keep in sync, and the id lookups that write
       * them (`#acBal` and friends) would find whichever came first in the
       * document. Moving keeps exactly one of each. */
      const host = size === 'collapsed'
        ? document.getElementById('tabsAcct')
        : document.getElementById('statusbar');
      /* The Ask button travels with them. Left behind it goes down with the
         hidden status bar, and a collapsed panel means no way to open the chat
         -- measured as a 0x0 button before this. */
      for (const id of ['acctCells', 'statusRight']) {
        const node = document.getElementById(id);
        if (node && host && node.parentElement !== host) host.append(node);
      }
    };
    this.setSize = setSize;
    /* Through setSize, not by assignment: it is what writes the strip's title,
       and a hint that only appears after you have already found the gesture is
       no hint at all.

       COLLAPSED on every load, deliberately not restored from the last session.
       The chart is what this app is for and the panel covers a third of it, so
       a reload should hand back the chart rather than whatever was open when
       you last closed the tab. Clicking the tab row opens it; the account
       figures ride along into the tab row and stay visible either way. */
    setSize('collapsed');

    /* HOVER PEEKS, CLICK PINS -- the contract the two side rails already use,
       and the tab row is this panel's stub: the part that stays on screen when
       it is collapsed.

       Either strip takes the click. They are the top and bottom edges of the
       same panel, and collapsed they are stacked together, so binding only one
       means guessing which band the pointer is in. Clicks landing on a button
       are ignored, or choosing a tab would collapse the panel out from under
       the choice. */
    const toggle = (e) => {
      if (e.target.closest('button')) return;
      e.preventDefault();
      /* ALT keeps the expanded state reachable without a control. Three states
         need two gestures somewhere; this is the one that adds no chrome. */
      if (e.altKey) setSize(this.size === 'expanded' ? 'normal' : 'expanded');
      else setSize(this.size === 'collapsed' ? 'normal' : 'collapsed');
    };
    $('#tabs').addEventListener('click', toggle);
    $('#statusbar').addEventListener('click', toggle);

    /* THE TAB BUTTONS ARE THE HOVER TARGET, not the strip. Peeking is a
       question -- "what is in History?" -- and the empty half of the strip does
       not ask it. Opening the panel there meant sweeping the pointer along the
       bottom of the window flashed the panel up over the chart for no reason.

       The buttons and the peeked panel are one hover region with a gap between
       them in event terms: leaving a button to enter the panel fires mouseleave
       before mouseenter. The same 140ms grace the rails use bridges it -- and
       it doubles as the bridge from one button to the next, so sliding across
       the strip does not flicker. */
    let shut = null;
    const hold = () => { clearTimeout(shut); shut = null; };

    /* HOVERING A TAB PREVIEWS THAT TAB. Peeking is a glance at what is down
       there, and on a five-tab strip the answer depends on which tab. The
       selection itself is NOT changed: `peekTab` remembers what to go back to,
       nothing is saved, and leaving restores it. A hover that silently
       re-pointed the panel would make the pointer a mode switch. */
    const preview = (name) => {
      if (this.size !== 'collapsed' || name === this.tab) return;
      if (!this.peekTab) this.peekTab = this.tab;
      this.tab = name;
      this.paintTabs();
      this.render();
    };
    const unpreview = () => {
      if (!this.peekTab) return;
      this.tab = this.peekTab;
      this.peekTab = null;
      this.paintTabs();
      this.render();
    };
    this.unpreview = unpreview;

    const release = () => {
      hold();
      /* Closing and un-previewing are the same event: the panel that fades out
         is the one showing the previewed tab, so putting the selection back any
         earlier would swap its contents on the way out. */
      shut = setTimeout(() => {
        document.body.classList.remove('bottom-peek');
        unpreview();
      }, 140);
    };
    const peek = (name) => {
      hold();
      if (this.size !== 'collapsed') return;
      document.body.classList.add('bottom-peek');
      if (name) preview(name);
    };
    for (const b of $('#tabs').querySelectorAll('.tab')) {
      b.addEventListener('mouseenter', () => peek(b.dataset.tab));
      b.addEventListener('mouseleave', release);
    }
    /* BACKTEST IS FOUR PANELS BEHIND ONE TAB, so hovering it offers them
       directly rather than making you open the tab and then find the picker.
       The same menu the tab's own toolbar builds -- one method, so a fifth view
       appears in both places or neither. Picking one pins the panel open on it,
       because choosing a view is not a peek. */
    const btTab = $('#tabs').querySelector('.tab[data-tab="backtest"]');
    if (btTab) {
      /* A HOVER-OPENED MENU HAS TO CLOSE ON HOVER-OUT. `openMenu` only closes on
         a pick or a pointerdown elsewhere, which is right for a menu you
         clicked open and wrong for one that appeared under the pointer: leaving
         the tab left it standing over the chart until you clicked something.

         The tab and the menu are one hover region with a gap between them --
         the menu is positioned 4px below the anchor, so crossing that gap fires
         mouseleave before mouseenter. The same grace window the rail peek uses
         bridges it. */
      /* SCOPED TO THIS TAB'S OWN MENU.
         `#menu` is shared by every menu in the app, so binding a hover-out
         auto-close to it closed ALL of them -- including the Snapshot menu,
         whose submenu you reach by leaving `#menu`. Moving from "Snapshot with
         info" to "Donchian rule" fired this mouseleave and took the whole menu
         down 160ms later, which looked exactly like the submenu vanishing
         under the pointer. Only close when the menu on screen is the one this
         tab opened. */
      let shutMenu = null;
      let owned = false;
      const holdMenu = () => { clearTimeout(shutMenu); shutMenu = null; };
      const releaseMenu = () => {
        holdMenu();
        if (!owned) return;
        shutMenu = setTimeout(() => { if (owned) closeMenu(); }, 160);
      };
      btTab.addEventListener('mouseenter', () => {
        holdMenu();
        owned = true;                 // this tab is the one showing a menu now
        this.backtest.openViewMenu(btTab, () => {
          this.peekTab = null;
          this.tab = 'backtest';
          save('panelTab', this.tab);
          if (this.size === 'collapsed') setSize('normal');
          this.paintTabs();          // after setSize, so the label sees the new size
          this.render();
        });
      });
      btTab.addEventListener('mouseleave', releaseMenu);
      const menuRoot = $('#menu');
      if (menuRoot) {
        menuRoot.addEventListener('mouseenter', holdMenu);
        menuRoot.addEventListener('mouseleave', releaseMenu);
      }
      /* another feature opened the shared menu: this tab no longer owns it, so
         its hover-out must not close what is now someone else's menu */
      document.addEventListener('menu:opened', (e) => {
        if (e.detail?.anchor !== btTab) { owned = false; holdMenu(); }
      });
    }
    /* The panel keeps itself open while the pointer is in it. Closed it is
       `pointer-events:none`, so this can never open one. */
    $('#panel').addEventListener('mouseenter', () => peek());
    $('#panel').addEventListener('mouseleave', release);
  }

  set(kind, rows) {
    this.data[kind] = rows || [];
    if (kind === 'positions') $('#posCount').textContent = this.data.positions.length;
    if (kind === 'orders') $('#ordCount').textContent = this.data.orders.length;
    /* Only repaint if this is the tab on screen -- including when that is a
       hover-preview, so live rows keep ticking under the pointer. */
    if (this.tab === kind) this.render();
  }

  /* The buttons follow `this.tab` wherever it points -- including at a preview,
     so the highlight says which tab you are looking at rather than which one is
     pinned. index.html marks Positions active, so a restored tab has to be
     applied here too or the panel renders History under a Positions highlight. */
  paintTabs() {
    for (const x of $('#tabs').querySelectorAll('.tab')) {
      x.classList.toggle('active', x.dataset.tab === this.tab);
    }
    /* THE BACKTEST TAB NAMES THE PANEL IT IS SHOWING. Four panels behind one
       label meant the strip could not say which one was up; now the tab reads
       `Elliott Replay` while that panel is open, and falls back to `Backtest`
       whenever it is not the thing on screen -- another tab is selected, or the
       panel is collapsed and there is nothing being shown at all. */
    const bt = $('#tabs').querySelector('.tab[data-tab="backtest"]');
    if (!bt) return;
    const showing = this.tab === 'backtest' && this.size !== 'collapsed';
    bt.textContent = showing ? this.backtest.viewLabel() : 'Backtest';
  }

  render() {
    const h = this.host;
    document.body.classList.toggle('bt-active', this.tab === 'backtest');
    document.body.classList.toggle('cal-active', this.tab === 'calendar');
    if (this.tab === 'backtest') {
      /* Not while PREVIEWING, and not while COLLAPSED. Backtest asks for the
         expanded panel, and a hover that throws the layout to 72vh -- then puts
         it back when the pointer moves on -- is the page jumping under the
         mouse. Worse, with Backtest as the pinned tab the RESTORE re-rendered it
         and this line un-collapsed a panel the user had just closed. A peek
         stays a peek; clicking it pins the panel first, and then it gets its
         room. */
      const asked = this.size !== 'expanded' && this.size !== 'collapsed';
      if (asked && !this.peekTab) this.setSize('expanded');
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

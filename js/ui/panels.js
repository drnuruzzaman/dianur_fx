/* panels.js — bottom tabs (positions / orders / history / calendar), the
   right-hand tape and the contract spec block. All read-only views. */

import { $, el, hhmmss, load, money, num, px, relTime, save, stamp,
         stampTz, tzLabel, TZ_MODES, TF_MS } from '../util.js';
import { Backtest } from './backtest.js';
import { SignalBoard } from './signalboard.js';
import { closeMenu } from './menu.js';
import { onRayoLedger, rayoFills, rayoRecordStart } from '../chart/rayoledger.js';

const table = (cols, rows) => {
  const t = el('table', { class: 'grid' });
  t.append(el('thead', {}, el('tr', {}, cols.map((c) => el('th', { text: c })))));
  t.append(el('tbody', {}, rows));
  return t;
};

const empty = (msg) => el('div', { class: 'empty', text: msg });

const plCell = (v, extra = '') => el('td', {
  class: `${v >= 0 ? 'up' : 'down'}${extra ? ' ' + extra : ''}`,
  text: (v >= 0 ? '+' : '') + num(v),
});

/* IS THE ACCOUNT COVERED. The eye in the status bar owns this class and the app
   starts with it on, so this is the default state, not the exception. */
const covered = () => document.body.classList.contains('acct-hidden');

/**
 * What Positions and Orders show while the account is covered.
 *
 * NOTHING, AND IT SAYS SO. These two tabs are the account by another route --
 * a row carries the instrument, the size and the running P&L, which is most of
 * what the eye was pressed to conceal. Leaving them readable made the eye a
 * gesture rather than a control.
 *
 * A NOTE RATHER THAN AN EMPTY PANEL, because "no open positions" and "not
 * shown" are opposite facts and a blank tab reads as the first. Someone
 * checking whether they are flat must not be told they are.
 */
const sealed = (what) => el('div', { class: 'empty sealed' },
  `${what} are hidden with the account figures — press the eye in the status `
  + 'bar to reveal');

/** Price decimals by instrument: gold 2, yen 3, the rest 5. */
const digitsFor = (sym) => (/JPY/i.test(sym || '') ? 3 : /XAU|XAG/i.test(sym || '') ? 2 : 5);

/**
 * Which Rayo trade a broker position is: same symbol and side, opened within
 * one bar of a fill the live scorer recorded (at least five minutes, for 1m).
 * Read straight from data/scalper_scored.jsonl through rayoledger.js, so it
 * needs neither the Signal Board nor any bars. Same side alone matched a
 * week-old hand trade to today's 1m cell, which is why the time window.
 *
 * `extra` is the Signal Board's in-trade state when it has polled: it adds a
 * trade the scorer (hourly) has not written yet.
 */
function ruleMatch(row, extra) {
  const tMs = row.time_ms || 0;
  const near = (x) => x.side === row.side && Number.isFinite(x.fillMs)
    && Math.abs(tMs - x.fillMs) <= Math.max(TF_MS[x.tf] || 0, 300e3);
  const fills = rayoFills(row.symbol).concat(extra || []);
  const hit = fills.filter(near)
    .sort((x, y) => Math.abs(tMs - x.fillMs) - Math.abs(tMs - y.fillMs));
  if (!hit.length) {
    const start = rayoRecordStart();
    if (start === null) {
      return el('td', { class: 'dim', text: '…', title: 'Loading the rule ledger' });
    }
    if (tMs < start - 3600e3) {
      return el('td', { class: 'dim', text: 'before record',
        title: `Opened before the rule's scored ledger begins (${stamp(start)}), `
               + 'so it cannot be matched either way' });
    }
    return el('td', { class: 'dim', text: 'manual',
      title: `No Rayo fill on ${row.symbol} ${String(row.side).toUpperCase()} within `
             + 'one bar of this open time -- placed by hand, or by something else' });
  }
  const h = hit[0];
  const label = `rule ${h.tf} ${String(h.side).toUpperCase()}`;
  return el('td', { class: 'up', text: label,
    title: `Opened ${Math.round(Math.abs(tMs - h.fillMs) / 1000)}s from the rule's ${h.tf} `
           + `${String(h.side).toUpperCase()} fill`
           + (h.id ? ` (${h.id}, ${h.outcome || 'open'}, stop ${h.stopAtr} ATR)` : '')
           + (hit.length > 1 ? `; also near ${hit.slice(1).map((x) => x.tf).join(', ')}` : '') });
}

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
    /* The Signal Board reads configs/alerts.json and asks the bridge; it owns
       its own poll timer, which is why it has to be told when it goes away. */
    this.signals = new SignalBoard();
    /* Rule matching on Positions reads the live ledger; repaint when it lands. */
    onRayoLedger(() => { if (this.tab === 'positions') this.render(); });
    this.tz = load('calendarTz', 'local');
    this.brokerOffsetMs = 0;          // handed over from /health by main.js

    $('#tabs').addEventListener('click', (e) => {
      const b = e.target.closest('.tab');
      if (!b) return;
      if (this.tab === 'backtest' && b.dataset.tab !== 'backtest') this.backtest.hide();
      if (this.tab === 'signals' && b.dataset.tab !== 'signals') this.signals.hide();
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
    /* The Signal Board's stop-loss warning counts positions: keep it current
       without rebuilding the board (which would restart its poll). */
    else if (kind === 'positions' && this.tab === 'signals' && this.signals.repaint) {
      this.signals.repaint();
    }
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
    document.body.classList.toggle('sb-active', this.tab === 'signals');
    /* TABS THAT ASK FOR THE ROOM. Backtest and the Signal Board are both full
       views rather than a list of rows -- thirteen columns over a dozen cells
       does not read in a 200px strip any better than an equity curve does.

       Not while PREVIEWING, and not while COLLAPSED. A hover that throws the
       layout to 72vh -- then puts it back when the pointer moves on -- is the
       page jumping under the mouse. Worse, with one of these as the pinned tab
       the RESTORE re-rendered it and this line un-collapsed a panel the user
       had just closed. A peek stays a peek; clicking it pins the panel first,
       and then it gets its room. */
    if (this.tab === 'backtest' || this.tab === 'signals') {
      const asked = this.size !== 'expanded' && this.size !== 'collapsed';
      if (asked && !this.peekTab) this.setSize('expanded');
    }

    if (this.tab === 'backtest') {
      this.backtest.show(h);
      return;
    }
    h.innerHTML = '';
    const d = this.data;

    /* NOT BEHIND `covered()`. The eye hides what the ACCOUNT holds; the board
       shows what the RULE says, which is a statement about the market and is
       the same whether or not a position exists. The Book column is the account
       speaking, so it is the one thing the eye does reach. */
    if (this.tab === 'signals') {
      this.signals.render(h, () => this.data.positions);
      return;
    }

    /* BROKER POSITIONS, read only, with the checks that matter on one
       screen: which rows have no stop, and which (if any) are the Rayo rule's
       own trades rather than placed by hand. Moved here from the Signal Board,
       which shows what the RULE says -- and which is not behind the account
       eye, so a positions table there leaked what the eye hides. Pending
       orders are on the Orders tab. */
    if (this.tab === 'positions') {
      if (covered()) return void h.append(sealed('positions'));
      /* The Signal Board's in-trade cells, if it has polled -- a fill the
         hourly scorer has not written yet. Optional: the ledger does the rest. */
      const boardFills = [];
      const scalp = this.signals && this.signals.scalp;
      for (const c of (this.signals && this.signals.scalpCells) || []) {
        const t = scalp && scalp.get(`${c.symbol}|${c.tf}`);
        if (t && t.held) boardFills.push({ tf: c.tf, side: t.side, fillMs: t.fillMs,
                                           symbol: c.symbol });
      }

      /* ---- open positions ---- */
      const P = d.positions.slice().sort((x, y) => (y.time_ms || 0) - (x.time_ms || 0));
      let pl = 0, sw = 0;
      const lots = { buy: 0, sell: 0 };
      const noStop = P.filter((p) => !p.sl).length;
      const prow = P.map((p) => {
        const dg = digitsFor(p.symbol);
        const buy = p.side === 'buy';
        const mine = boardFills.filter((x) => x.symbol.toUpperCase() === String(p.symbol).toUpperCase());
        pl += Number(p.profit) || 0;
        sw += Number(p.swap) || 0;
        lots[buy ? 'buy' : 'sell'] += Number(p.volume) || 0;
        return el('tr', { title: p.comment ? `comment: ${p.comment}` : '' },
          el('td', { text: stamp(p.time_ms) }),
          el('td', { class: 'sym', text: p.symbol }),
          el('td', { class: buy ? 'up' : 'down', text: String(p.side).toUpperCase() }),
          el('td', { class: 'pv', text: num(p.volume, 2) }),
          el('td', { class: 'pv', text: px(p.price_open, dg) }),
          el('td', { class: 'pv', text: px(p.price_current, dg) }),
          p.sl ? el('td', { class: 'pv down', text: px(p.sl, dg) })
               : el('td', { class: 'down sl-warn', text: '⚠ none',
                            title: 'No stop loss: this position has no protection at the broker' }),
          el('td', { class: 'pv', text: p.tp ? px(p.tp, dg) : '—' }),
          el('td', { class: 'pv', text: num(p.swap) }),
          plCell(p.profit, 'pv'),
          ruleMatch(p, mine),
          el('td', { class: 'dim', text: p.ticket }));
      });
      /* THE STOP-LOSS WARNING, as on the Orders tab: a red count in the
         header and `⚠ none` in the SL column. */
      h.append(el('div', { class: 'sb-sub' }, `Positions ${P.length}`,
        el('span', { class: 'sb-sub-note', text: P.length
          ? `buy ${num(lots.buy, 2)} lots · sell ${num(lots.sell, 2)} lots · `
            + `floating P/L ${num(pl)} ${this.currency} · swap ${num(sw)}`
          : 'flat' }),
        ...(noStop ? [el('span', { class: 'sb-sub-note down',
          title: 'These positions carry no stop loss at the broker',
          text: ` · ⚠ ${noStop} without a stop loss` })] : [])));
      if (P.length) {
        prow.push(el('tr', {},
          el('td', { text: '' }), el('td', { class: 'sym', text: 'TOTAL' }),
          ...Array.from({ length: 6 }, () => el('td', { text: '' })),
          el('td', { class: 'pv', text: num(sw) }), plCell(pl, 'pv'),
          el('td', { text: '' }), el('td', { text: '' })));
        h.append(table(['Opened', 'Symbol', 'Side', 'Lots', 'Open', 'Now', 'SL', 'TP',
          'Swap', `P/L ${this.currency}`, 'Rule', 'Ticket'], prow));
      } else {
        h.append(empty('no open positions'));
      }

      return;
    }

    /* PENDING ORDERS AT THE BROKER, read only: price decimals by instrument,
       and a stop-loss warning -- a count in the header and a red `⚠ none` in
       the SL column -- for any order that carries no stop. */
    if (this.tab === 'orders') {
      if (covered()) return void h.append(sealed('orders'));
      const O = d.orders.slice().sort((x, y) => (y.time_ms || 0) - (x.time_ms || 0));
      /* THE STOP-LOSS WARNING. An order without a stop becomes a position
         without one the moment it fills; say how many, before they do. */
      const bare = O.filter((o) => !o.sl).length;
      h.append(el('div', { class: 'sb-sub' }, `Pending orders ${O.length}`,
        el('span', { class: 'sb-sub-note',
                     text: O.length ? 'resting at the broker, not filled' : 'none resting' }),
        ...(bare ? [el('span', { class: 'sb-sub-note down',
          title: 'These orders carry no stop loss: if they fill, the position has no stop',
          text: ` · ⚠ ${bare} without a stop loss` })] : [])));
      if (!O.length) h.append(empty('no pending orders'));
      else {
        h.append(table(['Placed', 'Symbol', 'Type', 'Lots', 'Price', 'SL', 'TP', 'Ticket'],
          O.map((o) => {
            const dg = digitsFor(o.symbol);
            const type = String(o.type || o.side || '');
            const side = type.startsWith('buy') ? 'buy' : type.startsWith('sell') ? 'sell' : '';
            return el('tr', { title: o.comment ? `comment: ${o.comment}` : '' },
              el('td', { text: stamp(o.time_ms) }),
              el('td', { class: 'sym', text: o.symbol }),
              el('td', { class: side === 'buy' ? 'up' : side === 'sell' ? 'down' : '',
                         text: type.replace('_', ' ').toUpperCase() }),
              el('td', { class: 'pv', text: num(o.volume, 2) }),
              el('td', { class: 'pv', text: px(o.price_open ?? o.price, dg) }),
              o.sl ? el('td', { class: 'pv down', text: px(o.sl, dg) })
                   : el('td', { class: 'down sl-warn', text: '⚠ none',
                                title: 'No stop loss: if this order fills, the position has no stop' }),
              el('td', { class: 'pv', text: o.tp ? px(o.tp, dg) : '—' }),
              el('td', { class: 'dim', text: o.ticket }));
          })));
      }
      return;
    }

    if (this.tab === 'deals') {
      if (!d.deals.length) return void h.append(empty('no deals in the window'));
      const net = d.deals.reduce((a, x) => a + (x.profit || 0) + (x.commission || 0) + (x.swap || 0), 0);
      /* THE STOP-LOSS WARNING, as on Positions and Orders -- per POSITION,
         since a deal carries no stop of its own. The bridge sends the highest
         stop any order of the position carried (`position_sl`) and why each
         deal closed (`reason`: 4 stop loss, 5 take profit, 6 stop out). A stop
         added later by modification leaves no order, so a position counts as
         protected if it had an order stop, was closed by its stop, or is
         still open with a stop now. */
      const trade = (x) => x.side === 'buy' || x.side === 'sell';
      const stopped = new Set(d.deals.filter((x) => x.reason === 4 || x.reason === 6)
        .map((x) => x.position_id));
      const openSl = new Map((d.positions || []).map((p) => [p.ticket, p.sl]));
      const hasStop = (x) => (x.position_sl > 0) || stopped.has(x.position_id)
        || (openSl.get(x.position_id) > 0);
      const known = d.deals.some((x) => 'position_sl' in x);
      const bare = new Set(d.deals.filter((x) => trade(x) && x.position_id && !hasStop(x))
        .map((x) => x.position_id));
      const slCell = (x) => {
        if (!trade(x)) return el('td', { class: 'dim', text: '' });
        if (!known) return el('td', { class: 'dim', text: '—',
          title: 'Restart the bridge to read stops for history' });
        const dg = digitsFor(x.symbol);
        if (x.reason === 4 || x.reason === 6) {
          return el('td', { class: 'down', text: x.reason === 6 ? 'stop out' : 'stop hit',
            title: 'This deal was closed by the stop' });
        }
        if (x.position_sl > 0) return el('td', { class: 'pv down', text: px(x.position_sl, dg) });
        if (hasStop(x)) return el('td', { class: 'dim', text: 'set later',
          title: 'The stop was added after opening; its price is not in the order history' });
        return el('td', { class: 'down sl-warn', text: '⚠ none',
          title: 'No stop loss on this position' });
      };
      const rows = d.deals.map((x) => el('tr', {},
        el('td', { class: 'sym', text: x.symbol || '—' }),
        el('td', { class: x.side === 'buy' ? 'up' : 'down', text: String(x.side || x.type || '').toUpperCase() }),
        el('td', { text: num(x.volume, 2) }),
        el('td', { text: px(x.price, digitsFor(x.symbol)) }),
        slCell(x),
        el('td', { text: num(x.commission || 0) }),
        el('td', { text: num(x.swap || 0) }),
        plCell(x.profit || 0),
        el('td', { text: stamp(x.time_ms) })));
      rows.push(el('tr', {},
        el('td', { class: 'sym', text: 'NET' }),
        ...Array.from({ length: 6 }, () => el('td', { text: '' })),
        plCell(net), el('td', { text: '' })));
      const positions = new Set(d.deals.filter((x) => trade(x) && x.position_id)
        .map((x) => x.position_id));
      h.append(el('div', { class: 'sb-sub' }, `History ${d.deals.length} deals`,
        el('span', { class: 'sb-sub-note',
                     text: `${positions.size} positions · net ${num(net)} ${this.currency}` }),
        ...(known && bare.size ? [el('span', { class: 'sb-sub-note down',
          title: 'Positions in this window that had no stop loss',
          text: ` · ⚠ ${bare.size} without a stop loss` })] : [])));
      h.append(table(['Symbol', 'Side', 'Lots', 'Price', 'SL', 'Comm', 'Swap', 'Profit', 'Time'], rows));
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

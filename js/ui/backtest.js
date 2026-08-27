/* backtest.js — the Backtest tab.
 *
 * A VIEWER. Python computes and writes runs/index.json; this renders it and
 * nothing else. If a number is not in that file it does not appear here, which
 * is what stops the page and the engine ever disagreeing.
 *
 * Panels:
 *   Runs        every run on disk, sortable, click to inspect
 *   Simple      one run: equity curve, headline metrics, cost split
 *   Download market data
 *               is the machine trustworthy right now — data coverage, Stage 1
 *               gates, code hash, and the bulk download with progress. Named
 *               for what it is used for; the view key is still `components`
 *   Elliott     a replay SANDBOX: its own chart, its own bars, stepped one bar
 *               at a time with the future removed. It is here rather than on
 *               the live chart on purpose — a strategy under test must not be
 *               able to disturb the chart you trade from. See js/ui/replay.js.
 *
 * There is no Robustness panel: Stage 1 is a command-line verdict on eight
 * cells, and a UI for eight rows of CSV would be decoration. It belongs here
 * once there are enough runs to compare.
 */

import { api, base } from '../api.js';
import { $, compact, el, num, signed, stamp } from '../util.js';
import { openMenu, toast } from './menu.js';
import { ElliottReplay } from './replay.js';
import { StrategyReplay } from './strategyreplay.js';

const INDEX = 'runs/index.json';

/* The panels, in menu order. `components` keeps its key -- it is what the view
   state and every internal reference already use -- while the LABEL says what
   the panel is actually for, which is getting bar data onto disk. */
const VIEWS = [
  { key: 'run', label: 'Run a test' },
  { key: 'runs', label: 'Runs' },
  { key: 'components', label: 'Download market data' },
  { key: 'elliott', label: 'Elliott Replay' },
  { key: 'strategy', label: 'Strategy Replay' },
];

export class Backtest {
  constructor() {
    this.doc = null;
    this.selected = null;
    this.sortKey = 'created_utc';
    this.sortDir = -1;
    this.view = 'runs';
    this.pollTimer = null;
    this.jobTimer = null;
    this.page = 0;
    this.perPage = 20;
    /* Run ids currently being deleted. Held here rather than in the DOM: the
       panel re-renders whenever you pick another run or a poll lands, and a
       'deleting' state living in a node disappears with it. A Set, so several
       can be in flight and each row shows its own state. */
    this.deleting = new Set();
    /* Run selections live here so a re-render does not reset them. Symbols and
       timeframes are chosen from what is actually ON DISK: a backtest can only
       read downloaded history, so offering a free-text symbol invites a run that
       fails on a missing file. */
    this.sel = { kind: 'backtest', symbols: null, tfs: null };
    /* The download picker searches the BROKER's whole universe (500 tickers),
       not what is on disk — downloading something you already have is not the
       point. Fetched once and cached. */
    this.dl = { symbols: new Set(['XAUUSD.a', 'USDJPY.a']),
                tfs: new Set(['15m', '1h', '4h', '1d']) };
    this.brokerSymbols = null;
    /* The Elliott replay sandbox. Built lazily and kept across view switches so
       stepping through 300 bars is not thrown away by a click on Runs. It owns
       its own Chart and its own bars; see js/ui/replay.js for why it is here
       rather than on the live chart. */
    this.elliott = null;
    /* The strategy replay sandbox, kept for the same reason: stepping
       through 300 bars should survive a click on Runs. Same contract as
       the Elliott one -- its own Chart, its own bars, a no-op onChange. */
    this.strategy = null;
  }

  /* Watch the job runner for as long as the tab is open, not just while the Run
     panel is mounted. Previously polling stopped the moment you switched to
     Runs, so a finishing backtest never refreshed the list you were watching. */
  startWatch() {
    clearInterval(this.watchTimer);
    this.watchTimer = setInterval(async () => {
      let st;
      try {
        const res = await fetch(`${base()}/job/status`, { cache: 'no-store' });
        st = await res.json();
      } catch { return; }
      this.jobState = st;
      this.paintJob(st);
      const finished = !st.running && st.finished_ms;
      if (finished && this.seenFinish !== st.finished_ms) {
        const first = this.seenFinish === undefined;
        this.seenFinish = st.finished_ms;
        if (!first) {                       // a job completed while we watched
          toast(`${st.kind} finished — reloading runs`, 3500);
          this.view = 'runs';
          this.show(this.host);
        }
      }
    }, 1500);
  }

  /**
   * Re-read runs/index.json on its own.
   *
   * There was a ⟳ button for this, which is a manual step for something the
   * page can see for itself: the file changes when a run finishes or when
   * `tools/reindex_runs.py` is run beside the app, and neither is a moment the
   * reader is thinking about refreshing.
   *
   * It re-renders only when the file has ACTUALLY changed -- compared on the
   * index's own `generated_utc` and run count rather than on a timer -- because
   * a periodic re-render would reset scroll position and drop a half-typed
   * symbol every thirty seconds.
   */
  startIndexWatch() {
    clearInterval(this.indexTimer);
    this.indexTimer = setInterval(async () => {
      if (!this.host || document.hidden) return;
      try {
        const res = await fetch(INDEX + '?t=' + Date.now(), { cache: 'no-store' });
        if (!res.ok) return;
        const doc = await res.json();
        const same = this.doc
          && doc.generated_utc === this.doc.generated_utc
          && (doc.runs || []).length === (this.doc.runs || []).length;
        if (same) return;
        this.doc = doc;
        /* The replay sandbox owns a chart and a cursor; a re-render remounts it
           and would throw the reader's position away for a file they are not
           looking at. */
        if (this.view !== 'elliott') this.render();
      } catch { /* the next tick tries again */ }
    }, 15000);
  }

  /** Called when the tab becomes visible. */
  async show(host) {
    this.host = host;
    host.innerHTML = '';
    host.append(el('div', { class: 'bt-loading', text: 'reading runs/index.json…' }));
    try {
      const res = await fetch(INDEX + '?t=' + Date.now(), { cache: 'no-store' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      this.doc = await res.json();
    } catch (err) {
      host.innerHTML = '';
      host.append(el('div', { class: 'empty' },
        'no runs/index.json — build it with: python tools/reindex_runs.py'));
      return;
    }
    // a delete may still be in flight for a run already gone from the index
    for (const id of [...this.deleting]) {
      if (!this.doc.runs.some((r) => r.run_id === id)) this.deleting.delete(id);
    }
    this.startWatch();
    this.startIndexWatch();
    if (this.brokerSymbols === null) {
      // best effort: without the bridge we fall back to what is on disk
      this.brokerSymbols = [];
      fetch(`${base()}/symbols`, { cache: 'no-store' })
        .then((r) => r.json())
        .then((b) => { this.brokerSymbols = b.symbols || []; })
        .catch(() => { this.brokerSymbols = []; });
    }
    if (!this.selected && this.doc.runs.length) {
      this.selected = [...this.doc.runs].sort(byKey('created_utc', -1))[0].run_id;
    }
    this.render();
  }

  /**
   * The Elliott replay sandbox.
   *
   * Mounted into a host this panel owns, so it is torn down with the tab and
   * cannot outlive it. Nothing about it reaches the live charts: it fetches its
   * own bars and its Chart has a no-op `onChange`, which is what keeps a Chart
   * from writing workspace keys.
   */
  elliottPanel() {
    const host = el('div', { class: 'bt-panel bt-elliott' });
    if (!this.elliott) this.elliott = new ElliottReplay();
    /* Mount after the node is in the document: Chart measures its host on
       construction, and a detached div measures zero. */
    queueMicrotask(() => { if (host.isConnected) this.elliott.mount(host); });
    return host;
  }

  /**
   * The strategy replay sandbox: the one validated rule, stepped bar by bar.
   *
   * Same isolation contract as the Elliott panel -- see js/ui/replay.js for
   * why a validation tool must not be able to disturb the live chart.
   */
  strategyPanel() {
    const host = el('div', { class: 'bt-panel bt-strategy' });
    if (!this.strategy) this.strategy = new StrategyReplay();
    /* Mount after the node is in the document: Chart measures its host on
       construction, and a detached div measures zero. */
    queueMicrotask(() => { if (host.isConnected) this.strategy.mount(host); });
    return host;
  }

  hide() {
    if (this.elliott) this.elliott.unmount();
    if (this.strategy) this.strategy.unmount();
    clearInterval(this.indexTimer);
    clearInterval(this.pollTimer);
    clearInterval(this.jobTimer);
    clearInterval(this.watchTimer);
    this.pollTimer = null;
    this.jobTimer = null;
  }

  render() {
    const host = this.host;
    host.innerHTML = '';
    const body = el('div', { class: 'bt-body' });
    if (this.view === 'runs') body.append(this.runsPanel(), this.simplePanel());
    else if (this.view === 'run') body.append(this.runPanel());
    else if (this.view === 'elliott') body.append(this.elliottPanel());
    else if (this.view === 'strategy') body.append(this.strategyPanel());
    else body.append(this.componentsPanel());
    host.append(body);
  }

  /**
   * The view picker, opened from the toolbar button or from the BACKTEST tab.
   *
   * One method so the two entry points cannot drift: the tab strip's menu is
   * the toolbar's menu, and adding a fifth view adds it to both.
   */
  /** The label of the panel currently showing, for the tab that opens it. */
  viewLabel() {
    return (VIEWS.find((v) => v.key === this.view) || {}).label || 'Backtest';
  }

  openViewMenu(anchor, onPick) {
    openMenu(anchor,
      VIEWS.map((v) => ({ label: v.label, value: v.key, checked: this.view === v.key })),
      (key) => {
        this.view = key;
        if (onPick) onPick(key);
        if (this.host) this.render();
      });
  }

  /**
   * Searchable multi-select over a symbol universe. Shared by both panels so
   * they behave the same, but pointed at DIFFERENT universes:
   *   Run a Test  -> symbols with data on disk (a backtest can only read those)
   *   Components  -> every symbol the broker offers (the point is downloading
   *                  ones you do not have yet)
   * The list floats over the panel rather than sitting in the flow, so nothing
   * below it moves while you type.
   */
  symbolPicker({ selected, options, describe, placeholder, missingHint, onChange }) {
    const chips = el('div', { class: 'bt-chips' },
      ...[...selected].sort().map((sym) => el('button', {
        class: 'bt-chip-btn on', title: describe(sym) || sym,
        onclick: () => { selected.delete(sym); onChange(); },
      }, `${sym} \u00d7`)),
      selected.size ? null : el('span', { class: 'bt-meta', text: 'none selected' }));

    const err = el('div', { class: 'bt-symerr' });
    const list = el('div', { class: 'bt-suggest' });
    const search = el('input', { type: 'text', placeholder });
    const names = options.map((o) => o.name);

    const add = (sym) => {
      const match = names.find((n) => n.toUpperCase() === sym.toUpperCase());
      if (!match) { err.textContent = missingHint(sym); return; }
      selected.add(match);
      onChange();
    };

    const draw = () => {
      const q = search.value.trim().toUpperCase();
      err.textContent = '';
      list.innerHTML = '';
      if (!q) return;
      /* Rank: exact match, then match position, then SHORTER name. Without the
         length tiebreak, searching XAUUSD offered XAUUSD-F.a (the futures
         variant) above XAUUSD.a, because both match at 0 and '-' sorts before
         '.' — the wrong instrument, one keystroke away. */
      const rank = (n) => {
        const u = n.toUpperCase();
        return [u === q ? 0 : 1, u.indexOf(q), n.length, u];
      };
      const hits = options
        .filter((o) => o.name.toUpperCase().includes(q) && !selected.has(o.name))
        .sort((a, b) => {
          const ra = rank(a.name);
          const rb = rank(b.name);
          for (let i = 0; i < 3; i++) if (ra[i] !== rb[i]) return ra[i] - rb[i];
          return ra[3].localeCompare(rb[3]);
        })
        .slice(0, 10);
      if (!hits.length) {
        const taken = names.filter((n) => n.toUpperCase().includes(q) && selected.has(n));
        err.textContent = taken.length ? `${taken.join(', ')} already selected`
                                       : missingHint(search.value.trim());
        return;
      }
      hits.forEach((o, i) => list.append(el('button', {
        class: 'bt-suggest-row' + (i === 0 ? ' hi' : ''),
        onmousedown: (e) => e.preventDefault(),      // keep focus, beat blur
        onclick: () => add(o.name),
      },
      el('span', { class: 'sym', text: o.name }),
      el('span', { class: 'bt-meta', text: o.meta || '' }))));
    };

    const move = (delta) => {
      const rows = [...list.querySelectorAll('.bt-suggest-row')];
      if (!rows.length) return;
      const at = rows.findIndex((r) => r.classList.contains('hi'));
      const next = at < 0 ? (delta > 0 ? 0 : rows.length - 1)
                          : (at + delta + rows.length) % rows.length;
      rows.forEach((r, i) => r.classList.toggle('hi', i === next));
      rows[next].scrollIntoView({ block: 'nearest' });
    };

    search.addEventListener('input', draw);
    search.addEventListener('focus', draw);
    search.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowDown') { e.preventDefault(); move(1); return; }
      if (e.key === 'ArrowUp') { e.preventDefault(); move(-1); return; }
      if (e.key === 'Enter') {
        e.preventDefault();
        const hi = list.querySelector('.bt-suggest-row.hi .sym')
                || list.querySelector('.bt-suggest-row .sym');
        add(hi ? hi.textContent : search.value.trim());
      }
      if (e.key === 'Escape') { search.value = ''; draw(); }
    });
    search.addEventListener('blur', () => setTimeout(() => {
      if (list.isConnected) list.innerHTML = '';
    }, 150));

    return el('div', { class: 'bt-symbox' },
      el('div', { class: 'bt-symrow' },
        el('div', { class: 'bt-symfield' }, search, list), chips),
      err);
  }

  /* ------------------------------------------------------------- runs ---- */
  runsPanel() {
    const cols = [
      ['created_utc', 'When'],
      ['symbol', 'Symbol'], ['tf', 'TF'], ['strategy', 'Strategy'],
      ['trades', 'Trades'], ['return_pct', 'Return%'], ['pf', 'PF'],
      ['avg_R', 'avgR'], ['maxdd_pct', 'MaxDD%'],
    ];
    const table = el('table', { class: 'grid bt-runs' });
    table.append(el('thead', {}, el('tr', {}, cols.map(([k, label]) =>
      el('th', {
        onclick: () => {
          this.sortDir = this.sortKey === k ? -this.sortDir : -1;
          this.sortKey = k;
          this.render();
        },
        text: label + (this.sortKey === k ? (this.sortDir < 0 ? ' ↓' : ' ↑') : ''),
      })))));

    const all = [...(this.doc.runs || [])].sort(byKey(this.sortKey, this.sortDir));
    const pages = Math.max(1, Math.ceil(all.length / this.perPage));
    this.page = Math.min(this.page, pages - 1);
    const rows = all.slice(this.page * this.perPage, (this.page + 1) * this.perPage);
    const body = el('tbody');
    for (const r of rows) {
      const busy = this.deleting.has(r.run_id);
      body.append(el('tr', {
        class: (r.run_id === this.selected ? 'sel' : '') + (busy ? ' deleting' : ''),
        title: busy ? 'deleting' : null,
        onclick: busy ? null : () => { this.selected = r.run_id; this.render(); },
      },
      el('td', {
        class: 'bt-when' + (r.created_source === 'mtime' ? ' approx' : ''),
        title: (r.created_utc || 'unknown') +
               (r.created_source === 'mtime' ? '  (folder timestamp — this run wrote no config)' : ''),
        text: ago(r.created_utc) + (r.created_source === 'mtime' ? '~' : ''),
      }),
      el('td', { class: 'sym', text: r.symbol || '—' }),
      el('td', { text: r.tf || '' }),
      el('td', {}, busy
        ? el('span', { class: 'bt-busy', text: 'deleting\u2026' })
        : (r.strategy || '') + (r.confluence && r.confluence !== 'off'
          ? ' ·' + r.confluence : '')),
      el('td', { text: r.trades }),
      cell(r.return_pct, 1),
      cell(r.pf, 3, 1),
      cell(r.avg_R, 3),
      el('td', { class: 'down', text: r.maxdd_pct == null ? '—' : num(r.maxdd_pct, 1) })));
    }
    table.append(body);

    /* The table scrolls inside its own box, so the header stays put and the rest
       of the page does not move. 20 to a page keeps the DOM small: 68 runs today,
       and every backtest adds more. */
    const pager = el('div', { class: 'bt-pager' },
      el('button', {
        class: 'bt-btn', disabled: this.page === 0 || null,
        onclick: () => { this.page = Math.max(0, this.page - 1); this.render(); },
      }, '\u2039 prev'),
      el('span', { class: 'bt-meta',
        text: `${all.length ? this.page * this.perPage + 1 : 0}\u2013${
          Math.min((this.page + 1) * this.perPage, all.length)} of ${all.length}` }),
      el('button', {
        class: 'bt-btn', disabled: this.page >= pages - 1 || null,
        onclick: () => { this.page = Math.min(pages - 1, this.page + 1); this.render(); },
      }, 'next \u203a'),
      el('span', { class: 'spacer' }),
      el('label', { class: 'bt-meta' }, 'per page',
        el('select', {
          onchange: (e) => { this.perPage = Number(e.target.value); this.page = 0; this.render(); },
        }, ...[20, 50, 100].map((n) => el('option', {
          value: String(n), selected: n === this.perPage || null, text: String(n),
        })))));

    return el('div', { class: 'bt-runs-wrap' },
      el('div', { class: 'bt-scroll' }, table), pager);
  }

  /* ----------------------------------------------------------- simple ---- */
  simplePanel() {
    const r = (this.doc.runs || []).find((x) => x.run_id === this.selected);
    const wrap = el('div', { class: 'bt-detail' });
    if (!r) {
      wrap.append(el('div', { class: 'empty', text: 'select a run' }));
      return wrap;
    }

    // the sample floor, enforced in the UI as well as in the harness
    const thin = (r.trades || 0) < 200;
    wrap.append(el('div', { class: 'bt-head' },
      el('b', { text: `${r.symbol} ${r.tf} · ${r.strategy}` }),
      el('span', { class: 'bt-meta', text: r.run_id })));

    if (thin) {
      wrap.append(el('div', { class: 'bt-warn' },
        `${r.trades} trades — below the 200-trade floor. Metrics shown for ` +
        'inspection only; a result this size has reversed sign before.'));
    }

    const stats = el('div', { class: 'bt-stats' });
    const stat = (label, value, cls) => stats.append(el('div', { class: 'bt-stat' },
      el('span', { text: label }), el('b', { class: cls || '', text: value })));
    stat('Trades', String(r.trades));
    stat('Return', r.return_pct == null ? '—' : signed(r.return_pct, 1) + '%',
         (r.return_pct || 0) >= 0 ? 'up' : 'down');
    stat('Net', r.net_acct == null ? '—' : signed(r.net_acct, 0),
         (r.net_acct || 0) >= 0 ? 'up' : 'down');
    stat('Profit factor', r.pf == null ? '—' : num(r.pf, 3),
         (r.pf || 0) >= 1 ? 'up' : 'down');
    stat('avg R', r.avg_R == null ? '—' : signed(r.avg_R, 3),
         (r.avg_R || 0) >= 0 ? 'up' : 'down');
    stat('Win rate', r.win_pct == null ? '—' : num(r.win_pct, 1) + '%');
    stat('Max DD', r.maxdd_pct == null ? '—' : num(r.maxdd_pct, 1) + '%', 'down');
    stat('Buy & hold', r.buy_hold_pct == null ? '—' : signed(r.buy_hold_pct, 1) + '%');
    wrap.append(stats);

    wrap.append(this.equityCanvas(r));

    // costs, so their share of the result is never hidden
    const costs = [['spread', r.cost_spread], ['slippage', r.cost_slippage],
                   ['swap', r.cost_swap]].filter(([, v]) => v != null);
    if (costs.length) {
      wrap.append(el('div', { class: 'bt-costs' },
        el('span', { class: 'bt-meta', text: 'costs (profit ccy): ' }),
        ...costs.map(([k, v]) => el('span', { class: 'bt-chip' },
          `${k} ${num(v, 0)}`)),
        el('span', { class: 'bt-chip', text: `swap model: ${r.swap_model || '—'}` })));
    }

    const reasons = Object.entries(r.exit_reasons || {});
    if (reasons.length) {
      wrap.append(el('div', { class: 'bt-costs' },
        el('span', { class: 'bt-meta', text: 'exits: ' }),
        ...reasons.sort((a, b) => b[1] - a[1])
          .map(([k, v]) => el('span', { class: 'bt-chip', text: `${k} ${v}` }))));
    }

    wrap.append(this.deleteRow(r));
    wrap.append(el('div', { class: 'bt-meta bt-foot' },
      `${(r.first_bar || '').slice(0, 16)} → ${(r.last_bar || '').slice(0, 16)} · ` +
      `data ${r.data_hash || '?'} · params ${JSON.stringify(r.params || {})}`));
    return wrap;
  }

  /* Two clicks, never one: a run is cheap to regenerate but there is no undo,
     and the button sits next to a list you scroll fast. */
  deleteRow(r) {
    const row = el('div', { class: 'bt-delrow' });

    // already in flight: show that, and offer nothing to click
    if (this.deleting.has(r.run_id)) {
      row.append(el('span', { class: 'bt-busy',
                              text: `deleting ${r.run_id}\u2026` }));
      return row;
    }

    const arm = el('button', { class: 'bt-btn' }, 'Delete run');
    arm.addEventListener('click', () => {
      row.innerHTML = '';
      row.append(
        el('span', { class: 'bt-meta', text: `delete ${r.run_id}?` }),
        el('button', { class: 'bt-btn primary', onclick: async (e) => {
          if (e.currentTarget.disabled || this.deleting.has(r.run_id)) return;
          const id = r.run_id;
          this.deleting.add(id);        // survives every later re-render
          this.render();
          try {
            const res = await fetch(`${base()}/runs/delete?run_id=${encodeURIComponent(id)}`,
                                    { cache: 'no-store' });
            const body = await res.json();
            this.deleting.delete(id);
            if (body.error) {
              toast(body.error, 4000);
              this.render();            // back to a clickable Delete run
              return;
            }
            toast(`deleted ${id}`, 3000);
            if (this.selected === id) this.selected = null;
            this.show(this.host);       // re-read the index; the row is gone
          } catch (err) {
            this.deleting.delete(id);
            toast('bridge unreachable: ' + err.message, 4000);
            this.render();
          }
        } }, 'Confirm'),
        el('button', { class: 'bt-btn', onclick: () => this.render() }, 'Keep'));
    });
    row.append(arm);
    return row;
  }

  equityCanvas(r) {
    const wrap = el('div', { class: 'bt-equity' });
    const cv = el('canvas');
    wrap.append(cv);
    requestAnimationFrame(() => drawEquity(cv, r.equity || []));
    return wrap;
  }

  /* ------------------------------------------------------- components ---- */
  componentsPanel() {
    const d = this.doc;
    const wrap = el('div', { class: 'bt-components' });

    // ---- Stage 1 gates ----
    const gates = d.gates || [];
    const gsec = el('div', { class: 'bt-sec' },
      el('div', { class: 'bt-sec-h' }, 'Stage 1 gates',
        el('span', { class: 'bt-meta', text: gates.length
          ? `${gates.filter((g) => g.gate_sample && g.gate_control).length} of ${gates.length} cells pass both`
          : 'not run — python tools/stage1.py' })));
    if (gates.length) {
      const t = el('table', { class: 'grid' });
      t.append(el('thead', {}, el('tr', {}, ['Symbol', 'TF', 'Strategy', 'Trades',
        'avgR', 'Control p50', 'Pctile', 'Sample', 'Beats control']
        .map((h) => el('th', { text: h })))));
      t.append(el('tbody', {}, gates.map((g) => el('tr', {},
        el('td', { class: 'sym', text: g.symbol }),
        el('td', { text: g.tf }),
        el('td', { text: g.strategy }),
        el('td', { text: g.trades }),
        cell(g.avg_R, 3),
        cell(g.control_p50 ?? g.control_p50, 3),
        el('td', { text: g.percentile == null ? '—' : num(g.percentile, 1) }),
        el('td', { class: g.gate_sample ? 'up' : 'down', text: g.gate_sample ? 'ok' : 'low' }),
        el('td', { class: g.gate_control ? 'up' : 'down', text: g.gate_control ? 'PASS' : 'no' })))));
      gsec.append(t);
    }
    wrap.append(gsec);

    // ---- data coverage + download ----
    wrap.append(this.dataSection(d.data || {}));
    return wrap;
  }

  /* Backtests never touch MetaTrader -- they read the CSVs under data/ -- so the
     job runs without the MT5 lock and the live chart keeps ticking. The bridge
     only accepts a fixed set of commands; nothing here can name an executable. */
  runPanel() {
    const sec = el('div', { class: 'bt-components' });
    sec.append(el('div', { class: 'bt-sec-h' }, 'Run a test',
      el('span', { class: 'bt-meta', text: 'writes runs/, then reindexes' })));

    // what history exists, by symbol and timeframe
    const bars = (this.doc.data && this.doc.data.bars) || [];
    const bySymbol = new Map();
    for (const b of bars) {
      if (!bySymbol.has(b.symbol)) bySymbol.set(b.symbol, new Map());
      bySymbol.get(b.symbol).set(b.tf, b);
    }
    const allSymbols = [...bySymbol.keys()].sort();
    const TF_ORDER = ['1m', '5m', '15m', '30m', '1h', '4h', '1d', '1w'];
    const allTfs = TF_ORDER.filter((tf) =>
      allSymbols.some((sym) => bySymbol.get(sym).has(tf)));

    if (this.sel.symbols === null) {
      this.sel.symbols = new Set(allSymbols.filter((x) =>
        x === 'XAUUSD.a' || x === 'USDJPY.a'));
      if (!this.sel.symbols.size) this.sel.symbols = new Set(allSymbols.slice(0, 2));
    }
    if (this.sel.tfs === null) {
      this.sel.tfs = new Set(allTfs.filter((t) => t === '1h' || t === '4h'));
    }

    const kind = el('select', {
      onchange: (e) => { this.sel.kind = e.target.value; this.render(); },
    },
      el('option', { value: 'backtest', selected: this.sel.kind === 'backtest' || null,
                     text: 'baselines (donchian, ema_cross)' }),
      el('option', { value: 'backtest_tl', selected: this.sel.kind === 'backtest_tl' || null,
                     text: 'trendline + divergence' }),
      el('option', { value: 'stage1', selected: this.sel.kind === 'stage1' || null,
                     text: 'Stage 1 gates (control + bootstrap)' }));

    // the trendline runner fixes its own timeframes: 15M executes, 1H/4H/D1 are
    // context. Offering a timeframe picker there would be a lie.
    const tfLocked = this.sel.kind === 'backtest_tl';

    const chip = (label, on, disabled, title, toggle) => el('button', {
      class: 'bt-chip-btn' + (on ? ' on' : ''),
      disabled: disabled || null, title,
      onclick: disabled ? null : () => { toggle(); this.render(); },
    }, label);

    /* Symbols are searched, not listed: this broker offers 500 tickers and only
       a handful are downloaded, so a growing chip wall would be unusable. The
       search only matches symbols that HAVE data — asking for one that does not
       is the error case, and it says so rather than failing mid-run. */
    const chosenChips = el('div', { class: 'bt-chips' },
      ...[...this.sel.symbols].sort().map((sym) => {
        const tfMap = bySymbol.get(sym);
        const rows = tfMap ? [...tfMap.values()].reduce((a, b) => a + b.rows, 0) : 0;
        return el('button', {
          class: 'bt-chip-btn on', title: tfMap
            ? `${[...tfMap.keys()].join(', ')} — ${rows.toLocaleString()} bars on disk`
            : 'no data on disk',
          onclick: () => { this.sel.symbols.delete(sym); this.render(); },
        }, `${sym} \u00d7`);
      }),
      this.sel.symbols.size ? null : el('span', { class: 'bt-meta', text: 'none selected' }));

    const symError = el('div', { class: 'bt-symerr' });
    const suggestions = el('div', { class: 'bt-suggest' });
    const search = el('input', {
      type: 'text', placeholder: `search ${allSymbols.length} symbols with data\u2026`,
      value: this.sel.query || '',
    });

    const addSymbol = (sym) => {
      if (!bySymbol.has(sym)) {
        symError.textContent = `no bar data on disk for "${sym}" — download it first ` +
                               `(Components \u2192 Data on disk)`;
        return;
      }
      this.sel.symbols.add(sym);
      this.sel.query = '';
      this.render();
    };

    const renderSuggestions = () => {
      const q = search.value.trim().toUpperCase();
      this.sel.query = search.value;
      symError.textContent = '';
      suggestions.innerHTML = '';
      if (!q) return;
      const hits = allSymbols
        .filter((x) => x.toUpperCase().includes(q) && !this.sel.symbols.has(x))
        .sort((a, b) => (a.toUpperCase().indexOf(q) - b.toUpperCase().indexOf(q))
                        || a.localeCompare(b))
        .slice(0, 8);
      if (!hits.length) {
        const already = allSymbols.filter((x) => x.toUpperCase().includes(q));
        symError.textContent = already.length
          ? `${already.join(', ')} already selected`
          : `nothing on disk matches "${search.value.trim()}"`;
        return;
      }
      hits.forEach((sym, i) => {
        const tfMap = bySymbol.get(sym);
        suggestions.append(el('button', {
          class: 'bt-suggest-row' + (i === 0 ? ' hi' : ''),
          onmousedown: (e) => e.preventDefault(),   // keep focus, beat blur
          onclick: () => addSymbol(sym),
        },
        el('span', { class: 'sym', text: sym }),
        el('span', { class: 'bt-meta', text: [...tfMap.keys()].join(' ') })));
      });
    };
    const move = (delta) => {
      const rows = [...suggestions.querySelectorAll('.bt-suggest-row')];
      if (!rows.length) return;
      const at = rows.findIndex((r) => r.classList.contains('hi'));
      const next = at < 0 ? (delta > 0 ? 0 : rows.length - 1)
                          : (at + delta + rows.length) % rows.length;
      rows.forEach((r, i) => r.classList.toggle('hi', i === next));
      rows[next].scrollIntoView({ block: 'nearest' });
    };

    search.addEventListener('input', renderSuggestions);
    search.addEventListener('focus', renderSuggestions);
    search.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowDown') { e.preventDefault(); move(1); return; }
      if (e.key === 'ArrowUp') { e.preventDefault(); move(-1); return; }
      if (e.key === 'Enter') {
        e.preventDefault();
        const hi = suggestions.querySelector('.bt-suggest-row.hi .sym')
                || suggestions.querySelector('.bt-suggest-row .sym');
        if (hi) addSymbol(hi.textContent);
        else addSymbol(search.value.trim());
      }
      if (e.key === 'Escape') { search.value = ''; renderSuggestions(); }
    });
    // clicking away closes the list; the click on a row lands first
    search.addEventListener('blur', () => setTimeout(() => {
      if (suggestions.isConnected) suggestions.innerHTML = '';
    }, 150));
    requestAnimationFrame(renderSuggestions);

    /* The list is absolutely positioned over the panel rather than sitting in
       the flow: as an inline element it pushed the timeframe row, the hint and
       the progress bar down every keystroke, so the thing you were aiming at
       moved while you typed. */
    const symbolChips = el('div', { class: 'bt-symbox' },
      el('div', { class: 'bt-symrow' },
        el('div', { class: 'bt-symfield' }, search, suggestions),
        chosenChips),
      symError);

    const tfChips = el('div', { class: 'bt-chips' }, ...allTfs.map((tf) => {
      const have = [...this.sel.symbols].filter((sym) => bySymbol.get(sym).has(tf));
      const missing = [...this.sel.symbols].filter((sym) => !bySymbol.get(sym).has(tf));
      const unavailable = this.sel.symbols.size > 0 && have.length === 0;
      return chip(tf, !tfLocked && this.sel.tfs.has(tf), tfLocked || unavailable,
        tfLocked ? 'fixed by the trendline runner'
          : missing.length ? `not downloaded for ${missing.join(', ')}`
          : `${have.length} selected symbol(s) have this`,
        () => {
          if (this.sel.tfs.has(tf)) this.sel.tfs.delete(tf);
          else this.sel.tfs.add(tf);
        });
    }));

    const symbols = { get value() { return [...this.owner.sel.symbols].join(','); }, owner: this };
    const tfs = { get value() { return tfLocked ? '' : [...this.owner.sel.tfs].join(','); }, owner: this };
    // native pickers: the runners take YYYY-MM-DD, which is exactly what a
    // date input produces, so no parsing and no malformed dates
    const start = el('input', { type: 'date', value: '2021-01-01' });
    const end = el('input', { type: 'date', value: '' });
    const noSwap = el('input', { type: 'checkbox', checked: 'checked' });
    const tag = el('input', { type: 'text', value: '', placeholder: 'optional' });

    this.jobProgress = el('div', { class: 'bt-prog' },
      el('div', { class: 'bt-prog-bar' }), el('span', { class: 'bt-prog-txt' }));
    this.jobLog = el('pre', { class: 'bt-log' });

    const run = el('button', { class: 'bt-btn primary', disabled: true, onclick: async () => {
      if (problems().length) return;                 // belt and braces
      const q = new URLSearchParams({
        kind: kind.value, symbols: symbols.value.trim(), tfs: tfs.value.trim(),
        start: start.value, end: end.value, no_swap: noSwap.checked ? '1' : '0',
        tag: tag.value.trim(),
      });
      try {
        const res = await fetch(`${base()}/job/start?${q}`, { cache: 'no-store' });
        const body = await res.json();
        if (body.error) { toast(body.error, 4000); return; }
        toast(`${kind.value} started — ${body.expected_cells} cells`, 4000);
        this.pollJob();
      } catch (err) { toast('bridge unreachable: ' + err.message, 4000); }
    } }, 'Run');

    const stop = el('button', { class: 'bt-btn', onclick: async () => {
      try { await fetch(`${base()}/job/cancel`, { cache: 'no-store' }); } catch {}
      toast('cancelling job', 2500);
    } }, 'Cancel');

    const chosen = [...this.sel.symbols];
    const gaps = [];
    if (!tfLocked) {
      for (const sym of chosen) {
        for (const tf of this.sel.tfs) {
          if (!bySymbol.get(sym).has(tf)) gaps.push(`${sym} ${tf}`);
        }
      }
    }

    /* Run stays disabled until the request would actually be valid, and says
       which field is missing rather than making you guess. The date inputs are
       checked live because typing in them does not re-render the panel. */
    const problems = () => {
      const out = [];
      if (!this.sel.symbols.size) out.push('choose at least one symbol');
      if (!tfLocked && !this.sel.tfs.size) out.push('choose at least one timeframe');
      if (!start.value) out.push('set a start date');
      if (start.value && end.value && end.value < start.value) {
        out.push('the end date is before the start date');
      }
      return out;
    };
    const hint = el('div', { class: 'bt-warn' });
    const applyValidity = () => {
      const bad = problems();
      run.disabled = bad.length > 0;
      run.title = bad.length ? bad.join(' \u00b7 ') : 'start this test';
      hint.textContent = bad.length ? `needed: ${bad.join(' \u00b7 ')}` : '';
      hint.style.display = bad.length ? '' : 'none';
    };
    for (const input of [start, end, tag]) {
      input.addEventListener('input', applyValidity);
      input.addEventListener('change', applyValidity);
    }
    requestAnimationFrame(applyValidity);

    sec.append(el('div', { class: 'bt-dl' },
      el('div', { class: 'bt-dl-row' },
        el('label', {}, 'what', kind),
        el('label', {}, 'from', start),
        el('label', { title: 'leave empty for the latest bar' }, 'to', end),
        el('label', { title: 'carry is only knowable at today\u2019s rate' },
           'no swap', noSwap),
        el('label', {}, 'tag', tag),
        run, stop),
      el('div', { class: 'bt-pick' },
        el('span', { class: 'bt-pick-h', text: 'symbols' }), symbolChips),
      el('div', { class: 'bt-pick' },
        el('span', { class: 'bt-pick-h', text: 'timeframes' }), tfChips,
        tfLocked
          ? el('span', { class: 'bt-meta',
                         text: '15M executes, 1H/4H/D1 give context — fixed' })
          : null),
      hint,
      gaps.length
        ? el('div', { class: 'bt-warn',
                      text: `no data downloaded for ${gaps.join(', ')} — those cells will be skipped` })
        : null,
      this.jobProgress, this.jobLog,
      el('div', { class: 'bt-meta' },
        'Runs without the MetaTrader lock, so the live chart keeps ticking. ' +
        'Use the date range for in-sample and out-of-sample splits: on this feed ' +
        'USDJPY 15M starts 2007 but XAUUSD 15M only starts 2018, so a pre-2018 ' +
        'gold window is nearly empty.')));
    this.pollJob();
    return sec;
  }

  paintJob(st) {
    if (!st || !this.jobProgress || !this.jobProgress.isConnected) return;
    const bar = this.jobProgress.querySelector('.bt-prog-bar');
    const txt = this.jobProgress.querySelector('.bt-prog-txt');
    bar.style.width = `${st.pct || 0}%`;
    this.jobProgress.classList.toggle('running', !!st.running);
    txt.textContent = st.error ? `error: ${st.error}`
      : st.running ? `${st.kind} — ${st.done}/${st.total} cells (${st.pct}%)`
      : st.finished_ms ? `finished${st.exit_code ? ` (exit ${st.exit_code})` : ''}`
      : 'idle';
    if (this.jobLog) this.jobLog.textContent = (st.log || []).slice(-12).join('\n');
  }

  pollJob() {
    clearInterval(this.jobTimer);
    const tick = async () => {
      let st;
      try {
        const res = await fetch(`${base()}/job/status`, { cache: 'no-store' });
        st = await res.json();
      } catch { return; }
      if (!this.jobProgress || !this.jobProgress.isConnected) {
        clearInterval(this.jobTimer);
        return;
      }
      const bar = this.jobProgress.querySelector('.bt-prog-bar');
      const txt = this.jobProgress.querySelector('.bt-prog-txt');
      bar.style.width = `${st.pct || 0}%`;
      this.jobProgress.classList.toggle('running', !!st.running);
      txt.textContent = st.error ? `error: ${st.error}`
        : st.running ? `${st.kind} — ${st.done}/${st.total} cells (${st.pct}%)`
        : st.finished_ms ? `finished${st.exit_code ? ` (exit ${st.exit_code})` : ''}`
        : 'idle';
      this.jobLog.textContent = (st.log || []).slice(-12).join('\n');
      if (!st.running) {
        clearInterval(this.jobTimer);
        if (st.finished_ms && !this.lastJobSeen) {
          this.lastJobSeen = st.finished_ms;
        } else if (st.finished_ms && st.finished_ms !== this.lastJobSeen) {
          this.lastJobSeen = st.finished_ms;
          toast('job finished — reloading runs', 3000);
          this.show(this.host);
        }
      }
    };
    tick();
    this.jobTimer = setInterval(tick, 1200);
  }

  dataSection(data) {
    const sec = el('div', { class: 'bt-sec' });
    const man = data.manifest || {};
    sec.append(el('div', { class: 'bt-sec-h' }, 'Data on disk',
      el('span', { class: 'bt-meta', text: man.updated_utc
        ? `${man.broker_server || ''} · server offset ${(man.server_utc_offset_ms || 0) / 3600000}h · ${
            (man.on_disk && man.on_disk.bars ? man.on_disk.bars.mb : 0)}MB bars, ${
            (man.on_disk && man.on_disk.ticks ? (man.on_disk.ticks.mb / 1000).toFixed(1) : 0)}GB ticks`
        : 'no manifest' })));

    const t = el('table', { class: 'grid' });
    t.append(el('thead', {}, el('tr', {}, ['Symbol', 'TF', 'Rows', 'From', 'To', 'Size']
      .map((h) => el('th', { text: h })))));
    const body = el('tbody');
    for (const b of (data.bars || [])) {
      body.append(el('tr', {},
        el('td', { class: 'sym', text: b.symbol }), el('td', { text: b.tf }),
        el('td', { text: compact(b.rows) }), el('td', { text: b.first }),
        el('td', { text: b.last }), el('td', { text: b.mb + ' MB' })));
    }
    for (const k of (data.ticks || [])) {
      body.append(el('tr', {},
        el('td', { class: 'sym', text: k.symbol }), el('td', { text: 'tick' }),
        el('td', { text: k.days + ' days' }), el('td', { text: k.first }),
        el('td', { text: k.last }), el('td', { text: k.gb + ' GB' })));
    }
    t.append(body);
    sec.append(t);
    sec.append(el('div', { class: 'bt-meta bt-foot', text: man.time_base || '' }));
    sec.append(this.downloadBox());
    return sec;
  }

  /* -------------------------------------------------------- download ---- */
  downloadBox() {
    const box = el('div', { class: 'bt-dl' });
    const onDisk = new Map();
    for (const b of ((this.doc.data && this.doc.data.bars) || [])) {
      if (!onDisk.has(b.symbol)) onDisk.set(b.symbol, new Set());
      onDisk.get(b.symbol).add(b.tf);
    }

    // the broker universe if we have it, otherwise whatever is already local
    const universe = (this.brokerSymbols && this.brokerSymbols.length)
      ? this.brokerSymbols.map((x) => ({
          name: x.name,
          meta: (onDisk.has(x.name) ? 'on disk · ' : '') +
                String(x.path || '').split('\\').slice(0, -1).join(' / '),
        }))
      : [...onDisk.keys()].map((n) => ({ name: n, meta: 'on disk' }));

    const picker = this.symbolPicker({
      selected: this.dl.symbols,
      options: universe,
      describe: (sym) => (onDisk.has(sym)
        ? `already have ${[...onDisk.get(sym)].join(', ')}` : 'not downloaded yet'),
      placeholder: `search ${universe.length} broker symbols\u2026`,
      missingHint: (q) => (this.brokerSymbols && this.brokerSymbols.length
        ? `"${q}" is not offered by this broker`
        : `"${q}" not found — the bridge is offline, so only local symbols are listed`),
      onChange: () => this.render(),
    });

    const TF_ORDER = ['1m', '5m', '15m', '30m', '1h', '4h', '1d', '1w'];
    const tfChips = el('div', { class: 'bt-chips' }, ...TF_ORDER.map((tf) => {
      const have = [...this.dl.symbols].filter((sym) => onDisk.get(sym)?.has(tf));
      return el('button', {
        class: 'bt-chip-btn' + (this.dl.tfs.has(tf) ? ' on' : ''),
        title: have.length ? `already downloaded for ${have.join(', ')}`
                           : 'not downloaded for any selected symbol',
        onclick: () => {
          if (this.dl.tfs.has(tf)) this.dl.tfs.delete(tf);
          else this.dl.tfs.add(tf);
          this.render();
        },
      }, tf + (have.length === this.dl.symbols.size && have.length ? ' \u2713' : ''));
    }));

    const years = el('input', { type: 'number', value: String(this.dl.years || 20),
                                min: '1', max: '30' });
    const days = el('input', { type: 'number', value: String(this.dl.days || 0),
                               min: '0', max: '1200',
                               title: 'tick days back; 0 = bars only' });
    this.progress = el('div', { class: 'bt-prog' },
      el('div', { class: 'bt-prog-bar' }), el('span', { class: 'bt-prog-txt' }));

    const startBtn = el('button', { class: 'bt-btn primary', disabled: true,
      onclick: async () => {
        if (problems().length) return;
        const q = new URLSearchParams({
          symbols: [...this.dl.symbols].join(','),
          tfs: [...this.dl.tfs].join(','),
          years: years.value, days: days.value,
          bars: this.dl.tfs.size ? '1' : '0',
        });
        try {
          const res = await fetch(`${base()}/download/start?${q}`, { cache: 'no-store' });
          const body = await res.json();
          if (body.error) { toast(body.error, 4000); return; }
          toast('download started — live data pauses until it finishes', 5000);
          this.pollDownload();
        } catch (err) { toast('bridge unreachable: ' + err.message, 4000); }
      } }, 'Download');

    const cancel = el('button', { class: 'bt-btn', onclick: async () => {
      try { await fetch(`${base()}/download/cancel`, { cache: 'no-store' }); } catch {}
      toast('cancelling after the current chunk', 3000);
    } }, 'Cancel');

    /* The two size fields control DIFFERENT things, and reading them as one
       number is the obvious mistake, so the preview spells out what will be
       fetched before anything is. */
    const preview = el('div', { class: 'bt-preview' });
    const hint = el('div', { class: 'bt-warn' });

    const problems = () => {
      const out = [];
      if (!this.dl.symbols.size) out.push('choose at least one symbol');
      if (!this.dl.tfs.size && !(Number(days.value) > 0)) {
        out.push('choose a timeframe, or set tick days above 0');
      }
      if (Number(years.value) < 1) out.push('bar years must be at least 1');
      return out;
    };

    const refresh = () => {
      this.dl.years = Number(years.value) || 20;
      this.dl.days = Number(days.value) || 0;
      const yr = Math.max(1, this.dl.years);
      const thisYear = new Date().getUTCFullYear();
      const syms = [...this.dl.symbols].join(', ') || '(none)';
      const tfList = [...this.dl.tfs].join(', ');
      const barPart = tfList
        ? `bars ${syms} \u00d7 ${tfList} for calendar years ${thisYear - yr + 1}\u2013${thisYear}`
        : 'no bars (no timeframe selected)';
      const tickPart = this.dl.days > 0
        ? `ticks ${syms} for the last ${this.dl.days} day${this.dl.days === 1 ? '' : 's'} (rolling from today)`
        : 'no ticks (tick days = 0)';
      preview.textContent = `\u2192 ${barPart} \u00b7 ${tickPart}`;
      const bad = problems();
      startBtn.disabled = bad.length > 0;
      startBtn.title = bad.length ? bad.join(' \u00b7 ') : 'start this download';
      hint.textContent = bad.length ? `needed: ${bad.join(' \u00b7 ')}` : '';
      hint.style.display = bad.length ? '' : 'none';
    };
    [years, days].forEach((i2) => i2.addEventListener('input', refresh));
    refresh();

    /* One two-column grid for the whole form: a fixed label column and a
       content column. Previously the pick rows put their label BESIDE the
       control while the years/days row put labels ABOVE, so no two lines shared
       a left edge. */
    const field = (label, ...content) => [
      el('span', { class: 'bt-form-h', text: label }),
      el('div', { class: 'bt-form-c' }, ...content),
    ];

    box.append(el('div', { class: 'bt-form' },
      ...field('symbols', picker),
      ...field('bar timeframes', tfChips,
               el('span', { class: 'bt-meta',
                            text: '\u2713 = already on disk for every selected symbol' })),
      ...field('history',
               el('label', { class: 'bt-inline',
                             title: 'calendar years of BARS, back from this year' },
                  'bar years', years),
               el('label', { class: 'bt-inline',
                             title: 'days of TICKS, rolling back from today; 0 = none' },
                  'tick days', days)),
      ...field('', startBtn, cancel)),
      hint,
      preview,
      this.progress,
      el('div', { class: 'bt-meta' },
        'Bar years are CALENDAR years (1 = this year only); tick days roll back ' +
        'from today. Runs on the bridge holding the MetaTrader lock, so quotes, ' +
        'the chart and the tape pause until it finishes. Existing files are ' +
        'skipped, so a rerun fetches only what is missing.'));
    this.pollDownload();
    return box;
  }

  pollDownload() {
    clearInterval(this.pollTimer);
    const tick = async () => {
      let st;
      try {
        const res = await fetch(`${base()}/download/status`, { cache: 'no-store' });
        st = await res.json();
      } catch { return; }
      if (!this.progress || !this.progress.isConnected) { clearInterval(this.pollTimer); return; }
      const bar = this.progress.querySelector('.bt-prog-bar');
      const txt = this.progress.querySelector('.bt-prog-txt');
      bar.style.width = `${st.pct || 0}%`;
      this.progress.classList.toggle('running', !!st.running);
      const eta = st.eta_sec != null ? ` · eta ${fmtSecs(st.eta_sec)}` : '';
      txt.textContent = st.error ? `error: ${st.error}`
        : st.running
          ? `${st.phase} ${st.symbol || ''} ${st.tf || ''} ${st.item || ''} — ${
              st.done}/${st.total} (${st.pct}%)${eta} · ${compact(st.rows || 0)} rows`
          : st.phase === 'done'
            ? `finished — ${compact(st.rows || 0)} rows written`
            : 'idle';
      if (!st.running) clearInterval(this.pollTimer);
    };
    tick();
    this.pollTimer = setInterval(tick, 1000);
  }
}

/* ------------------------------------------------------------ helpers ---- */
function byKey(key, dir) {
  return (a, b) => {
    const x = a[key], y = b[key];
    if (x == null && y == null) return 0;
    if (x == null) return 1;
    if (y == null) return -1;
    if (typeof x === 'number' && typeof y === 'number') return (x - y) * dir;
    return String(x).localeCompare(String(y)) * dir;
  };
}

function cell(v, digits, pivot = 0) {
  if (v == null) return el('td', { text: '—' });
  return el('td', { class: v >= pivot ? 'up' : 'down', text: num(v, digits) });
}

/** Relative age, with the exact stamp on hover. */
function ago(iso) {
  if (!iso) return '—';
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return '—';
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 90) return 'just now';
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  if (s < 86400 * 30) return `${Math.round(s / 86400)}d ago`;
  return new Date(t).toISOString().slice(0, 10);
}

function fmtSecs(s) {
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}

function drawEquity(canvas, points) {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth || 300;
  const h = canvas.clientHeight || 70;
  canvas.width = Math.round(w * dpr);
  canvas.height = Math.round(h * dpr);
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  if (points.length < 2) {
    ctx.fillStyle = '#5d7794';
    ctx.font = '10px "Roboto Mono", monospace';
    ctx.fillText('no equity curve', 6, h / 2);
    return;
  }
  const vals = points.map((p) => p[1]);
  const lo = Math.min(...vals), hi = Math.max(...vals);
  const span = (hi - lo) || 1;
  const x = (i) => (i / (points.length - 1)) * (w - 2) + 1;
  const y = (v) => h - 3 - ((v - lo) / span) * (h - 6);
  const start = vals[0];

  // zero line at starting equity, so profit and loss are visually distinct
  ctx.strokeStyle = 'rgba(177,179,179,.3)';
  ctx.setLineDash([3, 3]);
  ctx.beginPath();
  ctx.moveTo(0, y(start));
  ctx.lineTo(w, y(start));
  ctx.stroke();
  ctx.setLineDash([]);

  const up = vals[vals.length - 1] >= start;
  const col = up ? '#93C90F' : '#E31C79';
  const grad = ctx.createLinearGradient(0, 0, 0, h);
  grad.addColorStop(0, up ? 'rgba(147,201,15,.28)' : 'rgba(227,28,121,.28)');
  grad.addColorStop(1, 'rgba(0,0,0,0)');
  ctx.beginPath();
  ctx.moveTo(x(0), y(vals[0]));
  vals.forEach((v, i) => ctx.lineTo(x(i), y(v)));
  ctx.lineTo(x(vals.length - 1), h);
  ctx.lineTo(x(0), h);
  ctx.closePath();
  ctx.fillStyle = grad;
  ctx.fill();

  ctx.beginPath();
  vals.forEach((v, i) => (i ? ctx.lineTo(x(i), y(v)) : ctx.moveTo(x(i), y(v))));
  ctx.strokeStyle = col;
  ctx.lineWidth = 1.4;
  ctx.stroke();
}

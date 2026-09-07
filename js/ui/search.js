/* search.js — the symbol picker (press "/"). Ranks exact prefix first so typing
   "eur" lands on EURUSD rather than the first alphabetical match containing it. */

import { $, clamp, el } from '../util.js';

export class SymbolSearch {
  constructor({ onPick }) {
    this.onPick = onPick;
    this.modal = $('#searchModal');
    this.input = $('#searchInput');
    this.list = $('#searchResults');
    this.symbols = [];
    this.quotes = {};
    this.rows = [];
    this.sel = 0;

    this.input.addEventListener('input', () => this.render());
    this.input.addEventListener('keydown', (e) => this.key(e));
    this.modal.addEventListener('pointerdown', (e) => { if (e.target === this.modal) this.close(); });
  }

  setSymbols(list) { this.symbols = list || []; if (!this.modal.hidden) this.render(); }
  setQuotes(q) { this.quotes = q || {}; }

  /**
   * `once` borrows the picker for a single choice.
   *
   * There is exactly one #searchModal in the document, so the replay sandboxes
   * cannot build their own without duplicating the whole overlay. They borrow
   * this one instead: the handler is used for the next pick and then dropped,
   * so the live chart's behaviour is untouched the moment the modal closes.
   */
  open(once = null) {
    this._once = once;
    this.modal.hidden = false;
    this.input.value = '';
    this.input.focus();
    this.render();
  }

  close() {
    this.modal.hidden = true;
    /* Cleared on close, not only on pick: dismissing with Escape must not
       leave a borrowed handler armed for whoever opens the picker next. */
    this._once = null;
  }
  get isOpen() { return !this.modal.hidden; }

  match() {
    const q = this.input.value.trim().toUpperCase();
    const scored = [];
    for (const s of this.symbols) {
      const name = (s.name || '').toUpperCase();
      if (!q) { scored.push([0, s]); continue; }
      const idx = name.indexOf(q);
      if (idx === 0) scored.push([0, s]);
      else if (idx > 0) scored.push([1, s]);
      else if ((s.path || '').toUpperCase().includes(q)) scored.push([2, s]);
    }
    scored.sort((a, b) => a[0] - b[0] || a[1].name.localeCompare(b[1].name));
    return scored.slice(0, 120).map((x) => x[1]);
  }

  render() {
    this.rows = this.match();
    this.sel = clamp(this.sel, 0, Math.max(0, this.rows.length - 1));
    this.list.innerHTML = '';
    if (!this.rows.length) {
      this.list.append(el('div', { class: 'empty', text: 'no match' }));
      return;
    }
    this.rows.forEach((s, i) => {
      const q = this.quotes[s.name];
      this.list.append(el('div', {
        class: 'res-row' + (i === this.sel ? ' sel' : ''),
        onclick: () => this.pick(i),
        onpointerenter: () => { this.sel = i; this.paintSel(); },
      },
      el('span', { class: 'sym', text: s.name }),
      el('span', { class: 'path', text: (s.path || '').replace(/\\/g, ' › ') }),
      el('span', { class: 'px', text: q ? Number(q.bid).toFixed(q.digits ?? 5) : '' })));
    });
  }

  paintSel() {
    [...this.list.children].forEach((c, i) => c.classList.toggle('sel', i === this.sel));
  }

  pick(i) {
    const s = this.rows[i];
    if (!s) return;
    const once = this._once;
    this.close();                       // clears _once, so read it first
    (once || this.onPick)(s.name);
  }

  key(e) {
    if (e.key === 'Escape') { this.close(); return; }
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      this.sel = clamp(this.sel + (e.key === 'ArrowDown' ? 1 : -1), 0, this.rows.length - 1);
      this.paintSel();
      this.list.children[this.sel]?.scrollIntoView({ block: 'nearest' });
      return;
    }
    if (e.key === 'Enter') { e.preventDefault(); this.pick(this.sel); }
  }
}


/* One picker, reachable without a wire from js/main.js.
 *
 * The replay sandboxes are deliberately unknown to main.js -- that isolation is
 * what keeps them from disturbing the live chart. Passing them a SymbolSearch
 * instance would undo it, so main.js registers the instance it builds here and
 * anything that needs a symbol asks for it. `openSymbolSearch` returns false
 * when nothing has registered, which is the honest answer for a caller that
 * then has to fall back to typing. */
let shared = null;

export function registerSymbolSearch(instance) { shared = instance; }

export function openSymbolSearch(onPick) {
  if (!shared) return false;
  shared.open(onPick);
  return true;
}

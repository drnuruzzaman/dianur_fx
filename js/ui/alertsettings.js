/**
 * alertsettings.js — the Settings modal behind the live pill.
 *
 * WHAT IT EDITS. `configs/alerts.json`, through the dev server's `/alerts`.
 * That file is read by `tools/signal_alert.py` and `tools/event_alert.py` on
 * EVERY poll, so a save takes effect on the next minute with nothing
 * restarted and nothing reloaded.
 *
 * THE FILE IS THE SOURCE OF TRUTH, NOT THIS PANEL. A text editor is an equally
 * valid way to change it, which is why the modal re-reads on every open rather
 * than caching what it last wrote: if the two ever disagree, the file is right.
 *
 * ONE ROW PER INSTRUMENT, TIMEFRAMES AS CHIPS. The stored shape is flat --
 * `watch` is a list of (symbol, timeframe) cells -- but that is not how the
 * choice is made. "Watch gold" and "on which frames" are two questions, and
 * the first design asked them as one: a row per cell with a timeframe
 * dropdown, so putting gold on three frames meant three separate adds that
 * then read as three unrelated lines. Grouping by symbol and toggling frames
 * makes the second question a click.
 *
 * A CHIP TURNED OFF DISABLES, IT DOES NOT DELETE. The cell keeps its `note` --
 * the only surviving record of why it was ever watched, since the file is
 * machine-written and a JSON comment would not last a save. The × on the
 * instrument row is the delete, and it is deliberately the larger gesture.
 *
 * ADDING BORROWS THE REAL SYMBOL PICKER rather than shipping a second search.
 * `search.js` already ranks the broker's full symbol list, exact prefix first,
 * and carries a `once` mode built for exactly this. A private text box here
 * would accept a typo silently and produce a cell that polls a symbol the
 * broker does not have.
 *
 * A WHOLESALE WRITE, unlike the workspace. `/workspace` merges because it is a
 * scatter of independent keys and a client must not assert what it lacks; this
 * is a LIST the user is editing as a list, and removing a row IS the edit. The
 * server refuses a body with no `signals.watch` array and keeps a `.prev`, so a
 * half-built request cannot blank the file.
 *
 * IT DOES NOT TOUCH THE SCHEDULED TASKS. Switching news alerts off leaves the
 * Windows task registered and polling; the tool reads the flag and exits
 * quietly. A checkbox in a web page that silently unregisters a system job is
 * a surprise nobody wants.
 */

import { $, el } from '../util.js';

/** The frames the bridge serves, in the order a reader thinks about them. */
const TFS = ['1m', '5m', '15m', '30m', '1h', '4h', '1d'];

export class AlertSettings {
  /** @param search the app's SymbolSearch, borrowed for "add instrument". */
  constructor(search) {
    this.search = search;
    this.modal = $('#alertsModal');
    this.cells = $('#alCells');
    this.status = $('#alStatus');
    this.cfg = null;

    $('#alAdd').addEventListener('click', () => this.addInstrument());
    /* Touching anything else in the panel disarms. Arming is a statement about
       the NEXT click, so a click that goes somewhere else has answered it. */
    this.modal.addEventListener('click', (e) => {
      if (!e.target.classList.contains('rm')) this.disarmAll();
    }, true);
    $('#alCancel').addEventListener('click', () => this.close());
    $('#alSave').addEventListener('click', () => this.save());
    $('#alNews').addEventListener('change', () => this.paintNews());
    this.modal.addEventListener('mousedown', (e) => {
      if (e.target === this.modal) this.close();       // click the backdrop
    });
    document.addEventListener('keydown', (e) => {
      // only when the picker is not the thing on top, or Escape would close
      // the modal underneath and leave the picker floating over nothing
      if (e.key === 'Escape' && !this.modal.hidden
          && (!this.search || !this.search.isOpen)) this.close();
    });
  }

  async open() {
    this.say('loading…');
    this.modal.hidden = false;
    try {
      const r = await fetch('/alerts', { cache: 'no-store' });
      this.cfg = await r.json();
    } catch (err) {
      /* A dev server that is not answering means the file cannot be read OR
         written, so the modal says so and edits nothing. Showing an empty form
         here would invite a save that wipes the real settings. */
      this.say('cannot reach the server — nothing loaded');
      this.cfg = null;
      this.cells.replaceChildren();
      return;
    }
    const news = (this.cfg && this.cfg.news) || {};
    $('#alNews').checked = news.enabled !== false;
    $('#alNewsLead').value = news.lead_minutes ?? 10;
    $('#alNewsImpact').value = news.impact || 'any';
    this.paintNews();
    this.paint();
    this.say('');
  }

  close() { this.modal.hidden = true; }
  say(msg) { this.status.textContent = msg || ''; }

  watch() { return ((this.cfg && this.cfg.signals) || {}).watch || []; }

  paintNews() {
    const on = $('#alNews').checked;
    $('#alNewsState').textContent = on ? 'on' : 'off';
    $('#alNewsState').style.opacity = on ? '' : '.45';
  }

  /** Group the flat cell list into one entry per symbol, order preserved. */
  grouped() {
    const map = new Map();
    for (const w of this.watch()) {
      if (!map.has(w.symbol)) map.set(w.symbol, []);
      map.get(w.symbol).push(w);
    }
    return map;
  }

  /** Return every armed remove to its resting state. */
  disarmAll() {
    for (const rm of this.cells.querySelectorAll('.rm')) {
      if (rm._t) { clearTimeout(rm._t); rm._t = null; }
      if (rm.dataset.armed === '1') {
        rm.dataset.armed = '';
        rm.textContent = '×';
        rm.classList.remove('arm');
      }
    }
  }

  paint() {
    const groups = this.grouped();
    this.cells.replaceChildren();
    if (!groups.size) {
      this.cells.append(el('div', { class: 'alerts-empty',
                                    text: 'nothing watched — no signal alerts will be sent' }));
    }
    for (const [symbol, rows] of groups) {
      /* TWO CLICKS TO REMOVE, and the first one changes what the button says.
         A single unconfirmed x on a row that carries several timeframes is one
         stray click away from silently dropping an instrument -- which is
         exactly what happened once during development, and the only reason it
         was noticed is that the server keeps a `.prev`.

         An inline arm rather than confirm(): a native dialog steals focus from
         a modal that is itself mid-edit, and this one has to survive being
         dismissed with Escape without also closing the settings panel. */
      const rm = el('span', { class: 'rm', text: '×', title: `stop watching ${symbol}` });
      rm.addEventListener('click', () => {
        if (rm.dataset.armed !== '1') {
          this.disarmAll();
          rm.dataset.armed = '1';
          rm.textContent = 'remove?';
          rm.classList.add('arm');
          rm.title = `click again to stop watching ${symbol}`;
          /* Disarms itself. An armed control left sitting on screen becomes an
             ordinary one in the reader's mind, and the next click is the very
             accident this exists to prevent. */
          rm._t = setTimeout(() => this.disarmAll(), 4000);
          return;
        }
        this.cfg.signals.watch = this.watch().filter((w) => w.symbol !== symbol);
        this.say(`${symbol} removed — Cancel still undoes it`);
        this.paint();
      });
      const head = el('div', { class: 'alerts-inst-head' },
        el('span', { class: 'sym', text: symbol }), rm);

      const chips = el('div', { class: 'alerts-chips' });
      for (const tf of TFS) {
        const cell = rows.find((w) => String(w.tf) === tf);
        const on = !!cell && cell.enabled !== false;
        const chip = el('div', { class: 'tf-chip' + (on ? ' on' : ''), text: tf,
                                 title: on ? 'announcing' : 'not announced' });
        chip.addEventListener('click', () => this.toggle(symbol, tf));
        chips.append(chip);
      }

      const box = el('div', { class: 'alerts-inst' }, head, chips);
      /* The note is shown, never edited here: it explains why a cell is worth
         watching and that belongs with whoever measured it, not with a click. */
      const note = (rows.find((w) => w.note) || {}).note;
      if (note) box.append(el('div', { class: 'alerts-note', text: note }));
      this.cells.append(box);
    }
    const n = this.watch().filter((w) => w.enabled !== false).length;
    $('#alCount').textContent = `${n} on`;
  }

  toggle(symbol, tf) {
    const list = this.watch();
    const cell = list.find((w) => w.symbol === symbol && String(w.tf) === tf);
    if (cell) {
      // OFF disables rather than removing, so the note survives a change of mind
      cell.enabled = cell.enabled === false;
    } else {
      list.push({ symbol, tf, enabled: true, note: '' });
    }
    this.paint();
  }

  addInstrument() {
    if (!this.cfg) return;
    if (!this.search) { this.say('symbol picker unavailable'); return; }
    this.search.open((sym) => {
      const name = typeof sym === 'string' ? sym : (sym && sym.name);
      if (!name) return;
      if (this.grouped().has(name)) { this.say(`${name} is already listed`); return; }
      this.cfg.signals = this.cfg.signals
        || { alert_on: ['buy', 'sell', 'exit'], watch: [] };
      this.cfg.signals.watch = this.cfg.signals.watch || [];
      /* Added with NO frame on. Picking a symbol says "I am interested"; it
         does not say which frames, and defaulting one on would start announcing
         a cell nobody chose. The chips are right there. */
      this.cfg.signals.watch.push({ symbol: name, tf: '4h', enabled: false, note: '' });
      this.say(`${name} added — pick a timeframe`);
      this.paint();
    });
  }

  async save() {
    if (!this.cfg) { this.say('nothing loaded — not saving'); return; }
    this.cfg.news = this.cfg.news || {};
    this.cfg.news.enabled = $('#alNews').checked;
    this.cfg.news.lead_minutes = Math.max(1, Number($('#alNewsLead').value) || 10);
    this.cfg.news.impact = $('#alNewsImpact').value;
    this.say('saving…');
    try {
      const r = await fetch('/alerts', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(this.cfg),
      });
      const body = await r.json();
      if (!r.ok || body.error) throw new Error(body.error || `HTTP ${r.status}`);
      const on = this.watch().filter((w) => w.enabled !== false).length;
      this.say(`saved — ${on} cell${on === 1 ? '' : 's'} announcing`);
      setTimeout(() => this.close(), 600);
    } catch (err) {
      // THE MODAL STAYS OPEN on a failed save, holding the edit. Closing would
      // lose work that was never written, and the user would have no way to
      // know which of the two states is on disk.
      this.say(`save failed: ${err.message || err}`);
    }
  }
}

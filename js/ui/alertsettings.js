/**
 * alertsettings.js — the Settings modal behind the live pill.
 *
 * WHAT IT EDITS. The RAYO SCALPER's cell list in `configs/alerts.json`,
 * through the dev server's `/alerts`. That file is read by
 * `tools/scalper.py --registered` and `tools/event_alert.py` on EVERY poll, so
 * a save takes effect on the next run with nothing restarted and nothing
 * reloaded.
 *
 * IT NO LONGER EDITS THE DONCHIAN. `signals.watch` was this project's signal
 * source until 2026-09-10 and is now retired in place: every cell disabled, its
 * grades and pre-registered OOS/IS numbers untouched, and the two Windows tasks
 * that polled it stopped. This panel edits `scalper.watch`.
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
 * server refuses a body with no `scalper.watch` array and keeps a `.prev`, so a
 * half-built request cannot blank the file.
 *
 * IT DOES NOT TOUCH THE SCHEDULED TASKS. Switching news alerts off leaves the
 * Windows task registered and polling; the tool reads the flag and exits
 * quietly. A checkbox in a web page that silently unregisters a system job is
 * a surprise nobody wants.
 */

import { $, el, relTime, stamp } from '../util.js';

/* EVERY FRAME THE BRIDGE SERVES. These were briefly cut back to the four the
   rule had been measured on, to stop anyone switching on a cell nobody had
   tested -- but hiding the control is the wrong place to enforce that, and it
   took away frames that are perfectly reasonable to watch. The guarantee lives
   where it belongs instead: tools/scalper.py will not MESSAGE a cell that is
   not marked tradeable, so an unmeasured frame can be journalled and looked at
   and can never reach Telegram. The chip says which it is. */
const TFS = ['1m', '5m', '15m', '30m', '1h', '4h', '1d'];

/**
 * How a Rayo cell grades, from its measurement rather than a hand-written word.
 *
 * The Donchian cells carried a `grade` string typed into the config. Rayo's
 * cells carry numbers, so the grade is derived and cannot drift from them:
 * a cell is green only if it is marked tradeable, which requires its median
 * sub-era AND its total R/yr to agree in sign and the frame to be one of the
 * two that clear the cost floor.
 */
function rayoGrade(c) {
  if (!c) return null;
  if (c.measurable === false) return 'below';   // cannot signal at all
  if (c.control) return 'below';
  return c.tradeable ? 'validated' : 'marginal';
}

/* A newline, as a constant -- the same guard js/ui/signalboard.js carries. A
   backslash-n written into a source file by a patch script has twice arrived
   as a REAL newline inside a string literal and taken the whole app down, and
   `node --check` accepted it both times. */
const NL = String.fromCharCode(10);

const GRADE_WORD = {
  validated: 'measured above friction',
  marginal: 'marginal against friction',
  below: 'below its friction floor',
};

/**
 * What a 404 from one of the alert endpoints actually means.
 *
 * serve.py IS A RUNNING PROCESS, and .js is not. Every static file here is
 * re-read per request (that is the whole reason this server exists instead of
 * `http.server`), so an edit to this panel shows up on a reload -- while an
 * edit to serve.py does NOT, because the interpreter that loaded it is still
 * running the old code. The two therefore drift apart silently, and the panel
 * ends up calling an endpoint that its own server has never heard of.
 *
 * It cost a real confusion the first time: `Test` reported "not found", which
 * reads as "that chat does not exist" -- so the reader goes looking at Telegram
 * for a fault that is on this machine, in a process that needs restarting.
 * A 404 from OUR OWN endpoints can only mean this, so it says this.
 */
const STALE_SERVER = 'the dev server is older than this panel — restart '
                     + 'serve.py (python serve.py) and reopen Settings';

export class AlertSettings {
  /** @param search the app's SymbolSearch, borrowed for "add instrument". */
  constructor(search) {
    this.search = search;
    this.modal = $('#alertsModal');
    this.cells = $('#alCells');
    this.status = $('#alStatus');
    this.cfg = null;

    /* TABS ARE VIEW ONLY. Both panes edit the same `this.cfg` and share one
       Save, so switching tabs never commits, discards or reloads anything --
       it decides what is on screen and nothing else. A tab that saved on
       switch would make Cancel a lie on the pane you had just left. */
    this.tabs = [
      [$('#alTabSignal'), $('#alPaneSignal')],
      [$('#alTabNews'), $('#alPaneNews')],
      [$('#alTabDest'), $('#alPaneDest')],
    ];
    for (const [tab] of this.tabs) {
      if (tab) tab.addEventListener('click', () => this.showTab(tab));
    }

    $('#alAdd').addEventListener('click', () => this.addInstrument());
    const da = $('#alDestAdd');
    if (da) da.addEventListener('click', () => this.addDest());
    const di = $('#alDestImport');
    if (di) di.addEventListener('click', () => this.importEnvDests());
    /* Touching anything else in the panel disarms. Arming is a statement about
       the NEXT click, so a click that goes somewhere else has answered it. */
    this.modal.addEventListener('click', (e) => {
      if (!e.target.classList.contains('rm')) this.disarmAll();
    }, true);
    $('#alCancel').addEventListener('click', () => this.close());
    $('#alSave').addEventListener('click', () => this.save());
    $('#alNews').addEventListener('change', () => this.paintNews());
    const ne = $('#alNewsEvery');
    if (ne) ne.addEventListener('change', () => this.paintNewsEvery());
    const nf = $('#alNewsFetch');
    if (nf) nf.addEventListener('click', () => this.fetchNews(nf));
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

  /** Show one tab. `hidden` rather than a class: the CSS reset makes
      `[hidden]` win over any display rule, so a pane cannot half-show. */
  showTab(which) {
    for (const [tab, pane] of this.tabs) {
      if (!tab || !pane) continue;
      const on = tab === which;
      tab.classList.toggle('on', on);
      tab.setAttribute('aria-selected', String(on));
      pane.hidden = !on;
    }
  }

  async open() {
    this.say('loading…');
    this.modal.hidden = false;
    /* Always opens on Signal alerts. The modal is reached from the live pill
       to change what is announced; news and the scheduler are the things you
       check occasionally, so they do not get the first screen. */
    this.showTab($('#alTabSignal'));
    this.destsTouched = false;            // per-open, like everything else here
    this.paintSched();                    // independent of the /alerts fetch
    this.paintNewsAge();                  // likewise: the feed is a file, not config
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
    /* THE SAME GUARD THE INTERVAL SELECT CARRIES, and for a sharper reason.
       Setting a <select> to a value with no matching <option> leaves
       selectedIndex -1 and `.value` an EMPTY STRING -- and save writes that
       straight back, where an empty impact reads as no filter at all. So a
       config saying `low` while the dropdown offered only any/high would have
       been silently widened to every event merely by opening this panel and
       pressing Save. */
    const imp = news.impact || 'any';
    this.loadedImpact = imp;          // the fallback for save, see below
    const isel = $('#alNewsImpact');
    if (!Array.from(isel.options).some((o) => o.value === imp)) {
      isel.append(el('option', { value: imp, text: `${imp} (from the file)` }));
    }
    isel.value = imp;
    /* A VALUE THE DROPDOWN DOES NOT OFFER IS KEPT, not silently rounded to one
       it does. The file may be hand-edited, and a select that snaps 45 to 30
       on open would change the setting merely by looking at it -- then save
       the change without anyone touching the control. */
    const every = Number(news.fetch_minutes) || 0;
    const sel = $('#alNewsEvery');
    if (!Array.from(sel.options).some((o) => Number(o.value) === every)) {
      sel.append(el('option', { value: String(every), text: `every ${every} min` }));
    }
    sel.value = String(every);
    this.paintNewsEvery();
    this.paintNews();
    this.paint();
    this.paintDests();
    this.paintCreds();                    // presence of tokens, server side
    this.say('');
  }

  /* THE SCHEDULER PANEL IS SEPARATE FROM THE CONFIG, deliberately.
     `configs/alerts.json` says what SHOULD be announced; the scheduled tasks
     decide whether anything runs at all. Editing the first while the second is
     unregistered produces a settings panel that looks completely healthy and
     sends nothing -- which is exactly what happened for a day when the bot task
     was missing and nobody could see it from here.

     STATUS IS READ ON EVERY OPEN and never cached: a task can be removed or
     disabled from outside this app, and a stale green light is worse than none.
     It is also painted BEFORE the /alerts fetch and independently of it, so a
     dead dev server or a broken config still shows the scheduler truth. */
  async paintSched() {
    const box = $('#alSched');
    if (!box) return;
    box.replaceChildren(el('div', { class: 'alerts-hint', text: 'checking…' }));
    let tasks;
    try {
      const r = await fetch('/scheduler', { cache: 'no-store' });
      tasks = (await r.json()).tasks || {};
    } catch (err) {
      box.replaceChildren(el('div', { class: 'alerts-hint',
                                      text: 'cannot reach the server' }));
      return;
    }
    box.replaceChildren();
    for (const [key, t] of Object.entries(tasks)) {
      const on = !!t.registered && !/disabled/i.test(t.status || '');
      /* `Last Result` 0 is success; 267009 is 0x41301, "currently running",
         which is the NORMAL state for a task that never exits; 267011 is
         0x41303, "has not run yet", which is what a freshly installed task
         reports until its first trigger. None of the three is a fault, and
         showing the raw number invites reading any of them as an error --
         267011 in particular put a red dot on a task that had just been
         installed correctly. */
      const res = t.last_result;
      const bad = t.registered && res !== undefined
                  && !['0', '267009', '267011'].includes(String(res).trim());
      const bits = [];
      if (!t.registered) bits.push('not registered');
      else {
        bits.push(t.status || 'registered');
        if (t.last_run) bits.push('last run ' + t.last_run);
        if (bad) bits.push('last result ' + res);
      }
      const btn = el('button', { class: 'btn alerts-schedbtn',
                                 text: t.registered ? 'Reinstall' : 'Install' });
      btn.addEventListener('click', () => this.installSched(key, btn));
      box.append(el('div', { class: 'alerts-sched-row' },
        el('span', { class: 'dot ' + (on && !bad ? 'ok' : 'off') }),
        el('div', { class: 'alerts-sched-txt' },
          el('div', { class: 'sym', text: t.task || key }),
          el('div', { class: 'alerts-note', text: bits.join(' · ') }),
          el('div', { class: 'alerts-note', text: t.why || '' })),
        btn));
    }
  }

  async installSched(key, btn) {
    /* No confirm(): a native dialog steals focus from a modal that is itself
       mid-edit, the same reason the row remove uses an inline arm. The button
       disables itself instead, which also stops a double click registering the
       task twice while the first call is still running. */
    const was = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'installing…';
    this.say(`registering ${key}…`);
    try {
      const r = await fetch('/scheduler/install', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ task: key }),
      });
      const body = await r.json();
      /* 409 IS NOT A FAILURE TO REPORT AND FORGET. It means the file moved
         under this panel -- a measurement script rewrote it while Settings sat
         open -- and posting anyway would delete whatever it added. That is not
         hypothetical: it removed 18 measured cells once. Reload, show the
         current file, and make the reader redo the edit on top of it. */
      if (r.status === 409) {
        this.say('the file changed while this was open — reloading it; '
                 + 'please redo your change');
        try {
          const rr = await fetch('/alerts', { cache: 'no-store' });
          this.cfg = await rr.json();
          this.paint();
        } catch (e) { this.say('reload failed — close and reopen Settings'); }
        return;
      }
      if (!r.ok || body.error) throw new Error(body.error || `HTTP ${r.status}`);
      this.say(body.ok ? `${key} registered`
                       : `${key}: installer exited ${body.code} — see the console`);
      if (!body.ok) console.warn('[scheduler]', body.out, body.err);
    } catch (err) {
      this.say(`install failed: ${err.message || err}`);
    } finally {
      btn.disabled = false;
      btn.textContent = was;
      this.paintSched();                  // re-read rather than assume it worked
    }
  }

  close() { this.modal.hidden = true; }
  say(msg) { this.status.textContent = msg || ''; }

  /**
   * The live cell list: THE RAYO SCALPER'S, not the Donchian's.
   *
   * `signals.watch` is the retired horizon-matched Donchian forward test. It is
   * still in configs/alerts.json with every grade and pre-registered number
   * intact, because it was opened 2026-09-08 and a registration deleted two days
   * in leaves no record of what was predicted beforehand. But nothing reads it,
   * every cell is `enabled: false`, and the two Windows tasks that polled it are
   * disabled. This panel configures the rule the project actually runs.
   */
  watch() { return ((this.cfg && this.cfg.scalper) || {}).watch || []; }

  /* WHAT THE INTERVAL ACTUALLY DEPENDS ON, said next to the control. The
     fetch is run by whichever supervisor is up -- this server while the app is
     open, alerts_daemon.py when it is not -- so an interval set on a machine
     where neither runs is a setting that does nothing, and the panel should
     not imply otherwise. */
  paintNewsEvery() {
    const out = $('#alNewsEveryNote');
    if (!out) return;
    const n = Number($('#alNewsEvery').value) || 0;
    out.textContent = n
      ? 'while the app or the alerts daemon is running'
      : 'the feed only changes when you press Fetch news now';
  }

  paintNews() {
    const on = $('#alNews').checked;
    $('#alNewsState').textContent = on ? 'on' : 'off';
    $('#alNewsState').style.opacity = on ? '' : '.45';
  }

  /**
   * HOW OLD THE NEWS ON THE LEFT RAIL ACTUALLY IS.
   *
   * The rail shows "2d" beside each story, which is the age of the STORY. That
   * is not the same fact as the age of the FEED, and while nothing said so, a
   * two-day-old file full of two-day-old headlines looked exactly like a quiet
   * two days. This line is the second fact.
   *
   * `fetchedAt` from the file rather than its mtime -- the tool writes it at
   * the moment it spoke to the API, and mtime moves for reasons that have
   * nothing to do with the news.
   */
  async paintNewsAge() {
    const out = $('#alNewsAge');
    if (!out) return;
    out.textContent = 'checking…';
    out.classList.remove('down');
    let d;
    try {
      d = await (await fetch('/news/status', { cache: 'no-store' })).json();
    } catch (err) {
      out.textContent = 'cannot reach the server';
      return;
    }
    if (d.error && !d.fetched_at) {
      out.textContent = d.error;
      out.classList.add('down');
      return;
    }
    /* `fetchedAt` IS EPOCH MILLISECONDS, not an ISO string -- the tool writes
       a number. Date.parse on a number returns NaN and this line would have
       fallen through to mtime and looked right, which is the worst kind of
       wrong: correct today, silently reporting the wrong fact the first time
       the file is copied. Both shapes handled, mtime kept as the last resort. */
    const ms = (typeof d.fetched_at === 'number' ? d.fetched_at
                : Date.parse(d.fetched_at || '')) || d.mtime_ms || 0;
    const age = ms ? Date.now() - ms : 0;
    out.textContent = ms
      ? `last fetched ${stamp(ms)} · ${relTime(ms)} · `
        + `${d.clusters ?? '?'} clusters, ${d.headlines ?? '?'} headlines`
      : 'never fetched';
    /* A DAY IS THE LINE. The feed is a daily-cadence product, so anything
       older than that is stale enough that the rail is describing a market
       that has moved on -- and the whole point of this line is to say so
       without the reader having to work out the date arithmetic. */
    out.classList.toggle('down', !!ms && age > 24 * 3600 * 1000);
  }

  async fetchNews(btn) {
    const was = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'fetching…';
    this.say('fetching news…');
    try {
      const r = await fetch('/news/fetch', { method: 'POST' });
      const d = await r.json();
      /* The LAST line of the tool's output, not all of it: a fetcher that
         printed a stack trace would otherwise put the useful sentence off the
         top of a one-line status strip. */
      if (!r.ok || !d.ok) {
        const why = String(d.log || d.error || `HTTP ${r.status}`);
        throw new Error(why.split(/\r?\n/).filter(Boolean).pop() || why);
      }
      this.say(`news updated — ${d.clusters ?? '?'} clusters, `
               + `${d.headlines ?? '?'} headlines`);
      /* THE RAIL CACHES ONE FETCH PER PAGE LOAD (see loadNewsDoc), so a fresh
         file on disk is not a fresh rail. Reloading is blunt and it is honest:
         a button that says the news was updated while the panel beside it still
         shows the old stories is worse than a reload. */
      setTimeout(() => window.location.reload(), 900);
    } catch (err) {
      this.say(`news fetch failed: ${err.message}`);
    } finally {
      btn.disabled = false;
      btn.textContent = was;
      this.paintNewsAge();
    }
  }

  /* ------------------------------------------------------------------ *
   * DESTINATIONS -- where an alert goes, as opposed to what is announced.
   *
   * THE STORED SHAPE, in configs/alerts.json:
   *
   *   "destinations": [
   *     { "id": "d1", "channel": "telegram", "label": "Gold group",
   *       "target": "-1001234567890", "enabled": true,
   *       "kinds": ["signals", "news"] }
   *   ]
   *
   * ABSENT IS NOT THE SAME AS EMPTY, and the distinction is the whole
   * migration story. No `destinations` key means nobody has managed this from
   * the UI yet, so tools/notify.py falls back to the TELEGRAM_CHAT_ID list in
   * configs/secrets.env -- an install that never opens this tab keeps working
   * exactly as before. An empty ARRAY means somebody removed every row, which
   * is a real decision and silences everything. So this pane shows the env
   * rows read-only until Import is clicked, rather than quietly adopting them:
   * adopting on open would rewrite the routing of an install whose owner only
   * came here to look.
   *
   * THE ADDRESS IS EDITED HERE, THE TOKEN IS NOT. A chat id is worthless
   * without the bot token and has to live in the file the UI writes; the token
   * stays in configs/secrets.env and never reaches this page. `paintCreds`
   * asks the server whether each one is PRESENT and gets back a boolean.
   * ------------------------------------------------------------------ */

  /** The editable list, or null when the file has no `destinations` key. */
  destList() {
    const d = this.cfg && this.cfg.destinations;
    return Array.isArray(d) ? d : null;
  }

  /** `d3`, `d4`... — unique within the file, and stable once written, because
      the per-row Test button and any future per-destination state key on it. */
  newDestId() {
    const used = new Set((this.destList() || []).map((d) => String(d.id)));
    for (let n = 1; n < 999; n++) {
      if (!used.has('d' + n)) return 'd' + n;
    }
    return 'd' + Date.now();
  }

  /* SILENT WHEN THERE IS NOTHING WRONG.
     This used to print the full state of both channels on every open --
     "Telegram bot token: set · WhatsApp (cloud): token MISSING, phone id
     MISSING" -- which is four facts, three of them irrelevant to a
     Telegram-only setup, sitting above the rows they were supposed to help
     read. A missing WhatsApp token is the NORMAL state for someone who does
     not use WhatsApp; announcing it as though it were a problem trains the
     reader to skip the line, and then it is not read on the one day a token
     really is missing. So the line appears only when a channel that is
     actually in use has no credential, and says just that. */
  async paintCreds() {
    const out = $('#alDestCreds');
    if (!out) return;
    /* NOTHING IS CLEARED UP FRONT, and that is not tidiness -- it is the fix
       for a race this had. Several things call paintCreds (a switch, a channel
       change, a remove) and each awaits a fetch, so two runs overlap: the first
       would finish and show the warning, then the second would blank the line
       and leave it blank for the length of its own request. The warning existed
       and was invisible. Every branch below now sets the FINAL state after its
       await, so whichever run finishes last is simply right. */
    let c;
    try {
      const r = await fetch('/notify/status', { cache: 'no-store' });
      /* CHECKED BEFORE .json(), because a stale server answers this one with
         an HTML 404 page. Parsing that throws and the catch below would have
         blamed the network -- and, worse, an endpoint that answered
         `{"error":"not found"}` as JSON would have left `c.telegram`
         undefined and printed "Telegram bot token: MISSING" about a token
         that is present. A settings panel lying about a credential is the
         last thing that should happen here. */
      if (r.status === 404) throw new Error(STALE_SERVER);
      c = await r.json();
    } catch (err) {
      /* SHOWN, because this one IS wrong: the panel cannot tell whether
         anything is configured, and a stale server also means Test and the
         address resolver are dead. */
      out.textContent = String(err.message || '').startsWith('the dev server')
        ? err.message : 'cannot reach the server — credentials unknown';
      out.classList.add('down');
      out.hidden = false;
      return;
    }
    this.creds = c;
    const w = c.whatsapp || {};
    /* IN USE = enabled and addressed. A switched-off row is not a reason to
       warn about a token, and neither is a row with no target yet -- both are
       states a reader passes through while setting one up. */
    const used = new Set((this.destList() || [])
      .filter((d) => d.enabled !== false && String(d.target || '').trim())
      .map((d) => d.channel || 'telegram'));
    const missing = [];
    if (used.has('telegram') && !(c.telegram && c.telegram.token)) {
      missing.push('Telegram bot token');
    }
    if (used.has('whatsapp')) {
      if (!w.token) missing.push('WhatsApp token');
      if (w.provider === 'cloud' && !w.phone_id) missing.push('WhatsApp phone id');
    }
    out.textContent = missing.length
      ? `${missing.join(' and ')} missing from configs/secrets.env`
        + ' — those alerts cannot send'
      : '';
    out.classList.toggle('down', missing.length > 0);
    out.hidden = !missing.length;
    /* Import is offered only when there is something to import AND nothing to
       overwrite -- see ABSENT IS NOT THE SAME AS EMPTY. */
    const imp = $('#alDestImport');
    if (imp) {
      const n = ((c.telegram && c.telegram.env_targets) || []).length;
      imp.hidden = !(n && this.destList() === null);
      imp.textContent = `Import ${n} chat${n === 1 ? '' : 's'} from secrets.env`;
    }
    this.paintDests();                   // env rows are drawn from what arrived
  }

  importEnvDests() {
    const env = (this.creds && this.creds.telegram && this.creds.telegram.env_targets) || [];
    if (!env.length) { this.say('nothing to import'); return; }
    /* LABELLED `Telegram <n>` ONLY AS A FALLBACK. resolveDest replaces it with
       the chat's real name as soon as the lookup answers -- see unnamed() --
       so this is what a row is called when the bot cannot read the name, not
       what it is normally called. */
    this.cfg.destinations = env.map((target, i) => ({
      id: 'd' + (i + 1), channel: 'telegram',
      label: 'Telegram ' + (i + 1), target,
      enabled: true, kinds: ['signals', 'news', 'scalper'],
    }));
    this.destsTouched = true;
    /* Said out loud because this is the moment the routing stops coming from
       secrets.env and starts coming from alerts.json -- and it does not take
       effect until Save, which is the sentence a reader needs. */
    this.say(`${env.length} imported — Save to make alerts.json the router`);
    this.paintDests();
    this.paintCreds();
  }

  addDest() {
    if (!this.cfg) return;
    if (this.destList() === null) this.cfg.destinations = [];
    /* DISABLED, AND TICKED FOR EVERYTHING. The two halves answer different
       questions: a new row must not start sending anywhere on its own (adding
       is not arming), but once armed it should behave the obvious way rather
       than silently receiving nothing because no kind was ever chosen. So the
       switch is the deliberate act and the kinds are the default. */
    this.cfg.destinations.push({
      id: this.newDestId(), channel: 'telegram', label: '', target: '',
      enabled: false, kinds: ['signals', 'news', 'scalper'],
    });
    /* Records that this list was EDITED, so an empty result at Save time is a
       removal (write []) and not an untouched fallback (write nothing). */
    this.destsTouched = true;
    this.say('added — paste the chat id, Test it, then switch it on');
    this.paintDests();
  }

  /** True when a WhatsApp target cannot possibly work on the Cloud API. */
  waGroupProblem(d) {
    if ((d.channel || 'telegram') !== 'whatsapp') return false;
    const provider = (this.creds && this.creds.whatsapp && this.creds.whatsapp.provider)
                     || 'cloud';
    if (provider !== 'cloud') return false;
    const t = String(d.target || '').trim();
    /* A group id in the forms the providers use: a JID (…@g.us) or the
       "<number>-<timestamp>" shape. Checked here as well as in notify.py
       because a warning where the target is typed is worth more than the same
       sentence in an error after a send. */
    const low = t.toLowerCase();
    return t.includes('@') || low.includes('g.us') || low.includes('newsletter');
  }

  paintDests() {
    const box = $('#alDests');
    if (!box) return;
    box.replaceChildren();
    const list = this.destList();
    const rows = list === null
      ? (((this.creds && this.creds.telegram && this.creds.telegram.env_targets) || [])
        .map((target, i) => ({ id: 'env-' + (i + 1), channel: 'telegram',
                               label: 'TELEGRAM_CHAT_ID #' + (i + 1), target,
                               enabled: true, kinds: null, fromEnv: true })))
      : list;

    if (!rows.length) {
      box.append(el('div', { class: 'alerts-empty',
        text: list === null
          ? 'no destinations, and TELEGRAM_CHAT_ID is empty — nothing is announced anywhere'
          : 'no destinations — nothing is announced anywhere' }));
    }

    for (const d of rows) {
      const ch = d.channel || 'telegram';
      const ro = !!d.fromEnv;               // env rows are shown, not edited

      /* A SWITCH, NOT A DOT. The dot was borrowed from the scheduler rows,
         where it is a STATUS light -- something the panel reports and you
         cannot click. Here the same shape was a CONTROL, and nothing about a
         small coloured circle says "press me to turn this off"; the only clue
         was the colour, which is exactly what a status light looks like. A
         track-and-knob reads as a switch at a glance and shows its state in
         the knob's POSITION as well as its colour, which also survives being
         looked at by someone who cannot separate the two greens.

         A <button role="switch">, so it is reachable by Tab and answers Space
         and Enter for free -- a span with a click handler is invisible to a
         keyboard. */
      const on = d.enabled !== false;
      const sw = el('button', {
        type: 'button',
        class: 'al-sw' + (on ? ' on' : ''),
        role: 'switch',
        'aria-checked': String(on),
        disabled: ro || null,
        title: ro ? 'from configs/secrets.env — always on'
                  : (on ? 'receiving — click to switch off'
                        : 'switched off — click to switch on'),
      }, el('span', { class: 'al-knob' }));
      if (!ro) {
        sw.addEventListener('click', () => {
          d.enabled = d.enabled === false;
          this.paintDests();
          this.paintCreds();               // a channel may have just come into use
        });
      }

      const label = el('input', { class: 'al-in al-label', type: 'text',
        value: d.label || '', placeholder: 'name, e.g. Gold group',
        disabled: ro || null });
      label.addEventListener('input', () => { d.label = label.value; });

      const sel = el('select', { class: 'al-in al-ch', disabled: ro || null });
      for (const c of ['telegram', 'whatsapp']) {
        sel.append(el('option', { value: c, text: c }));
      }
      sel.value = ch;
      sel.addEventListener('change', () => {
        d.channel = sel.value;
        /* Re-resolved, because the same string means different things on the
           two channels -- and the note under the row would otherwise still be
           describing the old one. */
        this.resolveDest(d);
        this.paintCreds();
      });

      const target = el('input', { class: 'al-in al-target', type: 'text',
        value: d.target || '',
        /* NAMES WHAT CAN BE PASTED, because the answer is not obvious and the
           id is the form nobody has to hand. @name and a t.me link are both
           accepted and resolved server-side; the note under the row says what
           the typed value became. */
        placeholder: ch === 'whatsapp'
          ? '+61412345678, or a wa.me link'
          : '@channel, t.me link, or -1001234567890',
        disabled: ro || null });
      target.addEventListener('input', () => { d.target = target.value.trim(); });
      /* Repainted on commit, not on every keystroke: the group warning and the
         "no target yet" note both depend on the finished value, and a panel
         that redraws under the cursor loses the caret mid-word. Resolving the
         address is asked of the server at the same moment, for the same
         reason. */
      target.addEventListener('change', () => this.resolveDest(d));

      const kinds = el('div', { class: 'alerts-chips al-kinds' });
      for (const k of ['signals', 'news', 'scalper']) {
        /* `kinds` ABSENT MEANS EVERYTHING (see notify.py), so an env row and a
           hand-written row with no key show every chip on. Clicking one on an
           editable row materialises the list, which is the moment
           "unspecified" becomes a choice. */
        const on = !Array.isArray(d.kinds) || d.kinds.includes(k);
        const chip = el('div', { class: 'tf-chip' + (on ? ' on' : ''), text: k,
          title: ro ? 'from secrets.env — receives everything'
                    : (on ? 'receives ' + k : 'does not receive ' + k) });
        if (!ro) {
          chip.addEventListener('click', () => {
            if (!Array.isArray(d.kinds)) d.kinds = ['signals', 'news', 'scalper'];
            d.kinds = on ? d.kinds.filter((x) => x !== k) : d.kinds.concat([k]);
            this.paintDests();
          });
        }
        kinds.append(chip);
      }

      /* The warning that used to sit at the top of the pane lives on the
         button that does it. It is a one-time fact -- true every time, needed
         once -- and a tooltip is where a one-time fact belongs. */
      const test = el('button', { class: 'btn alerts-schedbtn', text: 'Test',
        title: 'sends one real message to this row as it is on screen, '
               + 'saved or not' });
      test.addEventListener('click', () => this.testDest(d, test));

      /* CHANNEL FIRST, THEN NAME, THEN ADDRESS -- the order the row is filled
         in and the order it is read. The channel decides what the address even
         looks like (a chat id or a phone number) and what the placeholder
         says, so putting it after the field it governs asked the reader to
         guess the format and then re-read the row once it was resolved. */
      const head = el('div', { class: 'alerts-inst-head al-desthead' },
        sw, sel, label, target, test);
      if (!ro) {
        const rm = el('span', { class: 'rm', text: '×', title: 'remove this destination' });
        rm.addEventListener('click', () => {
          /* The same two-click arm the instrument rows use, for the same
             reason: one stray click here silently stops announcing to a whole
             group, and the file has no undo beyond the server's `.prev`. */
          if (rm.dataset.armed !== '1') {
            this.disarmAll();
            rm.dataset.armed = '1';
            rm.textContent = 'remove?';
            rm.classList.add('arm');
            rm._t = setTimeout(() => this.disarmAll(), 4000);
            return;
          }
          this.cfg.destinations = this.destList().filter((x) => x !== d);
          this.destsTouched = true;
          this.say('destination removed — Cancel still undoes it');
          this.paintDests();
          this.paintCreds();
        });
        head.append(rm);
      }

      const row = el('div', { class: 'alerts-inst al-dest' }, head, kinds);
      const notes = [];
      if (ro) notes.push('from configs/secrets.env — Import it to edit, or to split by kind');
      if (Array.isArray(d.kinds) && !d.kinds.length) {
        notes.push('receives nothing — no kind is ticked');
      }
      if (!ro && !String(d.target || '').trim()) notes.push('no target yet');
      /* WHAT IT WILL ACTUALLY SEND TO, and only when that differs from what
         was typed -- echoing an unchanged chat id back at the reader is noise.
         `_resolved` is filled by resolveDest() and is not saved: the FILE
         keeps what was typed, so the link stays recognisable, and the router
         resolves it again on every send. Storing the resolved form instead
         would quietly replace the reader's `t.me/goldsignals` with an id they
         would then have to decode to recognise their own channel. */
      if (d._resolveError) notes.push(d._resolveError);
      else {
        const line = [];
        /* THE KIND OF CHAT, because "Trade Like a Pro" alone does not say
           whether an alert is landing in a group of people or a private
           thread, and that is the difference between an embarrassing message
           and a private one. */
        if (d._type) line.push(d._type === 'private' ? 'private chat' : d._type);
        if (d._resolved && d._resolved !== String(d.target || '').trim()) {
          line.push('sends to ' + d._resolved);
        }
        if (line.length) notes.push(line.join(' · '));
        /* A NAME LOOKUP THAT FAILED IS DIAGNOSTIC, so it is shown -- "chat not
           found" here almost always means the bot is not in that chat yet,
           which is the same thing that will make the alert fail. The
           missing-token case is suppressed: the credential line above already
           says so, and repeating it on every row would bury this. */
        if (d._titleError && !/TELEGRAM_BOT_TOKEN|no name lookup/.test(d._titleError)) {
          notes.push('cannot read the name — ' + d._titleError);
        }
      }
      if (this.waGroupProblem(d)) {
        /* THE ONE THING THIS PANEL CANNOT DELIVER, said where the target is
           typed. The official Cloud API has no group endpoint, so a group id
           here is not a typo to be corrected -- it is a request Meta's API
           cannot serve. Better here than as an error code after a send. */
        notes.push('WhatsApp groups and channels are NOT supported by the official '
                   + 'Cloud API — use a phone number, or an unofficial provider '
                   + '(WHATSAPP_PROVIDER=whapi) that supports them');
      }
      for (const n of notes) row.append(el('div', { class: 'alerts-note', text: n }));
      box.append(row);
    }

    const on = rows.filter((d) => d.enabled !== false
                                  && String(d.target || '').trim()).length;
    const badge = $('#alDestCount');
    if (badge) badge.textContent = `${on} on`;
    this.resolvePending(rows);            // fills in "sends to ..." on first paint
  }

  /** Resolve every row that has never been asked about, then paint once.

      ONE PAINT, NOT ONE PER ROW. Called from paintDests, so resolving each row
      with its own repaint would have the pane redraw N times on open -- and
      each redraw re-enters the same loop. The `undefined` check is what stops
      that: resolveDest sets both fields to null before it awaits anything, so
      a row is only ever pending once. */
  async resolvePending(rows) {
    const todo = rows.filter((d) => d._resolved === undefined
                                    && d._resolveError === undefined
                                    && String(d.target || '').trim());
    if (!todo.length) return;
    await Promise.all(todo.map((d) => this.resolveDest(d, false)));
    this.paintDests();
  }

  /** True for a label that nobody chose: empty, or the Import placeholder. */
  unnamed(label) {
    const t = String(label || '').trim();
    return !t || /^(telegram|whatsapp)\s*\d*$/i.test(t);
  }

  /** Ask the server what this address resolves to. Never sends. */
  async resolveDest(d, repaint = true) {
    d._resolved = null;
    d._resolveError = null;
    const t = String(d.target || '').trim();
    if (t) {
      try {
        const q = new URLSearchParams({ channel: d.channel || 'telegram', target: t });
        const res = await fetch('/notify/resolve?' + q, { cache: 'no-store' });
        if (res.status === 404) {
          /* Left unresolved rather than marked bad: the address may be
             perfectly good and it is the SERVER that cannot answer. The
             credential line above carries the sentence about restarting. */
          d._resolved = null;
          d._resolveError = null;
          return;
        }
        const r = await res.json();
        if (r.ok) {
          d._resolved = r.target;
          d._type = r.type || null;
          d._titleError = r.title_error || null;
          /* THE CHAT NAMES ITSELF. `Telegram 1` is the one thing about a
             destination nobody needs told; "Trade Like a Pro" is what a reader
             wants beside a chat id, and Telegram already knows it.

             ONLY WHEN THE ROW IS UNNAMED, so a name somebody chose is never
             overwritten -- typing "Gold group (mine)" and having it silently
             become the channel's own title on the next open would be the panel
             arguing with the reader. `Telegram <n>` counts as unnamed because
             that is exactly the placeholder Import writes, and nobody types
             it. */
          if (r.title && this.unnamed(d.label)) d.label = r.title;
        } else {
          /* THE SERVER'S SENTENCE, not a restatement. "that is an invite link,
             which is a one-time hash rather than an address" is the whole
             explanation and the way out; "invalid target" is neither. */
          d._resolveError = r.error || 'unusable address';
        }
      } catch (err) {
        /* Silent on a network failure: the address may be perfectly good, and
           telling the reader it is broken because the check could not run
           would be a worse lie than saying nothing. The send still validates. */
      }
    }
    if (repaint) this.paintDests();
  }

  async testDest(d, btn) {
    if (!String(d.target || '').trim()) { this.say('no target to test'); return; }
    const was = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'sending…';
    this.say(`sending a test to ${d.label || d.target}…`);
    try {
      /* THE ROW AS IT IS ON SCREEN goes in the body, not an id to look up on
         disk. The button sits beside a row that may never have been saved, and
         a test that read the file would have tested the OLD address while the
         reader watched the new one. */
      const r = await fetch('/alerts/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ destination: {
          id: d.id, channel: d.channel || 'telegram',
          label: d.label, target: d.target,
        } }),
      });
      /* A 404 HERE IS NOT A FAILED SEND, and must not be reported as one --
         nothing was sent, and the fault is a stale server rather than the
         destination. This is the exact case that produced "test failed — not
         found" and sent the reader to look at Telegram. */
      if (r.status === 404) { this.say(`cannot test — ${STALE_SERVER}`); return; }
      const body = await r.json();
      if (body.ok) this.say(`test sent to ${d.label || d.target}`);
      /* THE API'S OWN WORDS, not "failed". "chat not found", "bot was kicked
         from the group" and "re-engagement message" each point at a different
         fix, and collapsing them into one word makes the panel useless at the
         only moment it matters. */
      else this.say(`test failed — ${body.error || `HTTP ${r.status}`}`);
    } catch (err) {
      this.say(`test failed — ${err.message || err}`);
    } finally {
      btn.disabled = false;
      btn.textContent = was;
    }
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

  /** Return every armed remove to its resting state.

      SCOPED TO THE WHOLE MODAL, not to `#alCells`. The destination rows carry
      the same two-click remove, and a query that only saw the instrument list
      would leave a destination armed forever -- exactly the state this exists
      to prevent, since an armed control that never disarms reads as an
      ordinary one. */
  disarmAll() {
    for (const rm of this.modal.querySelectorAll('.rm')) {
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
        this.cfg.scalper.watch = this.watch().filter((w) => w.symbol !== symbol);
        this.say(`${symbol} removed — Cancel still undoes it`);
        this.paint();
      });
      const head = el('div', { class: 'alerts-inst-head' },
        el('span', { class: 'sym', text: symbol }), rm);

      const chips = el('div', { class: 'alerts-chips' });
      for (const tf of TFS) {
        const cell = rows.find((w) => String(w.tf) === tf);
        const on = !!cell && cell.enabled !== false;
        /* THE GRADE IS SHOWN WHERE THE CHOICE IS MADE. `grade` is written by
           hand in configs/alerts.json from the friction floor measurement, and
           it already rides on every Telegram message -- but the message is read
           after the decision, and this panel is where the decision happens. A
           chip that is announcing from a cell measured below its own costs
           should not look identical to the one cell that was ever measured
           above them.

           This panel never WRITES grade. It is a measurement, not a preference,
           and a checkbox that let you promote a cell to `validated` would be a
           way to lie to yourself. It survives a save because the modal
           round-trips whole cell objects and only touches `enabled`. */
        const grade = rayoGrade(cell);
        /* THREE DIFFERENT REASONS A CHIP IS RED, and they are not the same
           sentence: a control was measured and loses, an unmeasurable frame
           was never scored because the rule cannot fire there, and an
           unregistered frame simply has no record. */
        const why = (cell && cell.measurable === false)
          ? ' — the rule cannot signal on this frame'
          : grade ? ' — ' + GRADE_WORD[grade]
          : cell ? ' — ungraded, nobody has measured this cell' : '';
        /* EACH CHIP CARRIES ITS OWN NUMBERS. They used to live in one line of
           prose under the whole instrument, taken from whichever cell happened
           to be listed first -- for XAUUSD that was 4h, so 4h's +0.2082 / +0.1896
           sat under all seven timeframes as though it described them. It does
           not: those seven cells run different channel lengths and grade out at
           validated, marginal five times, and below. The number that belongs to
           a timeframe now hangs on that timeframe. */
        /* EACH CHIP CARRIES ITS OWN MEASUREMENT. Net R per fill is what the
           cell earns per trade; total R/yr is what it earns the account, and
           the two rank cells differently -- 1h beats 30m per fill and 30m beats
           1h per year, because it trades three times as often. Both are here so
           the choice is made on the one that matters for the question. */
        const sgn = (v) => (v >= 0 ? '+' : '') + v.toFixed(4);
        /* A FRAME THE RULE CANNOT SIGNAL ON AT ALL is a different fact from
           one it signals on badly, and the chip must not conflate them. Every
           daily bar is stamped at broker hour 0 and the rule gates on
           07:00-21:00, so 1d yields zero tickets from 2,745 bars -- a category
           error, not a poor result. */
        const nums = (cell && cell.measurable === false)
          ? `${NL}the rule cannot signal on this frame at all`
            + `${NL}every daily bar is stamped at broker 00:00 and the session`
            + ` gate is 07:00-21:00, so it rejects all of them`
          : cell && Number.isFinite(cell.expected_net_r)
          ? `${NL}net ${sgn(cell.expected_net_r)} R per fill`
            + `, ${cell.total_r_per_year >= 0 ? '+' : ''}${cell.total_r_per_year} R/yr`
            + `${NL}${cell.n} fills, ${cell.win_pct}% win, drawdown ${cell.drawdown_r} R`
            + `${NL}worst sub-era ${sgn(cell.worst_era_net_r)}`
            + (cell.horizon_hours && cell.horizon_hours !== 24
                ? `${NL}resolved over ${cell.horizon_hours}h, not the 24h the`
                  + ' faster cells use' : '')
            + (cell.control
                ? `${NL}CONTROL — expected to lose; watched so the scoring can fail`
                : cell.tradeable ? '' : `${NL}journalled, NOT traded — see the note`)
          : (cell ? `${NL}no measurement recorded for this cell` : '');
        const chip = el('div', {
          class: 'tf-chip' + (on ? ' on' : '') + (grade ? ' g-' + grade : (cell ? ' g-none' : '')),
          text: tf,
          title: tf + ' · ' + (on ? 'announcing' : 'not announced') + why + nums,
        });
        chip.addEventListener('click', () => this.toggle(symbol, tf));
        chips.append(chip);
      }

      const box = el('div', { class: 'alerts-inst' }, head, chips);

      /* WHAT IS TRUE OF THE WHOLE INSTRUMENT, and nothing that is only true of
         one of its cells. The previous line here printed the first cell's note
         verbatim under all seven chips, which stated one timeframe's measured
         result as if it were the instrument's. This counts the grades instead,
         which is a fact about the row it sits under, and says where the
         per-timeframe numbers now live. */
      const graded = TFS.map((tf) => rows.find((w) => String(w.tf) === tf))
        .filter(Boolean);
      const on = graded.filter((c) => c.enabled !== false).length;
      const tradeable = graded.filter((c) => c.tradeable).length;
      const controls = graded.filter((c) => c.control).length;
      const journal = graded.filter((c) => !c.tradeable && !c.control
                                        && c.measurable !== false).length;
      const nosig = graded.filter((c) => c.measurable === false).length;
      const yr = graded.filter((c) => c.tradeable)
        .reduce((s, c) => s + (c.total_r_per_year || 0), 0);

      const bits = [`${on} of ${graded.length} announcing`];
      const roles = [];
      if (tradeable) roles.push(`${tradeable} tradeable`);
      if (journal) roles.push(`${journal} journalled only`);
      if (controls) roles.push(`${controls} control`);
      if (nosig) roles.push(`${nosig} cannot signal`);
      if (roles.length) bits.push(roles.join(', '));
      if (tradeable) bits.push(`${yr >= 0 ? '+' : ''}${yr.toFixed(1)} R/yr combined`);
      box.append(el('div', { class: 'alerts-note', text: bits.join(' · ') }));

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
      /* A CELL NOBODY HAS MEASURED IS NOT TRADEABLE, and says so from the
         moment it exists. `tradeable` is written by the measurement script,
         never here -- a switch that let you mark a cell green by hand would be
         a way to lie to yourself about what has been tested. */
      list.push({ symbol, tf, enabled: true, tradeable: false, note:
                  'added by hand, never measured — journalled only until '
                  + 'tools/scalper.py --backtest grades it' });
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
      this.cfg.scalper = this.cfg.scalper || { watch: [] };
      this.cfg.scalper.watch = this.cfg.scalper.watch || [];
      /* Added with NO frame on. Picking a symbol says "I am interested"; it
         does not say which frames, and defaulting one on would start announcing
         a cell nobody chose. The chips are right there. */
      this.cfg.scalper.watch.push({ symbol: name, tf: '1h', enabled: false,
                                    tradeable: false, note:
                                    'added by hand, never measured' });
      this.say(`${name} added — pick a timeframe`);
      this.paint();
    });
  }

  async save() {
    if (!this.cfg) { this.say('nothing loaded — not saving'); return; }
    this.cfg.news = this.cfg.news || {};
    this.cfg.news.enabled = $('#alNews').checked;
    this.cfg.news.lead_minutes = Math.max(1, Number($('#alNewsLead').value) || 10);
    /* FALL BACK TO WHAT THE FILE SAID, never to a literal.
       This read `|| 'any'`, and that turned an unreadable control into the
       LOUDEST possible setting: a config of `low` whose value the select could
       not represent came back as `any` -- every event including bank-holiday
       markers -- from a Save that touched nothing. An empty `.value` means the
       control could not show the stored setting, and the only safe reading of
       that is to leave the setting alone. */
    this.cfg.news.impact = $('#alNewsImpact').value
                           || this.loadedImpact || 'any';
    this.cfg.news.fetch_minutes = Math.max(0, Number($('#alNewsEvery').value) || 0);

    /* A ROW WITH NO TARGET IS DROPPED, NOT SAVED. "+ Add destination" creates
       an empty row on purpose -- it is the form -- so a Save while one is
       half-filled must not write a destination that addresses nothing. It is
       dropped rather than refused because refusing would block the Save of the
       OTHER tabs' edits on account of a row nobody finished, and the message
       says how many went.

       The key is deleted entirely when the list ends up empty AND was never
       populated, so an install that only looked at this tab keeps falling back
       to TELEGRAM_CHAT_ID instead of being silenced by an empty array it never
       asked for. An explicit removal of every row still saves `[]`, because
       that is a decision -- see ABSENT IS NOT THE SAME AS EMPTY. */
    const dl = this.destList();
    let dropped = 0;
    if (dl) {
      /* ONLY THE STORED FIELDS ARE WRITTEN. `_resolved`, `_resolveError` and
         `fromEnv` are panel state, and JSON.stringify would happily save them
         -- putting a cached resolution in a file that is read by tools which
         resolve the address themselves. A stale one there would be worse than
         none, since nothing would ever correct it. */
      const kept = dl.filter((d) => String(d.target || '').trim())
        .map((d) => ({ id: d.id, channel: d.channel || 'telegram',
                       label: d.label || '', target: String(d.target).trim(),
                       enabled: d.enabled !== false,
                       ...(Array.isArray(d.kinds) ? { kinds: d.kinds } : {}) }));
      dropped = dl.length - kept.length;
      if (!kept.length && !this.destsTouched) delete this.cfg.destinations;
      else this.cfg.destinations = kept;
    }
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
      const dests = (this.destList() || []).filter((d) => d.enabled !== false).length;
      /* BOTH HALVES OF THE ANSWER. "8 cells announcing" reads as success while
         every destination is switched off and nothing can arrive, which was
         the exact failure this tab exists to make visible. */
      this.say(`saved — ${on} cell${on === 1 ? '' : 's'} announcing to `
               + `${dests} destination${dests === 1 ? '' : 's'}`
               + (dropped ? ` · ${dropped} empty destination dropped` : ''));
      setTimeout(() => this.close(), 600);
    } catch (err) {
      // THE MODAL STAYS OPEN on a failed save, holding the edit. Closing would
      // lose work that was never written, and the user would have no way to
      // know which of the two states is on disk.
      this.say(`save failed: ${err.message || err}`);
    }
  }
}

/* chat.js — the Ask panel.
 *
 * WHAT THIS CAN AND CANNOT ANSWER, stated here because the distinction is the
 * whole design and a chat box that hides it is worse than no chat box.
 *
 *   TERMINOLOGY      answered from a glossary written in this project and
 *                    already vetted -- the same wording the tooltips use, so
 *                    the chat and the chart cannot disagree.
 *   CURRENT STATE    answered by READING the live panels: the trend read, the
 *                    signal engine and its scorecard, open positions, the
 *                    account. Every number quoted is one already on screen.
 *   NEWS, SENTIMENT  NOT answered. There is no feed in this project and no
 *                    model behind this box. Inventing a market narrative is
 *                    exactly the failure this whole codebase is built against,
 *                    so it says it does not know and points at the Calendar.
 *
 * There is no LLM here. If one is wired in later the honest shape is a server
 * proxy that receives this same grounded state as context; until then, being
 * unable to answer is the correct behaviour rather than a gap to paper over.
 */

import { $, el } from '../util.js';

/* Definitions lifted from the tooltips and the README, so a term means the same
   thing wherever it is read. Keys are matched as whole words, longest first. */
const GLOSSARY = {
  atr: 'Average True Range — the size of a normal bar. Almost every threshold '
    + 'here is expressed in ATR rather than in points, so the same number means '
    + 'the same thing on gold at 42 and EURUSD at 0.0008.',
  bos: 'Break of Structure. Price CLOSES through the last swing in the same '
    + 'direction as the current bias — a continuation break. Wicks never count. '
    + 'How far it closed past the level is shown by the weight of the mark: '
    + 'marks clearing 1.0 ATR draw bold, and about a third of BOS labels are '
    + 'closes under a quarter-ATR through, which is why the distinction exists.',
  choch: 'Change of Character. Price closes through the last swing AGAINST the '
    + 'current bias, so it is the first break that argues the trend has turned. '
    + 'Measured across three eras, the BOS/CHoCH label itself did not replicate '
    + '— treat it as bookkeeping, not as a signal.',
  displacement: 'How far the close travelled past the level it broke, in ATR. '
    + 'The frozen spec requires 1.0 ATR. Roughly 80-90% of the BOS marks on a '
    + 'chart would not clear it.',
  support: 'A price band where the market has repeatedly turned back UP. Drawn '
    + 'from clustered pivot lows. The ×N is how many times price came back.',
  resistance: 'The same thing above price: a band where the market has '
    + 'repeatedly turned back DOWN.',
  demand: 'A base that price left in a hurry to the UPSIDE. Marked ● while '
    + 'untested, meaning nothing has traded back through it since.',
  supply: 'A base that price left in a hurry to the DOWNSIDE.',
  channel: 'A corridor: two roughly parallel rails around price. It is drawn '
    + 'solid over the bars it was fitted to, and dashed past the last bar where '
    + 'there is no data. Measured over 29,208 samples, the DIRECTION of a '
    + 'channel carries no information about what price does next.',
  swing: 'A pivot high or low. A swing is MAJOR (ringed on the chart) when it '
    + 'also survives the strength-6 window — the same definition the Major '
    + 'structure setting uses.',
  'risk reward': 'Read as: risk 1 to make this much. The 1 is the distance from '
    + 'price to the Invalidation level — what it costs to be wrong. The second '
    + 'number is the distance to the first level in the way.',
  invalidation: 'The price that proves the current read wrong: the nearest '
    + 'level on the losing side. Through it, the reasoning has failed.',
  conviction: 'The 0-100 score beside the trend verdict. It combines whether '
    + 'the timeframes agree, whether swing structure confirms, and whether '
    + 'price is well placed. It is not a forecast of size.',
  baseline: 'The accuracy you would get by always guessing the more common '
    + 'direction. A model must beat THIS, not 50%.',
  'walk-forward': 'Every prediction is made by a model trained only on bars '
    + 'before it. Nothing is scored on data it was fitted to.',
  spread: 'The gap between bid and ask — paid on entry. In this project it is '
    + 'charged per instrument from the broker spec, and it is what killed most '
    + 'of the measured edges.',
  friction: 'Spread plus slippage, expressed in R. Every detector here produced '
    + 'a gross edge of 0.05-0.17 R against friction of 0.07-0.24 R.',
  r: 'One R is the distance from entry to the stop. A 2R target is twice that '
    + 'far away. Expressing results in R makes instruments comparable.',
  pip: 'The conventional smallest quote step: 0.0001 on most FX pairs, 0.01 on '
    + 'JPY crosses. Gold is usually quoted in dollars per ounce instead.',
  lot: 'Position size. One standard lot is 100,000 units of the base currency; '
    + '0.01 lots is a micro lot.',
  equity: 'Balance plus the profit or loss of everything still open. It is the '
    + 'number margin level is computed from, so it is the one that matters.',
  'margin level': 'Equity divided by the margin in use. It is the number that '
    + 'predicts a margin call — below 100% means open losses have eaten the '
    + 'deposit backing the positions.',
  'margin free': 'Equity minus margin in use: what is left to open anything '
    + 'new, or to absorb a further loss.',
  floating: 'The unrealised profit or loss of open positions. It moves every '
    + 'tick and is not yours until the position closes.',
};

const NO_FEED = ['news', 'sentiment', 'headline', 'nfp', 'cpi', 'fed', 'fomc',
                 'rate decision', 'twitter', 'reddit', 'analyst', 'forecast for',
                 'will it go', 'should i buy', 'should i sell', 'price target'];

function lookup(q) {
  const s = q.toLowerCase();
  const keys = Object.keys(GLOSSARY).sort((a, b) => b.length - a.length);
  const hits = keys.filter((k) => new RegExp(`\\b${k}\\b`, 'i').test(s));
  return hits.slice(0, 3);
}

/** Everything the panels currently show, read rather than recomputed. */
function liveState() {
  const txt = (sel) => ($(sel) ? $(sel).textContent.trim() : null);
  const facts = [...document.querySelectorAll('.tr-fact')]
    .map((f) => f.innerText.replace(/\n/g, ': '));
  const rows = [...document.querySelectorAll('.tr-row')]
    .map((r) => r.innerText.replace(/\n/g, ' '));
  const stats = [...document.querySelectorAll('.sig-stat')]
    .map((r) => r.innerText.replace(/\n/g, ': '));
  return {
    symbol: txt('#trSym'), verdict: txt('.tr-verdict'), score: txt('.tr-score'),
    note: txt('.tr-note'), frames: rows, facts,
    signal: txt('.sig-badge'), sigScore: txt('.sig-score'), stats,
    verdictLine: txt('.sig-verdict'),
    /* Read the cells that are THERE, rather than a hard-coded list of ids.
       The list named five cells by id and dereferenced each one's label
       without a null check, so removing a cell from the status bar took the
       whole Ask panel down with a TypeError -- a chart-furniture edit breaking
       an unrelated feature. Whatever the strip shows is what gets reported. */
    account: [...document.querySelectorAll('.acct-cell')].map((cell) => {
      const label = cell.querySelector('span');
      const value = cell.querySelector('b');
      return label && value ? `${label.textContent}: ${value.textContent}` : null;
    }).filter(Boolean),
    positions: document.querySelectorAll('#panel tbody tr').length,
  };
}

function answer(q) {
  const s = q.toLowerCase().trim();
  if (!s) return null;

  /* Refusals first, so a question that mentions a term AND asks for a forecast
     gets the refusal rather than a definition that dodges it. */
  if (NO_FEED.some((k) => s.includes(k))) {
    return {
      kind: 'cannot',
      text: 'I have no news or sentiment feed, and there is no model behind '
        + 'this box — so anything I said about headlines, positioning or where '
        + 'price is going next would be invented. The Calendar tab carries '
        + 'scheduled economic events, which is the only forward-looking data '
        + 'this app actually has.',
    };
  }

  if (/(current|right now|situation|what.s happening|read|state)/.test(s)) {
    const st = liveState();
    const lines = [
      `${st.symbol || 'chart'} — trend read ${st.verdict || '—'} (${st.score || '—'}/100)`,
      st.note ? `  ${st.note}` : '',
      ...st.frames.map((f) => `  ${f}`),
      ...st.facts.map((f) => `  ${f}`),
      `Signal engine ${st.signal || '—'} ${st.sigScore || ''}`.trim(),
      ...st.stats.map((x) => `  ${x}`),
      st.verdictLine ? `  ${st.verdictLine}` : '',
      `Account — ${st.account.join(' · ')}`,
      st.positions ? `${st.positions} row(s) in the open panel.` : '',
    ].filter(Boolean);
    return { kind: 'state', text: lines.join('\n') };
  }

  const hits = lookup(s);
  if (hits.length) {
    return {
      kind: 'term',
      text: hits.map((k) => `${k.toUpperCase()}\n${GLOSSARY[k]}`).join('\n\n'),
    };
  }
  return {
    kind: 'unknown',
    text: 'I do not have a grounded answer for that. I can define the terms '
      + 'this app uses (ATR, BOS, CHoCH, displacement, support, demand, '
      + 'channel, R, margin level, baseline, walk-forward…) and I can read back '
      + 'the current chart and account state. For anything needing news or a '
      + 'forecast, I would be making it up.',
  };
}

export function installChat() {
  const btn = $('#chatBtn');
  if (!btn) return;

  const box = el('div', { class: 'chat-panel', hidden: true });
  const log = el('div', { class: 'chat-log' });
  const form = el('form', { class: 'chat-form' });
  const input = el('input', {
    class: 'chat-input', type: 'text', autocomplete: 'off',
    placeholder: 'What is displacement?  ·  what is the current read?',
  });
  form.append(input);
  /* A HAND-BUILT GRIP, because the native one cannot go here.
   *
   * `resize: vertical` puts its handle in the bottom-right corner, and this
   * panel is anchored by its BOTTOM edge -- so the one corner CSS will give is
   * the one corner that never moves. The grip belongs on the edge that travels,
   * which is the top, and on the left where it is clear of the close gesture.
   *
   * Dragging up increases the height; the bottom stays pinned to wherever the
   * status bar is, so the panel grows into the chart rather than over the
   * account numbers. */
  const grip = el('div', { class: 'chat-grip', title: 'Drag to resize' });
  box.append(
    grip,
    el('div', { class: 'chat-head' },
       'Grounded on this app only — terms and live state. No news, no forecasts.'),
    log, form,
  );
  document.body.append(box);

  let drag = null;
  grip.addEventListener('pointerdown', (e) => {
    const r = box.getBoundingClientRect();
    /* The ceiling is captured HERE, from the bottom edge, and it is a fixed
       number for the whole gesture. Deriving it from `offsetTop` inside the
       move handler was wrong twice over: the top edge is what the drag is
       moving, so the limit shrank as fast as the panel grew and a 250px drag
       yielded 26. */
    const top = document.querySelector('.topbar');
    const ceiling = top ? top.getBoundingClientRect().bottom : 0;
    drag = { y: e.clientY, h: r.height, max: r.bottom - ceiling };
    try { grip.setPointerCapture(e.pointerId); } catch { /* no live pointer */ }
    e.preventDefault();
  });
  grip.addEventListener('pointermove', (e) => {
    if (!drag) return;
    /* UP is positive: the grip is on the top edge, so the height grows as the
       pointer rises. Clamped here rather than left to CSS, because an inline
       height set past max-height would stick around invisibly and the next
       drag would start from a number the panel never actually had. */
    const want = drag.h + (drag.y - e.clientY);
    box.style.height = `${Math.max(180, Math.min(want, drag.max))}px`;
  });
  const endDrag = () => { drag = null; };
  grip.addEventListener('pointerup', endDrag);
  grip.addEventListener('pointercancel', endDrag);

  const say = (who, text) => {
    const m = el('div', { class: `chat-msg chat-${who}` });
    for (const line of text.split('\n')) m.append(el('div', {}, line || ' '));
    log.append(m);
    log.scrollTop = log.scrollHeight;
  };

  say('bot', 'Ask about a term, or about the current read.');

  form.addEventListener('submit', (e) => {
    e.preventDefault();
    const q = input.value.trim();
    if (!q) return;
    say('you', q);
    input.value = '';
    const a = answer(q);
    say(a.kind === 'cannot' ? 'cannot' : 'bot', a.text);
  });

  const toggle = () => {
    box.hidden = !box.hidden;
    btn.classList.toggle('on', !box.hidden);
    if (!box.hidden) input.focus();
  };
  btn.addEventListener('click', toggle);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !box.hidden) toggle();
  });
}

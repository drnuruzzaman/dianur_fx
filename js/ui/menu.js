/* menu.js — the one popup menu and the one toast, reused by every caller. */

import { $, el } from '../util.js';

const root = () => $('#menu');
let closer = null;

/* THE SUBMENU IS A SECOND PANEL, not a nested list.
 *
 * It opens on HOVER and leaves the parent standing, because the parent is what
 * tells you where you are -- collapsing it the moment you slide sideways loses
 * the context you were navigating from, and makes a multi-select submenu
 * impossible to use: every tick would close the thing you were ticking in.
 *
 * Created lazily and reused, so nothing exists in the DOM until a menu with
 * children is actually opened.
 */
let subEl = null;
let subOwner = null;
/* Closing is DEFERRED, opening is not.
 *
 * Reaching WhatsApp means travelling from the Share row to a panel beside it,
 * and that path crosses other rows -- whose hover was closing the submenu out
 * from under the pointer before it arrived. Any diagonal move to a submenu
 * does this; it is not a slip by the user.
 *
 * So leaving a parent only SCHEDULES the close, and entering the submenu (or
 * coming back to its parent) cancels it. Opening another parent's submenu
 * still switches instantly, because that is a deliberate move with an obvious
 * target.
 */
let subSwitchTimer = null;
/* How long the pointer must REST on another parent before its panel takes
   over. Long enough that crossing the row on the way to the open panel never
   triggers it, short enough that a deliberate move does not feel stuck. */
const SUB_DWELL = 420;

function cancelSubClose() {
  if (subSwitchTimer) { clearTimeout(subSwitchTimer); subSwitchTimer = null; }
}

/* WHERE THE POINTER IS, not how long it lingered.
 *
 * The submenu closes when the pointer leaves BOTH panels, and stays open for
 * as long as it is over either one -- including the strip between them and any
 * row it merely crosses on the way. That makes the reach from "Share" to
 * "WhatsApp" survivable at any speed, which a grace period alone never could:
 * a slow, careful move is exactly the one a short timer punishes.
 *
 * The rects are read live because both panels are positioned per open.
 */
let regionWatch = null;
let lastX = null, lastY = null;

function inRect(r, x, y) {
  return r && x >= r.left - 6 && x <= r.right + 6 && y >= r.top - 6 && y <= r.bottom + 6;
}

/* IS THE POINTER ON ITS WAY TO THE OPEN SUBMENU?
 *
 * The reach from a parent row to its children is diagonal, and that diagonal
 * crosses the rows in between. Judging it by how LONG the pointer spends on
 * those rows is guesswork -- too short and a careful mover loses the panel,
 * too long and a deliberate switch feels stuck. Direction answers it properly:
 * while the pointer is travelling towards the open panel and is level with it,
 * the rows it passes over are scenery, not destinations.
 *
 * This is the "safe triangle" every good menu implements: the corridor between
 * where the pointer is and the panel it is heading for stays live.
 */
function headingToSub(x, y) {
  if (!subEl || subEl.hidden || lastX === null) return false;
  const r = subEl.getBoundingClientRect();
  const dx = x - lastX;
  const toRight = r.left >= lastX;             // panel sits to the right
  const closing = toRight ? dx > 0 : dx < 0;   // and we are moving that way
  if (!closing) return false;
  /* vertically within the corridor the panel occupies, with slack for the
     overshoot a hand naturally makes */
  return y >= r.top - 28 && y <= r.bottom + 28;
}

function startRegionWatch() {
  if (regionWatch) return;
  regionWatch = (e) => {
    const m = root();
    if (m.hidden || !subEl || subEl.hidden) { lastX = e.clientX; lastY = e.clientY; return; }
    const x = e.clientX, y = e.clientY;
    const overMenu = inRect(m.getBoundingClientRect(), x, y);
    const overSub = inRect(subEl.getBoundingClientRect(), x, y);

    /* NOTHING CLOSES THE PANEL ON A TIMER.
       Three attempts at "close it when the pointer has been away long enough"
       all produced the same complaint -- the panel going before it could be
       clicked -- because every threshold is wrong for someone, and a panel that
       vanishes while you are reaching for it is far worse than one that
       overstays. It now closes only when you pick something, when you open a
       different parent's panel, or when the whole menu closes. */

    /* Reaching the far end of an open panel means travelling down and across,
       and that path runs over the parent rows below -- which is how the
       Donchian checkbox list was being replaced by Share's on the way to
       "Signal engine". Being over another parent for an instant is not a
       request to switch; DWELLING on it is. The dwell timer lives on the row
       (see mouseenter below) and this only cancels it once the pointer is
       safely inside the panel. */
    if (overSub) cancelSubClose();
    lastX = x; lastY = y;
  };
  document.addEventListener('mousemove', regionWatch, true);
}

function stopRegionWatch() {
  if (!regionWatch) return;
  document.removeEventListener('mousemove', regionWatch, true);
  regionWatch = null;
}

function subRoot() {
  if (!subEl) {
    subEl = el('div', { class: 'menu submenu' });
    subEl.hidden = true;
    /* the pointer being INSIDE the submenu is the strongest possible signal
       that it should stay open */
    subEl.addEventListener('mouseenter', cancelSubClose);
    document.body.append(subEl);
  }
  return subEl;
}

export function closeSub() {
  cancelSubClose();
  stopRegionWatch();
  if (!subEl) return;
  subEl.hidden = true;
  subEl.innerHTML = '';
  if (subOwner) subOwner.classList.remove('open');
  subOwner = null;
}

export function closeMenu() {
  const m = root();
  m.hidden = true;
  m.innerHTML = '';
  closeSub();
  if (closer) { document.removeEventListener('pointerdown', closer, true); closer = null; }
}

/**
 * items: [{label, value, checked, color, kind:'sep'|'cap'}]
 * anchor: element the menu should sit under.
 */
export function openMenu(anchor, items, onPick) {
  const m = root();
  closeMenu();
  /* Whoever had the shared menu before does not have it any more. Broadcast it
     so a previous owner's hover-out timer cannot reach across and close a menu
     that now belongs to someone else. */
  document.dispatchEvent(new CustomEvent('menu:opened', { detail: { anchor } }));
  m.hidden = false;

  const build = (host, list, pick) => {
    for (const it of list) {
      if (it.kind === 'sep') { host.append(el('div', { class: 'sep' })); continue; }
      if (it.kind === 'cap') { host.append(el('div', { class: 'cap', text: it.label })); continue; }

      const row = el('button', {
        onclick: (e) => {
          e.stopPropagation();
          /* A row that only opens a submenu has nothing to do on click; acting
             on it would fire the first child's job by surprise. */
          if (it.sub && !it.value) { openSub(row, it); return; }
          pick(it.value, it);
          if (!it.keepOpen) closeMenu();
        },
      },
      el('span', { class: 'tick', text: it.checked ? '✓' : '' }),
      it.color ? el('span', { class: 'swatch', style: `background:${it.color}` }) : null,
      el('span', { text: it.label }),
      it.sub ? el('span', { class: 'arrow', text: '›' })
             : it.hint ? el('kbd', { text: it.hint }) : null);

      /* Hovering a PARENT row opens its children; hovering a plain row closes
         whatever was open, so the second panel always belongs to the row the
         pointer is actually on. */
      /* leaving the row abandons any pending switch it started */
      row.addEventListener('mouseleave', cancelSubClose);
      row.addEventListener('mouseenter', () => {
        /* A plain row NO LONGER closes the submenu.
           Timing-based closing was the wrong model: how long the pointer rests
           on a row it is only passing over is not something to guess at, and
           every guess is wrong for someone. What actually matters is whether
           the pointer has LEFT the menu system, which is a question of
           position, not duration -- see the region watcher below. */
        if (!it.sub) return;
        cancelSubClose();
        /* Opening the FIRST panel is instant -- nothing is open, so nothing can
           be stolen. Swapping to a DIFFERENT parent's panel waits for a dwell,
           and is abandoned the moment the pointer reaches the open one, so
           merely crossing this row on the way there costs nothing. */
        if (!subOwner || subOwner === row) { openSub(row, it); return; }
        if (headingToSub(lastX, lastY)) return;       // travelling to the panel
        subSwitchTimer = setTimeout(() => {
          subSwitchTimer = null;
          openSub(row, it);
        }, SUB_DWELL);
      });
      /* the watcher needs to reach these without re-deriving the item list */
      if (it.sub) {
        row.__subItem = it;
        row.__openSub = () => openSub(row, it);
      }
      host.append(row);
    }
  };

  function openSub(row, it) {
    cancelSubClose();
    /* the row being opened is now the owner; a pending close for the previous
       one must not fire after this point */
    if (subOwner === row) return;              // already showing this one
    closeSub();
    const sm = subRoot();
    sm.hidden = false;
    startRegionWatch();
    subOwner = row;
    row.classList.add('open');
    /* Named, because a multi-select row has to rebuild the panel it lives in
       and therefore needs to hand this same callback back to build().
       (`arguments.callee` would be the lazy way and is illegal here: modules
       are strict mode, so it throws rather than misbehaving quietly.) */
    const onSubPick = (v, sit) => {
      onPick(v, sit);
      /* redraw in place so the tick updates without the panel vanishing */
      if (sit.keepOpen) {
        sm.innerHTML = '';
        build(sm, typeof it.sub === 'function' ? it.sub() : it.sub, onSubPick);
      }
    };
    build(sm, typeof it.sub === 'function' ? it.sub() : it.sub, onSubPick);
    const rr = row.getBoundingClientRect();
    sm.style.visibility = 'hidden';
    sm.style.left = '0px'; sm.style.top = '0px';
    requestAnimationFrame(() => {
      const w = sm.offsetWidth, h = sm.offsetHeight;
      /* flip to the left when there is no room on the right */
      /* Flush against the parent, not offset by a gap: a 2px dead strip
         between the two panels is one more place the pointer can "leave" the
         menu on its way across. */
      const right = rr.right;
      const left = right + w > window.innerWidth - 8 ? rr.left - w : right;
      sm.style.left = Math.max(8, left) + 'px';
      sm.style.top = Math.min(rr.top - 4, window.innerHeight - h - 8) + 'px';
      sm.style.visibility = 'visible';
    });
  }

  build(m, items, onPick);

  const r = anchor.getBoundingClientRect();
  m.style.visibility = 'hidden';
  m.style.left = '0px'; m.style.top = '0px';
  requestAnimationFrame(() => {
    const mw = m.offsetWidth, mh = m.offsetHeight;
    m.style.left = Math.min(r.left, window.innerWidth - mw - 8) + 'px';
    m.style.top = Math.min(r.bottom + 4, window.innerHeight - mh - 8) + 'px';
    m.style.visibility = 'visible';
  });

  closer = (e) => {
    const inSub = subEl && !subEl.hidden && subEl.contains(e.target);
    if (!m.contains(e.target) && !inSub && e.target !== anchor) closeMenu();
  };
  setTimeout(() => document.addEventListener('pointerdown', closer, true), 0);
}

let toastTimer = null;
export function toast(msg, ms = 3200) {
  const t = $('#toast');
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, ms);
}

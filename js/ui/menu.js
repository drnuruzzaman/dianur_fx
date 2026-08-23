/* menu.js — the one popup menu and the one toast, reused by every caller. */

import { $, el } from '../util.js';

const root = () => $('#menu');
let closer = null;

export function closeMenu() {
  const m = root();
  m.hidden = true;
  m.innerHTML = '';
  if (closer) { document.removeEventListener('pointerdown', closer, true); closer = null; }
}

/**
 * items: [{label, value, checked, color, kind:'sep'|'cap'}]
 * anchor: element the menu should sit under.
 */
export function openMenu(anchor, items, onPick) {
  const m = root();
  closeMenu();
  m.hidden = false;

  for (const it of items) {
    if (it.kind === 'sep') { m.append(el('div', { class: 'sep' })); continue; }
    if (it.kind === 'cap') { m.append(el('div', { class: 'cap', text: it.label })); continue; }
    m.append(el('button', {
      onclick: (e) => { e.stopPropagation(); onPick(it.value, it); if (!it.keepOpen) closeMenu(); },
    },
    el('span', { class: 'tick', text: it.checked ? '✓' : '' }),
    it.color ? el('span', { class: 'swatch', style: `background:${it.color}` }) : null,
    el('span', { text: it.label }),
    it.hint ? el('kbd', { text: it.hint }) : null));
  }

  const r = anchor.getBoundingClientRect();
  m.style.visibility = 'hidden';
  m.style.left = '0px'; m.style.top = '0px';
  requestAnimationFrame(() => {
    const mw = m.offsetWidth, mh = m.offsetHeight;
    m.style.left = Math.min(r.left, window.innerWidth - mw - 8) + 'px';
    m.style.top = Math.min(r.bottom + 4, window.innerHeight - mh - 8) + 'px';
    m.style.visibility = 'visible';
  });

  closer = (e) => { if (!m.contains(e.target) && e.target !== anchor) closeMenu(); };
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

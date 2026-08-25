/* tips.js — one explanatory tooltip, shared by every panel.
 *
 * The side panels were built for someone who already knows the vocabulary.
 * "Majority baseline 52.7%", "R:R to first zone 0.31:1", "Meanrev -18" are all
 * precise and all opaque: they name a quantity without saying what it measures,
 * what a good value looks like, or what it should change about your reading of
 * the chart. Native `title=` attributes carried a one-line gloss, but they are
 * unstyled, slow to appear, capped at plain text, and invisible on touch.
 *
 * So the same tooltip the chart zones use is generalised here. Three parts,
 * because the three questions are always the same:
 *
 *     TITLE   what this row is called
 *     BODY    what it means, in a sentence, with no jargon
 *     NOTE    how to read the number you are looking at right now
 *
 * The NOTE is the part a static `title=` could never do -- it is written
 * against the live value, so "52%" can be told apart from "52% when the
 * baseline is 57%" without the reader having to do the subtraction.
 *
 * ONE node for the whole app, positioned on demand. Panels re-render often
 * (the Trend read repaints on every tick) and a per-row tooltip element would
 * be built and thrown away thousands of times a session.
 */

let node = null;

function ensure() {
  if (node) return node;
  node = document.createElement('div');
  node.className = 'info-tip';
  node.hidden = true;
  document.body.appendChild(node);
  return node;
}

/**
 * Attach an explanation to an element. Returns the element, so it composes with
 * the `el()` helper: `tip(el('span', ...), 'Title', 'what it means')`.
 */
export function tip(node_, title, body, note) {
  if (!node_) return node_;
  node_.dataset.tipTitle = title;
  if (body) node_.dataset.tipBody = body;
  if (note) node_.dataset.tipNote = note;
  node_.removeAttribute('title');      // or the native one fires on top
  return node_;
}

let host_ = null;                    // the row currently being explained

function show(host) {
  const t = ensure();
  const d = host.dataset;
  /* The arrow lives INSIDE the innerHTML rather than as a persistent child,
     because innerHTML replaces everything each time. It is absolutely
     positioned, so it takes no part in the flex column above it. */
  t.innerHTML = '<s class="tip-arrow"></s>'
    + `<b>${d.tipTitle}</b>`
    + (d.tipBody ? `<i>${d.tipBody}</i>` : '')
    + (d.tipNote ? `<u>${d.tipNote}</u>` : '');
  t.hidden = false;

  /* Anchored to the ELEMENT, not the cursor. These rows are thin and the
     panels are narrow, so a cursor-following tooltip covers the very number it
     is explaining. */
  const r = host.getBoundingClientRect();
  const w = t.offsetWidth, h = t.offsetHeight;
  const gap = 10;
  // Prefer the side with room. The panels sit against an edge, so the tooltip
  // almost always opens inward.
  let left = r.left - w - gap;
  let side = 'left';                 // which side of the ROW the tooltip is on
  if (left < 4) { left = r.right + gap; side = 'right'; }
  if (left + w > window.innerWidth - 4) {
    left = Math.max(4, window.innerWidth - w - 4);
  }
  let top = r.top + r.height / 2 - h / 2;
  top = Math.max(4, Math.min(top, window.innerHeight - h - 4));
  t.style.left = `${left}px`;
  t.style.top = `${top}px`;
  t.classList.toggle('from-left', side === 'left');
  t.classList.toggle('from-right', side === 'right');

  /* The arrow tracks the ROW, not the tooltip's own centre. Those are the same
     thing only while the tooltip is unclamped -- near the top or bottom of the
     screen `top` is pushed back inside the viewport, and an arrow pinned to
     50% would then point at a row several centimetres away. */
  const arrow = t.querySelector('.tip-arrow');
  if (arrow) {
    const y = r.top + r.height / 2 - top;      // row centre, tooltip-relative
    const half = (arrow.offsetHeight || 9) / 2; // measured, not assumed
    /* On a short viewport the tooltip can be taller than the room above or
       below the row, and the clamp above pushes it back inside. The arrow then
       CANNOT reach its row -- measured at up to 542px away on a 420px-high
       window. Rather than pin it to the nearest edge and have it point
       confidently at the wrong thing, drop it: the highlighted row is still
       there to say what is being explained. */
    const reachable = y >= 8 && y <= h - 8;
    arrow.style.display = reachable ? '' : 'none';
    if (reachable) arrow.style.top = `${y - half}px`;
  }

  if (host_ !== host) {
    if (host_) host_.classList.remove('tip-on');
    host.classList.add('tip-on');
    host_ = host;
  }
}

function hide() {
  if (node) node.hidden = true;
  if (host_) { host_.classList.remove('tip-on'); host_ = null; }
}

/** Delegated once, at startup. Rows created by a later render are covered. */
export function installTips() {
  ensure();
  document.addEventListener('pointerover', (e) => {
    const host = e.target.closest && e.target.closest('[data-tip-title]');
    if (host) show(host); else hide();
  });
  document.addEventListener('pointerdown', hide);
  // A panel can re-render under a parked cursor, leaving the tip explaining a
  // row that no longer exists.
  window.addEventListener('scroll', hide, true);
}

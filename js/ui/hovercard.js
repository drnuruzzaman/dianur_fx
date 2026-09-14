/* hovercard.js — a hover tooltip that can hold a table.
 *
 * WHY NOT `title`. Every other hint on this page is a native `title`, and that
 * is the right default: free, accessible, and consistent. It has two limits
 * that bite exactly where the `Live` column lives. It renders PLAIN TEXT in a
 * proportional font, so a column of counts cannot be aligned into anything a
 * reader can scan; and it is drawn by the OS, outside the page, so it cannot be
 * screenshotted -- which means a native tooltip is a piece of UI that ships
 * without anyone having looked at it.
 *
 * So: tabular hints get this, prose hints keep `title`. The rule is the shape
 * of the content, not the importance of it.
 *
 * ONE ELEMENT FOR THE PAGE, created on first use. A card per cell would be
 * twenty hidden nodes in a twenty-row table, all of them re-rendered on every
 * poll.
 *
 * IT CLAMPS TO THE VIEWPORT rather than being positioned cleverly. The column
 * sits on the right of a wide table, so a card anchored left-aligned under its
 * cell falls off the screen on the last few columns -- which is how a tooltip
 * ends up technically working and practically unreadable.
 */

let card = null;
let owner = null;

function ensure() {
  if (card) return card;
  card = document.createElement('div');
  card.className = 'hovercard';
  card.hidden = true;
  /* NOT CLICKABLE. The card can overlap the row it describes, and a tooltip
     that swallows the click meant for the thing underneath it is worse than no
     tooltip. `pointer-events: none` is in the CSS; this is the note saying it
     is load-bearing rather than decorative. */
  document.body.appendChild(card);
  addEventListener('scroll', hide, true);
  addEventListener('resize', hide);
  return card;
}

function place(target) {
  const r = target.getBoundingClientRect();
  const c = card.getBoundingClientRect();
  const pad = 8;
  let left = r.left;
  let top = r.bottom + 6;
  if (left + c.width > innerWidth - pad) left = innerWidth - c.width - pad;
  if (left < pad) left = pad;
  // Flip above when there is no room below, rather than hanging off the fold.
  if (top + c.height > innerHeight - pad) top = Math.max(pad, r.top - c.height - 6);
  card.style.left = `${Math.round(left)}px`;
  card.style.top = `${Math.round(top)}px`;
}

export function hide() {
  if (card && !card.hidden) { card.hidden = true; owner = null; }
}

/**
 * Show `build()`'s DOM when the pointer is over `target`.
 *
 * `build` is called on EVERY hover, not once at attach time, so a card cannot
 * show numbers from the poll before last.
 */
export function attach(target, build) {
  target.addEventListener('mouseenter', () => {
    ensure();
    card.innerHTML = '';
    const node = build();
    if (!node) return;
    card.appendChild(node);
    card.hidden = false;
    owner = target;
    place(target);
  });
  target.addEventListener('mouseleave', () => { if (owner === target) hide(); });
  return target;
}

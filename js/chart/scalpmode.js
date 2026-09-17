/* scalpmode.js — which entry mode the Rayo Scalper is showing, for the page.
 *
 * WHY THIS EXISTS. The rail panel has a BREAK/FADE toggle and the Rayo TP bands
 * study had its own `fade` input, and the two never spoke. Left at its default
 * the study drew BREAK trades while the panel beside it showed a FADE ticket --
 * a stop order at 4252 on the chart against a limit order at 4297 in the panel,
 * with different stops and a different ladder, and nothing on screen saying they
 * were answering different questions. The study's own comment already said a
 * band disagreeing with the panel beside it is worse than no band; this is the
 * one piece that makes that true.
 *
 * ONE MODE FOR THE PAGE, not one per chart. The mode is a way of LOOKING at the
 * rule rather than a property of an instrument -- "show me where the pullback
 * entries would have been" is a question about the rule, and answering it on
 * one chart and not the split beside it would be the same disagreement one
 * level up.
 *
 * A MODULE-LEVEL VARIABLE AND A LISTENER LIST, rather than threading the mode
 * through main.js into every Chart and down into `runStudy`'s ctx. `ctx.plan`
 * is threaded that way because the engine already owns the zones; nothing owns
 * the mode, so passing it would have meant a parameter on four functions that
 * do not otherwise care.
 *
 * NOT PERSISTED, on purpose. BREAK is the registered rule and the only one any
 * cell is graded on; FADE is a research view. A research view that silently
 * survived a reload would eventually be read as the rule.
 */

let mode = 'break';
const listeners = new Set();

/** The mode every Rayo surface should be drawing right now. */
export const scalpMode = () => mode;

/** Set it, and tell everyone who is drawing. A no-op change notifies nobody. */
export function setScalpMode(m) {
  const next = m === 'fade' ? 'fade' : 'break';
  if (next === mode) return;
  mode = next;
  for (const fn of listeners) {
    /* ONE BROKEN LISTENER MUST NOT STOP THE REST. A chart that has been
       destroyed between the toggle and the notify is the expected case, not a
       reason for the panel to fail to repaint. */
    try { fn(mode); } catch (e) { /* ignore */ }
  }
}

/** Call `fn` whenever the mode changes. Returns an unsubscribe. */
export function onScalpMode(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

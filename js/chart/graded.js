/* graded.js — the Rayo Scalper's registered cells, read from alerts.json.
 *
 * NOTHING IS COMPUTED HERE. Every figure a cell carries -- expected net R,
 * total R per year, fills, win rate, drawdown, whether it is tradeable -- was
 * written by the script that measured it. A grade invented in the browser
 * would be a verdict nobody ran.
 *
 * ONE FETCH FOR THE PAGE, shared by the Signal Board and the settings modal. A
 * second reader of the same file is a second chance for two surfaces to
 * disagree about what is being tested, which is the bug this module exists to
 * close.
 *
 * IT USED TO SERVE TWO RULES. `loadGrades` and `gradeFor` read `signals.watch`
 * for the horizon-matched Donchian, which was retired on 2026-09-10 and is now
 * in configs/retired/donchian_forward_test.json. They went with it.
 */

const ALERTS = 'configs/alerts.json';


/**
 * The cells the Rayo Scalper is registered on, enabled ones only.
 *
 * Not a sim.Strategy and never polled through the bridge's /signal: this rule
 * rests pending orders that fill intrabar and ladders three targets, and that
 * engine models neither. The board computes its tickets in the browser.
 */
let _raw = null;
let _scalper = null;

/* ONE FETCH OF alerts.json FOR THE WHOLE PAGE. The cells, the gate and the
   measurement block are three views of one file, and fetching it per view is
   the second-reader problem this module was written to avoid -- stated at the
   top and then almost reintroduced when the forward scores needed to know which
   stop the registry was measured at. */
function raw(url) {
  if (!_raw) {
    _raw = fetch(url, { cache: 'no-store' })
      .then((r) => (r.ok ? r.json() : null))
      .catch(() => null);
  }
  return _raw;
}

/**
 * The settings the registry was measured under -- `{stopAtr, measuredOn}`.
 *
 * THE FORWARD LEDGER NEEDS THIS TO AVOID POOLING TWO RULES. Tickets journalled
 * before 2026-09-14 were posted at a 4.0 ATR stop; showing their outcomes
 * beside a cell's registered figure would compare the new rule's expectation
 * against the old rule's results.
 */
export function loadMeasurement(url = ALERTS) {
  return raw(url).then((d) => {
    const m = (d && d.scalper && d.scalper.measurement) || {};
    return { stopAtr: m.stop_atr, measuredOn: m.measured_on };
  });
}

export function loadScalperCells(url = ALERTS) {
  if (!_scalper) {
    _scalper = raw(url)
      .then((d) => {
        const sc = (d && d.scalper) || {};
        return ((sc.watch) || []).filter((c) => c.enabled).map((c) => ({
          symbol: c.symbol, tf: c.tf,
          expected: c.expected_net_r,
          perYear: c.total_r_per_year,
          worst: c.worst_era_net_r,
          control: !!c.control,
          /* TRADEABLE IS NOT `expected > 0`, and conflating them was a real
             hazard once every instrument went on the board. XAU 15m carries
             expected +0.0075 and is NOT tradeable: it is positive only at the
             19-point spread captured today, zero at 24, and its drawdown is of
             the order of its whole lifetime profit. A cell that renders green
             because its expectation rounds above zero invites exactly the trade
             the measurement says not to take. */
          tradeable: !!c.tradeable,
          note: c.note || '',
          rule: sc.rule || '',
          quality: (d && d.scalper && d.scalper.quality) || null,
        }));
      })
      .catch(() => []);
  }
  return _scalper;
}

/**
 * The trend-age gate, as the live tool applies it.
 *
 * ONE DEFINITION FOR BOTH SURFACES. `tools/scalper.py` withholds the Telegram
 * message when a tradeable cell's ticket carries age > max_age; a board that
 * did not know that would announce ten resting orders while five were being
 * sent, which is the surface disagreement this module exists to prevent.
 *
 * Returns `{enforce, maxAge}` -- `enforce: false` until the fetch lands, so a
 * board painted before the config arrives shows everything rather than hiding
 * signals on the strength of a value it has not read yet.
 */
export function loadGate(url = ALERTS) {
  return loadScalperCells(url).then((cs) => {
    const q = (cs[0] && cs[0].quality) || null;
    return { enforce: !!(q && q.enforce), maxAge: (q && q.max_age) };
  });
}

/** Is this ticket silenced for being a stale trend? */
export function gated(gate, t) {
  return !!(gate && gate.enforce && gate.maxAge != null
            && t && t.age != null && t.age > gate.maxAge);
}

/** Bullish/neutral/bearish colouring, matching the Signal Board's columns. */
export const GRADE_CLASS = {
  validated: 'rp-g-ok',
  marginal: 'rp-g-mid',
  below: 'rp-g-low',
};


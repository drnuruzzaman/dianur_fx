/* rayoledger.js — the live Rayo record per cell, for the fixed-12 walker.
 *
 * `data/scalper_journal.jsonl` holds every ticket the scheduled run posted;
 * `data/scalper_scored.jsonl` says which of those tools/score_scalper.py took
 * and how each ended. Joined per (symbol, timeframe), they let
 * js/chart/rayorule.js replay the trades the live record actually holds
 * instead of a simulation that began in a different phase.
 *
 * Only BREAK mode at the current stop: the ledger holds nothing else.
 *
 * REFRESHED EVERY MINUTE. The journal is appended by the scheduled run and the
 * scorer writes hourly, so a copy loaded once at page open goes stale; the
 * page-wide caches in journal.js and scored.js are load-once and are not used
 * for that reason. Listeners hear about each reload so charts can repaint.
 */

import { STOP_ATR } from './scalper.js';

const REFRESH_MS = 60e3;
let _rows = null;          // { journal: [], scored: [] }
let _at = 0;
let _pending = null;
const _built = new Map();  // 'symbol|tf' -> ledger
const listeners = new Set();

async function text(url) {
  try {
    const r = await fetch(url, { cache: 'no-store' });
    return r.ok ? r.text() : '';
  } catch { return ''; }
}

function parse(t) {
  const out = [];
  for (const line of t.split(/\r?\n/)) {
    const s = line.trim();
    if (!s) continue;
    try { out.push(JSON.parse(s)); } catch { /* one bad line loses nothing else */ }
  }
  return out;
}

/** Fetch both files if the copy in hand is older than a minute. */
export function refreshRayoLedger(force = false) {
  if (_pending) return _pending;
  if (!force && _rows && Date.now() - _at < REFRESH_MS) return Promise.resolve();
  _pending = Promise.all([text('data/scalper_journal.jsonl'),
                          text('data/scalper_scored.jsonl')])
    .then(([j, s]) => {
      _rows = { journal: parse(j), scored: parse(s) };
      _at = Date.now();
      _built.clear();
      for (const fn of listeners) { try { fn(); } catch { /* keep going */ } }
    })
    .finally(() => { _pending = null; });
  return _pending;
}

/**
 * The ledger for one cell, or null (nothing loaded yet, or no record).
 * Synchronous, for panels and studies; kicks a refresh when stale.
 */
export function rayoLedgerFor(symbol, tf) {
  if (!_rows || Date.now() - _at >= REFRESH_MS) refreshRayoLedger();
  if (!_rows || !symbol || !tf) return null;
  const key = `${symbol}|${tf}`;
  if (_built.has(key)) return _built.get(key);
  const mine = (r) => r && r.symbol === symbol && r.tf === tf
    && (r.mode || 'break') === 'break' && r.stop_atr === STOP_ATR;
  const scored = new Map(_rows.scored.filter(mine).map((r) => [r.id, r]));
  const tickets = new Map();
  let start = Infinity, lastMs = -Infinity, scoredUntil = -Infinity;
  for (const j of _rows.journal) {
    if (!mine(j) || !Number.isFinite(j.ms) || !Number.isFinite(j.entry)) continue;
    const s = scored.get(j.id);
    start = Math.min(start, j.ms);
    lastMs = Math.max(lastMs, j.ms);
    tickets.set(j.ms, {
      id: j.id, mode: j.mode || 'break', side: j.side, order: j.order,
      entry: j.entry, stop: j.sl, tp: j.tp, risk: j.risk, atr: j.atr,
      /* Some rows journalled on 2026-09-17 carry `trend` as the higher-frame
         refs ({'1h': -1, '4h': -1}) instead of 'up'/'down'. A break ticket's
         side IS its trend, so read it from there when the field is not text. */
      level: j.level,
      trend: typeof j.trend === 'string' ? j.trend : (j.side === 'buy' ? 'up' : 'down'),
      age: j.age, expire: j.expire_bars || 12,
      barTime: j.ms,
      scored: s ? { outcome: s.outcome, fillMs: s.fill_ms, endMs: s.end_ms } : null,
    });
  }
  for (const s of scored.values()) scoredUntil = Math.max(scoredUntil, s.ms);
  const out = tickets.size ? { tickets, start, lastMs, scoredUntil } : null;
  _built.set(key, out);
  return out;
}

/**
 * Every fill the live scorer has recorded for a symbol, at any stop: what a
 * broker position is matched against to say whether it is the rule's trade.
 * Synchronous; empty until the first load lands (kicks one when stale).
 */
export function rayoFills(symbol) {
  if (!_rows || Date.now() - _at >= REFRESH_MS) refreshRayoLedger();
  if (!_rows || !symbol) return [];
  const sym = String(symbol).toUpperCase();
  return _rows.scored
    .filter((r) => r && String(r.symbol).toUpperCase() === sym
      && Number.isFinite(r.fill_ms))
    .map((r) => ({ tf: r.tf, side: r.side, fillMs: r.fill_ms, endMs: r.end_ms,
                   outcome: r.outcome, stopAtr: r.stop_atr, id: r.id }));
}

/** When the scored ledger begins (earliest ticket, any cell), or null. */
export function rayoRecordStart() {
  if (!_rows || !_rows.scored.length) return null;
  let m = Infinity;
  for (const r of _rows.scored) if (Number.isFinite(r.ms) && r.ms < m) m = r.ms;
  return Number.isFinite(m) ? m : null;
}

/** Called after every reload. Returns an unsubscribe. */
export function onRayoLedger(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/* newspanel.js — the news rail on the live chart.
 *
 * WHAT IT SHOWS, AND WHY IT DOES NOT SHOW SENTIMENT.
 *
 * The feed is QuantGist (tools/fetch_quantgist_news.py). On the plan this key
 * carries, `/v1/sentiment/*` and `/v1/intelligence/*` answer 402, and the rows
 * that DO come back carry `impact_score` -- a magnitude, how much a story is
 * expected to move something -- with no direction anywhere in the payload.
 *
 * So this panel reports impact and lets the reader judge direction. A green or
 * red pill here would be invented, and an invented direction on a trading
 * screen is worse than none: it reads as a measurement.
 *
 * RELEVANCE IS BY ASSET, NOT BY KEYWORD. Each radar cluster names the assets it
 * touches, and the fetcher carries a small alias table -- gold matches GLD and
 * XAUUSD alike, because the feed tags stories the way a US desk would. Matching
 * on the headline text instead would put every story mentioning "dollar" on the
 * EURUSD chart, which is most of them.
 *
 * The clusters relevant to the chart in front of you come first; everything
 * else follows under a divider, because a quiet instrument should still show
 * what is moving elsewhere rather than an empty rail.
 */

import { el } from '../util.js';

const SRC = 'data/news/quantgist.json';

let _doc = null;

/** One fetch per page load, shared. A missing file costs the rail, not the app. */
export function loadNewsDoc(url = SRC) {
  if (!_doc) {
    /* `no-store` for the same reason the calendar uses it: a tool rewrites
       this file and a cached copy would show yesterday's headlines with
       nothing on screen saying so. */
    _doc = fetch(url, { cache: 'no-store' })
      .then((r) => (r.ok ? r.json() : null))
      .catch(() => null);
  }
  return _doc;
}

/** The feed's tickers for a chart symbol: 'XAUUSD.a' -> XAUUSD, GLD, GC, IAU. */
export function aliasesFor(symbol, doc) {
  const base = String(symbol || '').replace(/\..*$/, '').toUpperCase();
  const table = (doc && doc.aliases) || {};
  return new Set(table[base] || [base]);
}

function relevance(cluster, alias) {
  let hits = 0;
  for (const a of cluster.assets || []) if (alias.has(String(a).toUpperCase())) hits++;
  return hits;
}

const pct = (v) => (Number.isFinite(v) ? Math.round(v * 100) + '%' : '—');

/** Newest first, most relevant first, at most `max`. */
export function rankClusters(doc, symbol, max = 12) {
  const alias = aliasesFor(symbol, doc);
  const rows = (doc && doc.clusters ? doc.clusters : []).map((c) => ({
    ...c, hits: relevance(c, alias),
  }));
  rows.sort((a, b) => (b.hits > 0) - (a.hits > 0)
    || (b.impact || 0) - (a.impact || 0)
    || (b.latestSeen || 0) - (a.latestSeen || 0));
  return rows.slice(0, max);
}

function ago(ms) {
  if (!Number.isFinite(ms)) return '';
  const m = Math.round((Date.now() - ms) / 60000);
  if (m < 1) return 'now';
  if (m < 60) return m + 'm';
  const h = Math.round(m / 60);
  return h < 48 ? h + 'h' : Math.round(h / 24) + 'd';
}

/**
 * Render into `host` for `symbol`.
 *
 * Re-rendering wholesale rather than diffing: this is at most a dozen rows and
 * it repaints only when the symbol changes or the file is refetched.
 */
export function renderNews(host, symbol, doc) {
  host.innerHTML = '';
  if (!doc) {
    host.append(el('div', { class: 'nw-empty' },
      'no news file — run tools/fetch_quantgist_news.py'));
    return;
  }
  const rows = rankClusters(doc, symbol);
  if (!rows.length) {
    host.append(el('div', { class: 'nw-empty' }, 'no clusters in the feed'));
    return;
  }

  /* THE HONEST HEADER. It says what the number is, because "impact" on a
     trading screen invites being read as "buy". */
  host.append(el('div', { class: 'nw-note' },
    doc.hasSentiment ? 'Impact and direction from the feed.'
      : 'Impact = expected size of the move. The feed carries no direction.'));

  let dividerDone = false;
  for (const c of rows) {
    if (!c.hits && !dividerDone && rows.some((r) => r.hits)) {
      host.append(el('div', { class: 'nw-div' }, 'elsewhere'));
      dividerDone = true;
    }
    const item = el('div', { class: 'nw-item' + (c.hits ? ' nw-on' : '') });
    item.append(
      el('div', { class: 'nw-top' },
        el('span', { class: 'nw-topic', text: c.topic || 'news' }),
        el('span', { class: 'nw-imp', text: pct(c.impact) }),
        el('span', { class: 'nw-age', text: ago(c.latestSeen) })),
      el('div', { class: 'nw-head', text: c.headline || '' }));
    if (c.why) {
      /* `why_it_matters` is the feed's own words, and it is the most useful
         thing in the payload -- a mechanism, not a score. Clamped by CSS so a
         long one does not push the rest of the rail off screen. */
      item.append(el('div', { class: 'nw-why', text: c.why }));
    }
    item.title = `confidence ${pct(c.confidence)}`
      + (c.sources ? ` · ${c.sources} sources` : '')
      + (c.assets && c.assets.length ? `\n${c.assets.slice(0, 12).join(' ')}` : '');
    host.append(item);
  }
}

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

/**
 * How old a cluster may be and still appear on the rail.
 *
 * TWO DAYS, chosen because that is where the feed's own behaviour changes: the
 * radar re-associates a live topic within hours, so a cluster whose newest
 * member story is older than this is one the vendor has stopped updating, not
 * a story that is still developing. Measured on the file that prompted the
 * change: eleven clusters sat at 2.8 days with headlines that no longer even
 * matched their topic, while `sanctions` and `trump-posts` were at 0.1 and 0.0.
 *
 * A CLUSTER WITH NO `latestSeen` IS KEPT. Absent is not old -- it means the
 * vendor did not date it, and dropping a story because its timestamp is
 * missing would silently hide news on the strength of a formatting detail.
 */
export const MAX_CLUSTER_AGE_MS = 2 * 24 * 60 * 60 * 1000;

/**
 * Fresh clusters only, relevant first, then NEWEST FIRST.
 *
 * THE ORDER CHANGED ON 2026-09-10, and the old one was not a bug. It ranked by
 * IMPACT and left recency as the last tiebreak, so a three-day-old cluster at
 * 84% sat above a fresh one at 60%. That answers "what is moving this
 * instrument", which is a fair question -- but it made a rail that had just
 * been refetched look like it had not refreshed at all, and a reader who
 * cannot trust the refresh cannot trust the rail. Impact is now the tiebreak
 * and time decides.
 *
 * RELEVANCE STILL SORTS FIRST, because it is what the `elsewhere` divider is
 * made of: the clusters touching the chart in front of you, then everything
 * else. With the age cut in place both sides are current anyway, so this costs
 * nothing in freshness. (For strict time order regardless of instrument, drop
 * the `hits` comparison from the sort -- one line.)
 *
 * THE AGE CUT IS THE OTHER HALF. Sorting alone would leave the stale clusters
 * on screen, just lower down, and a rail that is 60% three-day-old topics
 * still reads as stale however it is ordered.
 */
export function rankClusters(doc, symbol, max = 12) {
  const alias = aliasesFor(symbol, doc);
  const now = Date.now();
  const rows = (doc && doc.clusters ? doc.clusters : [])
    .filter((c) => !Number.isFinite(c.latestSeen)
                   || now - c.latestSeen <= MAX_CLUSTER_AGE_MS)
    .map((c) => ({ ...c, hits: relevance(c, alias) }));
  rows.sort((a, b) => (b.hits > 0) - (a.hits > 0)
    || (b.latestSeen || 0) - (a.latestSeen || 0)
    || (b.impact || 0) - (a.impact || 0));
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
  const total = (doc.clusters || []).length;
  if (!rows.length && total) {
    /* EVERY CLUSTER WAS DROPPED FOR AGE, which is a THIRD fact and not either
       of the two below: the feed answered, it carried news, and none of it is
       recent. Saying "no clusters in the feed" here would be the same lie in
       reverse -- a working fetch reported as an empty one -- which is the
       confusion the age cut was added to end, not to relocate. */
    const newest = Math.max(...(doc.clusters || [])
      .map((c) => (Number.isFinite(c.latestSeen) ? c.latestSeen : 0)));
    host.append(el('div', { class: 'nw-empty' },
      `${total} clusters, all older than 2 days — the feed is working, the `
      + `radar has not updated. Newest: ${newest ? ago(newest) : 'undated'}`));
    return;
  }
  if (!rows.length) {
    /* AN EMPTY FEED AND A BROKEN FEED LOOK IDENTICAL, and they are opposite
       facts: one says the world is quiet, the other says nobody is listening.
       The fetcher records why in `notes` -- a revoked API key, a 401, a
       timeout -- and reading it here is the difference between a rail that
       explains itself and a rail that quietly lies about the market. */
    const why = (doc.notes || []).filter(Boolean);
    host.append(el('div', { class: 'nw-empty' },
      why.length ? 'the news fetch failed — the rail is empty because nothing '
                   + 'came back, not because nothing happened'
                 : 'no clusters in the feed'));
    for (const n of why.slice(0, 3)) {
      host.append(el('div', { class: 'nw-note down', text: String(n).slice(0, 160) }));
    }
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

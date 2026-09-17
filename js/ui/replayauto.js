/**
 * replayauto.js — the Auto TL menu the two replays share.
 *
 * WHY THE REPLAYS DO NOT REUSE THE LIVE CHART'S SETTINGS. They used to: both
 * called `resolveAuto(symbol, tf, AUTO_DEFAULTS)`, which resolves the LIVE
 * chart's per-instrument overrides. That coupling reads as a feature and is a
 * trap. A replay is a proving surface -- you open it to judge what a detector
 * did on bars that already happened -- and having its overlays silently follow
 * whatever the live chart was last set to means two sessions a week apart are
 * not comparable, and worse, that turning something off to read the live chart
 * quietly changes what the replay showed you.
 *
 * So these settings are their own, stored under one key for BOTH replays. Not
 * per instrument and not per timeframe: the live chart's settings are
 * per-instrument because it is a working surface where gold and cable want
 * different sensitivities, but a replay is opened to answer one question and
 * the overlay set is part of how the reader reads it, not part of the market.
 *
 * DEFAULT IS EVERYTHING ON, three lines a side. The live chart defaults to two
 * because it is dense with live state -- positions, orders, the rule's plan --
 * and every extra line competes with that. A replay has none of it, so the
 * useful default is to show the reader what the detectors saw and let them
 * switch off what they do not want.
 */

import { load, save } from '../util.js';
import { openMenu } from './menu.js';

const KEY = 'replay.auto';

/** Everything on. See the note above for why this differs from AUTO_DEFAULTS. */
export const REPLAY_AUTO_DEFAULTS = {
  on: true,
  zones: true,        // support / resistance bands
  channels: true,     // parallel corridors
  swings: true,       // HH / HL / LH / LL marks
  ms: true,           // BOS / CHoCH
  zigzag: true,       // the ranked ZigZag polyline
  news: true,         // macro release marks
  maxLines: 2,        // trendlines per side
};

export function replayAuto() {
  const saved = load(KEY, {}) || {};
  return { ...REPLAY_AUTO_DEFAULTS, ...saved };
}

export function setReplayAuto(patch) {
  const next = { ...replayAuto(), ...patch };
  save(KEY, next);
  return next;
}

/**
 * Open the menu on `anchor`. `onChange` runs after every toggle.
 *
 * `keepOpen` ON EVERY ROW, and a re-open at the end: a reader switching
 * overlays on and off is comparing them, and a menu that closed on each click
 * would make that four gestures instead of two. The live chart's Auto TL menu
 * does the same for the same reason.
 *
 * `anchor` MAY BE A FUNCTION, and for these callers it must be. `onChange`
 * rebuilds the replay's toolbar, which REPLACES the button this menu was
 * opened on -- and a detached element has no bounding rect, so re-opening
 * against the original reference positioned the menu at the top-left corner of
 * the window. The live chart never hit this because it re-opens by id
 * (`$('#autoBtn').click()`), resolving a fresh element every time. A getter is
 * the same trick without needing an id on a button the replay builds.
 */
export function openReplayAutoMenu(anchor, onChange) {
  const at = () => {
    const node = typeof anchor === 'function' ? anchor() : anchor;
    /* A caller that hands back something detached anyway gets no menu rather
       than one in the corner: silently misplacing it is the worse failure. */
    return node && node.isConnected ? node : null;
  };
  const host = at();
  if (!host) return;
  const a = replayAuto();
  const items = [
    { kind: 'cap', label: 'Auto TL — this replay only' },
    { label: a.on ? 'On' : 'Off', value: 'on', checked: a.on, keepOpen: true },
    { kind: 'sep' },
    { label: 'Support / resistance', value: 'zones', checked: a.zones,
      keepOpen: true, hint: 'price turned repeatedly' },
    { label: 'Channels', value: 'channels', checked: a.channels,
      keepOpen: true, hint: 'parallel corridor' },
    { label: 'Swing points (HH/HL/LH/LL)', value: 'swings', checked: a.swings,
      keepOpen: true },
    { label: 'BOS / CHoCH', value: 'ms', checked: a.ms,
      keepOpen: true, hint: 'structure breaks' },
    { label: 'ZigZag line', value: 'zigzag', checked: a.zigzag,
      keepOpen: true, hint: 'ranked turns, joined' },
    { label: 'News marks', value: 'news', checked: a.news,
      keepOpen: true, hint: 'macro releases' },
    { kind: 'seg', label: 'Lines per side', keepOpen: true,
      options: [2, 3, 4, 6].map((n) => ({ label: String(n), value: 'max:' + n,
                                          checked: a.maxLines === n })) },
  ];
  openMenu(host, items, (v, item) => {
    const cur = replayAuto();
    if (v.startsWith('max:')) setReplayAuto({ maxLines: Number(v.slice(4)) });
    else if (v in cur) setReplayAuto({ [v]: !cur[v] });
    onChange();
    /* Re-render the ticks in place. `anchor` is resolved AGAIN inside the
       recursive call, which is the whole point of it being a getter: by now
       `onChange` may have rebuilt the toolbar and the button above is gone. */
    if (item && item.keepOpen) openReplayAutoMenu(anchor, onChange);
  });
}

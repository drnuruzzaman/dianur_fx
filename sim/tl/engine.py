"""
engine.py — the per-timeframe trendline engine.

It walks a series forward one bar at a time and maintains a live population of
Trendline objects: forming new candidates from confirmed pivots, registering
touches and violations, promoting and breaking and archiving. The state it holds
at bar i is, by construction, a state that only ever saw bars <= i — there is no
"compute over everything then slice", which is the usual way trendline
backtests leak the future.

    eng = TrendlineEngine('4h', tf_ms=14_400_000)
    snapshots = eng.walk(bars)        # one snapshot per bar
    snapshots[i].support  -> best support line as known at bar i

The engine knows nothing about entries, exits, risk or money. It describes
structure. Strategies consume snapshots.
"""

from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np

from ..indicators import atr as atr_series
from .clockguard import require_naive
from .lines import Direction, Role, Status, Trendline, classify_direction
from .pivots import find_pivots


@dataclass
class Snapshot:
    """
    What was knowable about structure at one bar.

    The line objects are kept for inspection, but every NUMBER a consumer reads
    is frozen here at snapshot time. Reading `support.quality_score` after the
    walk would return the score the line eventually reached — the line keeps
    being updated on later bars — which is a genuine look-ahead leak dressed up
    as a convenient attribute access.
    """
    i: int
    t: int
    support: Optional[Trendline] = None
    resistance: Optional[Trendline] = None
    live: List[Trendline] = field(default_factory=list)
    broken_now: List[Trendline] = field(default_factory=list)
    # frozen scalars — the only values features may use
    support_id: Optional[str] = None
    support_px: float = float('nan')
    support_q: float = float('nan')
    support_touches: int = 0
    resistance_id: Optional[str] = None
    resistance_px: float = float('nan')
    resistance_q: float = float('nan')
    resistance_touches: int = 0
    breaks: List[tuple] = field(default_factory=list)   # (role, price, quality)
    # One dict per entry in `breaks`, same order: how energetic the breaking
    # candle was. See _break_strength().
    break_strength: List[dict] = field(default_factory=list)
    live_count: int = 0
    # every TRADEABLE line as it stood at this bar: (id, role, value, quality).
    # Populated only when the engine is built with record_tradeable=True, because
    # diagnostics need it and the feature pipeline does not. Frozen here for the
    # usual reason: reading line.status after the walk reports the FINAL status,
    # not the status at this bar.
    tradeable: List[tuple] = field(default_factory=list)
    # Status per entry in `tradeable`, same order. Parallel rather than folded
    # into the tuple: that tuple is unpacked by arity in diagnostics.py, and
    # widening it broke three consumers at once the last time.
    tradeable_status: List[str] = field(default_factory=list)


@dataclass
class Params:
    strength: int = 3            # pivot fractal size
    window: int = 400            # lookback for pairing pivots
    max_pivots: int = 26         # most recent N per side (pairs grow as N^2)
    min_span: int = 6            # anchors at least this far apart
    tol_atr: float = 0.32        # touch/break tolerance, in ATR
    min_swing_atr: float = 0.0   # a pivot must stand out this far to count
    max_violations: int = 0      # closes beyond tolerance before BROKEN
    max_live: int = 20           # per role: a holding pool, not the offer list
    max_offered: int = 4         # per role, what strategies actually see
    archive_after: int = 40      # bars after breaking before archiving
    max_distance_atr: float = 10.0   # archive a line this far from price
    # Raised from 25 after measuring it on two disjoint out-of-sample eras.
    # Paired line-vs-placebo hold rates by quality bucket, 1999-2010 / 2011-2020:
    #     <=65    -7.8pp / -12.1pp
    #     65-80   -5.1pp /  -5.1pp   (z -4.3 / -4.8)
    #     80-90   -1.2pp /  -2.7pp
    #     >90     +2.5pp /  +0.6pp
    # The NEGATIVE band replicates hard: a sub-80 line holds ~5 points LESS
    # often than a random parallel line, in both eras, at z beyond 4. Offering
    # those lines was worse than offering nothing. The positive tail did NOT
    # replicate (+2.5 then +0.6), so 90 is not a threshold that buys an edge --
    # it is one that stops paying for a measured, repeatable harm.
    # Distinct touches that promote a candidate to CONFIRMED, anchors included.
    # 3 = one confirmation beyond the two anchors. 2 = the anchors alone, which
    # is the hand-drawing convention and confirms every line at creation.
    # Consecutive closes beyond tolerance required before a break counts.
    # 1 = the original rule (any single close kills the line). 2 is the common
    # hand-drawing convention and is what distinguishes "price poked through"
    # from "the line failed".
    break_confirm_bars: int = 1
    # Require the `strength` bars right of a pivot to CLOSE past it, not merely
    # to leave its wick unexceeded. Measured below before being switched on.
    close_confirm: bool = False
    # Layer D: pair anchors only when BOTH carry the same structural label --
    # HL to HL for support, LH to LH for resistance. Off reproduces the original
    # behaviour, which pairs any two confirmed lows regardless of what
    # structure.py called them, and so can join an HL to an LL: two points that
    # structure itself says belong to different regimes.
    structural_anchors: bool = False
    # Consecutive closes back on the working side that revive a BROKEN line.
    # 0 disables reclaim entirely (the original one-way lifecycle). Reclaim is
    # deliberately harder than breaking: a line should not resurrect because
    # price brushed past it. Only possible within `archive_after` bars of the
    # break, since the line is archived after that.
    # ON by default. Measured pooled effect on everything the engine offers:
    #   1999-2010  +0.21 -> +1.02 pp     2011-2020  -1.43 -> -0.40 pp
    #   2021-2026  +1.19 -> +0.90 pp
    # Better in two eras of three, and the gain lands where it was most needed:
    # 2011-2020 was significantly negative (z -3.50) and this pulls it to zero.
    # The reclaimed slice itself is +4.76 / +3.10 / +4.75 pp, all significant.
    reclaim_confirm_bars: int = 3
    # Quality floor a RECLAIMED line must clear to be offered or recorded as
    # tradeable. Measured, not chosen: paired line-vs-placebo on 42,273
    # reclaimed approaches across three eras, bucketed by the reclaimed line's
    # own LIVE quality --
    #     >= 0    +1.12 pp (z 3.51)     >= 65   +2.87 pp (z 7.43)
    #     >= 50   +1.37 pp (z 4.20)     >= 70   +3.41 pp (z 7.59)
    #     >= 60   +1.95 pp (z 5.52)     >= 75   +3.97 pp (z 6.23)
    # 70 is where it becomes both large and STABLE: +3.02 / +3.61 / +3.85 pp per
    # era, all three positive, while the half below is negative in two.
    #
    # APPLIED AFTER RESCORING, not at the transition. The first version gated
    # register_reclaim, which reads the score FROZEN at the break -- a line good
    # enough to have been tradeable before it broke, so almost always above 70.
    # That filtered 9% of reclaims and left the edge unchanged. The measurement
    # bucketed on the LIVE score, which carries the -12 violation penalty and
    # recency decay and has a median of 70. Same number, different quantity.
    reclaim_min_quality: float = 70.0
    min_touches: int = 3
    min_quality: float = 90.0    # below this a line is not offered to strategies


class TrendlineEngine:
    """One instrument, one timeframe. Incremental, causal, strategy-agnostic."""

    def __init__(self, timeframe: str, tf_ms: int, params: Params = None,
                 record_tradeable: bool = False, sensitivity=None):
        """
        `sensitivity` is an optional sim.tl.sensitivity.Sensitivity. When given,
        the per-SIDE thresholds on it override the flat ones in `params`:
        prominence, touch/break tolerance and the offer bar are all read per
        role. Detection WINDOW is shared, because the two sides' prominence
        distributions were measured to be the same to within 2% and splitting
        them would be an assumption rather than a calibration.

        Left None, every value comes from `params` exactly as before, so nothing
        that does not opt in changes behaviour.
        """
        self.timeframe = timeframe
        self.tf_ms = tf_ms
        self.p = params or Params()
        self.sens = sensitivity
        self.record_tradeable = record_tradeable
        self._seq = 0

    # ---- per-side thresholds -------------------------------------------- #
    # `sensitivity` may be a Sensitivity, or a CALLABLE i -> Sensitivity for the
    # rolling case. The callable form is what makes volatility_regime usable:
    # a regime is a statement about now, and freezing one for a whole backtest
    # applies a momentary reading to a decade -- which measurably damaged at
    # least one cell when it was tested that way.
    def _sens_at(self, i):
        if self.sens is None:
            return None
        return self.sens(i) if callable(self.sens) else self.sens

    def _tol_atr(self, role, i=None):
        sv = self._sens_at(i)
        return self.p.tol_atr if sv is None else sv.for_role(role).tol_atr

    def _offerable(self, line, role, i=None):
        """
        Tradeable AND, if it is a reclaimed line, above the measured floor.

        The floor is checked here rather than at the reclaim transition because
        this is where the line has been RESCORED -- which is the quantity the
        measurement bucketed on.
        """
        if not line.is_tradeable:
            return False
        if (line.status is Status.RECLAIMED
                and line.quality_score < self.p.reclaim_min_quality):
            return False
        return True

    def _min_quality(self, role, i=None):
        sv = self._sens_at(i)
        return self.p.min_quality if sv is None else sv.for_role(role).min_quality

    def _strength_at(self, i):
        sv = self._sens_at(i)
        return self.p.strength if sv is None else sv.support.strength

    def _prom_at(self, i):
        """(high_bar, low_bar) prominence thresholds at bar i."""
        sv = self._sens_at(i)
        if sv is None:
            return self.p.min_swing_atr, self.p.min_swing_atr
        return sv.resistance.min_prominence_atr, sv.support.min_prominence_atr

    def _new_id(self, role):
        self._seq += 1
        return '%s-%s-%d' % (self.timeframe, 'S' if role is Role.SUPPORT else 'R', self._seq)

    # ------------------------------------------------------------------ #
    def walk(self, bars, on_bar=None) -> List[Snapshot]:
        """
        `on_bar(i, snapshot, live_lines)` is called once per bar, DURING the
        walk, with the live population as it stands at that bar.

        This exists because `Snapshot.live` holds references to Trendline
        objects that keep mutating on later bars: reading `line.status` after
        the walk reports the status the line EVENTUALLY reached, not the status
        it had at that bar. Almost every line ends BROKEN, so a consumer that
        filters `snap.live` on `is_tradeable` after the fact sees an almost
        empty population -- which is how a channel diagnostic came to find 11
        channels in eleven years. The frozen scalars on Snapshot solve this for
        numbers; a callback solves it for anything that needs the objects.
        """
        # One clock, enforced here rather than trusted: a tz-aware frame would
        # misalign MTF context silently. See sim/tl/clockguard.py.
        require_naive(bars, 'engine.walk bars')
        high = np.asarray(bars['high'], dtype=float)
        low = np.asarray(bars['low'], dtype=float)
        close = np.asarray(bars['close'], dtype=float)
        times = np.asarray(bars.index.astype('int64') // 1_000_000)   # ms
        atr = atr_series(bars, 14)
        n = len(close)

        # Pivots are found over the whole array for speed, but each carries the
        # bar at which it became visible and the loop below only ever consults
        # pivots already confirmed. Fast and still honest.
        # Strength can CHANGE during a rolling calibration, and pivots depend
        # on it, so one pivot set is not enough. Each distinct strength gets its
        # own set, cached: `find_pivots` is the expensive part and a rolling
        # walk only ever visits a handful of strengths.
        #
        # This stays causal for the same reason the single-strength version did:
        # every pivot carries its own `confirmed_i` (i + strength for the wick
        # shape; a variable, later bar when close_confirm walks forward to
        # verify the turn), and the loop below only consults pivots already
        # confirmed at the bar it is on.
        pivot_cache = {}

        def _sets_for(st):
            if st in pivot_cache:
                return pivot_cache[st]
            ph, pl = find_pivots(high, low, st, close=close,
                                 close_confirm=self.p.close_confirm)
            if self.p.structural_anchors:
                # Label against the PREVIOUS pivot of the same kind, which is
                # exactly structure.label_swings. Done here so the label travels
                # with the pivot into the pool and pairing can read it without
                # re-deriving the sequence per candidate.
                from .structure import label_swings
                for src, labelled in ((ph, label_swings(ph, True, atr)),
                                      (pl, label_swings(pl, False, atr))):
                    for p_, lab in zip(src, labelled):
                        p_['label'] = lab['label']
            hi_bar, lo_bar = self._prom_at(None) if not callable(self.sens)                 else (0.0, 0.0)
            pivot_cache[st] = (ph, pl)
            return pivot_cache[st]

        strengths = sorted({self._strength_at(i) for i in range(n)}) if n else []
        buckets = {}
        for st in strengths:
            ph, pl = _sets_for(st)
            # Prominence is applied per side, and under a rolling calibration the
            # bar itself moves, so it is filtered at the pivot's OWN bar rather
            # than once globally.
            hb_lb = [self._prom_at(p['i']) for p in ph]
            ph = [p for p, (hb, _) in zip(ph, hb_lb)
                  if hb <= 0 or _prominence(p, high, low, atr, st, True) >= hb]
            lb_lb = [self._prom_at(p['i']) for p in pl]
            pl = [p for p, (_, lb) in zip(pl, lb_lb)
                  if lb <= 0 or _prominence(p, high, low, atr, st, False) >= lb]
            buckets[st] = (_bucket(ph, n), _bucket(pl, n))



        live = {Role.SUPPORT: [], Role.RESISTANCE: []}
        pool = {Role.SUPPORT: [], Role.RESISTANCE: []}
        snapshots: List[Snapshot] = []

        for i in range(n):
            t = int(times[i])
            a = float(atr[i]) if not np.isnan(atr[i]) else 0.0
            tol = a * self.p.tol_atr        # default; per-role below

            # 1. newly visible pivots enter the pool
            st_i = self._strength_at(i)
            highs_by_conf, lows_by_conf = buckets.get(
                st_i, buckets.get(strengths[0]) if strengths else ([[]] * (n + 1),
                                                                   [[]] * (n + 1)))
            for p in highs_by_conf[i]:
                pool[Role.RESISTANCE].append(p)
            for p in lows_by_conf[i]:
                pool[Role.SUPPORT].append(p)

            broken_now = []
            reclaimed_now = []
            for role in (Role.SUPPORT, Role.RESISTANCE):
                # 2. keep the pool bounded and inside the lookback window
                pool[role] = [p for p in pool[role] if p['i'] >= i - self.p.window][-self.p.max_pivots:]

                # 3. form candidates from the newest pivot against older ones
                fresh = [p for p in (highs_by_conf[i] if role is Role.RESISTANCE
                                     else lows_by_conf[i])]
                for newp in fresh:
                    for oldp in pool[role]:
                        if newp['i'] - oldp['i'] < self.p.min_span:
                            continue
                        if self.p.structural_anchors:
                            want = 'HL' if role is Role.SUPPORT else 'LH'
                            if newp.get('label') != want or oldp.get('label') != want:
                                continue
                        line = self._form(role, oldp, newp, times, a, i)
                        if line is None:
                            continue
                        if self._duplicate(line, live[role], t, tol):
                            continue
                        live[role].append(line)

                # 4. update every live line against this bar
                for line in list(live[role]):
                    line.age_bars = i - _index_of(line.pivot_1, times, i)
                    value = line.value_at(t)
                    if not np.isfinite(value) or value <= 0:
                        line.archive(t, 'degenerate')
                        live[role].remove(line)
                        continue

                    rtol = a * self._tol_atr(role, i)
                    if role is Role.RESISTANCE:
                        breached = close[i] > value + rtol
                        poked = high[i] > value + rtol      # WICK through
                        grazed = abs(high[i] - value) <= rtol
                    else:
                        breached = close[i] < value - rtol
                        poked = low[i] < value - rtol
                        grazed = abs(low[i] - value) <= rtol

                    # Wick opens a CANDIDATE break; close decides it. A wick that
                    # goes through and closes back is a FALSE break -- the single
                    # most informative thing a level does, and until now it left
                    # no record at all.
                    if poked and not line.break_candidate_open:
                        line.break_candidate_open = True
                        line.break_candidates += 1
                    elif line.break_candidate_open and not poked:
                        line.break_candidate_open = False
                        if not breached:
                            line.false_breaks += 1

                    if breached:
                        # A break only counts if the market had ACKNOWLEDGED the
                        # line first. A candidate breaking is just a bad guess
                        # expiring — reporting it as a break event floods a
                        # breakout strategy with noise (measured: ~600 events per
                        # quarter on 15m gold, of which the vast majority were
                        # lines that never earned a third touch).
                        was_tradeable = line.is_tradeable
                        if line.register_violation(t, self.p.max_violations,
                                               self.p.break_confirm_bars):
                            if was_tradeable:
                                broken_now.append(line)
                            else:
                                line.archive(t, 'candidate_failed')
                                live[role].remove(line)
                                continue
                    else:
                        # close back on the correct side: the run resets, which
                        # is what makes the rule CONSECUTIVE rather than
                        # cumulative. `elif grazed` semantics are preserved --
                        # a bar that breached does not also count as a touch.
                        line.register_inside()
                        if grazed:
                            line.register_touch(t, i, st_i + 1,
                                                self.p.min_touches)

                    # 4b. a broken line that price has returned above/below
                    if (self.p.reclaim_confirm_bars > 0
                            and line.status is Status.BROKEN):
                        inside = (close[i] < value - rtol) if role is Role.RESISTANCE                             else (close[i] > value + rtol)
                        if inside:
                            if line.register_reclaim(t, self.p.reclaim_confirm_bars):
                                reclaimed_now.append(line)
                        else:
                            line._back = 0

                    # 5. archive what no longer matters
                    if line.status is Status.BROKEN and line.broken_at is not None:
                        if (t - line.broken_at) / self.tf_ms >= self.p.archive_after:
                            line.archive(t, 'stale_break')
                            live[role].remove(line)
                            continue
                    if line.status is not Status.BROKEN:
                        line.score(i, self.p.window, float(close[i]), a)
                    if line.last_price_distance_atr > self.p.max_distance_atr:
                        line.archive(t, 'too_far')
                        live[role].remove(line)
                        continue

                # 6. cap the population, best scores first
                live[role].sort(key=lambda x: (-x.quality_score, x.created_at))
                if len(live[role]) > self.p.max_live:
                    for extra in live[role][self.p.max_live:]:
                        extra.archive(t, 'outranked')
                    live[role] = live[role][:self.p.max_live]

            offered = {r: live[r][:self.p.max_offered] for r in live}
            sup = self._best(offered[Role.SUPPORT], close[i], Role.SUPPORT, t, i)
            res = self._best(offered[Role.RESISTANCE], close[i], Role.RESISTANCE, t, i)
            snap = Snapshot(
                i=i, t=t, support=sup, resistance=res,
                live=[x for role in live for x in live[role]],
                broken_now=broken_now,
                live_count=sum(len(live[r]) for r in live),
                breaks=[(b.role.value, b.value_at(t),
                         b.quality_at_break if b.quality_at_break is not None
                         else b.quality_score) for b in broken_now],
                # Parallel to `breaks`, not folded into it: the tuple is
                # unpacked by name in features.py, in the JS port and in the
                # parity fixtures, so widening it broke three consumers at once.
                # Extra per-break data goes alongside instead.
                break_strength=[_break_strength(high, low, close, i, a, b.role)
                                for b in broken_now],
            )
            if on_bar is not None:
                on_bar(i, snap, [x for role in live for x in live[role]
                                 if x.is_tradeable])
            if sup is not None:
                snap.support_id = sup.id
                snap.support_px = sup.value_at(t)
                snap.support_q = sup.quality_score
                snap.support_touches = sup.touches
            if self.record_tradeable:
                snap.tradeable = [
                    (l.id, l.role.value, l.value_at(t), l.quality_score, l.touches)
                    for role in live for l in live[role]
                    if self._offerable(l, role, i)]
                snap.tradeable_status = [
                    l.status.value for role in live for l in live[role]
                    if self._offerable(l, role, i)]
            if res is not None:
                snap.resistance_id = res.id
                snap.resistance_px = res.value_at(t)
                snap.resistance_q = res.quality_score
                snap.resistance_touches = res.touches
            snapshots.append(snap)
        self.last_live = live
        return snapshots

    # ------------------------------------------------------------------ #
    def _form(self, role, p1, p2, times, atr, i) -> Optional[Trendline]:
        dt = int(times[p2['i']]) - int(times[p1['i']])
        if dt <= 0:
            return None
        slope = (p2['price'] - p1['price']) / dt
        line = Trendline(
            id=self._new_id(role), timeframe=self.timeframe, role=role,
            direction=classify_direction(slope, self.tf_ms, atr),
            pivot_1={'t': int(times[p1['i']]), 'price': p1['price'], 'i': p1['i']},
            pivot_2={'t': int(times[p2['i']]), 'price': p2['price'], 'i': p2['i']},
            slope=slope, intercept=p1['price'],
            created_at=int(times[i]), span_bars=p2['i'] - p1['i'],
            atr_at_creation=atr,
        )
        # A line is born with touches=2 (its anchors). At min_touches<=2 those
        # anchors ARE the confirmation, so it has to be promoted here rather
        # than waiting for a register_touch that would take it to 3.
        if self.p.min_touches <= 2:
            line.status = Status.CONFIRMED
            line.confirmed_at = int(times[i])
        return line

    def _duplicate(self, cand, existing, t, tol) -> bool:
        """Same line twice is churn: it costs updates and evicts real ones."""
        if tol <= 0:
            return False
        now = cand.value_at(t)
        then = cand.value_at(cand.pivot_1['t'])
        for l in existing:
            if abs(l.value_at(t) - now) < tol and abs(l.value_at(cand.pivot_1['t']) - then) < tol:
                return True
        return False

    def _best(self, lines, last_close, role, t, i_now=None) -> Optional[Trendline]:
        """
        The line a strategy should care about: tradeable, decent quality, and on
        the correct side of price — a 'support' above spot is not support.
        """
        best, best_key = None, None
        for l in lines:
            if not self._offerable(l, role, i_now)                     or l.quality_score < self._min_quality(role, i_now):
                continue
            v = l.value_at(t)
            if role is Role.SUPPORT and v > last_close:
                continue
            if role is Role.RESISTANCE and v < last_close:
                continue
            key = (-l.quality_score, abs(v - last_close))
            if best_key is None or key < best_key:
                best, best_key = l, key
        return best


def _break_strength(high, low, close, i, atr, role):
    """
    How energetic was the candle that broke the line?

    The engine has always treated any close beyond tolerance as a break, so a
    0.2 ATR drift and a 3 ATR impulse were recorded identically. Traders
    distinguish them constantly -- a big committed candle through a line versus
    a small hesitant one -- and it is a falsifiable claim, so it is measured
    rather than assumed.

    Returns a dict:
        body_atr   |close - open| of the breaking bar, in ATR
        range_atr  high - low, in ATR
        close_pos  where the close sits in the bar's range, 0 at the low and 1
                   at the high. A break that closes on its extreme in the break
                   DIRECTION is committed; one that closes back near the middle
                   is a wick through the line with second thoughts.
        conviction body_atr * directional close_pos -- one number combining
                   "big candle" with "closed in the right part of it"

    All ATR-normalised, so it means the same thing on gold and on yen.
    """
    if not np.isfinite(atr) or atr <= 0 or i <= 0:
        return {'body_atr': 0.0, 'range_atr': 0.0, 'close_pos': 0.5,
                'conviction': 0.0}
    o = close[i - 1]                     # previous close stands in for the open
    hi, lo, c = high[i], low[i], close[i]
    rng = hi - lo
    body = abs(c - o)
    pos = 0.5 if rng <= 0 else (c - lo) / rng
    # a support breaking is a DOWN break, so commitment means closing near the
    # low; a resistance breaking is an UP break and means closing near the high
    directional = (1.0 - pos) if role is Role.SUPPORT else pos
    return {'body_atr': body / atr, 'range_atr': rng / atr,
            'close_pos': pos, 'conviction': (body / atr) * directional}


def _prominence(p, high, low, atr, strength, is_high):
    """Depth of one pivot's turn, in ATR units. Shared by the filters below."""
    i = p['i']
    a = atr[i]
    if not np.isfinite(a) or a <= 0:
        return -1.0
    lo = max(0, i - strength)
    hi = min(len(high), i + strength + 1)
    d = (high[i] - np.min(low[lo:hi])) if is_high else (np.max(high[lo:hi]) - low[i])
    return d / a


def _significant(pivots, high, low, atr, strength, min_swing_atr, is_high):
    """
    Keep pivots whose prominence over the surrounding fractal window is at least
    `min_swing_atr` ATR. Prominence is measured against the opposite extreme in
    the window, which is the depth of the turn the pivot represents.
    """
    out = []
    n = len(high)
    for p in pivots:
        i = p['i']
        lo = max(0, i - strength)
        hi = min(n, i + strength + 1)
        a = atr[i]
        if not np.isfinite(a) or a <= 0:
            continue
        depth = (high[i] - np.min(low[lo:hi])) if is_high else (np.max(high[lo:hi]) - low[i])
        if depth >= min_swing_atr * a:
            out.append(p)
    return out


def _bucket(pivots, n):
    """Index pivots by the bar at which they become visible."""
    out = [[] for _ in range(n + 1)]
    for p in pivots:
        c = p['confirmed_i']
        if 0 <= c < n:
            out[c].append(p)
    return out


def _index_of(pivot, times, fallback):
    return pivot.get('i', fallback)

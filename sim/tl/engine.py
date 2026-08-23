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
    live_count: int = 0
    # every TRADEABLE line as it stood at this bar: (id, role, value, quality).
    # Populated only when the engine is built with record_tradeable=True, because
    # diagnostics need it and the feature pipeline does not. Frozen here for the
    # usual reason: reading line.status after the walk reports the FINAL status,
    # not the status at this bar.
    tradeable: List[tuple] = field(default_factory=list)


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
    min_quality: float = 25.0    # below this a line is not offered to strategies


class TrendlineEngine:
    """One instrument, one timeframe. Incremental, causal, strategy-agnostic."""

    def __init__(self, timeframe: str, tf_ms: int, params: Params = None,
                 record_tradeable: bool = False):
        self.timeframe = timeframe
        self.tf_ms = tf_ms
        self.p = params or Params()
        self.record_tradeable = record_tradeable
        self._seq = 0

    def _new_id(self, role):
        self._seq += 1
        return '%s-%s-%d' % (self.timeframe, 'S' if role is Role.SUPPORT else 'R', self._seq)

    # ------------------------------------------------------------------ #
    def walk(self, bars) -> List[Snapshot]:
        high = np.asarray(bars['high'], dtype=float)
        low = np.asarray(bars['low'], dtype=float)
        close = np.asarray(bars['close'], dtype=float)
        times = np.asarray(bars.index.astype('int64') // 1_000_000)   # ms
        atr = atr_series(bars, 14)
        n = len(close)

        # Pivots are found over the whole array for speed, but each carries the
        # bar at which it became visible and the loop below only ever consults
        # pivots already confirmed. Fast and still honest.
        piv_highs, piv_lows = find_pivots(high, low, self.p.strength)
        # A fractal pivot can be a 0.1 ATR wiggle. Requiring a minimum swing
        # magnitude is what separates "a bar higher than its neighbours" from
        # "a swing the market actually turned at" — without it, 62-65% of
        # candidate lines reached CONFIRMED, which made the status meaningless.
        if self.p.min_swing_atr > 0:
            piv_highs = _significant(piv_highs, high, low, atr, self.p.strength,
                                     self.p.min_swing_atr, is_high=True)
            piv_lows = _significant(piv_lows, high, low, atr, self.p.strength,
                                    self.p.min_swing_atr, is_high=False)
        highs_by_conf = _bucket(piv_highs, n)
        lows_by_conf = _bucket(piv_lows, n)

        live = {Role.SUPPORT: [], Role.RESISTANCE: []}
        pool = {Role.SUPPORT: [], Role.RESISTANCE: []}
        snapshots: List[Snapshot] = []

        for i in range(n):
            t = int(times[i])
            a = float(atr[i]) if not np.isnan(atr[i]) else 0.0
            tol = a * self.p.tol_atr

            # 1. newly visible pivots enter the pool
            for p in highs_by_conf[i]:
                pool[Role.RESISTANCE].append(p)
            for p in lows_by_conf[i]:
                pool[Role.SUPPORT].append(p)

            broken_now = []
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

                    if role is Role.RESISTANCE:
                        breached = close[i] > value + tol
                        grazed = abs(high[i] - value) <= tol
                    else:
                        breached = close[i] < value - tol
                        grazed = abs(low[i] - value) <= tol

                    if breached:
                        # A break only counts if the market had ACKNOWLEDGED the
                        # line first. A candidate breaking is just a bad guess
                        # expiring — reporting it as a break event floods a
                        # breakout strategy with noise (measured: ~600 events per
                        # quarter on 15m gold, of which the vast majority were
                        # lines that never earned a third touch).
                        was_tradeable = line.is_tradeable
                        if line.register_violation(t, self.p.max_violations):
                            if was_tradeable:
                                broken_now.append(line)
                            else:
                                line.archive(t, 'candidate_failed')
                                live[role].remove(line)
                                continue
                    elif grazed:
                        line.register_touch(t, i, self.p.strength + 1)

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
            sup = self._best(offered[Role.SUPPORT], close[i], Role.SUPPORT, t)
            res = self._best(offered[Role.RESISTANCE], close[i], Role.RESISTANCE, t)
            snap = Snapshot(
                i=i, t=t, support=sup, resistance=res,
                live=[x for role in live for x in live[role]],
                broken_now=broken_now,
                live_count=sum(len(live[r]) for r in live),
                breaks=[(b.role.value, b.value_at(t),
                         b.quality_at_break if b.quality_at_break is not None
                         else b.quality_score) for b in broken_now],
            )
            if sup is not None:
                snap.support_id = sup.id
                snap.support_px = sup.value_at(t)
                snap.support_q = sup.quality_score
                snap.support_touches = sup.touches
            if self.record_tradeable:
                snap.tradeable = [
                    (l.id, l.role.value, l.value_at(t), l.quality_score, l.touches)
                    for role in live for l in live[role] if l.is_tradeable]
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

    def _best(self, lines, last_close, role, t) -> Optional[Trendline]:
        """
        The line a strategy should care about: tradeable, decent quality, and on
        the correct side of price — a 'support' above spot is not support.
        """
        best, best_key = None, None
        for l in lines:
            if not l.is_tradeable or l.quality_score < self.p.min_quality:
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

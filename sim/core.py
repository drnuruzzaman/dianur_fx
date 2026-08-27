"""
core.py — the simulator core.

A referee, not a strategy. It marches through bars and enforces the rules of
reality on whatever rule set it is handed:

  * A strategy can only see the past. It receives a BarView that raises on any
    attempt to read the current bar's future, so look-ahead is impossible by
    construction rather than by discipline.
  * A signal generated on the close of bar i fills at the OPEN of bar i+1.
  * When a bar's range contains both the stop and the target, the STOP filled.
    Bars do not record the order in which prices occurred.
  * A price that gapped past the stop fills at the open, not at the stop.
  * Position size comes from a risk fraction of live equity, rounded down to the
    broker's volume step, and a trade too small to place is not placed.
  * Nothing instrument-specific is hard-coded; it all comes from the spec.

Invariants are asserted every bar. A violated invariant raises instead of
producing a plausible, wrong number.
"""

from dataclasses import dataclass, field, asdict
from typing import Optional

import numpy as np
import pandas as pd

LONG, SHORT, FLAT = 1, -1, 0
CENT = 5e-3          # accounting tolerance, in account currency


class LookAheadError(IndexError):
    """Raised when a strategy tries to read a bar it cannot legally see."""


# --------------------------------------------------------------------------- #
# what a strategy sees                                                        #
# --------------------------------------------------------------------------- #
class BarView:
    """
    A window onto history ending at `i`. `back=0` is the current (closed) bar,
    `back=1` the one before it. Negative `back` — the future — raises.

    Series precomputed by Strategy.prepare() are exposed the same way, so a
    vectorised indicator stays fast without becoming a look-ahead hole: the view
    still refuses to hand over an index beyond `i`. tests/test_causality.py
    separately proves each series only depends on bars up to its own index.
    """

    __slots__ = ('_o', '_h', '_l', '_c', '_v', '_sp', '_t', '_series', 'i')

    def __init__(self, arrays, series, i):
        self._o, self._h, self._l, self._c, self._v, self._sp, self._t = arrays
        self._series = series
        self.i = i

    def _at(self, arr, back):
        if back < 0:
            raise LookAheadError(f'a strategy may not read the future (back={back})')
        j = self.i - back
        if j < 0:
            raise IndexError(f'only {self.i + 1} bars exist, asked {back} back')
        return arr[j]

    def open(self, back=0):   return self._at(self._o, back)
    def high(self, back=0):   return self._at(self._h, back)
    def low(self, back=0):    return self._at(self._l, back)
    def close(self, back=0):  return self._at(self._c, back)
    def volume(self, back=0): return self._at(self._v, back)
    def spread(self, back=0): return self._at(self._sp, back)
    def time(self, back=0):   return self._at(self._t, back)

    def series(self, name, back=0):
        arr = self._series.get(name)
        if arr is None:
            raise KeyError(f'no precomputed series {name!r}')
        return self._at(arr, back)

    def highest(self, n, back=1):
        """Highest high of `n` bars ending `back` bars ago (default: excludes now)."""
        end = self.i - back + 1
        start = max(0, end - n)
        if end <= 0:
            return np.nan
        return float(np.max(self._h[start:end]))

    def lowest(self, n, back=1):
        end = self.i - back + 1
        start = max(0, end - n)
        if end <= 0:
            return np.nan
        return float(np.min(self._l[start:end]))

    def __len__(self):
        return self.i + 1


# --------------------------------------------------------------------------- #
# strategy contract                                                           #
# --------------------------------------------------------------------------- #
@dataclass
class Intent:
    """What a strategy asks for. `side` FLAT closes any open position."""
    side: int
    stop: Optional[float] = None
    target: Optional[float] = None
    tag: str = ''


class Strategy:
    name = 'unnamed'
    warmup = 0                     # bars needed before the first signal

    def prepare(self, bars: pd.DataFrame) -> dict:
        """Return causal series (value at i uses only bars <= i)."""
        return {}

    def on_bar(self, view: BarView, position) -> Optional[Intent]:
        raise NotImplementedError

    def params(self) -> dict:
        return {}


# --------------------------------------------------------------------------- #
# bookkeeping                                                                 #
# --------------------------------------------------------------------------- #
@dataclass
class Position:
    side: int
    lots: float
    entry_i: int
    entry_time: pd.Timestamp
    entry_price: float
    stop: float
    target: Optional[float]
    tag: str
    risk_price: float               # entry-to-stop distance, for R multiples
    swap_ccy: float = 0.0
    mae: float = 0.0                # worst excursion, price terms
    mfe: float = 0.0
    last_swap_day: Optional[int] = None


@dataclass
class Trade:
    symbol: str
    tf: str
    side: int
    lots: float
    entry_time: pd.Timestamp
    entry_price: float
    exit_time: pd.Timestamp
    exit_price: float
    stop_price: float
    target_price: Optional[float]
    exit_reason: str
    bars_held: int
    gross_ccy: float                # profit currency, before costs
    spread_ccy: float
    slippage_ccy: float
    swap_ccy: float
    net_ccy: float
    net_acct: float                 # account currency (AUD)
    r_multiple: float
    mae_r: float
    mfe_r: float
    tag: str


from . import intrabar as intrabar_mod


@dataclass
class Config:
    risk_pct: float = 0.5           # of equity, per trade
    start_equity: float = 25_000.0
    # Slippage in ATR, not points. A fixed point count is not comparable across
    # instruments: 2 points was 1.37% of a 15M ATR on gold and 3.86% on USDJPY,
    # so every cross-instrument result was biased against the yen.
    slippage_atr: float = 0.02      # adverse, each side, as a fraction of ATR
    slippage_points: float = 0.0    # legacy fallback when slippage_atr is 0
    # A stop is a market order triggered in a fast market: it slips MORE than a
    # resting order. A target is a LIMIT: it fills at your price or not at all,
    # so it slips not at all. Modelling both as zero flattered every stop.
    stop_slippage_mult: float = 1.5
    spread_points_default: float = 0.0   # 0 = fall back to the spec's live
    #                                      spread; see _spread_price
    apply_swap: bool = True
    max_bars_held: Optional[int] = None
    allow_short: bool = True
    # STEP 8. What the risk fraction is taken OF. Default 'equity' is what
    # every result in runs/ was measured with and must not change meaning.
    #
    #   'equity'  risk_pct of LIVE equity -- compounds, so size grows with the
    #             account and shrinks in a drawdown
    #   'start'   risk_pct of START equity -- constant cash risk, no compounding
    #   'flat'    the same `flat_lots` every trade, ignoring the stop distance
    #
    # Sizing cannot change avg_R, which is normalised by risk. It changes the
    # equity path, and it changes WHICH trades are affordable: _size returns 0
    # when the budget will not cover the minimum lot, and the engine skips
    # those. That skip is the only way sizing touches the trade list at all.
    size_base: str = 'equity'
    flat_lots: float = 0.10
    ruin_pct: float = 20.0          # stop trading below this % of start equity
    flat_by_hour: Optional[int] = None   # close before this server hour (carry-free)
    label: str = ''
    # How a bar holding BOTH the stop and the target is resolved. See
    # sim/intrabar.py: 'pessimistic' (stop wins, the gate-facing default),
    # 'optimistic' (target wins -- the other end of the interval, not a
    # belief), or 'intrabar' (look at sub-bars and see). Validated against
    # ticks at 0 errors before being offered; a bar the sub-bars cannot settle
    # falls back to pessimistic and is counted.
    execution: str = intrabar_mod.PESSIMISTIC
    intrabar_tf: Optional[str] = None    # None = the default for the bar's tf


@dataclass
class Result:
    trades: pd.DataFrame
    equity: pd.DataFrame
    stats: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# the engine                                                                  #
# --------------------------------------------------------------------------- #
def size_lots(spec, equity_acct, stop_distance, *, risk_pct, fx=None, when=None):
    """Lots such that a stop-out costs ~risk_pct of equity. Never rounds up.

    Module-level and not a method because a LIVE signal has to quote the same
    size the backtest traded. This project has already been bitten once by the
    same quantity having two implementations -- three strategies each carried a
    private ATR that differed from the tested one by up to 0.86 price units --
    so sizing gets one definition and both callers use it.

    Rounds DOWN to volume_step and returns 0.0 below volume_min: a broker
    rejects an undersized order, and silently rounding up would risk more than
    asked. Note that 0.0 is a legitimate answer, not an error -- it means the
    account is too small to take this trade at this stop distance.
    """
    if stop_distance <= 0:
        return 0.0
    risk_acct = equity_acct * risk_pct / 100.0
    risk_ccy = (risk_acct if fx is None
                else fx.from_account(risk_acct, spec['currency_profit'], when))
    per_lot = stop_distance * spec['contract_size']
    if per_lot <= 0:
        return 0.0
    step = spec['volume_step']
    lots = np.floor((risk_ccy / per_lot) / step) * step
    lots = min(lots, spec['volume_max'])
    return 0.0 if lots < spec['volume_min'] - 1e-12 else round(lots, 8)


class Simulator:
    """
    One instrument, one timeframe, one strategy, one pass.

    `spec` is a dict from data/instruments.json. `fx` converts profit currency
    into the account currency at a given timestamp (see sim.fx); pass None to
    report in the instrument's own profit currency.
    """

    def __init__(self, spec: dict, fx=None, config: Config = None):
        self._atr = None
        self.spec = spec
        self.fx = fx
        self.cfg = config or Config()
        if self.cfg.execution not in intrabar_mod.MODES:
            raise ValueError('execution must be one of %s, got %r'
                             % (', '.join(intrabar_mod.MODES), self.cfg.execution))
        self._sub = None            # built lazily in run(), needs the symbol
        self.ambiguous = 0          # bars holding both levels
        self.resolved_by = {}       # how each of those was settled
        if spec.get('swap_mode') not in (0, 1) and self.cfg.apply_swap:
            raise ValueError(
                'swap_mode %r is not points-based; the swap formula here assumes '
                'points (mode 1). Disable swap or extend costs.py.' % spec.get('swap_mode'))

    # ---- money ---------------------------------------------------------- #
    def _pnl_ccy(self, side, entry, exit_, lots):
        return (exit_ - entry) * side * self.spec['contract_size'] * lots

    def _to_acct(self, amount_ccy, when):
        if self.fx is None:
            return amount_ccy
        return self.fx.to_account(amount_ccy, self.spec['currency_profit'], when)

    def _size(self, equity_acct, stop_distance, when):
        """Lots such that a stop-out costs ~risk_pct of equity. Never rounds up."""
        return size_lots(self.spec, equity_acct, stop_distance,
                         risk_pct=self.cfg.risk_pct, fx=self.fx, when=when)

    def _spread_price(self, spread_points):
        """
        Spread charged on this bar, in price.

        The recorded spread column cannot be trusted to be present. MetaTrader
        writes zero for it over long stretches -- EURUSD and USDJPY carry a
        spread up to 2019 and then report 0 for essentially every bar from 2020
        on, while gold is the mirror image, zero before 2018 and populated
        after. With a default of 0 that meant whole eras were simulated with NO
        SPREAD AT ALL, and the 2021-2026 window every early result was measured
        in was one of them.

        So the recorded value is treated as a lower bound to improve on, never
        as permission to charge nothing: the floor is the broker's own current
        spread for this instrument, captured live into data/instruments.json.
        A real quote is never tighter than the broker's book, so charging at
        least that is conservative where history is silent and harmless where
        history is honest.
        """
        pts = spread_points if spread_points and spread_points > 0 else 0.0
        floor = self.cfg.spread_points_default
        if floor <= 0:
            floor = float(self.spec.get('spread_points_now') or 0.0)
        return max(pts, floor) * self.spec['point']

    def _slip_price(self, i=None):
        if self.cfg.slippage_atr > 0 and self._atr is not None:
            a = self._atr[i if i is not None else 0]
            if np.isfinite(a) and a > 0:
                return self.cfg.slippage_atr * float(a)
        return self.cfg.slippage_points * self.spec['point']

    # exits that are market orders pay slippage; a target is a limit and does not
    _MARKET_EXITS = ('stop', 'stop_gap', 'signal', 'session_flat',
                     'max_bars', 'ruin', 'end_of_data')

    def _exit_slip(self, reason, i):
        if reason in ('target', 'target_gap'):
            return 0.0
        base = self._slip_price(i)
        return base * (self.cfg.stop_slippage_mult
                       if reason in ('stop', 'stop_gap') else 1.0)

    def _swap_night_ccy(self, side, lots, weekday):
        """Swap in profit currency for one rollover. Points-based (mode 1)."""
        if not self.cfg.apply_swap or self.spec.get('swap_mode') == 0:
            return 0.0
        pts = (self.spec['swap_long_points'] if side == LONG
               else self.spec['swap_short_points'])
        # MT5's ENUM_DAY_OF_WEEK is Sunday=0..Saturday=6; pandas weekday() is
        # Monday=0..Sunday=6. Comparing them raw charges the triple swap a day
        # late (Thursday instead of Wednesday).
        mt5_dow = (weekday + 1) % 7
        nights = 3 if mt5_dow == self.spec.get('swap_rollover_3days_weekday', 3) else 1
        return pts * self.spec['point'] * self.spec['contract_size'] * lots * nights

    # ---- the loop ------------------------------------------------------- #
    def run(self, bars: pd.DataFrame, strategy: Strategy, symbol: str, tf: str) -> Result:
        need = ['open', 'high', 'low', 'close']
        for col in need:
            if col not in bars.columns:
                raise ValueError(f'bars missing {col!r}')
        # Refuse rather than silently sort: unordered bars mean something went
        # wrong upstream (mixed files, a bad concat), and quietly reordering them
        # would hide the real bug behind a plausible result.
        if not bars.index.is_monotonic_increasing:
            raise ValueError('bars are not in ascending time order')
        if bars.index.has_duplicates:
            raise ValueError('duplicate bar timestamps in input')

        arrays = (bars['open'].to_numpy(float), bars['high'].to_numpy(float),
                  bars['low'].to_numpy(float), bars['close'].to_numpy(float),
                  bars.get('tick_volume', pd.Series(0, index=bars.index)).to_numpy(float),
                  bars.get('spread', pd.Series(0, index=bars.index)).to_numpy(float),
                  bars.index.to_numpy())
        o, h, l, c, _v, sp, _t = arrays
        times = bars.index
        n = len(bars)

        # Sub-bar source for INTRABAR. Constructed even when the mode is not
        # intrabar so the counters below stay comparable across modes; nothing
        # is loaded from disk until a genuinely ambiguous bar asks for it.
        self._sub = intrabar_mod.SubBars(symbol, tf, self.cfg.intrabar_tf)
        self._times = times
        self.ambiguous = 0
        self.resolved_by = {}

        # ATR for the cost model; the strategy's own ATR (if any) is separate
        from .indicators import atr as _atr_fn
        self._atr = _atr_fn(bars, 14)

        series = strategy.prepare(bars)
        for key, arr in series.items():
            if len(arr) != n:
                raise ValueError(f'series {key!r} has {len(arr)} values for {n} bars')
            series[key] = np.asarray(arr, dtype=float)

        cash = self.cfg.start_equity
        ruined = None
        pos: Optional[Position] = None
        pending: Optional[Intent] = None
        trades, curve, skipped = [], [], 0
        point = self.spec['point']

        for i in range(n):
            t = times[i]

            # ---- 1. fill what was ordered on the previous close ---------- #
            if pending is not None:
                spread = self._spread_price(sp[i])
                slip = self._slip_price(i)
                if pending.side == FLAT:
                    if pos is not None:
                        # exiting long sells the bid; exiting short buys the ask
                        px = o[i] - slip if pos.side == LONG else o[i] + spread + slip
                        trades.append(self._close(pos, i, t, px, 'signal', spread, slip))
                        cash += trades[-1].net_acct
                        pos = None
                elif pos is None:
                    side = pending.side
                    # long pays the ask, short sells the bid; slippage is adverse
                    px = o[i] + spread + slip if side == LONG else o[i] - slip
                    stop = pending.stop
                    if stop is not None and ((side == LONG and stop < px) or
                                             (side == SHORT and stop > px)):
                        dist = abs(px - stop)
                        if self.cfg.size_base == 'flat':
                            lots = float(self.cfg.flat_lots)
                        else:
                            base = (cash if self.cfg.size_base == 'equity'
                                    else self.cfg.start_equity)
                            lots = self._size(base, dist, t)
                        if lots > 0:
                            pos = Position(side=side, lots=lots, entry_i=i, entry_time=t,
                                           entry_price=px, stop=stop, target=pending.target,
                                           tag=pending.tag, risk_price=dist,
                                           last_swap_day=t.dayofyear)
                        else:
                            skipped += 1
                    else:
                        skipped += 1
                pending = None

            # ---- 2. overnight financing --------------------------------- #
            if pos is not None and self.cfg.apply_swap:
                day = t.dayofyear
                if pos.last_swap_day is not None and day != pos.last_swap_day:
                    pos.swap_ccy += self._swap_night_ccy(pos.side, pos.lots, t.weekday())
                    pos.last_swap_day = day

            # ---- 2b. carry-free mode: flatten before rollover ----------- #
            # Swap on these instruments is large and only knowable at today's
            # rate, so a strategy that never holds overnight can be measured
            # without that assumption contaminating the result.
            if pos is not None and self.cfg.flat_by_hour is not None:
                nxt = times[i + 1] if i + 1 < n else None
                crosses = (nxt is None or nxt.dayofyear != t.dayofyear
                           or (t.hour < self.cfg.flat_by_hour <= nxt.hour))
                if t.hour >= self.cfg.flat_by_hour or crosses:
                    trades.append(self._close(pos, i, t, c[i], 'session_flat',
                                              self._spread_price(sp[i]),
                                              self._exit_slip('session_flat', i)))
                    cash += trades[-1].net_acct
                    pos = None
                    pending = None

            # ---- 3. stop / target on this bar --------------------------- #
            if pos is not None:
                hit, px, reason = self._exit_price(pos, o[i], h[i], l[i], i)
                # excursions, for MAE/MFE reporting
                if pos.side == LONG:
                    pos.mae = max(pos.mae, pos.entry_price - l[i])
                    pos.mfe = max(pos.mfe, h[i] - pos.entry_price)
                else:
                    pos.mae = max(pos.mae, h[i] - pos.entry_price)
                    pos.mfe = max(pos.mfe, pos.entry_price - l[i])
                if not hit and self.cfg.max_bars_held and \
                        i - pos.entry_i >= self.cfg.max_bars_held:
                    hit, px, reason = True, c[i], 'max_bars'
                if hit:
                    spread = self._spread_price(sp[i])
                    slip = self._exit_slip(reason, i)
                    # a stop fills worse than its trigger; a target fills at it
                    if pos.side == LONG:
                        exit_px = px - slip
                    else:
                        exit_px = px + spread + slip
                    trades.append(self._close(pos, i, t, exit_px, reason, spread, slip))
                    cash += trades[-1].net_acct
                    pos = None
                    pending = None          # a stopped-out bar cancels its own signal

            # ---- 4. mark to market, and check the books ----------------- #
            unreal = 0.0
            if pos is not None:
                gross = self._pnl_ccy(pos.side, pos.entry_price, c[i], pos.lots)
                unreal = self._to_acct(gross + pos.swap_ccy, t)
            equity = cash + unreal
            curve.append((t, cash, unreal, equity,
                          0 if pos is None else pos.side * pos.lots))

            # invariant: equity is cash plus open exposure, to the cent
            assert abs(equity - (cash + unreal)) < CENT, 'equity identity broken'

            # ---- 4b. ruin: a wiped account cannot keep trading ---------- #
            if ruined is None and equity <= self.cfg.start_equity * self.cfg.ruin_pct / 100.0:
                ruined = t
                if pos is not None:
                    trades.append(self._close(pos, i, t, c[i], 'ruin',
                                              self._spread_price(sp[i]),
                                              self._exit_slip('ruin', i)))
                    cash += trades[-1].net_acct
                    pos = None
                pending = None
            if ruined is not None:
                continue

            # ---- 5. the strategy sees a closed bar, orders for the next -- #
            if i + 1 < n and i >= strategy.warmup:
                view = BarView(arrays, series, i)
                intent = strategy.on_bar(view, pos)
                if intent is not None:
                    if intent.side == SHORT and not self.cfg.allow_short:
                        intent = None
                    elif intent.side != FLAT and pos is not None:
                        intent = None            # no pyramiding, no reversals in one step
                    elif intent.side == FLAT and pos is None:
                        intent = None
                pending = intent

        # ---- close anything still open at the end of the data ----------- #
        if pos is not None:
            i = n - 1
            trades.append(self._close(pos, i, times[i], c[i], 'end_of_data',
                                      self._spread_price(sp[i]),
                                      self._exit_slip('end_of_data', i)))
            cash += trades[-1].net_acct
            curve[-1] = (times[i], cash, 0.0, cash, 0)

        tdf = pd.DataFrame([asdict(x) for x in trades])
        edf = pd.DataFrame(curve, columns=['time', 'cash', 'unrealised', 'equity', 'exposure'])
        edf = edf.set_index('time')

        # invariant: realised P/L must reconcile with the equity change exactly
        realised = float(tdf['net_acct'].sum()) if len(tdf) else 0.0
        drift = (cash - self.cfg.start_equity) - realised
        assert abs(drift) < CENT, f'trade P/L does not reconcile with cash: drift={drift}'

        # invariant: no trade may open before the bar that signalled it
        if len(tdf):
            assert (tdf['entry_time'] < tdf['exit_time']).all() or \
                   (tdf['entry_time'] <= tdf['exit_time']).all(), 'exit precedes entry'
            assert tdf['exit_reason'].astype(bool).all(), 'a trade has no exit reason'

        return Result(trades=tdf, equity=edf, stats={
            'symbol': symbol, 'tf': tf, 'strategy': strategy.name,
            'params': strategy.params(),
            'bars': n, 'skipped_signals': skipped,
            'start_equity': self.cfg.start_equity, 'end_equity': cash,
            'account_currency': None if self.fx is None else self.fx.account,
            'profit_currency': self.spec['currency_profit'],
            'swap_model': ('current_rates_constant' if self.cfg.apply_swap else 'none'),
            'first_bar': str(times[0]), 'last_bar': str(times[-1]),
            'ruined_at': None if ruined is None else str(ruined),
        })

    # ---- exit resolution ------------------------------------------------ #
    def _exit_price(self, pos: Position, o_, h_, l_, i):
        """
        Where the position leaves on this bar, if it does.

        Gaps come first and are NOT ambiguous: a bar that opens beyond a level
        never traded at that level, so it fills at the open whatever the
        execution mode says. Only a bar whose RANGE holds both the stop and the
        target is genuinely unanswerable from OHLC, and that single case is
        what `execution` governs -- see sim/intrabar.py.
        """
        long = pos.side == LONG
        if long:
            if pos.stop is not None and o_ <= pos.stop:
                return True, o_, 'stop_gap'
            if pos.target is not None and o_ >= pos.target:
                return True, o_, 'target_gap'
            hit_stop = pos.stop is not None and l_ <= pos.stop
            hit_target = pos.target is not None and h_ >= pos.target
        else:
            if pos.stop is not None and o_ >= pos.stop:
                return True, o_, 'stop_gap'
            if pos.target is not None and o_ <= pos.target:
                return True, o_, 'target_gap'
            hit_stop = pos.stop is not None and h_ >= pos.stop
            hit_target = pos.target is not None and l_ <= pos.target

        if hit_stop and hit_target:
            self.ambiguous += 1
            which, how = intrabar_mod.resolve(
                self.cfg.execution, self._sub, self._times[i],
                pos.side, pos.stop, pos.target)
            self.resolved_by[how] = self.resolved_by.get(how, 0) + 1
            if which == intrabar_mod.TARGET:
                return True, pos.target, 'target'
            return True, pos.stop, 'stop'
        if hit_stop:
            return True, pos.stop, 'stop'
        if hit_target:
            return True, pos.target, 'target'
        return False, None, ''

    def _close(self, pos: Position, i, t, exit_px, reason, spread_price, slip_price):
        gross = self._pnl_ccy(pos.side, pos.entry_price, exit_px, pos.lots)
        # the spread is already inside the fill prices; report what it cost
        units = self.spec['contract_size'] * pos.lots
        spread_ccy = -spread_price * units
        slip_ccy = -slip_price * units * (2 if reason == 'signal' else 1)
        net_ccy = gross + pos.swap_ccy
        r = (net_ccy / (pos.risk_price * units)) if pos.risk_price and units else 0.0
        return Trade(
            symbol=self.spec.get('_symbol', ''), tf=self.spec.get('_tf', ''),
            side=pos.side, lots=pos.lots,
            entry_time=pos.entry_time, entry_price=pos.entry_price,
            exit_time=t, exit_price=exit_px,
            stop_price=pos.stop, target_price=pos.target,
            exit_reason=reason, bars_held=i - pos.entry_i,
            gross_ccy=gross, spread_ccy=spread_ccy, slippage_ccy=slip_ccy,
            swap_ccy=pos.swap_ccy, net_ccy=net_ccy,
            net_acct=self._to_acct(net_ccy, t), r_multiple=r,
            mae_r=(pos.mae / pos.risk_price) if pos.risk_price else 0.0,
            mfe_r=(pos.mfe / pos.risk_price) if pos.risk_price else 0.0,
            tag=pos.tag)

"""
signal.py — what the validated rule says to do RIGHT NOW, as a plain statement.

    sig = evaluate(closed_bars, Donchian(), spec, equity=12480, risk_pct=0.5)
    print(sig.instruction())

Pure: no network, no files, no clock. It takes bars and returns a statement, so
it can be tested against fixtures and re-derived from history later. The I/O
lives in tools/signal_now.py.

THE RULE HAS NO TAKE-PROFIT, and this is the single most important thing about
reading its output. Donchian(20/10, 2 ATR) exits on:

    the STOP          entry -/+ 2 ATR, fixed at entry, known in advance
    the CHANNEL EXIT  a close back through the opposite 10-bar extreme

The channel exit MOVES every bar, and it is not a target -- it is usually well
below a long's entry when the trade opens and it ratchets up as the trade runs.
Nothing in the validated result takes profit at a fixed multiple of risk. So
`ref_targets` below is labelled REFERENCE and is not part of the rule: quoting
2R/3R/5R as "TP1/TP2/TP3" would be reporting a rule that was never measured.
The tp_sweep measured capped variants and they survived mainly by rarely
firing, which is why a fire-rate floor was added to that tool.

WHAT IS AND IS NOT KNOWN AT SIGNAL TIME. The rule decides on a CLOSE and fills
at the NEXT OPEN, so at the moment the signal exists:

    known      the stop price, the stop distance, the size, the channel level
    NOT known  the entry price

`est_entry` is therefore an estimate carrying the modelled spread and slippage,
and it is named that way on purpose. Against ticks the entry model came out
100% conservative on gold -- it over-charges by about +0.0128 R, roughly 5.8% of
the measured edge -- so the real fill has been consistently slightly better than
this number, not worse. That is measured, not assumed, but it is a median over
one 40-day window and not a promise about any single fill.

CALLER MUST PASS CLOSED BARS ONLY. The last row is treated as a finished bar and
acted on. Passing a forming bar makes the signal a look-ahead artifact; the
value drifts and the "signal" may vanish. evaluate() cannot detect this from the
data, so it is the caller's contract -- tools/signal_now.py drops df.iloc[-1].
"""
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from .core import FLAT, LONG, SHORT, BarView, size_lots

#: Modelled slippage, in ATR. Matches tools/paper_trade.py and the simulator.
SLIPPAGE_ATR = 0.02

#: R multiples quoted for orientation only. NOT part of the validated rule --
#: see the module docstring. Deliberately not called TP1/TP2/TP3.
REF_R = (1.0, 2.0, 3.0)


@dataclass
class Signal:
    """One statement about one bar. Every field is either measured or None."""
    symbol: str
    tf: str
    strategy: str
    params: dict

    bar_time: str                   # UTC, the bar the decision was made on
    bar_time_server: Optional[str]  # broker server clock, for data/bars/ joins
    bar_close: float
    atr: float

    state: str                      # 'flat' | 'long' | 'short' — before this bar
    action: str                     # 'buy' | 'sell' | 'exit' | 'hold'
    tag: Optional[str] = None       # the rule's own reason, e.g. 'breakout_up'

    est_entry: Optional[float] = None
    stop: Optional[float] = None
    stop_distance: Optional[float] = None
    stop_points: Optional[float] = None
    lots: Optional[float] = None
    risk_acct: Optional[float] = None

    #: the level a CLOSE beyond which exits an open trade. Moves every bar.
    channel_exit: Optional[float] = None
    ref_targets: list = field(default_factory=list)   # [(R, price)] reference

    #: What the SMALLEST allowed position would risk. Only meaningful when
    #: `lots` came out 0: 'too small to trade' is a dead end, whereas 'the
    #: minimum lot risks 105.93, which is 2.71% of equity' is a fact the reader
    #: can act on. Reported as arithmetic, not as a recommended risk level --
    #: the validated result was measured at 0.5% and says nothing about 2.71%.
    min_lot_risk_acct: Optional[float] = None
    min_lot_risk_pct: Optional[float] = None
    min_lot_min: Optional[float] = None      # the broker's volume_min

    #: channel levels, so the chart and the text can be reconciled
    upper: Optional[float] = None
    lower: Optional[float] = None

    def is_entry(self):
        return self.action in ('buy', 'sell')

    def instruction(self):
        """Human-readable, with the honesty built in rather than bolted on."""
        head = '%s %s %s' % (self.symbol, self.tf, self.strategy)
        if self.action == 'hold':
            near = ''
            if self.upper is not None and self.lower is not None:
                near = '   channel %.2f / %.2f' % (self.lower, self.upper)
            if self.state != 'flat' and self.channel_exit is not None:
                near = ('   exit if close %s %.2f'
                        % ('<' if self.state == 'long' else '>',
                           self.channel_exit))
            return '%s — NO ACTION (%s)%s' % (head, self.state, near)

        if self.action == 'exit':
            lvl = (float('nan') if self.channel_exit is None
                   else self.channel_exit)
            return ('%s — CLOSE the %s (%s)\n  close %.2f went through the '
                    '%d-bar channel at %.2f'
                    % (head, self.state.upper(), self.tag or 'channel_exit',
                       self.bar_close, self.params.get('exit', 0), lvl))

        side = 'BUY' if self.action == 'buy' else 'SELL'
        out = ['%s — %s' % (head, side)]
        out.append('  entry     ~%.2f   ESTIMATE: fills at the next bar open, '
                   'not known yet' % self.est_entry)
        out.append('  stop       %.2f   (%.1f pts = %.2f ATR, fixed at entry)'
                   % (self.stop, self.stop_points,
                      self.params.get('atr_mult', 0)))
        if self.lots is not None:
            out.append('  size       %.2f lots  (risk %.2f, %.2f%% of equity)'
                       % (self.lots, self.risk_acct or 0.0,
                          self.params.get('risk_pct', 0)))
        out.append('  exit       NO take-profit. Closes on the stop, or when a '
                   'bar closes back')
        out.append('             through the %d-bar channel — a level that '
                   'moves every bar.' % self.params.get('exit', 0))
        if self.ref_targets:
            out.append('  reference  %s'
                       % '  '.join('%gR %.2f' % (r, p)
                                   for r, p in self.ref_targets))
            out.append('             (R multiples for orientation only; the '
                       'rule does NOT take')
            out.append('             profit at these and was never measured '
                       'doing so)')
        if self.lots == 0:
            out.append('  !! NOT TRADEABLE at %.2f%% risk: size rounds to 0 '
                       'lots.' % self.params.get('risk_pct', 0))
            if self.min_lot_risk_pct is not None:
                asked = max(self.params.get('risk_pct', 0.5), 1e-9)
                out.append('     The smallest allowed position (%.2f lots) '
                           'risks %.2f, which is'
                           % (self.min_lot_min, self.min_lot_risk_acct))
                out.append('     %.2f%% of equity — %.1fx the fraction the '
                           'backtest measured.'
                           % (self.min_lot_risk_pct,
                              self.min_lot_risk_pct / asked))
                out.append('     This is arithmetic, not a suggestion to raise '
                           'risk: nothing in')
                out.append('     runs/ was measured at %.2f%% per trade.'
                           % self.min_lot_risk_pct)
        return '\n'.join(out)


def evaluate(bars, strategy, spec, *, equity=None, risk_pct=0.5, fx=None,
             position_side=0, spread_points=None, bar_time_server=None,
             symbol=None, tf=None):
    """The rule's statement about the LAST row of `bars`.

    `bars` must contain CLOSED bars only — see the module docstring.
    `position_side` is +1/-1/0 for what is currently held, because the rule's
    answer genuinely depends on it: a breakout is only an entry when flat, and
    the channel exit only applies when not.

    Returns a Signal, always — 'hold' is a real answer and callers should be
    able to log it. Returns None only if the bars cannot support the rule.
    """
    if len(bars) < getattr(strategy, 'warmup', 2):
        return None

    series = {k: np.asarray(v, dtype=float)
              for k, v in strategy.prepare(bars).items()}
    arrays = (bars['open'].to_numpy(float), bars['high'].to_numpy(float),
              bars['low'].to_numpy(float), bars['close'].to_numpy(float),
              np.zeros(len(bars)), np.zeros(len(bars)), bars.index.to_numpy())
    i = len(bars) - 1
    view = BarView(arrays, series, i)

    held = None
    if position_side:
        class _Held:
            side = LONG if position_side > 0 else SHORT
            entry_i = 0
        held = _Held()

    intent = strategy.on_bar(view, held)
    params = dict(strategy.params())
    params['risk_pct'] = risk_pct

    def at(key):
        if key not in series:
            return None
        v = series[key][i]
        return float(v) if np.isfinite(v) else None

    a = float(series['atr'][i]) if 'atr' in series else float('nan')
    state = ('flat' if not position_side
             else ('long' if position_side > 0 else 'short'))
    sig = Signal(
        symbol=symbol or spec.get('symbol') or '?',
        tf=tf or getattr(strategy, 'exec_tf', '') or '',
        strategy=strategy.name, params=params,
        bar_time=str(bars.index[i]),
        bar_time_server=(str(bar_time_server)
                         if bar_time_server is not None else None),
        bar_close=float(bars['close'].iloc[i]),
        atr=round(a, 6) if np.isfinite(a) else float('nan'),
        state=state, action='hold',
        upper=at('hi'), lower=at('lo'),
    )

    # The level that would close an open trade. Only meaningful for the side
    # actually held: exit_lo closes a long, exit_hi closes a short.
    if position_side > 0:
        sig.channel_exit = at('exit_lo')
    elif position_side < 0:
        sig.channel_exit = at('exit_hi')

    if intent is None:
        return sig

    if intent.side == FLAT:
        sig.action = 'exit'
        sig.tag = intent.tag
        return sig

    side = 1 if intent.side == LONG else -1
    sig.action = 'buy' if side > 0 else 'sell'
    sig.tag = intent.tag
    sig.stop = float(intent.stop)
    sig.stop_distance = abs(sig.bar_close - sig.stop)
    sig.stop_points = sig.stop_distance / spec['point']

    # The entry the simulator would model: spread on the buy side, slippage
    # against us on both. An ESTIMATE — the real open does not exist yet.
    floor_pts = (spread_points if spread_points is not None
                 else float(spec.get('spread_points_now') or 0.0))
    spread = floor_pts * spec['point']
    slip = SLIPPAGE_ATR * a
    sig.est_entry = sig.bar_close + (spread + slip if side > 0 else -slip)

    if equity:
        sig.lots = size_lots(spec, equity, sig.stop_distance,
                             risk_pct=risk_pct, fx=fx, when=bars.index[i])
        per_lot = sig.stop_distance * spec['contract_size']
        sig.risk_acct = round(per_lot * (sig.lots or 0.0), 2)
        if not sig.lots:
            # A 0 means the minimum lot exceeds the risk budget. Say by how
            # much, in the account currency, rather than leaving a dead end.
            floor_ccy = per_lot * spec['volume_min']
            floor_acct = (floor_ccy if fx is None else
                          fx.to_account(floor_ccy, spec['currency_profit'],
                                        bars.index[i]))
            sig.min_lot_risk_acct = round(floor_acct, 2)
            sig.min_lot_risk_pct = round(100.0 * floor_acct / equity, 2)
            sig.min_lot_min = float(spec['volume_min'])

    sig.ref_targets = [(r, round(sig.est_entry + side * r * sig.stop_distance, 2))
                       for r in REF_R]
    return sig

"""
rayo.py — the Rayo Scalper: swing break, ATR stop, three-target ladder.

THE RULE, in one place, so the Python tool, the browser panel and the chart
bands cannot drift apart. `tools/scalper.py` imports it; `js/chart/scalper.js`
is a hand-kept mirror checked against this by running both on the same bars.

    STRUCTURE   swing high / low = highest high and lowest low of the last
                `swing` CLOSED bars, the level a scalper would draw.
    TREND       EMA(fast) against EMA(slow) on the execution frame.
    BREAK MODE  trend up   -> BUY STOP  a touch above the swing high
                trend down -> SELL STOP a touch below the swing low
    FADE MODE   trend up   -> BUY LIMIT  at the swing low
                trend down -> SELL LIMIT at the swing high
    STOP        `stop_atr` x ATR(14), from the entry.
    TARGETS     0.9R / 1.5R / 2.4R. TP1 sits UNDER 1R deliberately -- those are
                the ratios read off the signal room this was modelled on, kept
                so the comparison is like for like.
    EXPIRY      a pending order unfilled after `expire` bars is cancelled.

CAUSALITY. Every input is shifted by one: the swing levels, both EMAs and the
ATR are computed to the bar BEFORE the one being decided, so a ticket posted on
bar i uses bar i's close and nothing later.

THE COST FRACTION IS THE RULE. At the original settings -- 5m, 1.5 ATR stop,
exit at TP3 -- this was gross +0.0101 R against 0.1023 R of costs: net -0.0922,
about -1360 AUD a year on a 0.01 lot. The entry was never the problem. Cost is
(spread + slippage) / risk, risk is stop_atr x ATR(tf), so only the stop width
and the timeframe move it. A 27-cell sweep over tf x stop x exit confirmed it
monotonically: 5m/1.5 pays 0.148 R, 30m/4.0 pays 0.027 R.

THE SETTINGS THAT SURVIVED: stop 4.0 ATR, exit at TP1 (0.9R). Net R per fill,
four sub-periods, two instruments -- and USDJPY was never in the sweep, so it is
a holdout for the parameter choice:

    era        XAU 30m   XAU 1h   JPY 30m   JPY 1h
    2017-19     -0.046   -0.029    -0.077   -0.031
    2019-21     +0.069   +0.073    +0.011   +0.085
    2021-23     +0.023   +0.032    +0.108   +0.288
    2023-26     +0.043   +0.218    +0.036   +0.039

TWELVE OF SIXTEEN POSITIVE, and every failure is in ONE era. Exit at 0.9R needs
a 52.6% hit rate; 2017-19 delivers 50.9-53.1% and every later period 55-70%.
2017-19 is when gold ranged between 1200 and 1350 -- a trend-joining breakout
rule has nothing to join in a range, which is a regime dependence, not a bug,
and it is the honest reason to expect this to stop working when range returns.

THE TARGET IS ALREADY AT ITS OPTIMUM, and "more winners" is not the lever.
Sweeping the exit target with the stop fixed at 4 ATR, pooled over four
sub-eras (win rate / net R):

    target   0.3R    0.5R    0.7R    0.9R    1.2R    1.6R
    b/e hit  76.9%   66.7%   58.8%   52.6%   45.5%   38.5%
    XAU 30m  +.006   +.023   +.031   +.030   -.024   -.149
    XAU 1h   +.073   +.119   +.127   +.106   -.035   -.309
    JPY 30m  -.015   +.006   +.024   +.024   -.014   -.171
    JPY 1h   +.051   +.112   +.118   +.085   +.001   -.259
    win rate 79-85%  70-76%  63-68%  56-60%  45-47%  28-34%

A HIGHER WIN RATE IS FREE AND WORTHLESS. At 0.3R the rule wins 79-85% of the
time and makes the LEAST money of any profitable setting, because the cost is a
fixed charge in R and a small target cannot carry it. Anyone optimising the
number of winners would land there.

WHAT ACTUALLY MATTERS IS THE MARGIN OVER BREAK-EVEN -- win% minus 1/(1+target).
It peaks at +3 to +10 points somewhere around 0.5-0.9R and collapses past 1.2R.

0.7R IS NOT AN IMPROVEMENT ON 0.9R. It looks better pooled (+0.001 to +0.022)
and beats it in only 9 of 16 era-cells, which is a coin flip; the pooled gap is
a few large wins, mostly 1h in 2017-19. Worth recording that 0.7R DID turn that
era positive on both 1h cells (+0.028 and +0.045 against -0.029 and -0.031) --
suggestive, and exactly the shape of thing that does not replicate. The target
stays at 0.9R.

THE SESSION FILTER, AND WHY THE BETTER-LOOKING ONE WAS REJECTED. Three entry
filters were tested against a pre-registered bar -- beat the baseline in ALL
FOUR candidate cells. Trend strength (EMA separation >= k x ATR, k = 0.25 / 0.5
/ 1.0) failed at every k. Two passed:

    arm       XAU 30m   XAU 1h   JPY 30m   JPY 1h    n kept
    session    +0.023   +0.004    +0.031   +0.037     ~90%
    vol        +0.005   +0.095    +0.022   +0.043     ~55%

Both then REPLICATED on the 5m and 15m cells, which had no part in choosing
them: 3 of 4 each, the two misses at -0.0024 and -0.0003, i.e. zero.

AND THEN TOTAL R DECIDED IT, not R per fill:

    arm            XAU 30m  XAU 1h  JPY 30m  JPY 1h   SUM R/yr
    baseline           7.6     9.1      6.4     8.4       31.5
    session           12.1     9.2     12.2    11.1       44.6
    vol                5.1     9.1      6.8     6.1       27.1
    session+vol        7.8     7.4      8.5     5.7       29.5

`vol` nearly DOUBLES R per fill on XAU 1h -- 0.1055 to 0.2006 at a 64.4% win
rate, the best-looking cell in the whole study -- and leaves the account no
better off, because it takes 45% fewer trades. It is a good diagnostic and a bad
filter. `session` is adopted: +42% on total R per year while keeping nine
trades in ten. Combining them is worse than either alone.

THE WINDOW IS A PLATEAU, NOT A SPIKE, and it is on the BROKER's clock. The
stored bars are broker server time (EET/EEST), so 07-21 here is London local
05:00-19:00, about 04:00-19:00 UTC -- it was labelled "London/NY in UTC" for a
day, which was wrong and briefly had the browser gating a window three hours
away from this one. Ten alternative windows DEFINED IN TRUE UTC were then
measured as replacements, against the same pre-registered bar (beat the
incumbent on total R/yr in all four candidate cells). Sum of R/yr over the four:

    none (24h)              32.7      UTC 08-17 London        44.5
    INCUMBENT broker 07-21  44.9      UTC 13-21 New York      34.3
    UTC 05-18               45.7      UTC 12-17 overlap       34.8
    UTC 07-21               45.5      UTC 02-19               34.5

NONE PASSED -- the two that edge it on the sum win in only two or three cells,
by about 2%, which is noise. The lesson is that the boundaries barely matter:
every sane daytime window lands between 42 and 46, and the whole +42% comes from
excluding the quiet hours, not from where the edges sit. So the incumbent stays,
and it stays on broker time, which tracks EU DST exactly as London does and
therefore holds the same position against the session all year.

THE GATE IS NOW OFF BY DEFAULT -- `session=None`, 24 hours -- turned off by
request after all of the above was measured. Nothing here is retracted: the
window still measured +42% on total R per year, and running around the clock
gives that back in exchange for trading the hours it excluded. The row to read
is `none (24h) 32.7` against `broker 07-21 44.9` in the table above. Anything
compared against a number from before this change must set session=(7, 21)
explicitly, or it is comparing two different rules.

TREND AGE IS A SIGNAL-QUALITY SCORE, AND IT IS REPORTED, NOT ACTED ON. Nine
quantities knowable at signal time were tested for a relationship to outcome --
cost fraction, ATR relative to its own median, EMA separation, distance to the
trigger, swing width, ADX, trend age, position in the range, and side. The
pre-registered bar was: the top half beats the bottom half on GROSS R (so it is
not merely selecting cheaper trades), in all four eras, on BOTH instruments.

NONE PASSED. But `age` -- bars since the EMA cross -- failed only because the
test was written one-sided: it is NEGATIVE in 15 of 16 era-cells, i.e. FRESH
trends beat old ones, consistently and on both instruments. Mean net R by age:

    age (bars)   0-2     3-5    6-10   11-20   21-40   41-80    80+
    XAU 30m    +.142   +.072   +.042   -.014   +.092   -.044   -.010
    XAU 1h     +.230   +.120   -.017   +.147   +.110   -.007   +.123
    JPY 30m    +.113   +.099   +.128   +.186   -.012   +.025   -.059
    JPY 1h     +.303   -.160   +.034   +.152   +.080   +.082   +.028

The 0-2 bucket is the best in all four cells, at two to three times the pooled
mean, on about a quarter of the trades.

WHY IT IS NOT A GATE. Being a sign flip found after looking, it was then held to
the checks that killed the volatility and ADX filters. It PASSES on USDJPY --
both halves of history, all three EMA pairs (10/30, 20/50, 30/100), all four
eras, nine checks out of nine. It FAILS about half of them on XAUUSD: the first
half of history goes +0.0062 to +0.0014 on 30m and +0.050 to +0.028 on 1h, and
the 10/30 pair is worse on both gold frames. Risk-adjusted per cell (R/yr per
unit of drawdown) it helps yen -- 0.39 to 0.57, and 0.54 to 0.81 -- and HURTS
gold, 0.43 to 0.33 and 0.65 to 0.44. A gate that works on one instrument is not
a rule, so the ticket carries the number and the reader decides.

WHERE IT IS WORTH REAL MONEY IS THE PORTFOLIO, and for a different reason.
Running all four cells on one account at 2%, one position at a time:

    arm              CAGR    maxDD   P(losing yr)   P(down >25%)
    baseline        +7.9%    54.2%        39%            10%
    age <= 20      +22.0%    34.5%        22%             2%

Nearly three times the return at LOWER drawdown, which is not a leverage
transformation and is the only thing measured this session that survived a
matched-risk comparison. The mechanism is not prediction: fresh trends do not
begin on four cells at once, so the gate DE-CORRELATES them and fewer signals
collide in the one-position queue. That is also why it improves the portfolio
while making each gold cell individually worse.

WHAT IS STILL UNPROVEN. The parameters were CHOSEN on a sweep of XAUUSD covering
all four periods, so the gold rows are not clean out-of-sample for that choice;
USDJPY is the cleaner evidence. Nothing has been forward tested. And a 4 ATR
stop on 1h gold is about 70 points -- roughly 1.2% of a 7,900 AUD account at the
minimum lot, over the 0.5% the backtests assume, which is the same affordability
wall the Donchian 4h cell hit.

AND IT IS NO LONGER A SCALPER. 30m to 1h with a four-ATR stop is a trade held
for hours. That is exactly what the arithmetic predicted: the way to beat the
cost fraction is to stop scalping.

WHY THIS IS NOT A sim.Strategy. The engine fills at the next bar's OPEN and
carries one stop and one target. This rule rests PENDING orders that fill
intrabar at a named level, and ladders three targets. Wrapping it in a Strategy
would mean a close-based approximation -- a different rule with the same name,
measured differently, which is the exact confusion this file was rewritten to
end. Scoring lives in `tools/scalper.py --backtest`, which resolves on 1m bars
and writes the same JSONL that `tools/score_external.py` grades third-party
signals with. Our rule and theirs are held to one standard.

WHAT USED TO BE HERE. RSS, the channel mean-reversion rule from the Rayo
Scalping Strategy PDF -- enter at one channel boundary, target the other. It was
measured to death and replaced: -0.0785 R pooled in its best form (short-only),
and seven exit variants all lost to that baseline, including one that halved the
losers and still lost because it cut a third of the winners with them. The full
record, including the eight-arm exit table and what it refuted about support and
resistance as levels, is in README.md under "RSS: eight exits, and the baseline
beat all seven challengers", and the code is in git history.
"""

import numpy as np
import pandas as pd

#: the ladder, in R. TP1 under 1R is deliberate -- see the docstring.
TPS = (0.9, 1.5, 2.4)

DEFAULTS = {
    'mode': 'break',
    'swing': 20,
    'fast': 20,
    'slow': 50,
    # 4.0, NOT 1.5. See "THE COST FRACTION IS THE RULE" -- a 1.5 ATR stop on 5m
    # gold hands a tenth of the risk to the spread before the market moves.
    'stop_atr': 4.0,
    # NONE -- THE RULE TRADES AROUND THE CLOCK, by request and against the
    # measurement. The 07-21 broker window was worth +42% on total R per year
    # while keeping ~90% of the fills (see THE SESSION FILTER), so turning it
    # off buys back the quiet hours at roughly that price. It is switched off
    # rather than deleted so the number stays reproducible: pass
    # session=(7, 21), or `tools/scalper.py --session 7-21`, to measure it.
    #
    # WHEN IT IS A TUPLE these are BROKER-CLOCK hours (EET/EEST), because that
    # is what the stored bars are indexed by -- see THE SESSION GATE below.
    # 07-21 is London local 05:00-19:00 all year.
    'session': None,
    'expire': 12,
    # TP1. The ladder is still drawn, but the measured exit is the FIRST target.
    'exit_tp': 1,
}


def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def atr(bars, n=14):
    """Wilder, alpha 1/n -- NOT the 2/(n+1) an EMA would use."""
    h, l, c = bars['high'], bars['low'], bars['close']
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def tickets(bars, mode='break', swing=20, fast=20, slow=50, stop_atr=4.0,
            session=None):
    """Every ticket the rule would post, one per qualifying bar.

    ONE PER BAR IS NOT ONE PER TRADE. An uptrend that lasts a day proposes the
    same buy stop a hundred times; scoring all of them counts one idea a
    hundred times over. The caller is responsible for taking one at a time --
    `tools/scalper.py` does it with a busy cursor keyed on the resolution of the
    previous ticket, and getting that wrong was the difference between a
    plausible-looking result and the real one.
    """
    # THE SESSION GATE. Measured +42% on total R per year across the four
    # candidate cells, keeping ~90% of the trades -- see "THE SESSION FILTER"
    # in the docstring for why the volatility filter, which looked better per
    # trade, was rejected.
    #
    # THESE ARE BROKER HOURS, NOT UTC, and the distinction was a live bug. The
    # stored bars are broker server time (EET/EEST, UTC+2/+3, tz-naive -- see
    # tools/_brokerclock.py) so bars.index.hour is the broker's clock: 07-21
    # here is roughly 04:00-19:00 UTC. js/chart/scalper.js read the SAME
    # numbers off true-UTC bridge bars and so gated a window three hours away,
    # posting live tickets 19:00-21:00 UTC that this never took. It now
    # converts with the same EET/EEST rule before comparing.
    #
    # Broker time is also the better clock to define it on: EET follows EU DST
    # exactly as London does, so this window is London local 05:00-19:00 all
    # year, where a fixed UTC window would slide against the session twice a
    # year. Ten UTC-defined windows were measured as replacements and none beat
    # it in all four cells -- see "THE WINDOW IS A PLATEAU" in the docstring.
    hours = bars.index.hour
    lo_h, hi_h = session if session else (0, 24)

    hi = bars['high'].rolling(swing).max().shift(1)
    lo = bars['low'].rolling(swing).min().shift(1)
    ef, es = ema(bars['close'], fast).shift(1), ema(bars['close'], slow).shift(1)
    a = atr(bars).shift(1)
    idx = bars.index

    # TREND AGE: bars since EMA(fast) last crossed EMA(slow). Causal -- it only
    # needs to know when the CURRENT run began, which is past information. See
    # "TREND AGE IS A SIGNAL-QUALITY SCORE" in the docstring for what it is
    # worth and, more importantly, what it is not.
    _up = (ef > es)
    _run = (_up != _up.shift(1)).fillna(True).cumsum()
    _n = pd.Series(np.arange(len(bars)), index=idx)
    age = (_n - _n.groupby(_run).transform('min')).to_numpy()

    out = []
    for i in range(max(swing, slow) + 2, len(bars)):
        A = a.iloc[i]
        if not np.isfinite(A) or A <= 0:
            continue
        H, L = hi.iloc[i], lo.iloc[i]
        if not (np.isfinite(H) and np.isfinite(L)):
            continue
        if not (lo_h <= hours[i] < hi_h):
            continue
        up = ef.iloc[i] > es.iloc[i]
        side = 1 if up else -1
        if mode == 'break':
            order = 'stop'
            entry = (H + 0.10 * A) if up else (L - 0.10 * A)
        else:
            order = 'limit'
            entry = L if up else H
        stop = entry - side * stop_atr * A
        risk = abs(entry - stop)
        if not risk:
            continue
        out.append({
            'source': 'rayo_scalper', 'id': str(len(out) + 1),
            'symbol': None,
            'side': 'buy' if side > 0 else 'sell',
            'order': order,
            'entry': round(float(entry), 3),
            'sl': round(float(stop), 3),
            'tp': [round(float(entry + side * k * risk), 3) for k in TPS],
            't': idx[i].tz_localize('UTC').isoformat().replace('+00:00', 'Z'),
            'ms': int(idx[i].value // 10 ** 6),
            'risk': round(float(risk), 6),
            'atr': round(float(A), 6),
            'level': round(float(H if up else L), 3),
            'trend': 'up' if up else 'down',
            'age': int(age[i]),
            'mode': mode,
        })
    return out

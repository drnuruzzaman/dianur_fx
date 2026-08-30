"""
Strategies. The baselines are controls; the trendline and divergence strategies
are the candidates measured against them.
"""

from .donchian import Donchian
from .emafilter import DonchianEmaFilter
from .turtle_ea import TurtleEA
from .ema_cross import EmaCross
from .mean_revert import FADE, FOLLOW, MeanRevert
from .mtf import DonchianMTF, EmaCrossMTF
from .price_ema import PriceEma
from .rsi_divergence import RsiDivergence
from .tl_bounce import TrendlineBounce
from .tl_breakout import TrendlineBreakout
from .exits import DonchianExitEma, DonchianExitFixedR, DonchianExitTrail
from .rsi_ema_cross import RsiEmaCross
from .retest import DonchianRetest
from .trendlong import DonchianTrendLong
from .horizon import (BARS_PER_DAY, HORIZON_DAYS, HORIZON_TFS, n_for,
                      params_for_tf, strategy_for_tf)

# baselines need only bars; the rest need a feature table (see base.MTFStrategy)
def _donchian_high(**kw):
    """The intrabar-touch hypothesis: High[t] > Upper[t] rather than Close[t]."""
    return Donchian(trigger='high', **kw)


def _follow(**kw):
    """MeanRevert's mirror: same trigger and costs, opposite sign."""
    return MeanRevert(direction=FOLLOW, **kw)


#: STEP 1 of the roadmap, the gap the neighbourhood test does not cover.
#: `neighbourhood` swept entry 12..32 -- a tight ring around 20, which measures
#: local robustness. This is the different question: is 20 the right ORDER OF
#: MAGNITUDE at all, or is a much faster or much slower channel better?
#:
#: The exit is tied to N at half its length, keeping the validated 20/10 shape
#: across the family. Letting exit vary independently would turn one degree of
#: freedom into two and make any winner impossible to attribute.
DONCHIAN_N = (5, 10, 20, 30, 50, 100)


def _donchian_n(n):
    """Factory so stage1 can construct these with no arguments, like the rest."""
    def make(**kw):
        kw.setdefault('entry', n)
        kw.setdefault('exit', max(2, n // 2))
        return Donchian(**kw)
    make.__name__ = 'donchian_n%d' % n
    return make


_N_VARIANTS = {'donchian_n%d' % n: _donchian_n(n) for n in DONCHIAN_N}

#: The horizon-matched lengths, which are a DIFFERENT question from DONCHIAN_N
#: and must not be folded into it. DONCHIAN_N is a sweep -- an order-of-
#: magnitude probe around the validated 20 -- and every tool that iterates it
#: is asking "which N is best?". These are answers, one per timeframe, derived
#: from horizon.py's finding that the edge is a DURATION: 3.3 days of bars,
#: whatever number that turns out to be. Merging them would silently add four
#: arms to every sweep and make the winner unattributable.
#:
#: They are registered because a name that `strategy_for_tf` can return but
#: `REGISTRY` cannot construct is a rule the panel can quote and no one can
#: measure -- which is exactly the split tests/test_horizon_parity.py exists to
#: prevent.
_HORIZON_VARIANTS = {
    strategy_for_tf(tf): _donchian_n(n_for(tf))
    for tf in HORIZON_TFS
    if strategy_for_tf(tf) not in _N_VARIANTS and strategy_for_tf(tf) != 'donchian'
}

BASELINES = {'donchian': Donchian, 'donchian_high': _donchian_high,
             'donchian_ema200': DonchianEmaFilter,
             'turtle_ea': TurtleEA,
             'donchian_trendlong': DonchianTrendLong,
             'donchian_retest': DonchianRetest,
             'rsi_ema_cross': RsiEmaCross,
             'donchian_exit_ema': DonchianExitEma,
             'donchian_exit_trail': DonchianExitTrail,
             'donchian_exit_2r': DonchianExitFixedR,
             'ema_cross': EmaCross, 'price_ema': PriceEma,
             'mean_revert': MeanRevert, 'stretch_follow': _follow}
FEATURE_STRATEGIES = {
    'tl_bounce': TrendlineBounce,
    'tl_breakout': TrendlineBreakout,
    'rsi_divergence': RsiDivergence,
}
BASELINES.update(_N_VARIANTS)
BASELINES.update(_HORIZON_VARIANTS)

REGISTRY = {**BASELINES, **FEATURE_STRATEGIES}

# Higher-timeframe-gated variants. NOT in BASELINES: they cannot be constructed
# by name because they need the context series passed in, and resampling the
# execution bars to fake it would build the context out of the very data the
# rule is trading. tools/stage1.py builds the factory per cell.
MTF_STRATEGIES = {'donchian_mtf': DonchianMTF, 'ema_cross_mtf': EmaCrossMTF}

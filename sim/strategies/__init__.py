"""
Strategies. The baselines are controls; the trendline and divergence strategies
are the candidates measured against them.
"""

from .donchian import Donchian
from .ema_cross import EmaCross
from .mean_revert import FADE, FOLLOW, MeanRevert
from .rsi_divergence import RsiDivergence
from .tl_bounce import TrendlineBounce
from .tl_breakout import TrendlineBreakout

# baselines need only bars; the rest need a feature table (see base.MTFStrategy)
def _follow(**kw):
    """MeanRevert's mirror: same trigger and costs, opposite sign."""
    return MeanRevert(direction=FOLLOW, **kw)


BASELINES = {'donchian': Donchian, 'ema_cross': EmaCross,
             'mean_revert': MeanRevert, 'stretch_follow': _follow}
FEATURE_STRATEGIES = {
    'tl_bounce': TrendlineBounce,
    'tl_breakout': TrendlineBreakout,
    'rsi_divergence': RsiDivergence,
}
REGISTRY = {**BASELINES, **FEATURE_STRATEGIES}

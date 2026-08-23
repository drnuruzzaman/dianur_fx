"""
Trendline engine — deliberately independent of any trading strategy.

It describes structure (pivots, lines, lifecycle, quality, regime) and projects
higher-timeframe structure onto an execution timeframe without ever reading an
unclosed higher-timeframe bar. Strategies consume its output; it knows nothing
about entries, exits, risk or money.
"""

from .engine import Params, Snapshot, TrendlineEngine
from .features import build, build_timeframe_state
from .lines import Direction, Role, Status, Trendline
from .mtf import MTFContext, TF_MS, LookAheadViolation, align_index, confluence
from .pivots import find_pivots, pivots_confirmed_by
from . import regime

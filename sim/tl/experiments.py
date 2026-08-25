"""
experiments.py — frozen, versioned experiment definitions.

An experiment that can drift is not a baseline. Every parameter that changes a
trade list lives here as an immutable record, so a result can always be traced
to the exact definition that produced it, and so "we improved it" can never
quietly mean "we changed the question".

RULES

  1. Never edit a frozen spec. Add a new version.
  2. Every number reported anywhere must name the spec that produced it.
  3. `frozen_at` is the date the definition was locked, not the run date.

DISPLACEMENT_V1 is the first hypothesis in this project to reach a positive net
expectancy on any cell, and is therefore the baseline everything else must beat.
Its headline numbers are recorded on the spec itself rather than in a document,
because a baseline whose numbers live somewhere else stops being checkable.
"""
from dataclasses import asdict, dataclass, field
from typing import Optional, Tuple


@dataclass(frozen=True)
class ExperimentSpec:
    """One immutable experiment definition."""

    name: str
    frozen_at: str
    description: str

    # --- Layer B: swing detection --------------------------------------- #
    swing_strength: int = 3
    swing_close_confirm: bool = False

    # --- Layer C: structure --------------------------------------------- #
    structure_strength: int = 3
    require_bias_agreement: bool = True

    # --- the trigger ------------------------------------------------------ #
    trigger: str = 'structural_bos'
    displacement_atr: float = 1.0

    # --- Layer A: market-state gates ------------------------------------- #
    atr_pct_min: float = 5.0
    atr_pct_max: float = 98.0
    momentum_span: int = 14

    # --- risk geometry ---------------------------------------------------- #
    stop_atr: float = 1.0
    target_atr: float = 2.0
    horizon_bars: int = 96

    # --- cost model ------------------------------------------------------- #
    slippage_atr: float = 0.02
    friction_model: str = 'spread/stop_price + 2*slippage_atr/stop_atr'

    # --- what it measured, at freeze time -------------------------------- #
    baseline: Optional[dict] = None
    # A frozen baseline is never edited. When re-measurement changes what it
    # means, the new numbers land here and both stay readable.
    revision: Optional[dict] = None
    universe: Tuple[str, ...] = ()

    def to_row(self):
        return asdict(self)


#: THE BASELINE. 1h+4h pooled, three disjoint eras, EURUSD + USDJPY.
#:
#: Net is NEGATIVE. It is the baseline because it is the best measured result in
#: the project, not because it is profitable -- and recording it honestly is the
#: point: every later variant is judged against -0.018R, not against a number
#: that has been quietly rounded toward zero.
DISPLACEMENT_V1 = ExperimentSpec(
    name='displacement_v1',
    frozen_at='2026-08-24',
    description=(
        'Structural BOS trigger with 1.0 ATR displacement required before the '
        'break may fire. No trendline involvement of any kind. The first '
        'hypothesis here to reach positive net expectancy on any cell (4h).'),
    displacement_atr=1.0,
    baseline={
        'universe': '1h + 4h pooled, EURUSD.a + USDJPY.a, 3 disjoint eras',
        'trades': 1208,
        'win_rate_pct': 36.07,
        'rr': 2.0,
        'gross_avg_R': 0.082,
        'friction_R': 0.100,
        'net_avg_R': -0.018,
        'cells_positive': '6 of 11',
        'caveat': ('the two best cells are the two smallest (n=69, n=40); the '
                   'two worst are among the largest (n=235, n=220)'),
    },
    universe=('EURUSD.a', 'USDJPY.a'),
)

#: The 4h-only slice, which is the only positive net expectancy measured.
#: Frozen separately because it is a DIFFERENT universe, not a tuning of V1.
DISPLACEMENT_V1_4H = ExperimentSpec(
    name='displacement_v1_4h',
    frozen_at='2026-08-24',
    description=('displacement_v1 restricted to 4h. Positive net expectancy, '
                 'on a small sample.'),
    displacement_atr=1.0,
    baseline={
        'universe': '4h only, all instruments, 3 eras',
        'trades': 307,
        'win_rate_pct': 39.08,
        'rr': 2.0,
        'gross_avg_R': 0.172,
        'friction_R': 0.071,
        'net_avg_R': 0.102,
        'cells_positive': '8 of 30 across all timeframes',
        'caveat': '307 trades across 6 cells; ~51 per cell',
    },
    # The frozen baseline above is left exactly as recorded -- that is what
    # freezing is for. What it MEANT changed, and the re-measure sits beside it.
    revision={
        'measured_at': '2026-08-25',
        'what_changed': (
            'AUDUSD 4h bars now load. In the original run AUDUSD produced 1h '
            'cells only, so its 4h data was silently absent, and five further '
            'cells fell under the len(trades) < 30 reporting filter in '
            'metrics(). The +0.102 headline was therefore pooled over 6 of the '
            '12 cells that exist today. Reproduced exactly on all 6 -- the '
            'trade counts and per-cell R match the recorded CSV to the digit, '
            'so the arithmetic was right and the population was partial.'),
        'recorded_6_cells': {'trades': 307, 'net_avg_R': 0.1015},
        'same_filter_today': {'trades': 354, 'net_avg_R': 0.0469,
                              'note': 'the only cell the >=30 filter newly '
                                      'admits is AUDUSD 1999-2010, n=47, '
                                      'net -0.310 -- the worst cell in the '
                                      'study'},
        'all_cells': {'trades': 449, 'net_avg_R': 0.0767,
                      'ci95': (-0.0570, 0.2108),
                      'verdict': 'indistinguishable from zero'},
        'by_era_net_R': {'1999-2010': -0.0237, '2011-2020': 0.1634,
                         '2021-2026': 0.1136,
                         'note': 'every era CI spans zero'},
        'drop_best_cell': {'trades': 380, 'net_avg_R': 0.0395,
                           'ci95': (-0.1099, 0.1890)},
        'neighbourhood': (
            'not a spike -- stop 0.75 gives +0.104 and atr-pct 10-90 gives '
            '+0.103, both above the frozen point. But neighbours range 0.001 '
            'to 0.104, a spread the size of the effect, so the parameter '
            'choice does as much work as the signal.'),
        'friction_breakeven': '2.06x the modelled friction',
        'power': (
            'CI half-width 0.134 at n=449. Clearing zero at this effect size '
            'needs ~1368 trades, ~3x current, i.e. roughly 12 instruments. '
            'Only 4 have bars on disk and the history already runs 1999-2026, '
            'so the sample is exhausted: this question cannot be settled with '
            'more history, only with more instruments.'),
    },
    universe=('EURUSD.a', 'USDJPY.a', 'XAUUSD.a', 'AUDUSD.a'),
)

REGISTRY = {s.name: s for s in (DISPLACEMENT_V1, DISPLACEMENT_V1_4H)}


def get(name) -> ExperimentSpec:
    if name not in REGISTRY:
        raise KeyError('unknown experiment %r; known: %s'
                       % (name, sorted(REGISTRY)))
    return REGISTRY[name]

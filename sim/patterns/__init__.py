"""
patterns — propose candidate entries, score them against a null, keep almost none.

Three layers, deliberately separated so the middle one never has to change:

    proposer.py   "here is a bar I think is special, and a direction"
    outcomes.py   "here is what happened after every bar" (computed once)
    evaluate.py   "here is whether that was distinguishable from nothing"

The proposer is the only part that knows what a pattern IS. Trendlines are one
proposer; a symbolic motif finder is another. Because they emit the same table,
they are scored by identical code against an identical null, which is what makes
"is this discovered pattern better than a trendline" a question with an answer.
"""

from .outcomes import (CHOP, STOP_FIRST, TARGET_FIRST, UNDEFINED, fair_value,
                       triple_barrier)
from .proposer import Proposer, empty_proposals, validate_proposals

__all__ = ['Proposer', 'empty_proposals', 'validate_proposals',
           'triple_barrier', 'fair_value',
           'TARGET_FIRST', 'STOP_FIRST', 'CHOP', 'UNDEFINED']

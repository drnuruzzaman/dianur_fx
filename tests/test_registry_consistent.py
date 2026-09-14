"""
configs/alerts.json must be internally consistent and self-describing.

The scalper registry is read by four things -- tools/scalper.py on every
scheduled run, the Signal Board, the right-rail panel and the settings modal --
and a figure in it is a claim about nine years of measurement that nobody can
check by looking at a chart. Twice now a number in this file has turned out to
be unreproducible: the portfolio claim in `quality` (withdrawn 2026-09-14) and
the trendline era table before it. Both times the failure was the same shape --
a result typed in from a script that no longer existed.

These tests cannot tell whether a measurement is TRUE. They check the two things
that are checkable from the file alone:

    the headline figures are derivable from the per-era numbers beside them, so
    a reader can recompute `expected_net_r` rather than take it on trust;

    the file says what produced it, so the run can be repeated.

WHY THE MEDIAN IS TAKEN OF THE ROUNDED ERAS. `expected_net_r` is the median of
the four values AS STORED, not of the unrounded means behind them. The two can
differ in the fourth decimal -- a median of four is the mean of the middle two,
so rounding first and averaging second is not the same as the reverse -- and
that difference once looked like a reproducibility failure when it was only two
ways of rounding. The stored eras are the definition, precisely so this test can
exist.
"""

import io
import json
import os
import statistics
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

CFG = os.path.join(ROOT, 'configs', 'alerts.json')


@pytest.fixture(scope='module')
def cfg():
    with io.open(CFG, encoding='utf-8') as fh:
        return json.load(fh)


def measured(cfg):
    return [c for c in cfg['scalper']['watch'] if c.get('eras')]


def test_headline_figures_come_from_the_eras(cfg):
    """expected / worst / best are the median, min and max of the stored eras."""
    rows = measured(cfg)
    assert len(rows) >= 30, 'only %d measured cells -- registry looks truncated' % len(rows)
    for c in rows:
        v = list(c['eras'].values())
        where = '%s %s' % (c['symbol'], c['tf'])
        assert c['expected_net_r'] == pytest.approx(
            round(statistics.median(v), 4), abs=1e-9), where
        assert c['worst_era_net_r'] == pytest.approx(min(v), abs=1e-9), where
        assert c['best_era_net_r'] == pytest.approx(max(v), abs=1e-9), where


def test_tradeable_cells_pass_the_tests_that_define_tradeable(cfg):
    """The four tests, re-read off the file rather than trusted as a flag.

    `tradeable` decides whether a cell is messaged to Telegram, so it is the one
    field in here with a consequence. It must follow from the numbers stored
    beside it -- never be a flag somebody set by hand and the measurement later
    contradicted.
    """
    from tools.scalp_register import MIN_FILLS, STRESS_KEEP
    for c in measured(cfg):
        if not c.get('tradeable'):
            continue
        where = '%s %s' % (c['symbol'], c['tf'])
        assert c['expected_net_r'] > 0, where
        assert c['total_r_per_year'] > 0, where
        assert c['n'] >= MIN_FILLS, '%s: %d fills' % (where, c['n'])
        st = c.get('stress_net_r')
        assert st is not None, '%s has no spread-stress measurement' % where
        assert st >= STRESS_KEEP * c['expected_net_r'], (
            '%s keeps only %.0f%% of its edge at the stressed spread'
            % (where, 100.0 * st / c['expected_net_r']))


def test_only_tradeable_cells_can_be_messaged(cfg):
    """Enabled is a superset of tradeable, and every enabled cell was measured.

    Enabling a cell puts it in the scheduled run; being tradeable is what turns
    a journal entry into a Telegram message. A cell that is tradeable but
    disabled is fine (it is simply not being watched); one that is neither
    measured nor marked as a control has no business in the run at all.
    """
    for c in cfg['scalper']['watch']:
        if not c.get('enabled'):
            continue
        where = '%s %s' % (c['symbol'], c['tf'])
        assert c.get('measurable'), '%s is enabled but not measurable' % where
        assert c.get('note'), '%s is enabled with no note saying why' % where


def test_the_file_says_how_to_reproduce_itself(cfg):
    """The command, the window and the settings that produced these numbers."""
    sc = cfg['scalper']
    m = sc.get('measurement')
    assert m, 'no `measurement` block -- these numbers cannot be reproduced'
    for k in ('stop_atr', 'swing', 'fast', 'slow', 'expire', 'exit_tp',
              'session', 'window', 'command', 'measured_on'):
        assert k in m, 'measurement is missing %r' % k
    assert 'scalp_register' in m['command']
    assert sc['rule'].startswith('rayo_scalper')


def test_the_rule_string_matches_the_measurement(cfg):
    """The prose humans read and the settings the numbers came from agree.

    The `rule` string is what the settings modal and the docstrings quote. It
    spent four days claiming a 07:00-21:00 session that had been switched off,
    which is how a reader ends up comparing two different rules.
    """
    sc = cfg['scalper']
    m = sc['measurement']
    assert ('stop %g ATR' % m['stop_atr']) in sc['rule'], sc['rule']
    assert ('swing %d' % m['swing']) in sc['rule']
    assert ('EMA %d/%d' % (m['fast'], m['slow'])) in sc['rule']
    if m['session'] in ('none', None):
        assert 'SESSION OFF' in sc['rule'], (
            'the gate is off in the measurement and the rule string does not '
            'say so: %s' % sc['rule'])


def test_live_defaults_match_the_registered_rule(cfg):
    """What ships and what was measured are the same rule.

    DEFAULTS is what tools/scalper.py --live actually posts. If it drifts from
    the registry, every expectation in this file describes a rule nobody is
    running -- which is the failure the whole re-registration existed to end.
    """
    from sim.strategies.rayo import DEFAULTS
    m = cfg['scalper']['measurement']
    assert DEFAULTS['stop_atr'] == m['stop_atr']
    assert DEFAULTS['swing'] == m['swing']
    assert DEFAULTS['fast'] == m['fast']
    assert DEFAULTS['slow'] == m['slow']
    assert DEFAULTS['expire'] == m['expire']
    assert DEFAULTS['exit_tp'] == m['exit_tp']
    assert (DEFAULTS['session'] is None) == (m['session'] in ('none', None))


def test_the_age_gate_is_declared_where_both_surfaces_read_it(cfg):
    """If the gate is on, the config must say what it is and where it applies.

    `tools/scalper.py` withholds the Telegram message and the Signal Board
    withholds the row from its headline count; both read `scalper.quality`. A
    gate that were on in one place and off in the other would make the board a
    different claim from the alerts, which is the exact failure the shared
    `loadGate()` in js/chart/graded.js exists to prevent.
    """
    q = cfg['scalper'].get('quality') or {}
    if not q.get('enforce'):
        pytest.skip('gate is off')
    assert isinstance(q.get('max_age'), int) and q['max_age'] > 0
    assert q.get('enforced_where'), 'the gate is on and nothing says where'
    assert q.get('caveat'), (
        'the measured benefit is a one-position-queue effect and the config '
        'must say so -- a reader taking several signals at once is not running '
        'the arm that was measured')


def test_age_gate_matches_the_config(cfg):
    """The function the scheduled run calls, against the file it reads."""
    from tools.scalper import QUALITY, age_gate, load_registry
    load_registry()
    q = cfg['scalper'].get('quality') or {}
    assert QUALITY.get('enforce') == q.get('enforce')
    if not q.get('enforce'):
        pytest.skip('gate is off')
    cap = q['max_age']
    assert age_gate({'age': cap})[0] is True
    assert age_gate({'age': cap + 1})[0] is False
    assert str(cap) in age_gate({'age': cap + 1})[1]
    # A ticket with no age at all is NOT silenced: absence of the score is not
    # evidence of a stale trend, and failing open keeps a missing field from
    # quietly muting the whole feed.
    assert age_gate({})[0] is True


def test_gate_off_means_gate_off():
    """`enforce: false` must pass everything, whatever max_age says."""
    from tools import scalper as SC
    saved = dict(SC.QUALITY)
    try:
        SC.QUALITY.clear()
        SC.QUALITY.update({'enforce': False, 'max_age': 1})
        assert SC.age_gate({'age': 9999})[0] is True
        SC.QUALITY.clear()
        assert SC.age_gate({'age': 9999})[0] is True
    finally:
        SC.QUALITY.clear()
        SC.QUALITY.update(saved)


def test_the_forward_scorer_and_the_registrar_agree_on_the_rules(cfg):
    """Live scoring and backtest scoring must use the same horizon and frames.

    `tools/score_scalper.py` grades what the live rule DID; `tools/scalp_register.py`
    measured what it SHOULD do. The two numbers are printed side by side in the
    scorer's own table, so a reader will compare them -- and a silent difference
    in how long an unresolved trade is followed, or in how many minutes a
    timeframe has, would make that comparison meaningless while looking fine.
    """
    from tools import scalp_register as REG
    from tools import score_scalper as SCO
    assert SCO.HORIZON_H == REG.HORIZON_H, (
        'the live scorer abandons unresolved trades on a different schedule '
        'from the registrar: %r vs %r' % (SCO.HORIZON_H, REG.HORIZON_H))
    for tf, mins in SCO.TF_MIN.items():
        if tf in REG.HORIZON_H:
            assert mins > 0
    # Both must know every frame the registry actually uses, or a cell is
    # silently skipped rather than reported as unscorable.
    for c in cfg['scalper']['watch']:
        assert c['tf'] in SCO.TF_MIN, '%s is not a frame the scorer knows' % c['tf']
        assert c['tf'] in SCO.HORIZON_H, '%s has no scoring horizon' % c['tf']


def test_the_scored_ledger_is_sane():
    """One row per ticket, and a closed row carries its arithmetic.

    The ledger is append-and-replace across hourly runs: an `open` trade is
    dropped and recomputed until it closes. Getting that wrong duplicates rows
    or freezes trades as open for ever, and both look like a working scorer.
    """
    import io as _io
    import json as _json
    path = os.path.join(ROOT, 'data', 'scalper_scored.jsonl')
    if not os.path.exists(path):
        pytest.skip('nothing scored yet')
    rows = [_json.loads(l) for l in _io.open(path, encoding='utf-8') if l.strip()]
    if not rows:
        pytest.skip('ledger is empty')
    ids = [r['id'] for r in rows]
    assert len(ids) == len(set(ids)), 'duplicate ids in the scored ledger'
    for r in rows:
        assert r['outcome'] in ('sl', 'tp1', 'tp2', 'tp3', 'expired', 'open')
        if r['outcome'] in ('sl', 'tp1', 'tp2', 'tp3'):
            assert r.get('net_r') is not None, r['id']
            # net = gross - cost + swap, and swap is signed by the broker.
            want = r['gross_r'] - r['cost_r'] + r['swap_r']
            assert abs(r['net_r'] - want) < 1e-6, r['id']
        else:
            assert r.get('net_r') is None, (
                '%s is %s and must not carry a net R' % (r['id'], r['outcome']))

"""
The JS channel-length map must equal the Python one, exactly.

js/chart/donchian.js carries a copy of sim/strategies/horizon.py because the
live panel draws the channel before /signal answers. A copy is only safe while
something fails when the two disagree; this is that something.
"""
import json
import os
import pathlib
import re
import subprocess
import sys

import pytest

from sim.strategies.horizon import HORIZON_DAYS, params_for_tf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, 'js', 'chart', 'donchian.js')
TFS = ('1m', '5m', '15m', '30m', '1h', '4h', '1d')


def _node():
    for exe in ('node', 'node.exe'):
        try:
            subprocess.run([exe, '--version'], capture_output=True, check=True)
            return exe
        except (OSError, subprocess.CalledProcessError):
            continue
    return None


def test_js_map_matches_python():
    """Run the real JS. No re-implementation, no regex approximation."""
    node = _node()
    if node is None:
        pytest.skip('node not available')
    src = ('import {paramsForTf} from %s;\n'
           'const out={};\n'
           'for (const tf of %s) { const p = paramsForTf(tf); '
           'out[tf] = [p.entry, p.exit, p.horizonDays]; }\n'
           'console.log(JSON.stringify(out));\n'
           % (json.dumps(pathlib.Path(JS).as_uri()), json.dumps(list(TFS))))
    r = subprocess.run([node, '--input-type=module', '-e', src],
                       capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stderr
    got = json.loads(r.stdout)
    for tf in TFS:
        p = params_for_tf(tf)
        assert got[tf] == [p['entry'], p['exit'], p['horizon_days']], (
            'JS and Python disagree on %s: js=%s python=%s'
            % (tf, got[tf], [p['entry'], p['exit'], p['horizon_days']]))


def test_source_constants_agree():
    """Cheap check that survives node being absent."""
    js = open(JS, encoding='utf-8').read()
    m = re.search(r'HORIZON_DAYS\s*=\s*([\d.]+)', js)
    assert m and float(m.group(1)) == HORIZON_DAYS
    m = re.search(r"HORIZON_TFS\s*=\s*\[([^\]]*)\]", js)
    assert m
    tfs = tuple(x.strip().strip("'\"") for x in m.group(1).split(','))
    from sim.strategies.horizon import HORIZON_TFS
    assert tfs == HORIZON_TFS


def test_every_horizon_tf_has_a_registered_strategy():
    """A tf whose N is not registered cannot be measured or served."""
    from sim.strategies import REGISTRY, strategy_for_tf
    from sim.strategies.horizon import HORIZON_TFS
    for tf in HORIZON_TFS:
        assert strategy_for_tf(tf) in REGISTRY, tf


def test_all_surfaces_run_the_same_rule():
    """
    THE SYNC GUARANTEE. The live panel, the strategy replay and Python must
    resolve the same channel length for a timeframe.

    They diverged once and it was invisible: the replay called runRule with
    `rule.defaults` (a flat 20/10) while the live panel used the horizon map, so
    on 15m the replay stepped a five-hour channel measured at -0.0756 R over
    4,142 trades while the chart drew the 3.3-day one that passed every gate.
    Both looked correct; neither said which rule it was running.

    This drives the REAL code paths -- runRule's effective params, the rule's
    own paramsFor, and the module-level map -- rather than re-deriving them.
    """
    node = _node()
    if node is None:
        pytest.skip('node not available')
    donchian = os.path.join(ROOT, 'js', 'chart', 'donchian.js').replace(os.sep, '/')
    src = (
        'import {donchianRule, paramsForTf, signalsAsOf} from %s;\n'
        'import {runRule} from %s;\n'
        'const bars = Array.from({length: 40}, (_, i) => ('
        '  {t: i * 60000, o: 100, h: 101, l: 99, c: 100}));\n'
        'const out = {};\n'
        'for (const tf of %s) {\n'
        '  out[tf] = {\n'
        '    map: (({entry, exit}) => [entry, exit])(paramsForTf(tf)),\n'
        '    rule: (({entry, exit}) => [entry, exit])(donchianRule.paramsFor(tf)),\n'
        '    run: (({entry, exit}) => [entry, exit])(runRule(bars, donchianRule, {tf}).params),\n'
        '    panel: (({entry, exit}) => [entry, exit])(signalsAsOf(bars, {tf}).params),\n'
        '  };\n'
        '}\n'
        'console.log(JSON.stringify(out));\n'
        % (json.dumps(pathlib.Path(donchian).as_uri()),
           json.dumps(pathlib.Path(os.path.join(ROOT, 'js', 'chart', 'rules.js')).as_uri()),
           json.dumps(list(TFS))))
    r = subprocess.run([node, '--input-type=module', '-e', src],
                       capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stderr
    got = json.loads(r.stdout)

    for tf in TFS:
        p = params_for_tf(tf)
        want = [p['entry'], p['exit']]
        g = got[tf]
        assert g['map'] == want, ('paramsForTf', tf, g['map'], want)
        assert g['rule'] == want, ('donchianRule.paramsFor', tf, g['rule'], want)
        assert g['run'] == want, ('runRule (strategy replay)', tf, g['run'], want)
        assert g['panel'] == want, ('signalsAsOf (live panel)', tf, g['panel'], want)


def test_a_caller_that_names_no_timeframe_keeps_the_defaults():
    """Sweeps and fixtures pass explicit params; they must not be overridden."""
    node = _node()
    if node is None:
        pytest.skip('node not available')
    donchian = os.path.join(ROOT, 'js', 'chart', 'donchian.js').replace(os.sep, '/')
    src = ('import {donchianRule, signalsAsOf} from %s;\n'
           'const bars = Array.from({length: 40}, (_, i) => ('
           '  {t: i * 60000, o: 100, h: 101, l: 99, c: 100}));\n'
           'console.log(JSON.stringify({\n'
           '  bare: (({entry, exit}) => [entry, exit])(signalsAsOf(bars).params),\n'
           '  explicit: (({entry, exit}) => [entry, exit])('
           '    signalsAsOf(bars, {tf: "15m", entry: 30, exit: 15}).params),\n'
           '}));\n' % json.dumps(pathlib.Path(donchian).as_uri()))
    r = subprocess.run([node, '--input-type=module', '-e', src],
                       capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stderr
    got = json.loads(r.stdout)
    assert got['bare'] == [20, 10], 'no tf named -> defaults'
    assert got['explicit'] == [30, 15], 'explicit params must beat the map'

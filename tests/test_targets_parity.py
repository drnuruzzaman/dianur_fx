"""
The JS reference ladder must equal the Python one, exactly.

js/chart/targets.js mirrors sim/targets.py because the chart draws these levels
before /signal answers. The three surfaces used to disagree -- 2/3.5/5 on the
chart, a single hardcoded 2R on the live chart, and 1/2/3 in sim/signal.py,
which quoted the one multiple tools/tp_sweep.py measured as turning +43.7 net R
into -2.1. This test is what stops that recurring.

TWO NAMINGS ARE INTENTIONAL. The strategy replay has called these TP1/TP2/TP3
since it was written and still does: it is an inspection view whose own panel
states, in the same frame, that the rule has no take-profit. The live chart has
no panel beside that line, so it takes `refLabel` -- "2R ref". Same levels, same
arithmetic, different name on the line, and both are pinned here.
"""
import json
import os
import pathlib
import subprocess

import pytest

from sim.targets import BAND_HALF_R, CONDEMNED_BELOW_R, REF_LADDER, label, ref_levels

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, 'js', 'chart', 'targets.js')
ENTRY, STOP = 4587.35, 4144.31


def _node():
    for exe in ('node', 'node.exe'):
        try:
            subprocess.run([exe, '--version'], capture_output=True, check=True)
            return exe
        except (OSError, subprocess.CalledProcessError):
            continue
    return None


def _run(src):
    node = _node()
    if node is None:
        pytest.skip('node not available')
    r = subprocess.run([node, '--input-type=module', '-e', src],
                       capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _js(body):
    return 'import {TP_CENTRES, BAND_HALF_R, targetBands, refLabel} from %s;\n%s' % (
        json.dumps(pathlib.Path(JS).as_uri()), body)


def test_no_multiple_is_in_the_condemned_range():
    """1R was measured to LOSE money. It must never be offered as a level."""
    for r, _ in REF_LADDER:
        assert r >= CONDEMNED_BELOW_R, (
            '%gR is at or below the multiple tp_sweep condemned' % r)


def test_live_chart_naming_is_not_tp():
    """The live chart's line has no panel beside it to correct a wrong name."""
    for r, _ in REF_LADDER:
        assert 'TP' not in label(r).upper().replace('REF', ''), label(r)


def test_js_multiples_match_python():
    got = _run(_js(
        'const b = targetBands({side: 1, entry: %r, stop: %r, naming: "ref"});\n'
        'console.log(JSON.stringify({\n'
        '  multiples: TP_CENTRES.map(x => x.r),\n'
        '  half: BAND_HALF_R,\n'
        '  refLabels: b.map(x => x.label),\n'
        '  prices: b.map(x => [x.r, Number(x.price.toFixed(6))]),\n'
        '  noStop: targetBands({side: 1, entry: %r, stop: 0}).length,\n'
        '}));\n' % (ENTRY, STOP, ENTRY)))

    assert got['multiples'] == [r for r, _ in REF_LADDER]
    assert got['half'] == BAND_HALF_R
    assert got['refLabels'] == [label(r) for r, _ in REF_LADDER]
    assert got['noStop'] == 0, 'an unset stop (0.0) must yield no bands'

    want = ref_levels(ENTRY, STOP, 1)
    assert len(got['prices']) == len(want)
    for (jr, jp), (pr, pp, _) in zip(got['prices'], want):
        assert jr == pr
        assert abs(jp - pp) < 1e-6, (jr, jp, pp)


def test_replay_naming_is_untouched():
    """The strategy replay keeps TP1/TP2/TP3 and gets no label override."""
    got = _run(_js(
        'const b = targetBands({side: 1, entry: 100, stop: 90});\n'
        'console.log(JSON.stringify({keys: b.map(x => x.key),\n'
        '                            labels: b.map(x => x.label)}));\n'))
    assert got['keys'] == ['TP1', 'TP2', 'TP3']
    assert got['labels'] == [None, None, None]


def test_python_levels_need_a_real_stop():
    """MetaTrader reports an unset stop as 0.0; that is not zero risk."""
    assert ref_levels(ENTRY, 0.0, 1) == []
    assert ref_levels(ENTRY, 4600.0, 1) == []      # stop on the wrong side
    assert len(ref_levels(ENTRY, STOP, 1)) == len(REF_LADDER)

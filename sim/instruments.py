"""
instruments.py — instrument specs and bar loading for the simulator.

Every instrument-specific number lives in data/instruments.json (captured by
tools/capture_specs.py), never in engine code. Testing a new instrument is a row
in that file, not an edit here.
"""

import json
import os

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'data')
SPEC_PATH = os.path.join(DATA, 'instruments.json')

_cache = {}

REQUIRED = ('digits', 'point', 'contract_size', 'volume_min', 'volume_step',
            'volume_max', 'currency_profit', 'swap_mode')


def specs():
    """The whole snapshot, including capture time and account currency."""
    if 'doc' not in _cache:
        if not os.path.exists(SPEC_PATH):
            raise FileNotFoundError(
                f'{SPEC_PATH} missing — run: python tools/capture_specs.py')
        _cache['doc'] = json.load(open(SPEC_PATH, encoding='utf-8'))
    return _cache['doc']


def spec(symbol, tf=None):
    """One instrument's contract spec, validated, tagged with the symbol/tf."""
    doc = specs()
    if symbol not in doc['instruments']:
        raise KeyError(f'{symbol} not in instruments.json — add it to '
                       f'tools/capture_specs.py SYMBOLS and re-run')
    out = dict(doc['instruments'][symbol])
    missing = [k for k in REQUIRED if k not in out]
    if missing:
        raise ValueError(f'{symbol} spec missing {missing}')
    out['_symbol'] = symbol
    out['_tf'] = tf or ''
    return out


def account_currency():
    return specs().get('account_currency') or 'AUD'


def load(symbol, tf, start=None, end=None):
    """Bars for the simulator: OHLC plus per-bar spread, in ascending time."""
    from tools.dataset import load_bars
    df = load_bars(symbol, tf, start, end)
    return df


def load_bars_for_fx(symbol, freq='1h'):
    """
    Bars used to build conversion rates. Hourly by default — daily closes were
    measurably too coarse when reconciling against the broker (see sim/fx.py).
    Falls back to daily if the finer timeframe was never downloaded.
    """
    from tools.dataset import load_bars
    try:
        return load_bars(symbol, freq)
    except FileNotFoundError:
        return load_bars(symbol, '1d')


def available(symbol):
    """Which timeframes are on disk for a symbol."""
    base = os.path.join(DATA, 'bars', symbol)
    if not os.path.isdir(base):
        return []
    return sorted(d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d)))


def data_hash(symbol, tf):
    """
    Cheap fingerprint of the input files, stamped into every run so a result can
    be tied to the exact history that produced it.
    """
    import hashlib
    base = os.path.join(DATA, 'bars', symbol, tf)
    h = hashlib.sha256()
    for name in sorted(os.listdir(base)):
        path = os.path.join(base, name)
        st = os.stat(path)
        h.update(name.encode())
        h.update(str(st.st_size).encode())
    return h.hexdigest()[:16]

#!/usr/bin/env python
"""
mt5_bridge.py — read-only MetaTrader 5 -> localhost JSON bridge for Nur AI.

MT5 has no web API. This process talks to a MetaTrader 5 terminal running on
this machine via MetaQuotes' official Python package and serves the result as
JSON on 127.0.0.1, so the Nur AI page can show your account, positions,
pending orders and real broker tick data.

Design rules, deliberately:

*   READ ONLY. There is no order_send, no modify, no close, no endpoint that can
    move money or change a position. The bridge cannot trade even if something
    else on this machine reaches it.
*   LOCALHOST ONLY. It binds 127.0.0.1, never 0.0.0.0, and CORS is limited to the
    page's own origin, so no website you visit can read your account.
*   NO CREDENTIALS IN CODE OR CHAT. Credentials come from environment variables
    or bridge/mt5_config.json (gitignored), both of which you fill in yourself.
    Best of all, supply none: leave your terminal logged in and the bridge simply
    attaches to the session already running.

Usage:
    python bridge/mt5_bridge.py                 # attach to the running terminal
    python bridge/mt5_bridge.py --mock          # fake data, no terminal needed
    python bridge/mt5_bridge.py --port 8765

Requires:  pip install MetaTrader5      (Windows only — so is MT5)
"""

import argparse
import csv
import json
import math
import os
import random
import re
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from urllib.request import Request, urlopen

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CONFIG_PATH = os.path.join(HERE, 'mt5_config.json')
sys.path.insert(0, ROOT)

# One implementation of the server-clock guard, shared with tools/mt5_download.py.
# It used to live in both places and the copies drifted; see sim/clock.py.
from sim.clock import manifest_offset, resolve_offset    # noqa: E402

DEFAULT_ORIGINS = [
    'http://localhost:5173', 'http://127.0.0.1:5173',
    'http://localhost:8000', 'http://127.0.0.1:8000',
]

# MT5 timeframe constants are resolved lazily so --mock works without the package
TF_NAMES = ['1m', '5m', '15m', '30m', '1h', '4h', '1d', '1w']

mt5 = None            # the MetaTrader5 module, imported on demand
MT5_LOCK = threading.Lock()   # the MT5 API is not thread-safe
STATE = {
    'mock': False,
    'connected': False,
    'error': None,
    'login': None,
    'server': None,
    'terminal': None,
    'time_offset_ms': 0,          # broker server clock - true UTC
    'offset_confidence': 'none',
}

# Bulk history download, run in a worker thread.
#
# READ ONLY still holds: it calls copy_rates_range / copy_ticks_range and writes
# CSV files under data/. Nothing here can place, modify or close an order.
#
# The worker holds MT5_LOCK for the whole job, by design: MetaTrader's Python IPC
# is one conversation at a time, and interleaving a multi-gigabyte pull with
# per-second quote polling killed the session once already. The consequence is
# that /quotes, /bars, /ticks and /positions BLOCK while a download runs, so
# /download/status is deliberately lock-free -- the page reads it to pause its
# own polling and show progress instead of stacking up timeouts.
DOWNLOAD = {
    'running': False, 'phase': None, 'symbol': None, 'tf': None, 'item': None,
    'done': 0, 'total': 0, 'rows': 0, 'note': None,
    'started_ms': None, 'finished_ms': None, 'error': None, 'cancel': False,
    'log': [],
}
DOWNLOAD_LOCK = threading.Lock()

# --------------------------------------------------------------------------- #
# audit log                                                                   #
# --------------------------------------------------------------------------- #
# Everything that CHANGES something on disk gets a line here, kept by date.
# This exists because 40 run folders once disappeared and there was no way to
# tell what removed them: request logging was off by default, so there was no
# evidence either way. Reads (quotes, bars, ticks) are deliberately not logged —
# they arrive every second and would bury the events that matter.
LOG_DIR = os.path.join(os.path.dirname(HERE), 'logs')
LOG_LOCK = threading.Lock()


def log_event(kind, message):
    """
    Append one line to logs/bridge-YYYY-MM-DD.log. Never raises.

    Stamped in LOCAL time, because the reader is a person sitting in front of
    this machine trying to match a log line to something they just saw happen.
    The offset is written out (2026-08-22T21:11:34+10:00), so a line is still
    unambiguous when read from anywhere else -- and the third clock in this
    project, the broker's, never appears here at all.
    """
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        now = datetime.now().astimezone()
        path = os.path.join(LOG_DIR, 'bridge-%s.log' % now.strftime('%Y-%m-%d'))
        line = '%s  %-9s %s\n' % (now.isoformat(timespec='seconds'), kind, message)
        with LOG_LOCK:
            with open(path, 'a', encoding='utf-8') as fh:
                fh.write(line)
    except Exception:                                     # noqa: BLE001
        pass                                              # logging must never break serving

# Local job runner for backtests and reindexing.
#
# Unlike a download, a backtest never touches MetaTrader -- it reads the CSV
# files under data/ -- so this deliberately does NOT take MT5_LOCK and the live
# chart keeps ticking while it runs.
#
# Only these fixed commands can be launched. There is no shell and no path from
# a query parameter to an executable: the kind selects an argv template and the
# remaining parameters are appended as validated flags.
JOB_KINDS = {
    'backtest': ['-m', 'sim.run'],
    'backtest_tl': ['-m', 'sim.run_tl'],
    'stage1': ['tools/stage1.py'],
    'reindex': ['tools/reindex_runs.py'],
}
JOB = {
    'running': False, 'kind': None, 'cmd': None, 'done': 0, 'total': 0,
    'log': [], 'started_ms': None, 'finished_ms': None,
    'error': None, 'exit_code': None, 'cancel': False,
}
JOB_LOCK = threading.Lock()
JOB_PROC = {'p': None}


# --------------------------------------------------------------------------- #
# configuration                                                               #
# --------------------------------------------------------------------------- #
def load_config():
    """Credentials from the environment first, then mt5_config.json, else none."""
    cfg = {
        'login': os.environ.get('MT5_LOGIN'),
        'password': os.environ.get('MT5_PASSWORD'),
        'server': os.environ.get('MT5_SERVER'),
        'path': os.environ.get('MT5_PATH'),
    }
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as fh:
                disk = json.load(fh)
            for key in cfg:
                if not cfg[key] and disk.get(key):
                    cfg[key] = disk[key]
        except Exception as exc:                      # noqa: BLE001
            print('! could not read %s: %s' % (CONFIG_PATH, exc))
    if cfg['login']:
        try:
            cfg['login'] = int(str(cfg['login']).strip())
        except ValueError:
            print('! MT5 login must be numeric — ignoring it')
            cfg['login'] = None
    return cfg


# --------------------------------------------------------------------------- #
# MT5 session                                                                 #
# --------------------------------------------------------------------------- #
def mt5_connect(cfg):
    """Initialise the terminal link. Returns True on success."""
    global mt5
    try:
        import MetaTrader5 as _mt5
    except ImportError:
        STATE['error'] = ('MetaTrader5 package not installed — run: '
                          'pip install MetaTrader5')
        return False
    mt5 = _mt5

    # Connection ladder, cheapest and safest first.
    #
    # Passing login/password/server to initialize() asks MT5 to LAUNCH a terminal
    # and log it in. If a terminal is already running on that data folder, the
    # launch cannot happen and it fails with -10003 "IPC initialize failed,
    # Process create failed" — which looks like a credential problem but is not.
    # Attaching to the running, already-logged-in terminal works and never touches
    # the password, so it is tried first.
    has_creds = bool(cfg.get('login') and cfg.get('password') and cfg.get('server'))
    attempts = [({}, 'attach to the running terminal')]
    if cfg.get('path'):
        attempts.append(({'path': cfg['path']}, 'attach via the configured terminal path'))
    if has_creds:
        full = {'login': cfg['login'], 'password': cfg['password'], 'server': cfg['server']}
        if cfg.get('path'):
            full['path'] = cfg['path']
        attempts.append((full, 'launch a terminal and log in with configured credentials'))

    errors = []
    ok = False
    with MT5_LOCK:
        for kwargs, label in attempts:
            if mt5.initialize(**kwargs):
                ok = True
                print('+ %s: ok' % label)
                break
            errors.append('%s -> %s' % (label, mt5.last_error()))
            print('- %s failed: %s' % (label, mt5.last_error()))
            mt5.shutdown()

        if not ok:
            STATE['error'] = 'could not reach MetaTrader 5. ' + ' | '.join(errors)
            return False

        info = mt5.account_info()
        term = mt5.terminal_info()

        # Attached to a different account than the one configured? Switch on the
        # existing connection rather than trying to start a second terminal.
        if info is not None and has_creds and int(info.login) != int(cfg['login']):
            print('* attached account %s differs from configured %s — switching'
                  % (info.login, cfg['login']))
            if mt5.login(cfg['login'], password=cfg['password'], server=cfg['server']):
                info = mt5.account_info()
            else:
                print('! login switch failed: %s (staying on %s)'
                      % (mt5.last_error(), info.login))

    if info is None:
        STATE['error'] = ('terminal reachable but no account is logged in — '
                          'log in inside MetaTrader 5, then restart the bridge')
        return False

    if not getattr(term, 'trade_allowed', True):
        print('* note: "Algo Trading" is disabled in the terminal. Reading works; '
              'that toggle only affects order sending, which this bridge cannot do.')

    STATE.update(connected=True, error=None, login=info.login,
                 server=info.server,
                 terminal=getattr(term, 'name', None))
    measure_clock_offset()
    print('+ connected: account %s on %s (%s %s)'
          % (info.login, info.server, info.name, info.currency))
    return True


def measure_clock_offset():
    """
    MT5 timestamps are in the broker's server time, not UTC. Estimate the offset
    from the freshest tick we can find so the page can plot broker bars on the
    same axis as everything else.

    The previous version rejected a tick only when it read more than two minutes
    OLD, which is one-sided: a stale tick at an eastern broker still reads as a
    future timestamp and sailed straight through. Worse, on rejection it fell
    back to an offset of ZERO -- so restarting the bridge on a weekend silently
    moved every displayed time by three hours. It now seeds from the offset
    measured while the market was open (data/manifest.json) and keeps it.
    """
    probes = ['EURUSD', 'GBPUSD', 'USDJPY', 'XAUUSD', 'BTCUSD']
    ticks = []
    with MT5_LOCK:
        for name in probes:
            resolved = _resolve_one(name)
            if not resolved:
                continue
            tick = mt5.symbol_info_tick(resolved)
            if tick and tick.time_msc:
                ticks.append(tick.time_msc)

    known, known_confidence = manifest_offset(os.path.join(ROOT, 'data'))
    offset, confidence = resolve_offset(ticks, time.time() * 1000,
                                        known, known_confidence)
    STATE.update(time_offset_ms=offset, offset_confidence=confidence)
    log_event('clock', 'server offset %+d ms (UTC%+.2fh) confidence=%s from %d tick(s)'
              % (offset, offset / 3600000.0, confidence, len(ticks)))
    if confidence == 'stale':
        print('* clock    : market shut, keeping UTC%+.2fh measured while open'
              % (offset / 3600000.0))
    else:
        print('* clock    : broker runs UTC%+.2fh (%s)' % (offset / 3600000.0, confidence))


def _resolve_one(name):
    """Map a plain name (EURUSD) to this broker's symbol (EURUSD, EURUSD.a, …)."""
    if mt5 is None:
        return None
    info = mt5.symbol_info(name)
    if info is not None:
        if not info.visible:
            mt5.symbol_select(name, True)
        return info.name
    upper = name.upper()
    candidates = [s for s in (mt5.symbols_get() or [])
                  if s.name.upper().startswith(upper)]
    if not candidates:
        return None
    # prefer the shortest match, i.e. the plain contract over exotic variants
    best = sorted(candidates, key=lambda s: (len(s.name), s.name))[0]
    if not best.visible:
        mt5.symbol_select(best.name, True)
    return best.name


def tf_const(name):
    table = {
        '1m': mt5.TIMEFRAME_M1, '5m': mt5.TIMEFRAME_M5,
        '15m': mt5.TIMEFRAME_M15, '30m': mt5.TIMEFRAME_M30,
        '1h': mt5.TIMEFRAME_H1, '4h': mt5.TIMEFRAME_H4,
        '1d': mt5.TIMEFRAME_D1, '1w': mt5.TIMEFRAME_W1,
    }
    return table.get(name)


# --------------------------------------------------------------------------- #
# real data readers                                                           #
# --------------------------------------------------------------------------- #
POS_TYPE = {0: 'buy', 1: 'sell'}
ORDER_TYPE = {
    0: 'buy', 1: 'sell', 2: 'buy_limit', 3: 'sell_limit',
    4: 'buy_stop', 5: 'sell_stop', 6: 'buy_stop_limit', 7: 'sell_stop_limit',
}


def read_account():
    with MT5_LOCK:
        a = mt5.account_info()
    if a is None:
        raise RuntimeError('no account')
    return {
        'login': a.login, 'server': a.server, 'name': a.name,
        'currency': a.currency, 'leverage': a.leverage,
        'balance': a.balance, 'equity': a.equity, 'profit': a.profit,
        'margin': a.margin, 'margin_free': a.margin_free,
        'margin_level': a.margin_level, 'credit': a.credit,
        'trade_allowed': bool(a.trade_allowed),
    }


def read_positions():
    with MT5_LOCK:
        rows = mt5.positions_get() or []
    out = []
    for p in rows:
        out.append({
            'ticket': p.ticket, 'symbol': p.symbol,
            'side': POS_TYPE.get(p.type, str(p.type)),
            'volume': p.volume, 'price_open': p.price_open,
            'price_current': p.price_current, 'sl': p.sl, 'tp': p.tp,
            'profit': p.profit, 'swap': p.swap, 'comment': p.comment,
            'time_ms': int(p.time_msc) - STATE['time_offset_ms'],
        })
    return out


def read_orders():
    with MT5_LOCK:
        rows = mt5.orders_get() or []
    out = []
    for o in rows:
        out.append({
            'ticket': o.ticket, 'symbol': o.symbol,
            'side': ORDER_TYPE.get(o.type, str(o.type)),
            'volume': o.volume_current, 'price_open': o.price_open,
            'sl': o.sl, 'tp': o.tp, 'comment': o.comment,
            'time_ms': int(o.time_setup_msc) - STATE['time_offset_ms'],
        })
    return out


def read_deals(days):
    # same timezone rule as read_ticks: tz-aware UTC on the broker's clock
    server_now = time.time() + STATE['time_offset_ms'] / 1000.0
    base = datetime.fromtimestamp(server_now, tz=timezone.utc)
    to = base + timedelta(days=1)
    frm = base - timedelta(days=days)
    with MT5_LOCK:
        rows = mt5.history_deals_get(frm, to) or []
    out = []
    for d in rows:
        out.append({
            'ticket': d.ticket, 'order': d.order, 'symbol': d.symbol,
            'side': POS_TYPE.get(d.type, str(d.type)),
            'volume': d.volume, 'price': d.price, 'profit': d.profit,
            'commission': d.commission, 'swap': d.swap, 'comment': d.comment,
            # position_id and entry are what make a deal pairable: this is a
            # hedging account, so several positions in one symbol are open at
            # once and FIFO matching pairs the wrong legs together.
            # entry: 0 = opened, 1 = closed, 2 = reversed, 3 = closed by an opposite
            'position_id': d.position_id, 'entry': d.entry,
            'time_ms': int(d.time_msc) - STATE['time_offset_ms'],
        })
    out.sort(key=lambda r: r['time_ms'], reverse=True)
    return out


def read_quotes(names):
    out = {}
    with MT5_LOCK:
        for name in names:
            resolved = _resolve_one(name)
            if not resolved:
                continue
            t = mt5.symbol_info_tick(resolved)
            info = mt5.symbol_info(resolved)
            if t is None:
                continue
            out[name] = {
                'symbol': resolved, 'bid': t.bid, 'ask': t.ask,
                'last': t.last or ((t.bid + t.ask) / 2 if t.bid and t.ask else 0),
                'digits': getattr(info, 'digits', 5),
                'point': getattr(info, 'point', 0.00001),
                'time_ms': int(t.time_msc) - STATE['time_offset_ms'],
            }
    return out


def read_ticks(name, from_ms, limit):
    """Real broker ticks newer than from_ms (bridge-corrected epoch ms)."""
    with MT5_LOCK:
        resolved = _resolve_one(name)
        if not resolved:
            return {'symbol': None, 'ticks': []}
        # TZ TRAP: the MetaTrader5 package treats a NAIVE datetime as LOCAL time
        # and converts it, so on a UTC+10 machine a naive query silently asks for
        # data ten hours in the past (measured: naive returned ticks 10h stale,
        # tz-aware returned ticks 0.4s old). Always pass tz-aware UTC datetimes,
        # built from the BROKER's wall clock since MT5 stores server time.
        server_from = (from_ms + STATE['time_offset_ms']) / 1000.0
        frm = datetime.fromtimestamp(server_from, tz=timezone.utc)
        to = frm + timedelta(seconds=60)
        rows = mt5.copy_ticks_range(resolved, frm, to, mt5.COPY_TICKS_ALL)
        info = mt5.symbol_info(resolved)

    ticks = []
    if rows is not None:
        prev_mid = None
        for r in rows:
            ts = int(r['time_msc']) - STATE['time_offset_ms']
            if ts <= from_ms:
                continue
            bid, ask = float(r['bid']), float(r['ask'])
            last = float(r['last']) or ((bid + ask) / 2 if bid and ask else bid)
            mid = (bid + ask) / 2 if bid and ask else last
            flags = int(r['flags'])
            # 8 = TICK_FLAG_BUY, 16 = TICK_FLAG_SELL. FX feeds are usually
            # quote-only, so fall back to an uptick/downtick classification.
            if flags & 8:
                side = 'buy'
            elif flags & 16:
                side = 'sell'
            elif prev_mid is not None:
                side = 'buy' if mid > prev_mid else 'sell' if mid < prev_mid else 'buy'
            else:
                side = 'buy'
            prev_mid = mid
            ticks.append({
                'ts': ts, 'price': last, 'bid': bid, 'ask': ask,
                'size': float(r['volume_real'] or r['volume'] or 0),
                'side': side,
            })
    ticks = ticks[-limit:]
    return {
        'symbol': resolved,
        'digits': getattr(info, 'digits', 5),
        'point': getattr(info, 'point', 0.00001),
        'ticks': ticks,
    }


def read_bars(name, tf, count, months=0):
    """
    Recent bars by count, or a date range when `months` is set.

    Range mode exists for backtesting: 12 months of M15 is ~23,600 bars, far past
    what a count-based fetch is used for, and MetaTrader only holds history it has
    been asked to download — so the caller is told how much actually came back
    rather than silently getting a short series.
    """
    with MT5_LOCK:
        resolved = _resolve_one(name)
        if not resolved:
            return {'symbol': None, 'bars': []}
        if months and months > 0:
            to = datetime.now(timezone.utc)
            frm = to - timedelta(days=int(round(months * 30.44)))
            rows = mt5.copy_rates_range(resolved, tf_const(tf), frm, to)
            if rows is None or len(rows) == 0:
                # nudge the terminal into downloading, then retry once
                mt5.copy_rates_from_pos(resolved, tf_const(tf), 0, 100000)
                rows = mt5.copy_rates_range(resolved, tf_const(tf), frm, to)
        else:
            rows = mt5.copy_rates_from_pos(resolved, tf_const(tf), 0, count)
        info = mt5.symbol_info(resolved)
    bars = []
    for r in (rows if rows is not None else []):
        bars.append({
            't': int(r['time']) * 1000 - STATE['time_offset_ms'],
            'o': float(r['open']), 'h': float(r['high']),
            'l': float(r['low']), 'c': float(r['close']),
            'v': float(r['real_volume'] or r['tick_volume'] or 0),
            'ticks': int(r['tick_volume'] or 0),
        })
    span_days = 0
    if bars:
        span_days = round((bars[-1]['t'] - bars[0]['t']) / 86400000.0, 1)
    return {
        'symbol': resolved,
        'digits': getattr(info, 'digits', 5),
        'count': len(bars),
        'span_days': span_days,
        'bars': bars,
    }


def read_spec(name):
    """
    Contract specification for position sizing: tick value, contract size, volume
    limits, stop distance rules, and the margin one lot requires.

    order_calc_margin() is a pure calculation — it asks the terminal what a
    hypothetical position WOULD cost. It sends nothing and cannot open anything.
    """
    with MT5_LOCK:
        resolved = _resolve_one(name)
        if not resolved:
            return {'symbol': None}
        info = mt5.symbol_info(resolved)
        tick = mt5.symbol_info_tick(resolved)
        if info is None:
            return {'symbol': None}

        margin_per_lot = None
        try:
            price = (tick.ask if tick and tick.ask else info.ask) or 0
            if price:
                margin_per_lot = mt5.order_calc_margin(
                    mt5.ORDER_TYPE_BUY, resolved, 1.0, price)
        except Exception:                                  # noqa: BLE001
            margin_per_lot = None

    return {
        'symbol': resolved,
        'digits': info.digits,
        'point': info.point,
        'tick_size': info.trade_tick_size or info.point,
        'tick_value': info.trade_tick_value,          # account currency per tick, 1 lot
        'contract_size': info.trade_contract_size,
        'volume_min': info.volume_min,
        'volume_max': info.volume_max,
        'volume_step': info.volume_step,
        'stops_level_points': info.trade_stops_level,  # minimum stop distance
        'freeze_level_points': info.trade_freeze_level,
        'spread_current': (info.ask - info.bid) if (info.ask and info.bid) else None,
        'margin_per_lot': margin_per_lot,
        'currency_base': info.currency_base,
        'currency_profit': info.currency_profit,
        'currency_margin': info.currency_margin,
        'trade_mode': info.trade_mode,                 # 0 = disabled, 4 = full
        'session_deals': info.session_deals,
        # Carry, which the simulator needs to price anything held overnight.
        # These used to be reachable only through tools/capture_specs.py, and
        # that script calls mt5.shutdown() -- which, run beside a live bridge,
        # tears down the terminal session both processes share. Serving them
        # here means a new instrument can be specced without that risk.
        'swap_mode': info.swap_mode,
        'swap_long_points': info.swap_long,
        'swap_short_points': info.swap_short,
        'swap_rollover_3days_weekday': info.swap_rollover3days,
        'spread_points_now': info.spread,
        'margin_initial': info.margin_initial,
    }


def read_symbols():
    with MT5_LOCK:
        rows = mt5.symbols_get() or []
    return [{'name': s.name, 'path': s.path, 'digits': s.digits,
             'visible': bool(s.visible)}
            for s in rows if s.visible][:500]


# --------------------------------------------------------------------------- #
# external free data: economic calendar and CFTC positioning                  #
# --------------------------------------------------------------------------- #
#
# Both are fetched HERE rather than in the page, because neither sends CORS
# headers a browser would accept. They are cached so a reload does not hammer
# somebody else's server: the calendar changes weekly, COT is published Friday.
#
_HTTP_CACHE = {}


def _fetch(url, ttl, binary=False):
    hit = _HTTP_CACHE.get(url)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    req = Request(url, headers={'User-Agent': 'NurAI/1.0 (local research tool)'})
    with urlopen(req, timeout=20) as r:
        data = r.read()
    out = data if binary else data.decode('utf-8', 'replace')
    _HTTP_CACHE[url] = (time.time(), out)
    return out


CALENDAR_URL = 'https://nfs.faireconomy.media/ff_calendar_thisweek.json'


def read_calendar():
    """This week's economic events, normalised to UTC epoch milliseconds."""
    raw = _fetch(CALENDAR_URL, 900)
    events = []
    for e in json.loads(raw):
        stamp = e.get('date')
        ts = None
        if stamp:
            try:
                # e.g. 2026-08-16T18:30:00-04:00
                dt = datetime.fromisoformat(stamp)
                ts = int(dt.timestamp() * 1000)
            except ValueError:
                ts = None
        events.append({
            'ts': ts,
            'currency': e.get('country'),
            'title': e.get('title'),
            'impact': (e.get('impact') or '').lower(),
            'forecast': e.get('forecast'),
            'previous': e.get('previous'),
        })
    events = [e for e in events if e['ts']]
    events.sort(key=lambda x: x['ts'])
    return {'events': events, 'source': 'ForexFactory weekly calendar'}


COT_URL = 'https://www.cftc.gov/dea/newcot/deafut.txt'

# Legacy futures-only report. For USDXXX pairs the contract is the FOREIGN
# currency, so a net-long reading there is bearish for the pair — hence `invert`.
COT_MARKETS = {
    'EURUSD': ('EURO FX - CHICAGO MERCANTILE EXCHANGE', False),
    'GBPUSD': ('BRITISH POUND - CHICAGO MERCANTILE EXCHANGE', False),
    'AUDUSD': ('AUSTRALIAN DOLLAR - CHICAGO MERCANTILE EXCHANGE', False),
    'NZDUSD': ('NEW ZEALAND DOLLAR - CHICAGO MERCANTILE EXCHANGE', False),
    'USDJPY': ('JAPANESE YEN - CHICAGO MERCANTILE EXCHANGE', True),
    'USDCAD': ('CANADIAN DOLLAR - CHICAGO MERCANTILE EXCHANGE', True),
    'USDCHF': ('SWISS FRANC - CHICAGO MERCANTILE EXCHANGE', True),
    'XAUUSD': ('GOLD - COMMODITY EXCHANGE INC.', False),
}


def read_cot(symbol):
    """
    Weekly speculative positioning from the CFTC Commitments of Traders.

    This is real money's net exposure in the futures, published with a Tuesday
    as-of date each Friday — so it is genuine positioning data, but it is DAYS
    OLD and weekly. It is context, never a trigger.
    """
    base = (symbol or '').split('.')[0].upper()
    spec = COT_MARKETS.get(base)
    if not spec:
        return {'symbol': base, 'available': False,
                'note': 'no CFTC contract mapped for this symbol'}
    market, invert = spec

    raw = _fetch(COT_URL, 6 * 3600)
    for row in csv.reader(raw.splitlines()):
        if not row or row[0].strip().strip('"') != market:
            continue
        try:
            as_of = row[2].strip()
            oi = float(row[7])
            nc_long = float(row[8])
            nc_short = float(row[9])
        except (IndexError, ValueError):
            break
        net = nc_long - nc_short
        if invert:
            net = -net
        pct_oi = (net / oi * 100) if oi else 0.0
        long_share = (nc_long / (nc_long + nc_short) * 100) if (nc_long + nc_short) else 0.0
        if invert:
            long_share = 100 - long_share
        return {
            'symbol': base, 'available': True, 'market': market,
            'as_of': as_of, 'open_interest': oi,
            'net': net, 'pct_of_oi': pct_oi, 'long_share': long_share,
            'inverted': invert,
            'bias': 'long' if net > 0 else 'short' if net < 0 else 'flat',
            'note': ('speculative net is ' + ('LONG' if net > 0 else 'SHORT') +
                     ' this pair as of ' + as_of + ' — weekly data, days old, context only')
        }
    return {'symbol': base, 'available': False,
            'note': 'contract not found in the current CFTC report'}


# --------------------------------------------------------------------------- #
# mock mode — exercises the UI with no terminal and no credentials             #
# --------------------------------------------------------------------------- #
class Mock:
    SPEC = {
        'EURUSD': (1.0865, 5, 0.000055), 'GBPUSD': (1.2735, 5, 0.000065),
        'USDJPY': (157.42, 3, 0.0075), 'AUDUSD': (0.6642, 5, 0.00005),
        'USDCAD': (1.3718, 5, 0.00005), 'NZDUSD': (0.6105, 5, 0.00005),
        'XAUUSD': (2412.50, 2, 0.22),
    }

    def __init__(self):
        self.price = {k: v[0] for k, v in self.SPEC.items()}
        self.last_emit = {k: time.time() * 1000 for k in self.SPEC}
        self.positions = [
            {'ticket': 300111001, 'symbol': 'EURUSD', 'side': 'buy',
             'volume': 0.50, 'price_open': 1.08412, 'sl': 1.08100, 'tp': 1.09050,
             'comment': 'mock', 'swap': -0.42},
            {'ticket': 300111002, 'symbol': 'XAUUSD', 'side': 'sell',
             'volume': 0.10, 'price_open': 2418.80, 'sl': 2431.00, 'tp': 2396.50,
             'comment': 'mock', 'swap': -1.15},
            {'ticket': 300111003, 'symbol': 'USDJPY', 'side': 'buy',
             'volume': 0.25, 'price_open': 157.105, 'sl': 0.0, 'tp': 157.900,
             'comment': 'mock', 'swap': 0.31},
        ]
        self.orders = [
            {'ticket': 300111010, 'symbol': 'GBPUSD', 'side': 'buy_limit',
             'volume': 0.30, 'price_open': 1.26900, 'sl': 1.26550,
             'tp': 1.27600, 'comment': 'mock pending'},
            {'ticket': 300111011, 'symbol': 'EURUSD', 'side': 'sell_stop',
             'volume': 0.20, 'price_open': 1.08200, 'sl': 1.08480,
             'tp': 1.07700, 'comment': 'mock pending'},
        ]

    def step(self, symbol):
        seed, digits, vol = self.SPEC[symbol]
        g = (random.random() + random.random() + random.random() - 1.5) / 1.5
        self.price[symbol] = max(vol, self.price[symbol] + g * vol
                                 + (seed - self.price[symbol]) * 0.0004)
        return round(self.price[symbol], digits)

    def quote(self, symbol):
        seed, digits, vol = self.SPEC[symbol]
        px = self.step(symbol)
        point = 10 ** -digits
        spread = point * random.randint(6, 22)
        return {'symbol': symbol, 'bid': round(px - spread / 2, digits),
                'ask': round(px + spread / 2, digits), 'last': px,
                'digits': digits, 'point': point,
                'time_ms': int(time.time() * 1000)}

    def ticks(self, symbol, from_ms, limit):
        if symbol not in self.SPEC:
            return {'symbol': None, 'ticks': []}
        seed, digits, vol = self.SPEC[symbol]
        now = time.time() * 1000
        start = max(from_ms, now - 5000)
        out = []
        t = start + random.randint(80, 400)
        prev = self.price[symbol]
        while t < now and len(out) < limit:
            px = self.step(symbol)
            point = 10 ** -digits
            spread = point * random.randint(6, 22)
            out.append({'ts': int(t), 'price': px,
                        'bid': round(px - spread / 2, digits),
                        'ask': round(px + spread / 2, digits),
                        'size': round(random.uniform(0.05, 6), 2),
                        'side': 'buy' if px >= prev else 'sell'})
            prev = px
            t += random.randint(80, 620)
        return {'symbol': symbol, 'digits': digits,
                'point': 10 ** -digits, 'ticks': out}

    def bars(self, symbol, tf, count):
        if symbol not in self.SPEC:
            return {'symbol': None, 'bars': []}
        seed, digits, vol = self.SPEC[symbol]
        step = {'1m': 60, '5m': 300, '15m': 900, '30m': 1800, '1h': 3600,
                '4h': 14400, '1d': 86400, '1w': 604800}.get(tf, 60) * 1000
        sigma = vol * (step / 400.0) ** 0.35
        now = int(time.time() * 1000)
        start = (now - step * count) // step * step
        path, price = [0.0] * (count + 1), self.price[symbol]
        for i in range(count, -1, -1):
            path[i] = price
            g = (random.random() + random.random() + random.random() - 1.5) / 1.5
            price = max(vol, price - g * sigma)
            price += (seed - price) * 0.02
        bars = []
        for i in range(count):
            o, c = path[i], path[i + 1]
            wick = sigma * (0.5 + random.random())
            bars.append({'t': start + i * step, 'o': round(o, digits),
                         'h': round(max(o, c) + wick * random.random(), digits),
                         'l': round(min(o, c) - wick * random.random(), digits),
                         'c': round(c, digits),
                         'v': round(random.uniform(20, 200), 2),
                         'ticks': random.randint(4, 400)})
        return {'symbol': symbol, 'digits': digits, 'bars': bars}

    def account(self):
        floating = sum(self._pnl(p) for p in self.positions)
        balance = 25000.0
        return {'login': 0, 'server': 'MOCK-Demo', 'name': 'Mock Account',
                'currency': 'AUD', 'leverage': 30, 'balance': balance,
                'equity': round(balance + floating, 2),
                'profit': round(floating, 2), 'margin': 1842.55,
                'margin_free': round(balance + floating - 1842.55, 2),
                'margin_level': round((balance + floating) / 1842.55 * 100, 2),
                'credit': 0.0, 'trade_allowed': True}

    def _pnl(self, p):
        seed, digits, vol = self.SPEC[p['symbol']]
        px = self.price[p['symbol']]
        diff = (px - p['price_open']) * (1 if p['side'] == 'buy' else -1)
        # rough contract sizing, enough to make the UI move believably
        unit = 100000 if p['symbol'] not in ('XAUUSD',) else 100
        if p['symbol'] == 'USDJPY':
            return round(diff * unit * p['volume'] / px, 2)
        return round(diff * unit * p['volume'], 2)

    def read_positions(self):
        out = []
        for p in self.positions:
            digits = self.SPEC[p['symbol']][1]
            row = dict(p)
            row['price_current'] = round(self.price[p['symbol']], digits)
            row['profit'] = self._pnl(p)
            row['time_ms'] = int(time.time() * 1000) - 3600_000
            out.append(row)
        return out

    def read_orders(self):
        return [dict(o, time_ms=int(time.time() * 1000) - 7200_000)
                for o in self.orders]

    def read_deals(self, days):
        out = []
        now = int(time.time() * 1000)
        for i in range(14):
            sym = list(self.SPEC)[i % len(self.SPEC)]
            digits = self.SPEC[sym][1]
            out.append({
                'ticket': 299000000 + i, 'order': 299500000 + i, 'symbol': sym,
                'side': 'buy' if i % 2 else 'sell',
                'volume': round(0.1 * (1 + i % 4), 2),
                'price': round(self.price[sym] * (1 + (i % 5 - 2) / 1000), digits),
                'profit': round(random.uniform(-140, 220), 2),
                'commission': -round(random.uniform(0.5, 4), 2),
                'swap': -round(random.uniform(0, 2), 2),
                'comment': 'mock history',
                'time_ms': now - i * 5400_000,
            })
        return out


MOCK = Mock()


def job_worker(kind, argv, total, chain_reindex=True):
    """Run one allowlisted command, streaming its output into JOB['log']."""
    import subprocess
    root = os.path.dirname(HERE)
    cmd = [sys.executable] + JOB_KINDS[kind] + argv
    with JOB_LOCK:
        JOB.update(running=True, kind=kind, cmd=' '.join(cmd[1:]), done=0,
                   total=total, log=[], error=None, exit_code=None, cancel=False,
                   started_ms=int(time.time() * 1000), finished_ms=None)
    try:
        proc = subprocess.Popen(cmd, cwd=root, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, bufsize=1)
        JOB_PROC['p'] = proc
        for line in proc.stdout:
            line = line.rstrip()
            if not line:
                continue
            with JOB_LOCK:
                JOB['log'] = (JOB['log'] + [line])[-200:]
                # both runners print one line per completed cell
                if 'trades=' in line:
                    JOB['done'] += 1
                cancelled = JOB['cancel']
            log_event('job.out', line[:200])
            if cancelled:
                proc.terminate()
                break
        proc.wait(timeout=30)
        with JOB_LOCK:
            JOB['exit_code'] = proc.returncode
    except Exception as exc:                              # noqa: BLE001
        with JOB_LOCK:
            JOB['error'] = '%s: %s' % (type(exc).__name__, exc)
    finally:
        JOB_PROC['p'] = None

    # a fresh run is invisible to the page until the index is rebuilt
    if chain_reindex and kind != 'reindex':
        with JOB_LOCK:
            cancelled = JOB['cancel']
            JOB['log'] = (JOB['log'] + ['* reindexing runs...'])[-200:]
        if not cancelled:
            try:
                import subprocess as sp
                sp.run([sys.executable, 'tools/reindex_runs.py'], cwd=root,
                       capture_output=True, text=True, timeout=900)
            except Exception:                             # noqa: BLE001
                pass
    with JOB_LOCK:
        JOB.update(running=False, finished_ms=int(time.time() * 1000))
        log_event('job', 'finished kind=%s exit=%s done=%s/%s error=%s'
                  % (kind, JOB['exit_code'], JOB['done'], JOB['total'], JOB['error']))


def _dl_progress(**kw):
    with DOWNLOAD_LOCK:
        DOWNLOAD.update(kw)
        line = '%s %s %s %s (%d/%d)' % (kw.get('phase'), kw.get('symbol'),
                                        kw.get('tf'), kw.get('item'),
                                        kw.get('done', 0), kw.get('total', 0))
        DOWNLOAD['log'] = (DOWNLOAD['log'] + [line])[-40:]


def _dl_cancelled():
    with DOWNLOAD_LOCK:
        return bool(DOWNLOAD['cancel'])


def download_worker(symbols, tfs, years, days, want_bars, want_ticks):
    """Bulk pull on the bridge own MT5 session, holding the lock throughout."""
    sys.path.insert(0, os.path.dirname(HERE))
    try:
        import importlib

        from tools import mt5_download as dl
        # Reloaded, like _reindex_now does. A long-lived bridge caches the
        # module from its first import, so an edit to the download tool has no
        # effect until a restart -- which cost a re-download here, silently,
        # with the fix already on disk and the old code still running.
        dl = importlib.reload(dl)
    except Exception as exc:                              # noqa: BLE001
        with DOWNLOAD_LOCK:
            DOWNLOAD.update(running=False,
                            error='cannot import tools.mt5_download: %s' % exc,
                            finished_ms=int(time.time() * 1000))
        return

    rows = 0
    try:
        with MT5_LOCK:                       # one conversation with the terminal
            if want_bars:
                rows += dl.fetch_bars(symbols, tfs, years,
                                      progress=_dl_progress, cancelled=_dl_cancelled)
            if want_ticks and not _dl_cancelled():
                rows += dl.fetch_ticks(symbols, days,
                                       progress=_dl_progress, cancelled=_dl_cancelled)
            try:
                dl.write_manifest(None)
            except Exception:                             # noqa: BLE001
                pass
    except Exception as exc:                              # noqa: BLE001
        with DOWNLOAD_LOCK:
            DOWNLOAD['error'] = '%s: %s' % (type(exc).__name__, exc)
    finally:
        # The Backtest tab is a viewer over runs/index.json, a STATIC file. New
        # data on disk is invisible until that file is rebuilt, so a download
        # that did not reindex left the UI quietly showing the old inventory --
        # a freshly downloaded EURUSD simply never appeared, with nothing to say
        # why. Reindexing here makes the write and its index one operation.
        with DOWNLOAD_LOCK:
            DOWNLOAD.update(phase='indexing')
        note = _reindex_now()
        with DOWNLOAD_LOCK:
            DOWNLOAD.update(running=False, rows=rows,
                            finished_ms=int(time.time() * 1000),
                            phase='cancelled' if DOWNLOAD['cancel'] else 'done')
            if note:
                DOWNLOAD['note'] = note
            log_event('download', 'finished rows=%s phase=%s error=%s reindex=%s'
                      % (rows, DOWNLOAD['phase'], DOWNLOAD['error'], note or 'ok'))


def _reindex_now():
    """
    Rebuild runs/index.json in-process. Returns None on success, else a note.

    Deliberately not a subprocess: this runs inside the download worker's
    finally block, where a hung child would strand the MT5 lock.
    """
    try:
        root = os.path.dirname(HERE)
        sys.path.insert(0, root)
        import importlib

        from tools import reindex_runs
        importlib.reload(reindex_runs)
        reindex_runs.main()
        return None
    except Exception as exc:                              # noqa: BLE001
        return 'reindex failed (%s: %s) -- run tools/reindex_runs.py' % (
            type(exc).__name__, exc)


# --------------------------------------------------------------------------- #
# HTTP layer                                                                  #
# --------------------------------------------------------------------------- #
def signal_now(symbol, tf, strategy='donchian', position=None):
    """sim.signal.evaluate over the bars MT5 is serving right now.

    Imported lazily and RELOADED, for the reason recorded in _download_worker:
    a bridge that has been up for hours holds the module it first imported, so
    an edit to the rule would be invisible here until a restart -- and a signal
    service silently running last week's rule is worse than one that is down.

    `position` may be 'flat'/'long'/'short' to override what is held; without it
    the live position is read from the terminal, because the rule's answer
    genuinely depends on it (a breakout is an entry only when flat).
    """
    import importlib

    try:
        from sim import signal as sig_mod
        sig_mod = importlib.reload(sig_mod)
        from sim.fx import FX
        from sim.instruments import account_currency, spec as spec_of
        from sim.strategies import BASELINES
    except Exception as exc:                              # noqa: BLE001
        return {'error': 'sim import failed: %s' % exc}

    if strategy not in BASELINES:
        return {'error': 'unknown strategy %r' % strategy}

    payload = (MOCK.bars(symbol, tf, 600) if STATE['mock']
               else read_bars(symbol, tf, 600, 0))
    rows = payload.get('bars') or []
    if len(rows) < 120:
        return {'error': 'only %d bars' % len(rows), 'bars': len(rows)}

    import pandas as pd

    df = pd.DataFrame(rows)
    df['ts'] = pd.to_datetime(df['t'], unit='ms')
    df = df.set_index('ts')[['o', 'h', 'l', 'c']].rename(
        columns={'o': 'open', 'h': 'high', 'l': 'low', 'c': 'close'})
    # THE LAST BAR IS STILL FORMING. Deciding on it is look-ahead: the close
    # moves, so the signal can appear and vanish inside one bar.
    closed = df.iloc[:-1]
    if closed.empty:
        return {'error': 'no closed bars'}

    side, note = 0, 'flat'
    if position in ('flat', 'long', 'short'):
        side = {'flat': 0, 'long': 1, 'short': -1}[position]
        note = 'overridden: %s' % position
    else:
        # read_positions returns a LIST, not {'positions': [...]}
        legs = [p for p in (read_positions() or [])
                if (p.get('symbol') or '') == symbol]
        net = 0.0
        for leg in legs:
            v = float(leg.get('volume') or 0)
            net += v if str(leg.get('side')).lower() == 'buy' else -v
        side = 0 if abs(net) < 1e-9 else (1 if net > 0 else -1)
        note = ('flat' if not legs else
                '%d leg%s, net %+.2f lots' % (len(legs), '' if len(legs) == 1
                                              else 's', net))

    equity = 0.0
    try:
        equity = float((read_account() or {}).get('equity') or 0)
    except Exception:                                     # noqa: BLE001
        pass

    try:
        sp = spec_of(symbol, tf)
        fx = FX.build(account_currency())
    except Exception as exc:                              # noqa: BLE001
        return {'error': 'spec/fx failed: %s' % exc}

    offset = int(STATE.get('time_offset_ms') or 0)
    last_t = closed.index[-1]
    sig = sig_mod.evaluate(
        closed, BASELINES[strategy](), sp,
        equity=equity or None, risk_pct=float(STATE.get('risk_pct') or 0.5),
        fx=fx, position_side=side, symbol=symbol, tf=tf,
        bar_time_server=last_t + pd.Timedelta(milliseconds=offset))
    if sig is None:
        return {'error': 'not enough bars for %s' % strategy}

    out = dict(vars(sig))
    out['broker_position'] = note
    out['equity'] = equity
    out['instruction'] = sig.instruction()
    # never a number the caller could mistake for an order ticket
    out['read_only'] = True
    return out


class Handler(BaseHTTPRequestHandler):
    server_version = 'NurAI-MT5-Bridge/1.0'
    origins = list(DEFAULT_ORIGINS)

    # ---- plumbing ----
    def log_message(self, fmt, *args):      # quieter than the default
        if '--verbose' in sys.argv:
            sys.stderr.write('  %s\n' % (fmt % args))

    def _cors(self):
        origin = self.headers.get('Origin')
        if origin and origin in self.origins:
            self.send_header('Access-Control-Allow-Origin', origin)
        elif not origin:
            self.send_header('Access-Control-Allow-Origin', 'null')
        self.send_header('Vary', 'Origin')

    def _json(self, payload, status=200):
        body = json.dumps(payload, default=str).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):                   # noqa: N802
        self.send_response(204)
        self._cors()
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Access-Control-Max-Age', '600')
        self.end_headers()

    # ---- routes ----
    def do_GET(self):                       # noqa: N802
        url = urlparse(self.path)
        q = parse_qs(url.query)
        route = url.path.rstrip('/') or '/'
        one = lambda k, d=None: (q.get(k, [d]) or [d])[0]   # noqa: E731

        try:
            if route == '/runs/delete':
                # Deleting is the only destructive thing this bridge can do, so
                # it is fenced in: the name must look like a run id, must resolve
                # to a DIRECT child of runs/, and must contain a metrics.json --
                # i.e. it must prove it is a run folder and not something else.
                run_id = one('run_id', '') or ''
                root = os.path.dirname(HERE)
                runs_dir = os.path.join(root, 'runs')
                if not run_id or not re.fullmatch(r'[A-Za-z0-9_.\-]{3,120}', run_id):
                    return self._json({'error': 'bad run_id'}, 400)
                target = os.path.abspath(os.path.join(runs_dir, run_id))
                if os.path.dirname(target) != os.path.abspath(runs_dir):
                    return self._json({'error': 'run_id must name a direct child of runs/'}, 400)
                if not os.path.isdir(target):
                    return self._json({'error': 'no such run'}, 404)
                if not os.path.exists(os.path.join(target, 'metrics.json')):
                    return self._json({'error': 'not a run folder (no metrics.json)'}, 400)
                import shutil
                try:
                    contents = sorted(os.listdir(target))
                    shutil.rmtree(target)
                except OSError as exc:
                    log_event('delete', 'FAILED %s: %s' % (run_id, exc))
                    return self._json({'error': 'could not delete: %s' % exc}, 500)
                log_event('delete', 'removed run %s (%d files: %s)'
                          % (run_id, len(contents), ','.join(contents[:6])))
                # Reindex in the background: rebuilding the index over 40+ runs
                # takes seconds, and holding the response for it made the page sit
                # on 'deleting...' long after the folder was already gone.
                def _reindex():
                    try:
                        import subprocess as sp
                        sp.run([sys.executable, 'tools/reindex_runs.py'], cwd=root,
                               capture_output=True, text=True, timeout=900)
                    except Exception:                     # noqa: BLE001
                        pass
                threading.Thread(target=_reindex, daemon=True).start()
                return self._json({'ok': True, 'deleted': run_id,
                                   'reindex': 'started'})

            if route == '/job/status':
                with JOB_LOCK:
                    out = dict(JOB)
                out['pct'] = (round(100.0 * out['done'] / out['total'], 1)
                              if out['total'] else 0.0)
                return self._json(out)

            if route == '/job/cancel':
                with JOB_LOCK:
                    JOB['cancel'] = True
                log_event('job', 'cancel requested')
                proc = JOB_PROC.get('p')
                if proc is not None:
                    try:
                        proc.terminate()
                    except Exception:                     # noqa: BLE001
                        pass
                return self._json({'ok': True, 'cancelling': True})

            if route == '/job/start':
                kind = one('kind', '')
                if kind not in JOB_KINDS:
                    return self._json({'error': 'unknown job kind %r' % kind,
                                       'allowed': sorted(JOB_KINDS)}, 400)
                with JOB_LOCK:
                    if JOB['running']:
                        return self._json({'error': 'a job is already running'}, 409)

                argv, total = [], 0
                if kind in ('backtest', 'backtest_tl', 'stage1'):
                    symbols = [x for x in (one('symbols', '') or '').split(',') if x]
                    tfs = [x for x in (one('tfs', '') or '').split(',') if x]
                    strategy = one('strategy', '')
                    if symbols:
                        argv += ['--symbols', ','.join(symbols)]
                    if tfs:
                        argv += ['--tfs', ','.join(tfs)]
                    if one('start'):
                        argv += ['--start', one('start')]
                    if one('end'):
                        argv += ['--end', one('end')]
                    if kind == 'stage1':
                        if one('shifts'):
                            argv += ['--shifts', str(int(one('shifts')))]
                    else:
                        if strategy and strategy != 'all':
                            argv += ['--strategy', strategy]
                        else:
                            argv += ['--all']
                        if one('no_swap') in ('1', 'true', 'yes'):
                            argv += ['--no-swap']
                        if one('carry_free') in ('1', 'true', 'yes') and kind == 'backtest_tl':
                            argv += ['--carry-free']
                        if one('ab') in ('1', 'true', 'yes') and kind == 'backtest_tl':
                            argv += ['--ab']
                        if one('tag'):
                            argv += ['--tag', ''.join(ch for ch in one('tag')
                                                      if ch.isalnum() or ch in '-_')]
                    n_strat = 1 if (strategy and strategy != 'all') else (
                        2 if kind == 'backtest' else 3)
                    total = max(1, len(symbols or ['x']) * len(tfs or ['x']) * n_strat)
                    if kind == 'stage1':
                        total = max(1, len(symbols or ['x', 'y']) * len(tfs or ['x']) * 2)

                log_event('job', 'start kind=%s argv=%s cells=%s' % (kind, argv, total))
                threading.Thread(target=job_worker, daemon=True,
                                 args=(kind, argv, total)).start()
                return self._json({'ok': True, 'kind': kind, 'argv': argv,
                                   'expected_cells': total})

            # lock-free on purpose: it must answer while the worker holds MT5
            if route == '/download/status':
                with DOWNLOAD_LOCK:
                    out = dict(DOWNLOAD)
                out['pct'] = (round(100.0 * out['done'] / out['total'], 1)
                              if out['total'] else 0.0)
                if out['running'] and out['started_ms'] and out['done']:
                    elapsed = time.time() * 1000 - out['started_ms']
                    per = elapsed / max(out['done'], 1)
                    out['eta_sec'] = int(max(0, (out['total'] - out['done']) * per) / 1000)
                return self._json(out)

            if route == '/download/cancel':
                with DOWNLOAD_LOCK:
                    DOWNLOAD['cancel'] = True
                return self._json({'ok': True, 'cancelling': True})

            if route == '/download/start':
                with DOWNLOAD_LOCK:
                    if DOWNLOAD['running']:
                        return self._json({'error': 'a download is already running'}, 409)
                    symbols = [x for x in (one('symbols', '') or '').split(',') if x]
                    # No default: an empty tfs means "no bars, ticks only".
                    # parse_qs drops empty values, so a defaulted tfs silently
                    # turned a tick-only request into a 20-year bar pull.
                    tfs = [x for x in (one('tfs', '') or '').split(',') if x]
                    years = max(1, min(30, int(one('years', '20'))))
                    days = max(0, min(1200, int(one('days', '0'))))
                    want_bars = bool(tfs) and one('bars', '1') not in ('0', 'false', 'no')
                    want_ticks = days > 0
                    if not symbols:
                        return self._json({'error': 'symbols required'}, 400)
                    DOWNLOAD.update(running=True, cancel=False, error=None, note=None,
                                    phase='starting', symbol=None, tf=None, item=None,
                                    done=0, total=0, rows=0, log=[],
                                    started_ms=int(time.time() * 1000), finished_ms=None)
                log_event('download', 'start symbols=%s tfs=%s years=%s days=%s'
                          % (','.join(symbols), ','.join(tfs), years, days))
                threading.Thread(target=download_worker, daemon=True,
                                 args=(symbols, tfs, years, days, want_bars,
                                       want_ticks)).start()
                return self._json({'ok': True, 'symbols': symbols, 'tfs': tfs,
                                   'years': years, 'days': days,
                                   'note': 'live data calls block until this finishes'})

            if route == '/health':
                return self._json({
                    'ok': STATE['mock'] or STATE['connected'],
                    'mock': STATE['mock'],
                    'connected': STATE['connected'],
                    'error': STATE['error'],
                    'login': STATE['login'], 'server': STATE['server'],
                    'terminal': STATE['terminal'],
                    'time_offset_ms': STATE['time_offset_ms'],
                    'offset_confidence': STATE['offset_confidence'],
                    'read_only': True,
                    'timeframes': TF_NAMES,
                    'downloading': DOWNLOAD['running'],
                })

            if not (STATE['mock'] or STATE['connected']):
                return self._json({'error': STATE['error'] or 'not connected'}, 503)

            if route == '/account':
                return self._json(MOCK.account() if STATE['mock'] else read_account())

            if route == '/positions':
                rows = MOCK.read_positions() if STATE['mock'] else read_positions()
                return self._json({'positions': rows})

            if route == '/orders':
                rows = MOCK.read_orders() if STATE['mock'] else read_orders()
                return self._json({'orders': rows})

            if route == '/deals':
                days = max(1, min(365, int(one('days', '7'))))
                rows = MOCK.read_deals(days) if STATE['mock'] else read_deals(days)
                return self._json({'deals': rows})

            if route == '/spec':
                name = (one('symbol', '') or '').upper()
                if not name:
                    return self._json({'error': 'symbol required'}, 400)
                if STATE['mock']:
                    spec = Mock.SPEC.get(name)
                    if not spec:
                        return self._json({'symbol': None})
                    digits = spec[1]
                    return self._json({
                        'symbol': name, 'digits': digits, 'point': 10 ** -digits,
                        'tick_size': 10 ** -digits,
                        'tick_value': 1.0 if digits >= 4 else 0.68,
                        'contract_size': 100000 if not name.startswith('XAU') else 100,
                        'volume_min': 0.01, 'volume_max': 100.0, 'volume_step': 0.01,
                        'stops_level_points': 0, 'freeze_level_points': 0,
                        'spread_current': 10 ** -digits * 12,
                        'margin_per_lot': 3500.0, 'currency_base': name[:3],
                        'currency_profit': 'AUD', 'currency_margin': 'AUD',
                        'trade_mode': 4, 'session_deals': 0,
                        'swap_mode': 1, 'swap_long_points': 0.0,
                        'swap_short_points': 0.0,
                        'swap_rollover_3days_weekday': 3,
                        'spread_points_now': 12, 'margin_initial': 0.0,
                    })
                return self._json(read_spec(name))

            if route == '/symbols':
                if STATE['mock']:
                    rows = [{'name': n, 'path': 'Forex\\%s' % n,
                             'digits': v[1], 'visible': True}
                            for n, v in Mock.SPEC.items()]
                else:
                    rows = read_symbols()
                return self._json({'symbols': rows})

            if route == '/quotes':
                names = [n for n in (one('symbols', '') or '').split(',') if n]
                if STATE['mock']:
                    out = {n: MOCK.quote(n) for n in names if n in Mock.SPEC}
                else:
                    out = read_quotes(names)
                return self._json({'quotes': out})

            if route == '/ticks':
                name = (one('symbol', '') or '').upper()
                from_ms = int(one('from_ms', '0') or 0)
                limit = max(1, min(5000, int(one('limit', '2000'))))
                if not name:
                    return self._json({'error': 'symbol required'}, 400)
                if not from_ms:
                    from_ms = int(time.time() * 1000) - 5000
                payload = (MOCK.ticks(name, from_ms, limit) if STATE['mock']
                           else read_ticks(name, from_ms, limit))
                return self._json(payload)

            if route == '/calendar':
                if STATE['mock']:
                    return self._json({'events': [], 'source': 'mock'})
                return self._json(read_calendar())

            if route == '/cot':
                name = (one('symbol', '') or '').upper()
                if STATE['mock']:
                    return self._json({'symbol': name, 'available': False, 'note': 'mock'})
                return self._json(read_cot(name))

            if route == '/bars':
                name = (one('symbol', '') or '').upper()
                tf = one('tf', '1m')
                months = float(one('months', '0') or 0)
                count = max(10, min(60000, int(one('count', '1000'))))
                if tf not in TF_NAMES:
                    return self._json({'error': 'bad timeframe %s' % tf}, 400)
                payload = (MOCK.bars(name, tf, count) if STATE['mock']
                           else read_bars(name, tf, count, months))
                return self._json(payload)

            if route == '/signal':
                # THE RULE'S CURRENT STATEMENT, computed in Python.
                #
                # This exists so a lot size has ONE implementation. The browser
                # cannot size a position honestly: it would need an FX rate, and
                # MetaTrader's live rate and sim/fx.py's bar-close rate differ by
                # ~0.095% on USDJPY -- enough to flip a whole 0.01 lot step in 30
                # of 420 tested cases, and the browser's answer came out LARGER
                # every time. A JS sizing module was written, parity-tested,
                # caught doing exactly that, and deleted. So sizing is served
                # from here instead.
                #
                # READ ONLY, like everything else on this bridge: it returns a
                # statement and cannot act on one.
                # NOT upper-cased, unlike /spec: sim/instruments.json is keyed
                # by the exact broker name, so 'XAUUSD.a' must stay lower-case
                # in the suffix. MT5 resolves either; the sim does not.
                name = one('symbol', '') or ''
                tf = one('tf', '4h')
                strat = one('strategy', 'donchian')
                if not name:
                    return self._json({'error': 'symbol required'}, 400)
                if tf not in TF_NAMES:
                    return self._json({'error': 'bad timeframe %s' % tf}, 400)
                return self._json(signal_now(name, tf, strat, one('position')))

            return self._json({'error': 'no such endpoint', 'path': route}, 404)

        except Exception as exc:                          # noqa: BLE001
            log_event('error', '%s %s: %s' % (route, type(exc).__name__, exc))
            return self._json({'error': '%s: %s' % (type(exc).__name__, exc)}, 500)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--port', type=int, default=8765)
    ap.add_argument('--mock', action='store_true',
                    help='serve fabricated data; no terminal or credentials needed')
    ap.add_argument('--origin', action='append', default=[],
                    help='extra allowed browser origin (repeatable)')
    ap.add_argument('--verbose', action='store_true')
    args = ap.parse_args()

    Handler.origins = DEFAULT_ORIGINS + args.origin

    if args.mock:
        STATE['mock'] = True
        print('* MOCK MODE — fabricated account data, no MT5 terminal touched')
    else:
        cfg = load_config()
        where = ('config/env credentials' if cfg.get('login')
                 else 'the terminal session already logged in')
        print('* connecting to MetaTrader 5 using %s' % where)
        if not mt5_connect(cfg):
            print('! %s' % STATE['error'])
            print('! serving /health only — fix the above and restart, or use --mock')

    log_event('start', 'bridge on 127.0.0.1:%d mock=%s connected=%s server=%s'
              % (args.port, STATE['mock'], STATE['connected'], STATE['server']))
    srv = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print('* read-only bridge on http://127.0.0.1:%d  (Ctrl+C to stop)' % args.port)
    print('* endpoints: /health /account /positions /orders /deals /symbols '
          '/spec /quotes /ticks /bars /calendar /cot')
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print('\n* stopping')
    finally:
        log_event('stop', 'bridge shutting down')
        srv.server_close()
        if mt5 is not None and STATE['connected']:
            with MT5_LOCK:
                mt5.shutdown()


if __name__ == '__main__':
    main()

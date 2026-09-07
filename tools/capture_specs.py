#!/usr/bin/env python
"""
capture_specs.py — snapshot the contract specification of each instrument.

Everything instrument-specific in the simulator is DATA, not code: contract
size, tick value, digits, volume limits, currencies and swap rates all come from
this file. Adding an instrument to a backtest is a row here, not an edit to the
engine.

    python tools/capture_specs.py                    # -> data/instruments.json

Read only: symbol_info() and nothing else.

CAVEAT recorded in the file itself: `spread` and `swap_*` are POINT-IN-TIME. MT5
exposes only today's values, so a 20-year backtest cannot know the swap rate
that applied in 2011. Per-bar spread from the downloaded history is used where
available; swap is a modelled assumption, and for XAUUSD it is large enough to
dominate an overnight strategy's result.
"""

import json
import os
import sys
from datetime import datetime, timezone

try:
    import MetaTrader5 as mt5
except ImportError:
    sys.exit('pip install MetaTrader5')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'data')

# the instruments under test, plus whatever is needed to convert their profit
# currency back into the account currency
# EVERY symbol that must survive a re-run, not just the newest one: the
# writer rebuilds `instruments` from scratch, so anything missing from this
# list is DELETED from instruments.json. EURUSD.a was already in the file
# and not in this list, so a re-run would have silently dropped it.
SYMBOLS = ['XAUUSD.a', 'USDJPY.a', 'AUDUSD.a', 'EURUSD.a', 'GBPUSD.a']

SWAP_MODES = {
    0: 'disabled', 1: 'points', 2: 'symbol_base_currency', 3: 'margin_currency',
    4: 'deposit_currency', 5: 'interest_current', 6: 'interest_open',
    7: 'reopen_current', 8: 'reopen_bid',
}


def main():
    if not mt5.initialize():
        sys.exit('cannot reach MetaTrader 5: %s' % (mt5.last_error(),))
    acct = mt5.account_info()
    out = {
        'captured_utc': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'account_currency': acct.currency if acct else None,
        'account_leverage': acct.leverage if acct else None,
        'broker_server': acct.server if acct else None,
        'note': 'spread and swap_* are point-in-time values, valid at captured_utc only',
        'instruments': {},
    }
    for name in SYMBOLS:
        mt5.symbol_select(name, True)
        i = mt5.symbol_info(name)
        if i is None:
            print('! %s not offered' % name)
            continue
        out['instruments'][name] = {
            'digits': i.digits,
            'point': i.point,
            'contract_size': i.trade_contract_size,
            'tick_size': i.trade_tick_size,
            'tick_value': i.trade_tick_value,
            'volume_min': i.volume_min,
            'volume_step': i.volume_step,
            'volume_max': i.volume_max,
            'currency_base': i.currency_base,
            'currency_profit': i.currency_profit,
            'currency_margin': i.currency_margin,
            'spread_points_now': i.spread,
            'swap_long_points': i.swap_long,
            'swap_short_points': i.swap_short,
            'swap_mode': i.swap_mode,
            'swap_mode_name': SWAP_MODES.get(i.swap_mode, str(i.swap_mode)),
            'swap_rollover_3days_weekday': i.swap_rollover3days,   # 3 = Wednesday
            'stops_level_points': i.trade_stops_level,
            'margin_initial': i.margin_initial,
        }
        print('* %-10s contract=%g tick_value=%.5f profit=%s swap L/S=%.2f/%.2f (%s)'
              % (name, i.trade_contract_size, i.trade_tick_value, i.currency_profit,
                 i.swap_long, i.swap_short, SWAP_MODES.get(i.swap_mode)))
    mt5.shutdown()

    os.makedirs(DATA, exist_ok=True)
    path = os.path.join(DATA, 'instruments.json')
    json.dump(out, open(path, 'w', encoding='utf-8'), indent=2)
    print('* wrote %s' % path)


if __name__ == '__main__':
    main()

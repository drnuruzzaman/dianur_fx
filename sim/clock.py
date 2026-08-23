"""
Broker server clock vs UTC.

MetaTrader reports every timestamp in the broker's server time. Pepperstone runs
UTC+3, and that offset shifts with the broker's DST, so nothing can be corrected
by a constant. What is stored instead is the raw server time plus the offset as
measured at capture, and everything downstream converts through that number:
sim/fx.py uses it to find the hour whose AUD rate prices a trade, and the bridge
uses it to put broker bars on the same axis as the browser's clock.

A wrong offset is therefore silent and total -- every trade in every run gets
priced off the wrong hour, and nothing looks broken.

It has gone wrong twice, both the same way: measured on a weekend against the
last tick before the market shut, so the broker appeared 7.75h and later 13.6h
from where it actually is. The guard written to stop the first occurrence
rounded (tick - now) to the nearest MINUTE and then measured the residue of its
own rounding -- a quantity that cannot exceed 30 seconds -- so its freshness
test passed unconditionally and it never once fired. It was also written twice,
once here and once in the bridge, which is how the second copy kept a one-sided
version of the same mistake. Hence this module: one implementation, no
dependencies beyond the standard library, tested in tests/test_offset.py.
"""

# Broker server clocks sit on a whole or half hour relative to UTC and are never
# further out than UTC-12..UTC+14. Both facts are what make a stale tick
# detectable at all.
OFFSET_GRID_MS = 30 * 60 * 1000
MAX_OFFSET_MS = 14 * 60 * 60 * 1000
FRESH_MS = 5 * 60 * 1000
MAX_DRIFT_MS = 90 * 60 * 1000           # DST moves the clock by exactly one hour


def resolve_offset(tick_times_ms, now_ms, previous=None, previous_confidence=None):
    """
    Broker server clock minus true UTC, inferred from the freshest tick.

    Pure, so it can be tested without a terminal. Returns (offset_ms, confidence):

        'ok'          measured and corroborated -- trust it
        'unverified'  measured, but nothing to corroborate it against
        'stale'       not believable; `previous` is handed back unchanged

    Two gates, because neither is sufficient alone:

    1. Distance from the legal offset grid. A fresh tick puts (tick - now)
       within seconds of a half-hour boundary; a tick stale by 14h lands 13
       minutes off it.
    2. Drift from the last known-good value. A tick stale by exactly 2h lands
       back on the grid looking perfectly fresh, and only this gate sees it:
       no broker moves its clock by more than the one DST hour.

    With no known-good previous value the second gate cannot run, so a lone
    grid-clean stale tick is genuinely indistinguishable from a fresh one. That
    case returns 'unverified' rather than pretending to be sure.
    """
    usable = [t for t in tick_times_ms if t]
    keep = (previous if previous is not None else 0), 'stale'
    if not usable:
        return keep

    # One server clock stands behind every symbol, so the newest tick is the
    # best evidence and a dead symbol cannot outvote a live one.
    raw = max(usable) - now_ms
    offset = int(round(raw / OFFSET_GRID_MS) * OFFSET_GRID_MS)
    if abs(raw - offset) > FRESH_MS or abs(offset) > MAX_OFFSET_MS:
        return keep
    if previous is None or previous_confidence != 'ok':
        return offset, 'unverified'
    if abs(offset - previous) > MAX_DRIFT_MS:
        return keep
    return offset, 'ok'


def manifest_offset(data_dir):
    """
    The last offset written to data/manifest.json, as (offset_ms, confidence).

    Used to seed resolve_offset so a process starting on a weekend inherits the
    value measured while the market was open instead of guessing from a dead
    tick or falling back to zero. Returns (None, None) if there is nothing to
    inherit; never raises.
    """
    try:
        import json
        import os

        with open(os.path.join(data_dir, 'manifest.json'), encoding='utf-8') as fh:
            doc = json.load(fh)
        offset = doc.get('server_utc_offset_ms')
        return (None, None) if offset is None else (int(offset),
                                                    doc.get('server_offset_confidence'))
    except Exception:                                       # noqa: BLE001
        return None, None

"""
UTC -> the broker's server clock, for joining calendar events to STORED bars.

WHY THIS EXISTS. Two clocks are in play and the project is explicit about both
(js/util.js, sim/tl/clockguard.py):

    live bars over the bridge   TRUE UTC -- mt5_bridge.py subtracts the
                                measured offset before sending
    stored research bars        BROKER SERVER TIME, tz-naive, deliberately not
                                converted, because one constant offset is wrong
                                across twenty years of the broker's own DST

Calendar events are UTC. So anything that joins `data/calendar/history.json` to
`data/bars/**` has to move one of them, and it must be the EVENT: the bars are
what was recorded, and rewriting them would corrupt the archive to fix a join.

MEASURED, NOT ASSUMED. Profiling the mean 1-minute range around 2025 payrolls
releases puts the spike at exactly +180 minutes from the calendar time in
summer and +120 in winter -- EET/EEST, the usual MetaTrader server clock. A
tool that skipped this was reading the hour BEFORE each release and calling it
the reaction; the volatility ratio came out at 0.97, which is how the error
announced itself.

    winter (EET)  UTC+2
    summer (EEST) UTC+3, from the last Sunday in March 01:00 UTC
                  to the last Sunday in October 01:00 UTC
"""

import datetime


def _last_sunday(year, month):
    d = datetime.datetime(year, month, 31, tzinfo=datetime.timezone.utc)
    while d.month != month:
        d -= datetime.timedelta(days=1)
    while d.weekday() != 6:                       # 6 = Sunday
        d -= datetime.timedelta(days=1)
    return d


def eu_dst(t_ms):
    """True when EU summer time is in force at this UTC instant."""
    d = datetime.datetime.fromtimestamp(t_ms / 1000, datetime.timezone.utc)
    start = _last_sunday(d.year, 3).replace(hour=1)
    end = _last_sunday(d.year, 10).replace(hour=1)
    return start <= d < end


def offset_ms(t_ms):
    """The broker's offset from UTC at this instant, in milliseconds."""
    return (3 if eu_dst(t_ms) else 2) * 3600 * 1000


def to_server_ms(t_ms):
    """A UTC timestamp as the matching broker-server timestamp."""
    return t_ms + offset_ms(t_ms)

"""
fx.py — convert an instrument's profit currency into the account currency.

Gold settles in USD, USDJPY in JPY, the account is in AUD. Adding those together
unconverted is meaningless, and converting 20 years at today's rate is worse
than useless: the AUD bought 1.10 USD in 2011 and 0.65 in 2025. So conversion
uses the rate that applied at the time of the trade.

Rates come from the downloaded history, HOURLY where available:
    AUD -> USD   AUDUSD close
    AUD -> JPY   AUDUSD close x USDJPY close
    AUD -> AUD   1

Reconciling against the broker's own numbers showed why hourly matters: with
daily closes, 9 of 108 real trades disagreed with the broker by up to 1.27 AUD,
and every one of those gaps was a conversion-rate error of under 1.7% — never a
P/L formula error.

TIME BASE: every series here is indexed in BROKER SERVER time, the same as the
bar data. A timestamp from the bridge is UTC-corrected, so shift it by
`server_offset_ms` before looking a rate up (see FX.at_utc).
"""

import pandas as pd

from .instruments import load_bars_for_fx, specs


class FX:
    """Point-in-time conversion between profit currency and account currency."""

    def __init__(self, account='AUD', rates=None, freq='1h'):
        self.account = account
        self.rates = rates or {}
        self.freq = freq
        self._extrapolated = 0

    @classmethod
    def build(cls, account='AUD', freq='1h'):
        if account != 'AUD':
            raise NotImplementedError('only an AUD account is wired up so far')
        audusd = load_bars_for_fx('AUDUSD.a', freq)['close']
        usdjpy = load_bars_for_fx('USDJPY.a', freq)['close']
        # AUDJPY is a cross: reindex the yen leg onto the AUD leg's stamps and
        # carry the last known value forward rather than interpolating
        audjpy = (audusd * usdjpy.reindex(audusd.index).ffill()).dropna()
        return cls(account, {'AUD': None, 'USD': audusd, 'JPY': audjpy}, freq)

    def _rate(self, ccy, when):
        """Units of `ccy` per 1 unit of the account currency, as at `when`."""
        if ccy == self.account:
            return 1.0
        s = self.rates.get(ccy)
        if s is None:
            raise KeyError(f'no {self.account}->{ccy} rate series; '
                           f'download that pair and rebuild FX')
        i = s.index.searchsorted(pd.Timestamp(when), side='right') - 1
        if i < 0:
            self._extrapolated += 1        # before the rate history begins
            return float(s.iloc[0])
        return float(s.iloc[i])

    def to_account(self, amount_ccy, ccy, when):
        return amount_ccy / self._rate(ccy, when)

    def from_account(self, amount_acct, ccy, when):
        """Used for risk sizing: how much profit currency is this much equity?"""
        return amount_acct * self._rate(ccy, when)

    # ---- timeline helpers ---------------------------------------------- #
    @staticmethod
    def server_offset_ms():
        """Broker server clock minus UTC, as measured when the data was captured."""
        try:
            import json
            import os
            from .instruments import DATA
            man = os.path.join(DATA, 'manifest.json')
            return int(json.load(open(man, encoding='utf-8')).get('server_utc_offset_ms') or 0)
        except Exception:                                   # noqa: BLE001
            return 0

    def to_account_utc(self, amount_ccy, ccy, when_utc):
        """Convert when the timestamp is UTC (e.g. from the bridge), not server time."""
        shifted = pd.Timestamp(when_utc) + pd.Timedelta(milliseconds=self.server_offset_ms())
        return self.to_account(amount_ccy, ccy, shifted)

    @property
    def extrapolated(self):
        return self._extrapolated

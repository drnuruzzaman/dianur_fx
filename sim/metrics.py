"""
metrics.py — what a run reports.

Ordered deliberately: trade count first, because 40 trades is an anecdote no
matter how good the profit factor looks, and a buy-and-hold column because any
long-biased rule on gold from 2007 to 2026 will look like genius against nothing.
"""

import numpy as np
import pandas as pd


def _max_drawdown(equity: pd.Series):
    peak = equity.cummax()
    dd = equity - peak
    i = dd.idxmin() if len(dd) else None
    return (float(dd.min()) if len(dd) else 0.0,
            float((dd / peak.replace(0, np.nan)).min() * 100) if len(dd) else 0.0,
            i)


def summarise(result, bars: pd.DataFrame = None) -> dict:
    t, e = result.trades, result.equity
    out = dict(result.stats)
    n = len(t)
    out['trades'] = n

    if n == 0:
        out.update({'note': 'no trades — check warmup, sizing or signal logic'})
        return out

    wins = t[t['net_acct'] > 0]
    losses = t[t['net_acct'] < 0]
    gross_win = float(wins['net_acct'].sum())
    gross_loss = float(-losses['net_acct'].sum())

    out['net_acct'] = round(float(t['net_acct'].sum()), 2)
    out['return_pct'] = round(100 * (e['equity'].iloc[-1] / out['start_equity'] - 1), 2)
    out['win_rate_pct'] = round(100 * len(wins) / n, 1)
    out['avg_R'] = round(float(t['r_multiple'].mean()), 3)
    out['expectancy_R'] = out['avg_R']              # per trade, in risk units
    out['profit_factor'] = round(gross_win / gross_loss, 3) if gross_loss else np.inf
    out['avg_win_acct'] = round(float(wins['net_acct'].mean()), 2) if len(wins) else 0.0
    out['avg_loss_acct'] = round(float(losses['net_acct'].mean()), 2) if len(losses) else 0.0
    out['largest_loss_acct'] = round(float(t['net_acct'].min()), 2)
    out['avg_bars_held'] = round(float(t['bars_held'].mean()), 1)

    dd_abs, dd_pct, dd_at = _max_drawdown(e['equity'])
    out['max_drawdown_acct'] = round(dd_abs, 2)
    out['max_drawdown_pct'] = round(dd_pct, 2)
    out['max_drawdown_at'] = str(dd_at) if dd_at is not None else None

    # costs, so their share of the result is never hidden
    out['cost_spread_ccy'] = round(float(t['spread_ccy'].sum()), 2)
    out['cost_slippage_ccy'] = round(float(t['slippage_ccy'].sum()), 2)
    out['cost_swap_ccy'] = round(float(t['swap_ccy'].sum()), 2)

    # exposure and a risk-adjusted number from the daily equity path
    daily = e['equity'].resample('1D').last().dropna()
    rets = daily.pct_change().dropna()
    if len(rets) > 30 and rets.std() > 0:
        out['sharpe_daily_annualised'] = round(float(rets.mean() / rets.std() * np.sqrt(252)), 2)
    out['time_in_market_pct'] = round(100 * float((e['exposure'] != 0).mean()), 1)

    span_years = max((e.index[-1] - e.index[0]).days / 365.25, 1e-9)
    out['years'] = round(span_years, 2)
    out['trades_per_year'] = round(n / span_years, 1)
    if e['equity'].iloc[-1] > 0:
        out['cagr_pct'] = round(100 * ((e['equity'].iloc[-1] / out['start_equity'])
                                       ** (1 / span_years) - 1), 2)

    out['exit_reasons'] = t['exit_reason'].value_counts().to_dict()
    out['by_side'] = {
        'long': int((t['side'] == 1).sum()), 'short': int((t['side'] == -1).sum()),
    }

    if bars is not None and len(bars):
        first, last = float(bars['close'].iloc[0]), float(bars['close'].iloc[-1])
        out['buy_and_hold_pct'] = round(100 * (last / first - 1), 2)

    return out


def by_year(result) -> pd.DataFrame:
    """Per-calendar-year breakdown. One blended curve hides everything."""
    t = result.trades
    if not len(t):
        return pd.DataFrame()
    t = t.copy()
    t['year'] = pd.to_datetime(t['exit_time']).dt.year
    g = t.groupby('year')
    df = pd.DataFrame({
        'trades': g.size(),
        'net_acct': g['net_acct'].sum().round(2),
        'win_rate_pct': (100 * g['net_acct'].apply(lambda s: (s > 0).mean())).round(1),
        'avg_R': g['r_multiple'].mean().round(3),
        'worst_acct': g['net_acct'].min().round(2),
    })
    return df.reset_index()

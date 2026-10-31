"""
quantlab_stats — reusable backtest metrics + tearsheet for QuantLab notebooks.

    from quantlab_stats import metrics, report, tearsheet

    m = metrics(strategy_returns)          # pd.Series of risk/return metrics
    report(strategy_returns, "TSMOM")      # pretty-printed metrics table
    tearsheet(strategy_returns, rets_bh)   # equity / drawdown / rolling-Sharpe fig

All functions take a pd.Series of periodic (default daily) simple returns.
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

PERIODS_PER_YEAR = 252


def _clean(returns: pd.Series) -> pd.Series:
    r = pd.Series(returns).dropna()
    if r.empty:
        raise ValueError("returns series is empty after dropna()")
    return r


def max_drawdown(returns: pd.Series) -> float:
    equity = (1 + _clean(returns)).cumprod()
    return float((equity / equity.cummax() - 1).min())


def metrics(returns: pd.Series, rf: float = 0.0,
            periods: int = PERIODS_PER_YEAR) -> pd.Series:
    """Standard risk/return metrics as a named pd.Series."""
    r = _clean(returns)
    excess = r - rf / periods
    years = len(r) / periods
    equity = (1 + r).cumprod()
    total = float(equity.iloc[-1] - 1)
    cagr = float(equity.iloc[-1] ** (1 / years) - 1) if years > 0 else np.nan
    vol = float(r.std() * np.sqrt(periods))
    sharpe = float(np.sqrt(periods) * excess.mean() / excess.std()) if excess.std() > 0 else np.nan
    downside = excess[excess < 0].std()
    sortino = float(np.sqrt(periods) * excess.mean() / downside) if downside and downside > 0 else np.nan
    mdd = max_drawdown(r)
    calmar = float(cagr / abs(mdd)) if mdd else np.nan

    worst_month = np.nan
    if isinstance(r.index, pd.DatetimeIndex):
        monthly = r.resample("ME").apply(lambda x: float((1 + x).prod() - 1))
        if len(monthly):
            worst_month = float(monthly.min())

    return pd.Series({
        "total_return": total,
        "cagr": cagr,
        "ann_vol": vol,
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": calmar,
        "max_drawdown": mdd,
        "worst_day": float(r.min()),
        "worst_month": worst_month,
        "hit_rate": float((r > 0).mean()),
        "skew": float(r.skew()),
        "kurtosis": float(r.kurtosis()),
        "n_periods": float(len(r)),
        "years": float(years),
    })


def report(returns: pd.Series, name: str = "strategy", rf: float = 0.0,
           periods: int = PERIODS_PER_YEAR) -> pd.Series:
    """Print a formatted metrics table; returns the metrics Series."""
    m = metrics(returns, rf=rf, periods=periods)
    pct = {"total_return", "cagr", "ann_vol", "max_drawdown", "worst_day",
           "worst_month", "hit_rate"}
    print(f"── {name} ──")
    for key, val in m.items():
        if key in ("n_periods",):
            print(f"{key:>14}: {int(val)}")
        elif key in pct:
            print(f"{key:>14}: {val:8.2%}")
        else:
            print(f"{key:>14}: {val:8.2f}")
    return m


def tearsheet(returns: pd.Series, benchmark: Optional[pd.Series] = None,
              name: str = "strategy", periods: int = PERIODS_PER_YEAR):
    """Three-panel tearsheet: log equity, drawdown, rolling 1y Sharpe."""
    import matplotlib.pyplot as plt

    r = _clean(returns)
    fig, (ax1, ax2, ax3) = plt.subplots(
        3, 1, figsize=(9, 8), sharex=True,
        gridspec_kw={"height_ratios": [3, 1, 1]})

    equity = (1 + r).cumprod()
    ax1.plot(equity, label=name)
    if benchmark is not None:
        b = _clean(benchmark).reindex(r.index).fillna(0)
        ax1.plot((1 + b).cumprod(), alpha=0.7, label="benchmark")
    ax1.set_yscale("log")
    ax1.legend()
    ax1.set_title(f"{name}: equity (log)")

    dd = equity / equity.cummax() - 1
    ax2.fill_between(dd.index, dd, 0, color="crimson", alpha=0.5)
    ax2.set_title("drawdown")

    roll = r.rolling(periods)
    rolling_sharpe = np.sqrt(periods) * roll.mean() / roll.std()
    ax3.plot(rolling_sharpe, color="seagreen")
    ax3.axhline(0, color="grey", lw=0.5)
    ax3.set_title("rolling 1y Sharpe")

    plt.tight_layout()
    plt.show()
    return fig

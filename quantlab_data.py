"""
quantlab_data — offline access to the bundled index price history.

The CSVs in data/ are daily auto-adjusted OHLCV downloaded from Yahoo via
`download_data.py` (network needed only for refreshing them). Everything here
works with zero connectivity:

    from quantlab_data import available, load_index, closes

    available()                      # {'sp500': 'S&P 500 (1927-12-30 → …)', ...}
    spx = load_index("sp500")        # OHLCV DataFrame, DatetimeIndex
    px  = closes()                   # wide close-price panel, all indices
    px  = closes("sp500", "nikkei225", start="2000-01-01")
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent / "data"

NAMES: Dict[str, str] = {
    "sp500": "S&P 500",
    "nasdaq": "NASDAQ Composite",
    "dow": "Dow Jones Industrial Average",
    "russell2000": "Russell 2000",
    "ftse100": "FTSE 100",
    "dax": "DAX",
    "nikkei225": "Nikkei 225",
    "vix": "CBOE Volatility Index",
}


def available() -> Dict[str, str]:
    """Bundled datasets with their coverage: {key: 'Name (start → end)'}."""
    out = {}
    for f in sorted(DATA_DIR.glob("*.csv")):
        key = f.stem
        try:
            idx = pd.read_csv(f, usecols=["Date"], parse_dates=["Date"])["Date"]
            span = f" ({idx.iloc[0].date()} → {idx.iloc[-1].date()}, {len(idx)} rows)"
        except Exception:
            span = ""
        out[key] = NAMES.get(key, key) + span
    return out


def load_index(key: str, start: Optional[str] = None,
               end: Optional[str] = None) -> pd.DataFrame:
    """Daily OHLCV for one index as a DataFrame with a DatetimeIndex."""
    path = DATA_DIR / f"{key}.csv"
    if not path.exists():
        known = ", ".join(sorted(p.stem for p in DATA_DIR.glob("*.csv"))) or "<none>"
        raise FileNotFoundError(
            f"No bundled data for '{key}'. Available: {known}. "
            f"Refresh/extend with: python download_data.py")
    df = pd.read_csv(path, index_col="Date", parse_dates=True).sort_index()
    return df.loc[start:end]


def closes(*keys: str, start: Optional[str] = None,
           end: Optional[str] = None) -> pd.DataFrame:
    """Wide panel of close prices (one column per index), inner-aligned on
    business days where every requested index traded."""
    keys = keys or tuple(sorted(p.stem for p in DATA_DIR.glob("*.csv")))
    panel = pd.DataFrame({k: load_index(k, start, end)["Close"] for k in keys})
    return panel.dropna(how="all")

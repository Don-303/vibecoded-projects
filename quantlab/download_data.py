#!/usr/bin/env python3
"""
Fetch daily OHLCV history for major stock indices into data/ as CSV, so
QuantLab notebooks can do real index research fully offline.

Needs network only when you run *this* script:

    .venv/bin/python download_data.py            # all indices, max history
    .venv/bin/python download_data.py sp500 vix  # refresh a subset

Load the result inside a notebook with the bundled helper:

    from quantlab_data import load_index, available
    spx = load_index("sp500")
"""

from __future__ import annotations

import sys
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"

# key -> (yahoo ticker, human name)
INDICES: dict[str, tuple[str, str]] = {
    "sp500":   ("^GSPC",  "S&P 500"),
    "nasdaq":  ("^IXIC",  "NASDAQ Composite"),
    "dow":     ("^DJI",   "Dow Jones Industrial Average"),
    "russell2000": ("^RUT", "Russell 2000"),
    "ftse100": ("^FTSE",  "FTSE 100"),
    "dax":     ("^GDAXI", "DAX"),
    "nikkei225": ("^N225", "Nikkei 225"),
    "vix":     ("^VIX",   "CBOE Volatility Index"),
}


def download(keys: list[str]) -> int:
    import yfinance as yf

    DATA_DIR.mkdir(exist_ok=True)
    failures = 0
    for key in keys:
        ticker, name = INDICES[key]
        print(f"  {key:<12} {ticker:<8} {name} ... ", end="", flush=True)
        try:
            df = yf.download(ticker, period="max", interval="1d",
                             auto_adjust=True, progress=False)
            if df is None or df.empty:
                raise RuntimeError("empty response")
            # yfinance >=0.2.40 returns a (field, ticker) MultiIndex even for
            # a single ticker — flatten to plain OHLCV columns.
            if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
                df.columns = df.columns.get_level_values(0)
            df.index.name = "Date"
            out = DATA_DIR / f"{key}.csv"
            df.to_csv(out)
            print(f"{len(df):>6} rows  {df.index[0].date()} → {df.index[-1].date()}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAILED: {exc}")
    return failures


def main() -> int:
    keys = sys.argv[1:] or list(INDICES)
    unknown = [k for k in keys if k not in INDICES]
    if unknown:
        print(f"Unknown index keys: {unknown}. Available: {list(INDICES)}")
        return 2
    print(f"Downloading {len(keys)} indices into {DATA_DIR}/")
    failures = download(keys)
    print("Done." if not failures else f"Done with {failures} failure(s).")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

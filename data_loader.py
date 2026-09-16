import os
import pandas as pd
import numpy as np
import yfinance as yf

try:
    from config import CACHE_DIR, EARNINGS_CACHE_DIR, ETF_SYMBOLS
except ImportError:
    CACHE_DIR = "cache"
    EARNINGS_CACHE_DIR = os.path.join(CACHE_DIR, "earnings")
    ETF_SYMBOLS = {
        "SPY", "QQQ", "IWM", "SMH", "XLV", "XLI", "XLE", "XLK",
        "XLP", "XLU", "XLB", "XLY", "XLF", "DIA", "MDY", "SOXX",
        "IGV", "XBI", "XHB", "XRT", "XOP", "KRE", "ITA"
    }

def _get_cached_price(symbol: str, start_date: str) -> pd.DataFrame:
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_file = os.path.join(CACHE_DIR, f"{symbol}_{start_date}.csv")
    if os.path.exists(cache_file):
        try:
            df = pd.read_csv(cache_file, index_col=0, parse_dates=True)
            if not df.empty and len(df) >= 50:
                return df
        except Exception:
            pass

    ticker = yf.Ticker(symbol)
    df = ticker.history(start=start_date, auto_adjust=True)
    if df.empty:
        return pd.DataFrame()

    df.columns = [c.lower() for c in df.columns]
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    df.index = df.index.normalize()
    df.to_csv(cache_file)
    return df

def _get_earnings_dates(symbol: str) -> list[pd.Timestamp]:
    if symbol.upper() in ETF_SYMBOLS:
        return []

    os.makedirs(EARNINGS_CACHE_DIR, exist_ok=True)
    cache_file = os.path.join(EARNINGS_CACHE_DIR, f"{symbol}_earnings.csv")

    if os.path.exists(cache_file):
        try:
            edf = pd.read_csv(cache_file)
            if "earnings_date" in edf.columns:
                return [pd.to_datetime(d) for d in edf["earnings_date"].dropna().tolist()]
        except Exception:
            pass

    try:
        ticker = yf.Ticker(symbol)
        ed = ticker.get_earnings_dates(limit=60)
        if ed is not None and not ed.empty:
            raw_dates = ed.index.tolist()
            save_df = pd.DataFrame({"earnings_date": [str(d) for d in raw_dates]})
            save_df.to_csv(cache_file, index=False)
            return [pd.to_datetime(d) for d in raw_dates]
    except Exception:
        pass

    return []

def get_historical_data(symbol: str, start_date: str = "2023-01-01") -> pd.DataFrame:
    df = _get_cached_price(symbol, start_date)
    if df.empty:
        return df

    df["earnings_exit"] = False
    df["earnings_blackout"] = False

    if symbol.upper() in ETF_SYMBOLS:
        return df

    earnings_raw = _get_earnings_dates(symbol)
    if not earnings_raw:
        return df

    trading_dates_list = df.index.tolist()
    exit_days = set()

    for ts in earnings_raw:
        hour = ts.hour if hasattr(ts, "hour") else 0
        date = ts.tz_localize(None).normalize() if ts.tzinfo is not None else ts.normalize()

        if hour >= 12:  # After Market Close
            prior_tds = [d for d in trading_dates_list if d <= date]
            if prior_tds: exit_days.add(prior_tds[-1])
        else:           # Before Market Open
            prior_tds = [d for d in trading_dates_list if d < date]
            if prior_tds: exit_days.add(prior_tds[-1])

    df["earnings_exit"] = [d in exit_days for d in df.index]

    # Block entries 5 days prior to an earnings announcement
    is_exit = df["earnings_exit"].values
    blackout = np.zeros(len(df), dtype=bool)
    exit_indices = np.where(is_exit)[0]
    blackout_window = 5

    for idx in exit_indices:
        start_idx = max(0, idx - blackout_window)
        blackout[start_idx : idx + 1] = True

    df["earnings_blackout"] = blackout
    return df
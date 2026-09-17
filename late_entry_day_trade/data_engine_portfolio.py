import os
import sys
import pandas as pd
import numpy as np
import yfinance as yf

# Ensure project root is in python path
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from data_vault import get_1m_bars


def get_strategy_data(ticker_symbol: str, days: int = None) -> pd.DataFrame:
    """
    Fetches continuous 1-minute intraday data from the SQLite Data Vault
    and merges daily 5 and 10 SMAs with VWAP, 9 EMA, LOD, VWAP SD bands, ATR, and ADX.
    """
    # 1. Fetch Daily Data for Daily SMAs (3 years for multi-year backtesting)
    daily = yf.download(ticker_symbol, period="3y", interval="1d", progress=False)
    if daily.empty:
        raise ValueError(f"No daily data for {ticker_symbol}.")

    if isinstance(daily.columns, pd.MultiIndex):
        daily.columns = daily.columns.get_level_values(0)

    daily["SMA_5"] = daily["Close"].rolling(window=5).mean()
    daily["SMA_10"] = daily["Close"].rolling(window=10).mean()

    # Shift daily data so today's intraday logic relies on YESTERDAY'S closing SMA
    # This completely eliminates lookahead bias in the backtest.
    daily["SMA_5"] = daily["SMA_5"].shift(1)
    daily["SMA_10"] = daily["SMA_10"].shift(1)

    daily = daily[["SMA_5", "SMA_10"]]
    daily.index = pd.to_datetime(daily.index).date

    # 2. Fetch 1-Minute Intraday Data from the SQLite Vault
    intraday = get_1m_bars(ticker_symbol, days=days, auto_sync=True)
    if intraday.empty:
        raise ValueError(f"No 1m data available for {ticker_symbol} in Data Vault.")

    # Preserve full datetime for chronological portfolio sorting
    intraday["Datetime"] = intraday.index
    intraday["Date"] = intraday.index.date
    intraday["Time"] = intraday.index.time

    # 3. Calculate Intraday Indicators
    intraday["Typical_Price"] = (intraday["High"] + intraday["Low"] + intraday["Close"]) / 3.0
    intraday["Vol_x_TP"] = intraday["Typical_Price"] * intraday["Volume"]
    intraday["Vol_x_TP2"] = (intraday["Typical_Price"] ** 2) * intraday["Volume"]

    cum_vol = intraday.groupby("Date")["Volume"].cumsum()
    cum_vol_x_tp = intraday.groupby("Date")["Vol_x_TP"].cumsum()
    cum_vol_x_tp2 = intraday.groupby("Date")["Vol_x_TP2"].cumsum()
    intraday["VWAP"] = cum_vol_x_tp / cum_vol

    # Intraday VWAP Standard Deviation Bands
    var = (cum_vol_x_tp2 / cum_vol) - (intraday["VWAP"] ** 2)
    intraday["VWAP_SD"] = np.sqrt(var.clip(lower=0.0))
    intraday["VWAP_Lower_2SD"] = intraday["VWAP"] - (2.0 * intraday["VWAP_SD"])

    intraday["EMA_9"] = intraday["Close"].ewm(span=9, adjust=False).mean()
    intraday["LOD"] = intraday.groupby("Date")["Low"].cummin()

    # 1m ATR (14-period)
    h = intraday["High"]
    l = intraday["Low"]
    c_prev = intraday["Close"].shift(1)
    tr = pd.concat([h - l, (h - c_prev).abs(), (l - c_prev).abs()], axis=1).max(axis=1)
    intraday["ATR_1m"] = tr.rolling(14).mean().bfill()

    # 5-minute ADX(14) calculation
    df_5m = intraday[["High", "Low", "Close"]].resample("5min").agg({
        "High": "max", "Low": "min", "Close": "last"
    }).dropna()
    if len(df_5m) >= 30:
        h5 = df_5m["High"]
        l5 = df_5m["Low"]
        c5 = df_5m["Close"]
        c5_prev = c5.shift(1)
        tr5 = pd.concat([h5 - l5, (h5 - c5_prev).abs(), (l5 - c5_prev).abs()], axis=1).max(axis=1)
        up5 = h5 - h5.shift(1)
        down5 = l5.shift(1) - l5
        plus_dm = np.where((up5 > down5) & (up5 > 0), up5, 0.0)
        minus_dm = np.where((down5 > up5) & (down5 > 0), down5, 0.0)
        alpha = 1.0 / 14.0
        atr5 = tr5.ewm(alpha=alpha, adjust=False).mean()
        plus_di = 100.0 * (pd.Series(plus_dm, index=df_5m.index).ewm(alpha=alpha, adjust=False).mean() / atr5)
        minus_di = 100.0 * (pd.Series(minus_dm, index=df_5m.index).ewm(alpha=alpha, adjust=False).mean() / atr5)
        dx = 100.0 * ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
        df_5m["ADX_5m"] = dx.ewm(alpha=alpha, adjust=False).mean().fillna(20.0)
        merged_adx = pd.merge_asof(intraday[["Datetime"]], df_5m[["ADX_5m"]], left_on="Datetime", right_index=True, direction="backward")
        intraday["ADX_5m"] = merged_adx["ADX_5m"].fillna(20.0).values
    else:
        intraday["ADX_5m"] = 20.0

    # 4. Merge Daily SMAs
    intraday = intraday.merge(daily, left_on="Date", right_index=True, how="inner")
    intraday.dropna(subset=["EMA_9", "VWAP", "SMA_5"], inplace=True)

    return intraday
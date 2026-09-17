import os
import sys
import pandas as pd
import yfinance as yf

# Ensure project root is in python path
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from data_vault import get_1m_bars


def get_strategy_data(ticker_symbol: str, days: int = None) -> pd.DataFrame:
    """
    Fetches continuous 1-minute intraday data from the SQLite Data Vault
    and merges daily 5 and 10 SMAs with VWAP, 9 EMA, and LOD indicators.
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

    cum_vol = intraday.groupby("Date")["Volume"].cumsum()
    cum_vol_x_tp = intraday.groupby("Date")["Vol_x_TP"].cumsum()
    intraday["VWAP"] = cum_vol_x_tp / cum_vol

    intraday["EMA_9"] = intraday["Close"].ewm(span=9, adjust=False).mean()
    intraday["LOD"] = intraday.groupby("Date")["Low"].cummin()

    # 4. Merge Daily SMAs
    intraday = intraday.merge(daily, left_on="Date", right_index=True, how="inner")
    intraday.dropna(subset=["EMA_9", "VWAP", "SMA_5"], inplace=True)

    return intraday
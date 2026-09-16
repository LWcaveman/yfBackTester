"""
yfBackTester Configuration Module
Central repository for directory paths, universe lists, and simulation parameters.
"""

import os
from pathlib import Path

# Paths
BASE_DIR = Path(__file__).resolve().parent
CACHE_DIR = os.path.join(BASE_DIR, "cache")
EARNINGS_CACHE_DIR = os.path.join(CACHE_DIR, "earnings")
DATA_DIR = os.path.join(BASE_DIR, "data")
INTRADAY_DB_PATH = os.path.join(DATA_DIR, "intraday_1m.db")

# Portfolio Simulation Defaults
DEFAULT_STARTING_CAPITAL: float = 1000.0
DEFAULT_START_DATE: str = "2023-01-01"
DEFAULT_RISK_PCT: float = 0.02
DEFAULT_MAX_POSITIONS: int = 2
DEFAULT_STALE_BARS: int = 14
DEFAULT_MIN_RISK_PCT: float = 0.02
DEFAULT_STOP_BUFFER_PCT: float = 0.008  # 0.8% below signal candle low
DEFAULT_TARGET_R: float = 2.0

# Strategy Parameter Defaults
DEFAULT_EMA_PERIOD: int = 20
DEFAULT_SMA_PERIOD: int = 50
DEFAULT_MIN_CLOSE_PCT: float = 0.60  # Upper 40% of candle range

# Curated 30-Ticker Institutional Universe (used by run_vwap.py & run_vwap_swing.py)
EXPANDED_UNIVERSE = [
    # Core Index & Sector ETFs (Liquidity Foundation)
    "SPY", "QQQ", "IWM", "SMH", "XLV", "XLI", "XLE",
    # MegaCap Tech & Cloud Momentum
    "AAPL", "NVDA", "META", "AMZN", "GOOGL",
    # High-Performance Semiconductor Leaders
    "AMAT", "LRCX", "AVGO", "ADI", "MU",
    # Healthcare & Biotech Trends
    "LLY", "MRK", "UNH", "AMGN", "TMO",
    # Financial Leaders
    "BLK", "GS", "V",
    # Industrials & Defense
    "CAT", "UNP", "PH",
    # Quality Retail & Consumer
    "COST", "HD"
]

# ETF Symbols (Exempt from single-stock earnings blackout/exit logic)
ETF_SYMBOLS = {
    "SPY", "QQQ", "IWM", "SMH", "XLV", "XLI", "XLE", "XLK",
    "XLP", "XLU", "XLB", "XLY", "XLF", "DIA", "MDY", "SOXX",
    "IGV", "XBI", "XHB", "XRT", "XOP", "KRE", "ITA"
}

# Curated Elite Day-Trading Universe (Optimized for 9 EMA / VWAP 3R Setup)
DAYTRADE_TICKERS = [
    "ARM", "HOOD", "PLTR", "AMZN", "AAPL", "GOOGL"
]

# Day-Trading Edge Parameters (Small Cash Account Optimized)
DEFAULT_DAYTRADE_MIN_CLOSE_PCT: float = 0.60
DEFAULT_DAYTRADE_MIN_VOL_RATIO: float = 0.80
DEFAULT_DAYTRADE_RATCHET_1_5R: bool = True

# Market Scanner Universe (market_scanner.py)
SCANNER_TICKERS = [
    "SPY", "QQQ", "DIA", "IWM", "SMH", "XLF", "XLE", "XLV",
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA",
    "AVGO", "NFLX", "AMD", "QCOM", "NOW", "PANW", "CRWD",
    "COST", "WMT", "JPM", "V", "MA", "UNH", "LLY", "XOM"
]

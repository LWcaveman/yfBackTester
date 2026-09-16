"""
yfBackTester Data Vault: Local High-Resolution 1-Minute Time Series Store
Uses SQLite for zero-dependency, atomic, and incrementally expanding market data.
"""

import os
import sqlite3
import argparse
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
import pytz

try:
    from config import INTRADAY_DB_PATH, DATA_DIR, DAYTRADE_TICKERS
except ImportError:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    DATA_DIR = os.path.join(BASE_DIR, "data")
    INTRADAY_DB_PATH = os.path.join(DATA_DIR, "intraday_1m.db")
    DAYTRADE_TICKERS = ["ARM", "HOOD", "PLTR", "AMZN", "AAPL", "GOOGL"]


def get_connection(db_path: str = INTRADAY_DB_PATH) -> sqlite3.Connection:
    """Returns an optimized SQLite connection with WAL mode enabled."""
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def init_db(db_path: str = INTRADAY_DB_PATH):
    """Initializes the bars_1m table and compound index."""
    with get_connection(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bars_1m (
                ticker      TEXT NOT NULL,
                datetime    TEXT NOT NULL,
                open        REAL NOT NULL,
                high        REAL NOT NULL,
                low         REAL NOT NULL,
                close       REAL NOT NULL,
                volume      REAL NOT NULL,
                PRIMARY KEY (ticker, datetime)
            );
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_ticker_datetime 
            ON bars_1m (ticker, datetime);
        """)


def sync_ticker(ticker: str, period: str = "7d", db_path: str = INTRADAY_DB_PATH) -> int:
    """
    Fetches latest 1-minute bars from Yahoo Finance and merges into SQLite.
    Returns the number of newly inserted bars.
    """
    ticker = ticker.upper().strip()
    init_db(db_path)

    # yfinance 1m data is capped at 7 days per call
    df = yf.download(ticker, period=period, interval="1m", progress=False)
    if df.empty:
        return 0

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    # Standardize column names to lowercase
    col_map = {c: c.lower() for c in df.columns}
    df = df.rename(columns=col_map)
    required = ["open", "high", "low", "close", "volume"]
    if not all(c in df.columns for c in required):
        return 0

    # Ensure timezone is America/New_York (US market hours)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC").tz_convert("America/New_York")
    else:
        df.index = df.index.tz_convert("America/New_York")

    # Format datetime as ISO string: YYYY-MM-DD HH:MM:SS
    rows = []
    for dt, row in df.iterrows():
        dt_str = dt.strftime("%Y-%m-%d %H:%M:%S")
        rows.append((
            ticker,
            dt_str,
            float(row["open"]),
            float(row["high"]),
            float(row["low"]),
            float(row["close"]),
            float(row["volume"]),
        ))

    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM bars_1m WHERE ticker = ?", (ticker,))
        count_before = cursor.fetchone()[0]

        cursor.executemany("""
            INSERT OR IGNORE INTO bars_1m (ticker, datetime, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, rows)
        conn.commit()

        cursor.execute("SELECT COUNT(*) FROM bars_1m WHERE ticker = ?", (ticker,))
        count_after = cursor.fetchone()[0]

    return count_after - count_before


def sync_watchlist(tickers: list[str] = None, period: str = "7d", db_path: str = INTRADAY_DB_PATH) -> dict[str, int]:
    """Syncs 1-minute bars for all tickers in watchlist."""
    if tickers is None:
        tickers = DAYTRADE_TICKERS

    results = {}
    total = len(tickers)
    print(f"Syncing 1-minute bars into Vault for {total} tickers (period={period})...")
    for idx, sym in enumerate(tickers, 1):
        try:
            new_bars = sync_ticker(sym, period=period, db_path=db_path)
            results[sym] = new_bars
            print(f"[{idx:02d}/{total:02d}] {sym:5s} -> +{new_bars} new bars added.")
        except Exception as e:
            print(f"[{idx:02d}/{total:02d}] {sym:5s} -> Error: {e}")
            results[sym] = 0
    return results


def get_1m_bars(ticker: str, days: int = None, start_date: str = None, auto_sync: bool = True, db_path: str = INTRADAY_DB_PATH) -> pd.DataFrame:
    """
    Retrieves continuous 1-minute bars from SQLite.
    Returns DataFrame indexed by America/New_York pd.DatetimeIndex with columns [Open, High, Low, Close, Volume].
    If the database has no data, triggers an automatic sync from yfinance.
    """
    ticker = ticker.upper().strip()
    init_db(db_path)

    query = "SELECT datetime, open, high, low, close, volume FROM bars_1m WHERE ticker = ?"
    params = [ticker]

    if start_date:
        query += " AND datetime >= ?"
        params.append(str(start_date))
    elif days:
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        query += " AND datetime >= ?"
        params.append(cutoff)

    query += " ORDER BY datetime ASC"

    with get_connection(db_path) as conn:
        df = pd.read_sql_query(query, conn, params=params)

    # If vault has no data, auto-sync once and re-query
    if df.empty and auto_sync:
        print(f"[Vault] No local 1m bars for {ticker}. Fetching from yfinance...")
        sync_ticker(ticker, period="7d", db_path=db_path)
        with get_connection(db_path) as conn:
            df = pd.read_sql_query(query, conn, params=params)

    if df.empty:
        return pd.DataFrame()

    df["datetime"] = pd.to_datetime(df["datetime"]).dt.tz_localize("America/New_York")
    df.set_index("datetime", inplace=True)
    df.columns = [c.capitalize() for c in df.columns]
    return df


def get_vault_stats(db_path: str = INTRADAY_DB_PATH) -> pd.DataFrame:
    """Returns summary statistics for all tickers in the vault."""
    init_db(db_path)
    with get_connection(db_path) as conn:
        query = """
            SELECT 
                ticker,
                COUNT(*) as total_bars,
                MIN(datetime) as earliest_bar,
                MAX(datetime) as latest_bar,
                COUNT(DISTINCT substr(datetime, 1, 10)) as trading_days
            FROM bars_1m
            GROUP BY ticker
            ORDER BY ticker ASC
        """
        df = pd.read_sql_query(query, conn)
    return df


def main():
    parser = argparse.ArgumentParser(description="yfBackTester Data Vault: 1-Minute Historical SQLite Store")
    parser.add_argument("--sync", action="store_true", help="Sync latest 1m bars for watchlist")
    parser.add_argument("--tickers", nargs="+", default=None, help="Specific tickers to sync")
    parser.add_argument("--period", type=str, default="7d", help="yfinance pull period (default: 7d)")
    parser.add_argument("--stats", action="store_true", help="Display Vault database statistics")
    args = parser.parse_args()

    if args.sync or not args.stats:
        tickers = args.tickers if args.tickers else DAYTRADE_TICKERS
        sync_watchlist(tickers, period=args.period)

    # Always show stats
    stats_df = get_vault_stats()
    print("\n=======================================================")
    print(" INTRADAY 1-MINUTE DATA VAULT STATUS")
    print("=======================================================")
    if stats_df.empty:
        print("Vault is currently empty. Run with --sync to fetch data.")
    else:
        print(stats_df.to_string(index=False))
        if os.path.exists(INTRADAY_DB_PATH):
            size_mb = os.path.getsize(INTRADAY_DB_PATH) / (1024 * 1024)
            print(f"\nDatabase Location: {INTRADAY_DB_PATH} ({size_mb:.2f} MB)")
    print("=======================================================\n")


if __name__ == "__main__":
    main()

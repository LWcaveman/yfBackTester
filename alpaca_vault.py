"""
yfBackTester Alpaca Data Vault
High-Resolution 1-Minute Multi-Year Time Series Engine using Alpaca Markets Data API v2.
Fetches consolidated tape (SIP) 1-minute historical bars and indexes them into local SQLite.
"""

import os
import sys
import time
import sqlite3
import argparse
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
import pytz
import requests
from dotenv import load_dotenv

# Ensure project root in sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    INTRADAY_DB_PATH,
    DATA_DIR,
    DAYTRADE_TICKERS,
    INDEX_TICKERS,
    INVERSE_TICKERS,
    DAYTRADE_EXTENDED_UNIVERSE
)
from data_vault import get_connection, init_db


class AlpacaDataVault:
    """
    Ingests and stores multi-year 1-minute historical market data from Alpaca
    into the local SQLite database.
    """

    def __init__(
        self,
        db_path: str = INTRADAY_DB_PATH,
        feed: str = "sip",
        rth_only: bool = True
    ):
        self.db_path = db_path
        self.feed = feed.lower()
        self.rth_only = rth_only
        self.tz_ny = pytz.timezone("America/New_York")
        self.tz_utc = pytz.utc
        self._key, self._secret, self._base_url = self._resolve_credentials()
        init_db(self.db_path)

    def _resolve_credentials(self) -> Tuple[str, str, str]:
        load_dotenv()
        # Support various environment variable naming conventions
        key = (
            os.getenv("ALPACA_API_KEY") or
            os.getenv("ALPACA_KEY") or
            os.getenv("APCA_API_KEY_ID")
        )
        secret = (
            os.getenv("ALPACA_SECRET") or
            os.getenv("APCA_API_SECRET_KEY")
        )
        endpoint = (
            os.getenv("ALPACA_ENDPOINT") or
            os.getenv("APLACA_ENDPOINT") or
            "https://data.alpaca.markets"
        )

        if not key or not secret:
            raise ValueError(
                "Missing Alpaca API credentials. Please set ALPACA_API_KEY and "
                "ALPACA_SECRET in your .env file."
            )

        # Ensure we target the Market Data endpoint
        if "data.alpaca.markets" not in endpoint:
            base_url = "https://data.alpaca.markets"
        else:
            base_url = endpoint.rstrip("/")

        return key, secret, base_url

    def _get_headers(self) -> Dict[str, str]:
        return {
            "APCA-API-KEY-ID": self._key,
            "APCA-API-SECRET-KEY": self._secret,
            "Accept": "application/json"
        }

    def fetch_bars_page(
        self,
        ticker: str,
        start_iso: str,
        end_iso: str,
        page_token: Optional[str] = None,
        limit: int = 10000
    ) -> Tuple[List[dict], Optional[str]]:
        """Fetches a single page of 1-minute bars from Alpaca."""
        url = f"{self._base_url}/v2/stocks/{ticker}/bars"
        params = {
            "timeframe": "1Min",
            "start": start_iso,
            "end": end_iso,
            "limit": limit,
            "adjustment": "split",
            "feed": self.feed
        }
        if page_token:
            params["page_token"] = page_token

        # Free tier rate limit is 200 req/min (~3.3 req/s).
        # We enforce a polite 0.35s delay (~170 req/min) to avoid HTTP 429.
        time.sleep(0.35)

        for attempt in range(4):
            try:
                response = requests.get(url, headers=self._get_headers(), params=params, timeout=25)
                if response.status_code == 200:
                    data = response.json()
                    bars = data.get("bars", []) or []
                    next_token = data.get("next_page_token")
                    return bars, next_token
                elif response.status_code == 429:
                    wait_sec = 5 * (attempt + 1)
                    print(f"[{ticker}] Rate limit encountered (429). Backing off for {wait_sec}s...")
                    time.sleep(wait_sec)
                elif response.status_code == 403 and self.feed == "sip":
                    print(f"[{ticker}] SIP feed restricted, falling back to IEX feed...")
                    self.feed = "iex"
                    params["feed"] = "iex"
                else:
                    print(f"[{ticker}] API Warning ({response.status_code}): {response.text[:120]}")
                    time.sleep(2)
            except requests.RequestException as e:
                print(f"[{ticker}] Network error: {e}. Retrying ({attempt+1}/4)...")
                time.sleep(3)

        return [], None

    def sync_ticker(
        self,
        ticker: str,
        years: float = 2.0,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> Dict[str, any]:
        """
        Synchronizes historical 1-minute bars for a single ticker into SQLite.
        """
        ticker = ticker.upper().strip()
        now_dt = datetime.now(self.tz_utc)

        if end_date:
            end_dt = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=self.tz_ny).astimezone(self.tz_utc)
        else:
            # Offset by 20 minutes to safely satisfy Alpaca Free Tier 15-min SIP delay rule
            end_dt = now_dt - timedelta(minutes=20)

        if start_date:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=self.tz_ny).astimezone(self.tz_utc)
        else:
            start_dt = end_dt - timedelta(days=int(years * 365.25))

        start_iso = start_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        end_iso = end_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        # Check existing count before sync
        with get_connection(self.db_path) as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM bars_1m WHERE ticker = ?", (ticker,))
            count_before = cur.fetchone()[0]

        print(f"[{ticker}] Fetching Alpaca {self.feed.upper()} 1m bars from {start_iso[:10]} to {end_iso[:10]}...")

        page_token = None
        total_fetched = 0
        total_inserted = 0
        page_num = 1

        rth_start = datetime.strptime("09:30:00", "%H:%M:%S").time()
        rth_end = datetime.strptime("16:00:00", "%H:%M:%S").time()

        while True:
            bars, next_token = self.fetch_bars_page(
                ticker=ticker,
                start_iso=start_iso,
                end_iso=end_iso,
                page_token=page_token,
                limit=10000
            )

            if not bars:
                break

            total_fetched += len(bars)
            rows_to_insert = []

            for b in bars:
                # b['t'] format: "2024-09-16T13:30:00Z"
                utc_dt = datetime.fromisoformat(b["t"].replace("Z", "+00:00"))
                ny_dt = utc_dt.astimezone(self.tz_ny)

                # RTH filter (09:30 - 16:00 EST)
                if self.rth_only:
                    bar_time = ny_dt.time()
                    if not (rth_start <= bar_time <= rth_end):
                        continue

                dt_str = ny_dt.strftime("%Y-%m-%d %H:%M:%S")
                rows_to_insert.append((
                    ticker,
                    dt_str,
                    float(b["o"]),
                    float(b["h"]),
                    float(b["l"]),
                    float(b["c"]),
                    float(b["v"])
                ))

            if rows_to_insert:
                with get_connection(self.db_path) as conn:
                    cur = conn.cursor()
                    cur.executemany("""
                        INSERT OR IGNORE INTO bars_1m (ticker, datetime, open, high, low, close, volume)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, rows_to_insert)
                    conn.commit()
                total_inserted += len(rows_to_insert)

            sys.stdout.write(f"\r[{ticker}] Page {page_num:02d}: Fetched {total_fetched:,} raw bars -> {total_inserted:,} RTH bars queued...")
            sys.stdout.flush()

            if not next_token:
                break

            page_token = next_token
            page_num += 1

        # Check final count
        with get_connection(self.db_path) as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT COUNT(*), MIN(datetime), MAX(datetime), COUNT(DISTINCT substr(datetime, 1, 10))
                FROM bars_1m WHERE ticker = ?
            """, (ticker,))
            count_after, earliest, latest, days_count = cur.fetchone()

        new_bars = count_after - count_before
        print(f"\r[{ticker}] Done: +{new_bars:,} new bars saved. Total in Vault: {count_after:,} bars ({days_count} trading days: {earliest[:10]} to {latest[:10]}).")

        return {
            "ticker": ticker,
            "new_bars": new_bars,
            "total_bars": count_after,
            "trading_days": days_count,
            "earliest": earliest,
            "latest": latest
        }

    def bulk_sync(
        self,
        tickers: Optional[List[str]] = None,
        years: float = 2.0
    ) -> List[Dict[str, any]]:
        """
        Synchronizes historical data for an entire universe of tickers.
        Defaults to DAYTRADE_EXTENDED_UNIVERSE (10 tickers including SPY, QQQ, SH, PSQ).
        """
        if tickers is None:
            tickers = DAYTRADE_EXTENDED_UNIVERSE

        print("=" * 65)
        print(f" ALPACA DATA VAULT BULK SYNC ({len(tickers)} TICKERS | {years:.1f} YEARS)")
        print(f" Universe: {', '.join(tickers)}")
        print(f" Target Database: {self.db_path}")
        print("=" * 65)

        results = []
        start_time = time.time()

        for idx, sym in enumerate(tickers, 1):
            print(f"\n({idx}/{len(tickers)}) Processing {sym}...")
            res = self.sync_ticker(sym, years=years)
            results.append(res)

        elapsed = time.time() - start_time
        total_new = sum(r["new_bars"] for r in results)
        total_stored = sum(r["total_bars"] for r in results)
        db_size_mb = os.path.getsize(self.db_path) / (1024 * 1024) if os.path.exists(self.db_path) else 0.0

        print("\n" + "=" * 65)
        print(" BULK SYNC COMPLETE")
        print(f" Total New Bars Added:  {total_new:,}")
        print(f" Total Vault Bars:      {total_stored:,}")
        print(f" Database Disk Size:    {db_size_mb:.2f} MB")
        print(f" Time Elapsed:          {elapsed:.1f} seconds")
        print("=" * 65)

        return results

    def print_stats(self):
        """Displays database statistics for all tickers in the vault."""
        with get_connection(self.db_path) as conn:
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
            cur = conn.cursor()
            rows = cur.execute(query).fetchall()

        print("\n=================================================================")
        print(" INTRADAY 1-MINUTE SQLITE DATA VAULT STATUS")
        print("=================================================================")
        if not rows:
            print("Vault is empty. Run with --bulk-sync or --add-ticker to populate.")
        else:
            header = f"{'Ticker':<8} | {'Total Bars':<12} | {'Trading Days':<14} | {'Earliest':<19} | {'Latest':<19}"
            print(header)
            print("-" * len(header))
            total_bars = 0
            for r in rows:
                sym, cnt, early, late, days = r
                total_bars += cnt
                print(f"{sym:<8} | {cnt:<12,} | {days:<14,} | {early:<19} | {late:<19}")
            print("-" * len(header))
            print(f"TOTAL:    | {total_bars:<12,} |")

        if os.path.exists(self.db_path):
            size_mb = os.path.getsize(self.db_path) / (1024 * 1024)
            print(f"\nDatabase File: {self.db_path} ({size_mb:.2f} MB)")
        print("=================================================================\n")


def main():
    parser = argparse.ArgumentParser(description="yfBackTester Alpaca Data Vault Engine")
    parser.add_argument("--bulk-sync", action="store_true", help="Sync full extended universe (10 tickers) for N years")
    parser.add_argument("--add-ticker", type=str, default=None, help="Add/sync a single ticker into the vault")
    parser.add_argument("--tickers", nargs="+", default=None, help="Specific list of tickers to sync")
    parser.add_argument("--years", type=float, default=2.0, help="Number of years of historical 1m data (default: 2.0)")
    parser.add_argument("--feed", type=str, default="sip", choices=["sip", "iex"], help="Alpaca data feed (default: sip)")
    parser.add_argument("--all-hours", action="store_true", help="Store all hours including premarket/afterhours (default is RTH only)")
    parser.add_argument("--stats", action="store_true", help="Display Vault database statistics")

    args = parser.parse_args()

    vault = AlpacaDataVault(feed=args.feed, rth_only=not args.all_hours)

    if args.bulk_sync:
        vault.bulk_sync(tickers=args.tickers, years=args.years)
        vault.print_stats()
    elif args.add_ticker:
        vault.sync_ticker(args.add_ticker, years=args.years)
        vault.print_stats()
    elif args.tickers:
        for t in args.tickers:
            vault.sync_ticker(t, years=args.years)
        vault.print_stats()
    elif args.stats or len(sys.argv) == 1:
        vault.print_stats()


if __name__ == "__main__":
    main()

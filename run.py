"""
Unified CLI Entrypoint for yfBackTester
Runs multi-ticker portfolio backtests across supported swing and intraday strategies,
and manages the local 1-minute historical SQLite Data Vault.
"""

import argparse
import sys
import pandas as pd
from tabulate import tabulate

from config import (
    EXPANDED_UNIVERSE,
    DAYTRADE_TICKERS,
    DEFAULT_START_DATE,
    DEFAULT_STARTING_CAPITAL,
    DEFAULT_RISK_PCT,
    DEFAULT_MAX_POSITIONS,
    DEFAULT_STALE_BARS,
    DEFAULT_MIN_RISK_PCT,
    DEFAULT_STOP_BUFFER_PCT,
    DEFAULT_TARGET_R,
)
from data_loader import get_historical_data
from data_vault import sync_watchlist, get_vault_stats
from strategies.ema_shelf import EMAShelfStrategy
from strategies.vwap_shelf import VWAPEMAShelfStrategy
from portfolio_engine import PortfolioBacktester
from late_entry_day_trade.backtest_portfolio import FashionablyLatePortfolio


def parse_args():
    parser = argparse.ArgumentParser(
        description="yfBackTester: Institutional-grade algorithmic portfolio backtester"
    )
    parser.add_argument(
        "--strategy",
        type=str,
        choices=["vwap", "ema", "daytrade"],
        default="vwap",
        help="Strategy to test: 'vwap' (Weekly VWAP + 20 EMA + RS), 'ema' (20 EMA / 50 SMA Pullback), or 'daytrade' (9 EMA / VWAP Crossover 1m Intraday)",
    )
    parser.add_argument(
        "--tickers",
        nargs="+",
        default=None,
        help="List of ticker symbols (defaults to strategy's curated universe)",
    )
    parser.add_argument(
        "--start",
        type=str,
        default=DEFAULT_START_DATE,
        help=f"Historical daily data start date (default: {DEFAULT_START_DATE})",
    )
    parser.add_argument(
        "--slots",
        type=int,
        default=None,
        help="Maximum simultaneous open positions (default: 2 for swing, 1 for daytrade)",
    )
    parser.add_argument(
        "--capital",
        type=float,
        default=DEFAULT_STARTING_CAPITAL,
        help=f"Starting account equity (default: ${DEFAULT_STARTING_CAPITAL:.2f})",
    )
    parser.add_argument(
        "--risk",
        type=float,
        default=DEFAULT_RISK_PCT * 100,
        help=f"Account risk percentage per trade (e.g., 2.0 for 2%%, default: {DEFAULT_RISK_PCT * 100}%%)",
    )
    parser.add_argument(
        "--reward",
        type=float,
        default=None,
        help="Target reward multiple in R (default: 2.0 for swing, 3.0 for daytrade)",
    )
    parser.add_argument(
        "--buffer",
        type=float,
        default=None,
        help="Stop buffer percentage below candle low (e.g., 0.8 for 0.8%%, default: 0.0%% for standard swing, 0.8%% for swing buffer)",
    )
    parser.add_argument(
        "--stale",
        type=int,
        default=DEFAULT_STALE_BARS,
        help=f"Max bars to hold a stagnant trade before stale exit (default: {DEFAULT_STALE_BARS})",
    )
    parser.add_argument(
        "--min-close-pct",
        type=float,
        default=0.60,
        help="Minimum candle close percentile (e.g., 0.60 for upper 40%% of daily range)",
    )
    parser.add_argument(
        "--sync-intraday",
        action="store_true",
        help="Sync latest 1-minute historical bars into local SQLite Data Vault",
    )
    parser.add_argument(
        "--vault-stats",
        action="store_true",
        help="Display summary statistics of the local 1-minute SQLite Data Vault",
    )
    parser.add_argument(
        "--show-trades",
        action="store_true",
        help="Display individual executed trade ledger in terminal",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="CSV filename to save executed trade log (optional)",
    )
    return parser.parse_args()


def run_intraday_backtest(args, tickers):
    print("\n" + "=" * 65)
    print(" EXECUTING 1-MINUTE INTRADAY SIMULATION (Data Vault)")
    print(f" Universe: {tickers} | Risk: {args.risk}%")
    print("=" * 65)

    sim = FashionablyLatePortfolio(
        tickers=tickers,
        start_capital=args.capital,
        risk_pct=args.risk / 100.0,
        max_trades_per_day=1,
        morning_cutoff="10:45",
        midday_max_unit_pct=0.0075,
    )
    sim.generate_signals()
    sim.run_portfolio_simulation()

    if args.show_trades and sim.executed_trades:
        print("\n--- INTRADAY TRADE LEDGER ---")
        df_trades = pd.DataFrame(sim.executed_trades)
        display_cols = ["Ticker", "Entry Time", "Exit Time", "Entry Price", "Exit Price", "Shares", "Net PnL", "Reason"]
        cols_present = [c for c in display_cols if c in df_trades.columns]
        print(tabulate(df_trades[cols_present], headers="keys", tablefmt="github", showindex=False))

    if args.output and sim.executed_trades:
        df_trades = pd.DataFrame(sim.executed_trades)
        df_trades.to_csv(args.output, index=False)
        print(f"Trade ledger exported to {args.output}")


def run_swing_backtest(args, tickers):
    slots = args.slots if args.slots is not None else DEFAULT_MAX_POSITIONS
    target_r = args.reward if args.reward is not None else DEFAULT_TARGET_R
    buffer_pct = (args.buffer / 100.0) if args.buffer is not None else 0.0

    # Ensure SPY is loaded for relative strength and market regime filter
    needed_symbols = list(dict.fromkeys(["SPY"] + tickers))

    print(f"Loading historical bars and earnings data for {len(needed_symbols)} symbols (from {args.start})...")
    data = {}
    for idx, sym in enumerate(needed_symbols, 1):
        print(f"[{idx:02d}/{len(needed_symbols)}] Processing {sym}...", end="\r", flush=True)
        df = get_historical_data(sym, start_date=args.start)
        if not df.empty and len(df) >= 50:
            data[sym] = df
    print(f"\nSuccessfully loaded {len(data)} valid datasets.")

    if not data:
        print("Error: No valid ticker data could be loaded. Aborting.")
        sys.exit(1)

    # Strategy Selection
    if args.strategy == "vwap":
        strategy = VWAPEMAShelfStrategy(min_close_pct=args.min_close_pct)
    else:
        strategy = EMAShelfStrategy(min_close_pct=args.min_close_pct)

    sim = PortfolioBacktester(
        strategy=strategy,
        starting_capital=args.capital,
        risk_pct=args.risk / 100.0,
        target_r=target_r,
        max_positions=slots,
        allow_fractional=True,
        min_risk_pct=DEFAULT_MIN_RISK_PCT,
        require_spy_regime=True,
        stale_bars=args.stale,
        stop_buffer_pct=buffer_pct,
    )

    res = sim.run(data)
    if "error" in res:
        print(f"Simulation Error: {res['error']}")
        sys.exit(1)

    print("\n" + "=" * 65)
    print(f" BACKTEST RESULTS: {strategy.name}")
    print(f" Universe: {len(tickers)} Tickers | Slots: {slots} | Risk: {args.risk}% | Target: {target_r}R | Stale: {args.stale} bars")
    print("=" * 65)
    print(f"Starting Balance: ${res['starting_capital']:.2f}")
    print(f"Final Balance:    ${res['final_balance']:.2f}")
    print(f"Net Profit:       ${res['net_profit']:+.2f} ({res['total_return_pct']})")
    print(f"Total Trades:     {res['total_trades']}")
    print(f"Wins / Losses:    {res['wins']} / {res['losses']} (Breakeven: {res['breakeven']})")
    print(f"Win Rate:         {res['win_rate']}")
    print(f"Profit Factor:    {res['profit_factor']}")
    print(f"Max Drawdown:     {res['max_drawdown']}")

    tdf = res.get("trades_df")
    if tdf is not None and not tdf.empty:
        print("\n--- EXIT REASONS ---")
        print(tdf["reason"].value_counts().to_string())

        if args.show_trades:
            print("\n--- TRADE LEDGER ---")
            display_cols = ["symbol", "entry_date", "exit_date", "entry", "exit", "r_multiple", "pnl", "reason", "bars"]
            cols_present = [c for c in display_cols if c in tdf.columns]
            print(tabulate(tdf[cols_present], headers="keys", tablefmt="github", showindex=False))

        out_path = args.output or f"backtest_{args.strategy}_detailed.csv"
        tdf.to_csv(out_path, index=False)
        print(f"\nDetailed trade ledger saved to {out_path}")
    print("=" * 65 + "\n")


def main():
    args = parse_args()

    # Vault Management Commands
    if args.sync_intraday:
        tickers = args.tickers if args.tickers else DAYTRADE_TICKERS
        sync_watchlist(tickers)
        return

    if args.vault_stats:
        stats = get_vault_stats()
        print("\n=======================================================")
        print(" INTRADAY 1-MINUTE DATA VAULT STATUS")
        print("=======================================================")
        if stats.empty:
            print("Vault is currently empty. Run with --sync-intraday to fetch data.")
        else:
            print(stats.to_string(index=False))
        print("=======================================================\n")
        return

    # Dispatch to appropriate backtest runner
    if args.strategy == "daytrade":
        tickers = args.tickers if args.tickers else DAYTRADE_TICKERS
        run_intraday_backtest(args, tickers)
    else:
        tickers = args.tickers if args.tickers else EXPANDED_UNIVERSE
        run_swing_backtest(args, tickers)


if __name__ == "__main__":
    main()

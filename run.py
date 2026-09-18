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
    DAYTRADE_EXTENDED_UNIVERSE,
    INDEX_TICKERS,
    INVERSE_TICKERS,
    DEFAULT_START_DATE,
    DEFAULT_STARTING_CAPITAL,
    DEFAULT_RISK_PCT,
    DEFAULT_MAX_POSITIONS,
    DEFAULT_STALE_BARS,
    DEFAULT_MIN_RISK_PCT,
    DEFAULT_STOP_BUFFER_PCT,
    DEFAULT_TARGET_R,
    DEFAULT_DAYTRADE_ENABLE_DUAL_ENGINE,
    DEFAULT_DAYTRADE_ENABLE_PARTIAL_SCALE,
    DEFAULT_DAYTRADE_PARTIAL_SCALE_R,
    DEFAULT_DAYTRADE_PARTIAL_SCALE_PCT,
    DEFAULT_DAYTRADE_RUNNER_R,
    DEFAULT_DAYTRADE_MORNING_CUTOFF,
    DEFAULT_DAYTRADE_MAX_TRADES_PER_DAY,
    DEFAULT_DAYTRADE_BUYING_POWER_MULT,
    DEFAULT_DAYTRADE_INDEX_GATE,
    DEFAULT_DAYTRADE_FRACTIONAL,
    DEFAULT_VWAP_RECLAIM_TICKERS,
    DEFAULT_DAYTRADE_PRIORITY_MODE,
    DEFAULT_DAYTRADE_REGIME_ROUTING,
)
from data_loader import get_historical_data
from data_vault import sync_watchlist, get_vault_stats
from alpaca_vault import AlpacaDataVault
from strategies.ema_shelf import EMAShelfStrategy
from strategies.vwap_shelf import VWAPEMAShelfStrategy
from portfolio_engine import PortfolioBacktester
from late_entry_day_trade.backtest_portfolio import FashionablyLatePortfolio
from vwap_reclaim.backtest import VWAPReclaimPortfolio


def parse_args():
    parser = argparse.ArgumentParser(
        description="yfBackTester: Institutional-grade algorithmic portfolio backtester"
    )
    parser.add_argument(
        "--strategy",
        type=str,
        choices=["vwap", "ema", "daytrade", "vwap-reclaim"],
        default="vwap",
        help="Strategy to test: 'vwap', 'ema', 'daytrade' (9 EMA / VWAP Crossover + VWAP Reclaim), or 'vwap-reclaim' (Pure VWAP Reclaim)",
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
        help="Minimum candle close percentile (default: 0.60 for upper 40%% of candle range)",
    )
    parser.add_argument(
        "--min-vol-ratio",
        type=float,
        default=0.80,
        help="Minimum volume relative to 10-bar SMA on 1m cross (default: 0.80)",
    )
    parser.add_argument(
        "--no-ratchet",
        action="store_true",
        help="Disable +1.5R breakeven ratchet defense",
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
        "--days",
        type=int,
        default=None,
        help="Number of days to backtest (defaults to all available data in vault)",
    )
    parser.add_argument(
        "--bulk-sync",
        action="store_true",
        help="Sync full 10-ticker universe (ARM, HOOD, PLTR, AMZN, AAPL, GOOGL, SPY, QQQ, SH, PSQ) via Alpaca SIP for N years",
    )
    parser.add_argument(
        "--add-ticker",
        type=str,
        default=None,
        help="Add/sync a single ticker into local SQLite Vault via Alpaca SIP for N years",
    )
    parser.add_argument(
        "--years",
        type=float,
        default=2.0,
        help="Historical lookback in years for Alpaca sync (default: 2.0)",
    )
    parser.add_argument(
        "--feed",
        type=str,
        default="sip",
        choices=["sip", "iex"],
        help="Alpaca data feed type (default: sip)",
    )
    parser.add_argument(
        "--index-gate",
        action=argparse.BooleanOptionalAction,
        default=DEFAULT_DAYTRADE_INDEX_GATE,
        help="Enable Market Regime Index Gate (QQQ 50 EMA Regime + Intraday SPY/QQQ VWAP tide)",
    )
    parser.add_argument(
        "--require-rs",
        action="store_true",
        help="Require 20-day Relative Strength >= 0 vs SPY for long positions",
    )
    parser.add_argument(
        "--fractional",
        action=argparse.BooleanOptionalAction,
        default=DEFAULT_DAYTRADE_FRACTIONAL,
        help="Enable fractional share position sizing (matching live Robinhood execution)",
    )
    parser.add_argument(
        "--max-trades",
        type=int,
        default=DEFAULT_DAYTRADE_MAX_TRADES_PER_DAY,
        help=f"Maximum trades per day (default: {DEFAULT_DAYTRADE_MAX_TRADES_PER_DAY})",
    )
    parser.add_argument(
        "--buying-power-mult",
        type=float,
        default=DEFAULT_DAYTRADE_BUYING_POWER_MULT,
        help=f"Buying power multiplier (default: {DEFAULT_DAYTRADE_BUYING_POWER_MULT} for 1x pure cash)",
    )
    parser.add_argument(
        "--enable-chop-stop",
        action="store_true",
        help="Enable 15-minute chop time-stop (default: False, NO_CHOP_STOP policy)",
    )
    parser.add_argument(
        "--enable-dual-engine",
        action="store_true",
        default=DEFAULT_DAYTRADE_ENABLE_DUAL_ENGINE,
        help="Enable Dual-Engine mode (Engine 1: Morning Momentum + Engine 2: Midday VWAP Reversion)",
    )
    parser.add_argument(
        "--enable-partial-scale",
        action=argparse.BooleanOptionalAction,
        default=DEFAULT_DAYTRADE_ENABLE_PARTIAL_SCALE,
        help="Enable partial scale-out (bank 33% at +1.5R, move stop to breakeven, runner to 4.0R)",
    )
    parser.add_argument(
        "--enable-morning-momentum",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable Morning 9 EMA / VWAP Momentum engine (Engine 1)",
    )
    parser.add_argument(
        "--enable-vwap-reclaim",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable Morning VWAP Reclaim engine (Engine 3) alongside 9 EMA crossover",
    )
    parser.add_argument(
        "--deposit",
        type=float,
        default=0.0,
        help="Weekly fresh capital deposit injection (e.g. 15.0 for $15/week compounding)",
    )
    parser.add_argument(
        "--reclaim-tickers",
        nargs="+",
        default=None,
        help="Ticker symbols for Morning VWAP Reclaim engine (default: DEFAULT_VWAP_RECLAIM_TICKERS from config)",
    )
    parser.add_argument(
        "--priority",
        action=argparse.BooleanOptionalAction,
        default=DEFAULT_DAYTRADE_PRIORITY_MODE,
        help="Prioritize early VWAP Reclaim setups, falling back to Late Entry (default: True)",
    )
    parser.add_argument(
        "--regime-routing",
        action=argparse.BooleanOptionalAction,
        default=DEFAULT_DAYTRADE_REGIME_ROUTING,
        help="Route strategy by regime: Bull (QQQ > 50 EMA) -> Late Entry; Bear (QQQ <= 50 EMA) -> VWAP Reclaim (default: True)",
    )
    parser.add_argument(
        "--morning-cutoff",
        type=str,
        default=DEFAULT_DAYTRADE_MORNING_CUTOFF,
        help=f"Cutoff time for morning momentum entries (default: {DEFAULT_DAYTRADE_MORNING_CUTOFF})",
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
    reclaim_tickers = args.reclaim_tickers if args.reclaim_tickers else DEFAULT_VWAP_RECLAIM_TICKERS
    print("\n" + "=" * 65)
    print(" EXECUTING 1-MINUTE INTRADAY SIMULATION (Data Vault)")
    gate_status = "ENABLED (QQQ 50 EMA + Intraday VWAP)" if args.index_gate else "DISABLED"
    rs_status = "ENABLED (20d RS >= 0)" if args.require_rs else "DISABLED"
    chop_status = "ENABLED (15m Time Stop)" if args.enable_chop_stop else "DISABLED (NO_CHOP_STOP Policy)"
    dual_status = "ENABLED (Morning Momentum + Midday VWAP Reversion)" if args.enable_dual_engine else "DISABLED"
    reclaim_status = "ENABLED (Morning Liquidity Sweep & Reclaim)" if args.enable_vwap_reclaim else "DISABLED"
    momentum_status = "ENABLED (9 EMA / VWAP Crossover)" if args.enable_morning_momentum else "DISABLED"
    scale_status = "ENABLED (Scale 33% @ 1.5R, Runner to 4.0R)" if args.enable_partial_scale else "DISABLED (Pure 3.0R Target)"
    priority_status = "ENABLED (Reclaim First -> Late Entry Fallback)" if args.priority else "DISABLED"
    routing_status = "ENABLED (Bull: Late Entry / Bear: VWAP Reclaim)" if args.regime_routing else "DISABLED"
    print(f" Late Entry Universe: {tickers}")
    if args.enable_vwap_reclaim:
        print(f" VWAP Reclaim Universe: {reclaim_tickers}")
        print(f" Regime Routing: {routing_status} | Priority Fallback: {priority_status}")
    print(f" Capital: ${args.capital:.2f} | Weekly Deposit: ${args.deposit:.2f} | Risk: {args.risk}%")
    print(f" Max Trades/Day: {args.max_trades} | Buying Power: {args.buying_power_mult}x | Morning Cutoff: {args.morning_cutoff}")
    print(f" Morning Momentum: {momentum_status} | VWAP Reclaim: {reclaim_status}")
    print(f" Dual-Engine: {dual_status} | Partial Scale: {scale_status}")
    print(f" Chop Stop: {chop_status} | Index Gate: {gate_status} | RS Filter: {rs_status}")
    print("=" * 65)

    sim = FashionablyLatePortfolio(
        tickers=tickers,
        reclaim_tickers=reclaim_tickers,
        priority_mode=args.priority,
        enable_regime_routing=args.regime_routing,
        start_capital=args.capital,
        risk_pct=args.risk / 100.0,
        max_trades_per_day=args.max_trades,
        morning_cutoff=args.morning_cutoff,
        midday_max_unit_pct=0.0075,
        min_close_pct=args.min_close_pct,
        min_vol_ratio=args.min_vol_ratio,
        ratchet_1_5r=not args.no_ratchet,
        days=args.days,
        index_gate=args.index_gate,
        require_rs=args.require_rs,
        fractional=args.fractional,
        enable_chop_stop=args.enable_chop_stop,
        enable_dual_engine=args.enable_dual_engine,
        enable_morning_momentum=args.enable_morning_momentum,
        enable_vwap_reclaim=args.enable_vwap_reclaim,
        enable_partial_scale=args.enable_partial_scale,
        buying_power_mult=args.buying_power_mult,
        weekly_deposit=args.deposit,
    )
    sim.generate_signals()
    sim.run_portfolio_simulation()

    if args.show_trades and sim.executed_trades:
        print("\n--- INTRADAY TRADE LEDGER ---")
        df_trades = pd.DataFrame(sim.executed_trades)
        display_cols = ["Ticker", "Strategy", "Entry Time", "Exit Time", "Entry Price", "Exit Price", "Shares", "Net PnL", "Reason"]
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

    # Alpaca Data Vault Commands
    if args.bulk_sync:
        vault = AlpacaDataVault(feed=args.feed)
        tickers = args.tickers if args.tickers else DAYTRADE_EXTENDED_UNIVERSE
        vault.bulk_sync(tickers=tickers, years=args.years)
        vault.print_stats()
        return

    if args.add_ticker:
        vault = AlpacaDataVault(feed=args.feed)
        vault.sync_ticker(args.add_ticker, years=args.years)
        vault.print_stats()
        return

    # Legacy Vault Management Commands
    if args.sync_intraday:
        tickers = args.tickers if args.tickers else DAYTRADE_TICKERS
        sync_watchlist(tickers)
        return

    if args.vault_stats:
        vault = AlpacaDataVault(feed=args.feed)
        vault.print_stats()
        return

    # Dispatch to appropriate backtest runner
    if args.strategy == "daytrade":
        tickers = args.tickers if args.tickers else DAYTRADE_TICKERS
        run_intraday_backtest(args, tickers)
    elif args.strategy == "vwap-reclaim":
        tickers = args.tickers if args.tickers else DEFAULT_VWAP_RECLAIM_TICKERS
        print("\n" + "=" * 65)
        print(" EXECUTING PURE VWAP RECLAIM INTRADAY SIMULATION")
        print(f" Universe: {tickers}")
        print(f" Capital: ${args.capital:.2f} | Weekly Deposit: ${args.deposit:.2f}")
        print("=" * 65)
        port = VWAPReclaimPortfolio(
            tickers=tickers,
            start_capital=args.capital,
            weekly_deposit=args.deposit,
            days=args.days
        )
        port.generate_signals()
        port.run_portfolio_simulation()
        if args.show_trades and port.executed_trades:
            print("\n--- VWAP RECLAIM TRADE LEDGER ---")
            df_trades = pd.DataFrame(port.executed_trades)
            print(tabulate(df_trades, headers="keys", tablefmt="github", showindex=False))
        if args.output and port.executed_trades:
            df_trades = pd.DataFrame(port.executed_trades)
            df_trades.to_csv(args.output, index=False)
            print(f"Trade ledger exported to {args.output}")
    else:
        tickers = args.tickers if args.tickers else EXPANDED_UNIVERSE
        run_swing_backtest(args, tickers)


if __name__ == "__main__":
    main()

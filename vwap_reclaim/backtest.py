"""
VWAP Reclaim Backtest Runner
Simulates the VWAP Reclaim Strategy standalone or across a multi-ticker portfolio.
Supports small cash account compounding, full cash utilization, and weekly deposits.
"""

import os
import sys
import argparse
import pandas as pd
from datetime import datetime

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from data_vault import get_1m_bars
from vwap_reclaim.engine import VWAPReclaimEngine
from config import (
    DAYTRADE_TICKERS,
    DEFAULT_DAYTRADE_FULL_CASH_UNDER_1K,
    DEFAULT_DAYTRADE_MAX_STOP_PCT,
    DEFAULT_VWAP_RECLAIM_START_TIME,
    DEFAULT_VWAP_RECLAIM_END_TIME,
    DEFAULT_VWAP_RECLAIM_MIN_SWEEP_BARS,
    DEFAULT_VWAP_RECLAIM_MIN_DEPTH_PCT,
    DEFAULT_VWAP_RECLAIM_MAX_DEPTH_PCT,
    DEFAULT_VWAP_RECLAIM_MIN_CLOSE_PCT,
    DEFAULT_VWAP_RECLAIM_MIN_VOL_RATIO,
    DEFAULT_DAYTRADE_PARTIAL_SCALE_R,
    DEFAULT_DAYTRADE_PARTIAL_SCALE_PCT,
    DEFAULT_DAYTRADE_RUNNER_R,
)


class VWAPReclaimPortfolio:
    def __init__(
        self,
        tickers=None,
        start_capital: float = 70.0,
        risk_pct: float = 0.02,
        weekly_deposit: float = 15.0,
        full_cash_under_1k: bool = True,
        max_stop_pct: float = 0.025,
        max_trades_per_day: int = 1,
        index_gate: bool = True,
        fractional: bool = True,
        start_time: str = DEFAULT_VWAP_RECLAIM_START_TIME,
        end_time: str = DEFAULT_VWAP_RECLAIM_END_TIME,
        days: int = None
    ):
        self.tickers = tickers or DAYTRADE_TICKERS
        self.start_capital = start_capital
        self.risk_pct = risk_pct
        self.weekly_deposit = weekly_deposit
        self.full_cash_under_1k = full_cash_under_1k
        self.max_stop_pct = max_stop_pct
        self.max_trades_per_day = max_trades_per_day
        self.index_gate = index_gate
        self.fractional = fractional
        self.days = days

        self.engine = VWAPReclaimEngine(
            start_time=start_time,
            end_time=end_time,
            min_sweep_bars=DEFAULT_VWAP_RECLAIM_MIN_SWEEP_BARS,
            min_depth_pct=DEFAULT_VWAP_RECLAIM_MIN_DEPTH_PCT,
            max_depth_pct=DEFAULT_VWAP_RECLAIM_MAX_DEPTH_PCT,
            min_close_pct=DEFAULT_VWAP_RECLAIM_MIN_CLOSE_PCT,
            min_vol_ratio=DEFAULT_VWAP_RECLAIM_MIN_VOL_RATIO,
            target_r=DEFAULT_DAYTRADE_RUNNER_R,
            ratchet_r=DEFAULT_DAYTRADE_PARTIAL_SCALE_R,
            partial_scale_pct=DEFAULT_DAYTRADE_PARTIAL_SCALE_PCT,
            enable_partial_scale=True
        )

        self.raw_signals = []
        self.executed_trades = []

    def _prepare_ticker_data(self, ticker: str) -> pd.DataFrame:
        df = get_1m_bars(ticker, days=self.days, auto_sync=True)
        if df.empty:
            return df

        df = df.copy()
        df['Datetime'] = df.index
        df['Date'] = df.index.date
        df['Time'] = df.index.time

        # Calculate VWAP
        df['TP'] = (df['High'] + df['Low'] + df['Close']) / 3.0
        df['Vol_x_TP'] = df['TP'] * df['Volume']
        cum_vol = df.groupby('Date')['Volume'].cumsum()
        cum_vol_x_tp = df.groupby('Date')['Vol_x_TP'].cumsum()
        df['VWAP'] = cum_vol_x_tp / cum_vol
        df['Vol_SMA10'] = df['Volume'].rolling(10).mean()
        return df

    def generate_signals(self):
        print(f"Scanning {len(self.tickers)} tickers for historical VWAP Reclaim setups...")
        for ticker in self.tickers:
            try:
                df = self._prepare_ticker_data(ticker)
                if df.empty:
                    continue
                sigs = self.engine.scan_dataframe(ticker, df)
                self.raw_signals.extend(sigs)
            except Exception as e:
                print(f"Warning: Could not process {ticker} ({e})")

    def run_portfolio_simulation(self):
        self.raw_signals.sort(key=lambda x: pd.to_datetime(x['Entry Time']))

        # Load Index Gate (SPY intraday VWAP)
        spy_vwap_map = {}
        if self.index_gate:
            try:
                spy_df = self._prepare_ticker_data("SPY")
                if not spy_df.empty:
                    spy_df['SPY_Above_VWAP'] = spy_df['Close'] >= spy_df['VWAP']
                    spy_vwap_map = dict(zip(spy_df['Datetime'].astype(str).str[:19], spy_df['SPY_Above_VWAP']))
            except Exception as e:
                print(f"Warning: Could not load SPY index gate ({e})")

        equity = self.start_capital
        total_deposited = self.start_capital
        peak_equity = equity
        max_drawdown = 0.0
        current_week = None
        locked_until = None
        trades_per_day = {}
        days_to_1000 = None
        first_trade_dt = None

        for sig in self.raw_signals:
            sig_dt = pd.to_datetime(sig['Entry Time'])
            sig_date = sig.get('Date', sig_dt.date())
            dt_key = str(sig['Entry Time'])[:19]
            ticker = sig['Ticker']

            if first_trade_dt is None:
                first_trade_dt = sig_dt

            # Weekly Deposit Injection
            if self.weekly_deposit > 0:
                iso_year, iso_week, _ = sig_date.isocalendar()
                week_key = (iso_year, iso_week)
                if current_week is None:
                    current_week = week_key
                elif week_key != current_week:
                    equity += self.weekly_deposit
                    total_deposited += self.weekly_deposit
                    current_week = week_key
                    if equity > peak_equity:
                        peak_equity = equity

            # Enforce max trades per day
            if trades_per_day.get(sig_date, 0) >= self.max_trades_per_day:
                continue

            # SPY Index Gate Alignment
            if self.index_gate and spy_vwap_map:
                if not spy_vwap_map.get(dt_key, True):
                    continue

            # Settlement / Capital Lock check
            if locked_until is not None and sig['Entry Time'] < locked_until:
                continue

            # Position Sizing
            stop_dist = sig['Unit'] / 3.0
            if stop_dist <= 0:
                continue

            entry_p = sig['Entry Price']
            stop_dist_pct = stop_dist / entry_p if entry_p > 0 else 0.0

            if self.full_cash_under_1k and equity < 1000.0:
                # 100% Cash Utilization for Small Account Compounding
                if stop_dist_pct <= self.max_stop_pct:
                    shares = round(equity / entry_p, 4) if self.fractional else int(equity / entry_p)
                else:
                    risk_dollars = equity * self.max_stop_pct
                    shares = round(risk_dollars / stop_dist, 4) if self.fractional else int(risk_dollars / stop_dist)
            else:
                risk_amount = equity * self.risk_pct
                shares = round(risk_amount / stop_dist, 4) if self.fractional else int(risk_amount / stop_dist)
                max_shares = round(equity / entry_p, 4) if self.fractional else int(equity / entry_p)
                shares = min(shares, max_shares)

            if shares <= 0:
                continue

            # Execute Trade
            gross_pnl = shares * (sig['Exit Price'] - sig['Entry Price'])
            equity += gross_pnl

            if equity > peak_equity:
                peak_equity = equity
            dd = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0.0
            if dd > max_drawdown:
                max_drawdown = dd

            locked_until = sig['Exit Time']
            trades_per_day[sig_date] = trades_per_day.get(sig_date, 0) + 1

            if equity >= 1000.0 and days_to_1000 is None and first_trade_dt is not None:
                days_to_1000 = (sig_dt - first_trade_dt).days

            self.executed_trades.append({
                'Ticker': ticker,
                'Strategy': 'VWAP_RECLAIM',
                'Entry Time': sig['Entry Time'],
                'Exit Time': sig['Exit Time'],
                'Shares': shares,
                'Entry Price': round(sig['Entry Price'], 2),
                'Exit Price': round(sig['Exit Price'], 2),
                'Net PnL': round(gross_pnl, 2),
                'Reason': sig['Reason'],
                'Equity': round(equity, 2)
            })

        self.print_results(equity, total_deposited, max_drawdown, days_to_1000)

    def print_results(self, final_equity, total_deposited, max_dd, days_to_1000):
        if not self.executed_trades:
            print("\nNo trades executed with current parameters.")
            return

        df = pd.DataFrame(self.executed_trades)
        total_trades = len(df)
        wins = len(df[df['Net PnL'] > 0])
        win_rate = (wins / total_trades) * 100
        gross_profit = df[df['Net PnL'] > 0]['Net PnL'].sum()
        gross_loss = abs(df[df['Net PnL'] < 0]['Net PnL'].sum())
        profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else float('inf')
        trading_pnl = final_equity - total_deposited

        print("\n" + "=" * 55)
        print(" VWAP RECLAIM PORTFOLIO SIMULATION RESULTS")
        print("=" * 55)
        print(f"Starting Balance:   ${self.start_capital:.2f}")
        print(f"Total Deposited:    ${total_deposited:.2f} (${self.weekly_deposit:.2f}/week)")
        print(f"Net Trading PnL:    ${trading_pnl:+.2f}")
        print(f"Final Account Value:${final_equity:.2f}")
        print(f"Total Trades:       {total_trades}")
        print(f"Win Rate:           {win_rate:.1f}%")
        print(f"Profit Factor:      {profit_factor}")
        print(f"Max Drawdown:       -{max_dd * 100:.2f}%")
        if days_to_1000:
            print(f"Days to $1,000:     {days_to_1000} calendar days (~{days_to_1000 // 30} months)")
        else:
            print(f"Days to $1,000:     Not reached within timeframe")

        print("\n--- PERFORMANCE BY TICKER ---")
        t_stats = df.groupby('Ticker').agg(
            Trades=('Net PnL', 'count'),
            Net_PnL=('Net PnL', 'sum'),
            Wins=('Net PnL', lambda x: (x > 0).sum())
        )
        t_stats['Win_Rate'] = (t_stats['Wins'] / t_stats['Trades']) * 100
        for t, row in t_stats.iterrows():
            print(f" {t:5s} : {int(row['Trades']):2d} Trades | Net: ${row['Net_PnL']:+7.2f} | Win Rate: {row['Win_Rate']:4.1f}%")

        print("\n--- EXIT REASONS ---")
        print(df['Reason'].value_counts())
        print("=" * 55)


def main():
    parser = argparse.ArgumentParser(description="VWAP Reclaim Backtest Runner")
    parser.add_argument("--capital", type=float, default=70.0, help="Starting capital (default: 70.0)")
    parser.add_argument("--deposit", type=float, default=15.0, help="Weekly deposit (default: 15.0)")
    parser.add_argument("--days", type=int, default=None, help="Lookback days")
    parser.add_argument("--tickers", nargs="+", default=None, help="Specific tickers to backtest")
    parser.add_argument("--show-trades", action="store_true", help="Print trade ledger")
    args = parser.parse_args()

    port = VWAPReclaimPortfolio(
        tickers=args.tickers,
        start_capital=args.capital,
        weekly_deposit=args.deposit,
        days=args.days
    )
    port.generate_signals()
    port.run_portfolio_simulation()

    if args.show_trades and port.executed_trades:
        df = pd.DataFrame(port.executed_trades)
        print("\nTrade Ledger:")
        print(df.to_string())


if __name__ == "__main__":
    main()

import pandas as pd
from datetime import datetime, time
try:
    from data_engine_portfolio import get_strategy_data
except ImportError:
    from late_entry_day_trade.data_engine_portfolio import get_strategy_data

class FashionablyLatePortfolio:
    def __init__(
        self,
        tickers,
        start_capital=1000.0,
        risk_pct=0.02,
        max_trades_per_day: int = 1,
        morning_cutoff: str = "10:45",
        midday_max_unit_pct: float = 0.0075
    ):
        self.tickers = tickers
        self.start_capital = start_capital
        self.risk_pct = risk_pct 
        self.max_trades_per_day = max_trades_per_day
        self.morning_cutoff = datetime.strptime(morning_cutoff, "%H:%M").time() if isinstance(morning_cutoff, str) else morning_cutoff
        self.midday_max_unit_pct = midday_max_unit_pct
        
        self.raw_signals = []
        self.executed_trades = []
        
    def generate_signals(self):
        print(f"Scanning {len(self.tickers)} tickers for historical setups...")
        for ticker in self.tickers:
            try:
                df = get_strategy_data(ticker)
                self._scan_ticker(ticker, df)
            except Exception:
                pass # Silently skip tickers with missing data
                
    def _scan_ticker(self, ticker, df):
        df['Prev_EMA_9'] = df['EMA_9'].shift(1)
        df['Prev_VWAP'] = df['VWAP'].shift(1)
        
        in_trade = False
        entry_price = stop_loss = target = unit = 0.0
        entry_time = None
        entry_idx = 0
        
        for i in range(1, len(df)):
            row = df.iloc[i]
            current_time = row['Time']
            current_dt = row['Datetime']
            
            if in_trade:
                mins_in_trade = i - entry_idx
                
                # Pessimistic Backtesting: Assume stop loss is hit before target if both occur in the same minute
                if row['Low'] <= stop_loss:
                    self._record_signal(ticker, entry_time, current_dt, entry_price, stop_loss, stop_loss, "STOP_LOSS", unit)
                    in_trade = False
                    continue
                    
                if row['High'] >= target:
                    self._record_signal(ticker, entry_time, current_dt, entry_price, stop_loss, target, "TARGET_3R", unit)
                    in_trade = False
                    continue
                    
                # 15-Minute Chop Time-Stop
                if mins_in_trade >= 15:
                    progress_thresh = entry_price + ((target - entry_price) * 0.3)
                    if row['Close'] < progress_thresh:
                        self._record_signal(ticker, entry_time, current_dt, entry_price, stop_loss, row['Close'], "CHOP_TIME_STOP", unit)
                        in_trade = False
                        continue
                        
                # End of Day Flush
                if current_time >= time(15, 58):
                    self._record_signal(ticker, entry_time, current_dt, entry_price, stop_loss, row['Close'], "EOD_EXIT", unit)
                    in_trade = False
                continue
                
            # Scan for Entry Parameters
            valid_time = (time(10, 0) <= current_time <= time(10, 45)) or (time(10, 46) <= current_time <= time(13, 30))
            if not valid_time: continue
            
            cross_up = (row['EMA_9'] > row['VWAP']) and (row['Prev_EMA_9'] <= row['Prev_VWAP'])
            
            if cross_up and (row['EMA_9'] > row['Prev_EMA_9']):
                near_5 = abs(row['Close'] - row['SMA_5']) / row['SMA_5'] < 0.03
                near_10 = abs(row['Close'] - row['SMA_10']) / row['SMA_10'] < 0.03
                
                if near_5 or near_10:
                    lod = row['LOD']
                    entry_price = row['Close']
                    if entry_price <= lod: continue # Data anomaly failsafe
                    
                    unit = entry_price - lod
                    in_trade = True
                    entry_price = entry_price
                    stop_loss = entry_price - (unit / 3.0)
                    target = entry_price + unit
                    entry_time = current_dt
                    entry_idx = i

    def _record_signal(self, ticker, entry_dt, exit_dt, entry_price, stop_loss, exit_price, reason, unit=0.0):
        unit_pct = (unit / entry_price) if entry_price > 0 else 0.0
        self.raw_signals.append({
            'Ticker': ticker,
            'Entry Time': entry_dt,
            'Exit Time': exit_dt,
            'Entry Price': entry_price,
            'Stop Loss': stop_loss,
            'Exit Price': exit_price,
            'Reason': reason,
            'Unit': unit,
            'Unit Pct': unit_pct
        })

    def run_portfolio_simulation(self):
        # Sort all theoretical setups chronologically to mimic live market execution
        self.raw_signals.sort(key=lambda x: x['Entry Time'])
        
        equity = self.start_capital
        peak_equity = equity
        max_drawdown = 0.0
        locked_until = None
        trades_per_day = {}
        
        for sig in self.raw_signals:
            sig_dt = sig['Entry Time']
            sig_date = sig_dt.date() if hasattr(sig_dt, 'date') else sig_dt
            sig_time = sig_dt.time() if hasattr(sig_dt, 'time') else sig_dt

            # Enforce 1 Trade Per Day (Cash Account Limit)
            if trades_per_day.get(sig_date, 0) >= self.max_trades_per_day:
                continue

            # Enforce Afternoon Tight Unit Filter
            if sig_time > self.morning_cutoff:
                if sig.get('Unit Pct', 0.0) > self.midday_max_unit_pct:
                    continue

            if locked_until is not None and sig['Entry Time'] < locked_until:
                continue
                
            stop_dist = sig['Entry Price'] - sig['Stop Loss']
            if stop_dist <= 0: continue
            
            # Position Sizing Math
            risk_amount = equity * self.risk_pct
            shares = int(risk_amount / stop_dist)
            
            # Constraint: Cannot buy more shares than available account cash
            max_shares_cash = int(equity / sig['Entry Price'])
            shares = min(shares, max_shares_cash)
            
            if shares <= 0:
                continue
                
            # Execute Trade
            gross_pnl = shares * (sig['Exit Price'] - sig['Entry Price'])
            equity += gross_pnl
            
            # Track Equity Curve for Drawdown
            if equity > peak_equity:
                peak_equity = equity
            dd = (peak_equity - equity) / peak_equity
            if dd > max_drawdown:
                max_drawdown = dd
                
            # Lock the capital until this trade completes
            locked_until = sig['Exit Time']
            trades_per_day[sig_date] = trades_per_day.get(sig_date, 0) + 1
            
            self.executed_trades.append({
                'Ticker': sig['Ticker'],
                'Entry Time': sig['Entry Time'],
                'Exit Time': sig['Exit Time'],
                'Shares': shares,
                'Entry Price': round(sig['Entry Price'], 2),
                'Exit Price': round(sig['Exit Price'], 2),
                'Net PnL': round(gross_pnl, 2),
                'Reason': sig['Reason'],
                'Equity': round(equity, 2)
            })
            
        self.print_results(equity, max_drawdown)

    def print_results(self, final_equity, max_dd):
        if not self.executed_trades:
            print("\nNo trades executed. Increase ticker list or wait for better market momentum.")
            return
            
        df = pd.DataFrame(self.executed_trades)
        
        total_trades = len(df)
        wins = len(df[df['Net PnL'] > 0])
        win_rate = (wins / total_trades) * 100
        
        gross_profit = df[df['Net PnL'] > 0]['Net PnL'].sum()
        gross_loss = abs(df[df['Net PnL'] < 0]['Net PnL'].sum())
        profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else float('inf')
        
        net_profit = final_equity - self.start_capital
        net_profit_pct = (net_profit / self.start_capital) * 100
        
        # Formatted Output
        print("\n" + "="*40)
        print(f"Starting Balance:  ${self.start_capital:.2f}")
        print(f"Final Balance:     ${final_equity:.2f}")
        print(f"Net Profit:        ${net_profit:+.2f} ({net_profit_pct:+.2f}%)")
        print(f"Total Trades:      {total_trades}")
        print(f"Win Rate:          {win_rate:.1f}%")
        print(f"Profit Factor:     {profit_factor}")
        print(f"Max Drawdown:      -{max_dd * 100:.2f}%")
        
        print("\n--- EXIT REASONS ---")
        print(df['Reason'].value_counts().to_string())
        
        csv_filename = "fashionably_late_trades.csv"
        df.to_csv(csv_filename, index=False)
        print(f"\nTrade log saved to {csv_filename}")
        print("="*40 + "\n")

if __name__ == "__main__":
    import sys, os
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    try:
        from config import DAYTRADE_TICKERS as tickers
    except ImportError:
        # Curated Elite Day-Trading Universe
        tickers = ["ARM", "HOOD", "PLTR", "AMZN", "AAPL", "GOOGL"]
    
    sim = FashionablyLatePortfolio(tickers, start_capital=1000.0, risk_pct=0.02)
    sim.generate_signals()
    sim.run_portfolio_simulation()
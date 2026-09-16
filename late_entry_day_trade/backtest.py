import pandas as pd
from datetime import time
try:
    from data_engine import get_strategy_data
except ImportError:
    from late_entry_day_trade.data_engine import get_strategy_data

class FashionablyLateBacktester:
    def __init__(self, ticker):
        self.ticker = ticker
        self.df = get_strategy_data(ticker)
        self.trades = []
        self.in_trade = False
        
        # State tracking
        self.entry_price = 0.0
        self.stop_loss = 0.0
        self.target = 0.0
        self.entry_time = None
        self.entry_index = 0
        self.current_date = None
        
    def run(self):
        # Shift values to detect the exact minute the crossover happens
        self.df['Prev_EMA_9'] = self.df['EMA_9'].shift(1)
        self.df['Prev_VWAP'] = self.df['VWAP'].shift(1)
        
        for i in range(1, len(self.df)):
            row = self.df.iloc[i]
            
            # --- 1. MANAGE ACTIVE TRADE ---
            if self.in_trade:
                minutes_in_trade = i - self.entry_index
                
                # Check Target (Win)
                if row['High'] >= self.target:
                    self.close_trade(row['Time'], self.target, "Target Hit")
                    continue
                    
                # Check Stop Loss (Loss)
                if row['Low'] <= self.stop_loss:
                    self.close_trade(row['Time'], self.stop_loss, "Stop Loss Hit")
                    continue
                    
                # The 15-Minute "Chop" Time-Stop Bailout
                if minutes_in_trade >= 15:
                    # If price hasn't moved at least 30% of the way to target, bail.
                    progress_threshold = self.entry_price + ((self.target - self.entry_price) * 0.3)
                    if row['Close'] < progress_threshold:
                        self.close_trade(row['Time'], row['Close'], "Time Stop (Chop/No Momentum)")
                        continue
                        
                # Ensure we close out completely at the end of the market day
                if row['Time'] >= time(15, 58):
                    self.close_trade(row['Time'], row['Close'], "End of Day Close")
                continue
                
            # --- 2. SCAN FOR NEW ENTRY ---
            current_time = row['Time']
            
            # Spiro's strict execution windows
            valid_morning = time(10, 0) <= current_time <= time(10, 45)
            valid_midday = time(10, 46) <= current_time <= time(13, 30)
            if not (valid_morning or valid_midday):
                continue
                
            # Trigger: 9 EMA crosses strictly ABOVE VWAP
            cross_up = (row['EMA_9'] > row['VWAP']) and (row['Prev_EMA_9'] <= row['Prev_VWAP'])
            
            if cross_up:
                # Nuance: Reject flat momentum (EMA slope must be positive)
                if row['EMA_9'] <= row['Prev_EMA_9']:
                    continue 
                    
                # Nuance: Daily Context (Price must be pulling back to the Daily 5 or 10 SMA)
                # We define "pulling back" as being within 3% of the SMA line
                near_5 = abs(row['Close'] - row['SMA_5']) / row['SMA_5'] < 0.03
                near_10 = abs(row['Close'] - row['SMA_10']) / row['SMA_10'] < 0.03
                if not (near_5 or near_10):
                    continue
                
                # Setup the Measured Move bounds
                lod = row['LOD']
                entry_price = row['Close'] # Simulation enters exactly as the 1m crossing candle closes
                
                if entry_price <= lod:
                    continue # Failsafe against bad data
                    
                unit = entry_price - lod
                
                self.in_trade = True
                self.entry_price = entry_price
                self.target = entry_price + unit
                self.stop_loss = entry_price - (unit / 3.0)
                self.entry_time = current_time
                self.entry_index = i
                self.current_date = row['Date']

    def close_trade(self, exit_time, exit_price, reason):
        pnl = exit_price - self.entry_price
        pnl_pct = pnl / self.entry_price
        
        self.trades.append({
            "Date": self.current_date,
            "Entry Time": self.entry_time,
            "Exit Time": exit_time,
            "Entry Price": round(self.entry_price, 2),
            "Exit Price": round(exit_price, 2),
            "PnL %": round(pnl_pct * 100, 2),
            "Reason": reason
        })
        self.in_trade = False

    def print_results(self):
        if not self.trades:
            print(f"\nNo trades triggered for {self.ticker} in the last 7 days.")
            return
            
        trades_df = pd.DataFrame(self.trades)
        wins = len(trades_df[trades_df['PnL %'] > 0])
        win_rate = (wins / len(trades_df)) * 100
        
        print(f"\n=== BACKTEST RESULTS: {self.ticker} (Last 7 Days) ===")
        print(f"Total Trades: {len(trades_df)}")
        print(f"Win Rate: {win_rate:.1f}%")
        print(f"Total PnL %: {trades_df['PnL %'].sum():.2f}%\n")
        print(trades_df.to_string(index=False))


if __name__ == "__main__":
    import sys, os
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    try:
        from config import DAYTRADE_TICKERS as tickers_to_test
    except ImportError:
        tickers_to_test = ["ARM", "HOOD", "PLTR", "AMZN", "AAPL", "GOOGL"]
    
    for symbol in tickers_to_test:
        try:
            bot = FashionablyLateBacktester(symbol)
            bot.run()
            bot.print_results()
        except Exception as e:
            print(f"Error testing {symbol}: {e}")
import os
import sys
import pandas as pd
from datetime import datetime, time

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

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
        midday_max_unit_pct: float = 0.0075,
        min_close_pct: float = 0.60,
        min_vol_ratio: float = 0.80,
        ratchet_1_5r: bool = True,
        days: int = None,
        index_gate: bool = False,
        require_rs: bool = False,
        fractional: bool = False,
        enable_chop_stop: bool = False
    ):
        self.tickers = tickers
        self.start_capital = start_capital
        self.risk_pct = risk_pct 
        self.max_trades_per_day = max_trades_per_day
        self.morning_cutoff = datetime.strptime(morning_cutoff, "%H:%M").time() if isinstance(morning_cutoff, str) else morning_cutoff
        self.midday_max_unit_pct = midday_max_unit_pct
        self.min_close_pct = min_close_pct
        self.min_vol_ratio = min_vol_ratio
        self.ratchet_1_5r = ratchet_1_5r
        self.days = days
        self.index_gate = index_gate
        self.require_rs = require_rs
        self.fractional = fractional
        self.enable_chop_stop = enable_chop_stop
        
        self.raw_signals = []
        self.executed_trades = []
        
    def generate_signals(self):
        print(f"Scanning {len(self.tickers)} tickers for historical setups...")
        for ticker in self.tickers:
            try:
                df = get_strategy_data(ticker, days=self.days)
                self._scan_ticker(ticker, df)
            except Exception:
                pass # Silently skip tickers with missing data
                
    def _scan_ticker(self, ticker, df):
        df['Prev_EMA_9'] = df['EMA_9'].shift(1)
        df['Prev_VWAP'] = df['VWAP'].shift(1)
        df['Vol_SMA10'] = df['Volume'].rolling(10).mean()
        
        in_trade = False
        entry_price = stop_loss = target = unit = 0.0
        entry_time = None
        entry_idx = 0
        
        for i in range(10, len(df)):
            row = df.iloc[i]
            current_time = row['Time']
            current_dt = row['Datetime']
            
            if in_trade:
                mins_in_trade = i - entry_idx
                
                # +1.5R Breakeven Ratchet (Protects small account gains)
                if self.ratchet_1_5r and stop_loss < entry_price:
                    half_target = entry_price + (unit * 0.5)  # +1.5R in 3:1 geometry
                    if row['High'] >= half_target:
                        stop_loss = entry_price  # Ratchet stop to Breakeven
                
                # Stop Loss check
                if row['Low'] <= stop_loss:
                    reason = "BREAKEVEN" if abs(stop_loss - entry_price) < 0.02 else "STOP_LOSS"
                    self._record_signal(ticker, entry_time, current_dt, entry_price, stop_loss, stop_loss, reason, unit)
                    in_trade = False
                    continue
                    
                if row['High'] >= target:
                    self._record_signal(ticker, entry_time, current_dt, entry_price, stop_loss, target, "TARGET_3R", unit)
                    in_trade = False
                    continue
                    
                # 15-Minute Chop Time-Stop (disabled by default under NO_CHOP_STOP policy)
                if self.enable_chop_stop and mins_in_trade >= 15:
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
                    # 1. Bullish Close Filter (Must close in upper 40% of candle range)
                    c_range = row['High'] - row['Low']
                    cpct = (row['Close'] - row['Low']) / c_range if c_range > 0 else 0.5
                    if cpct < self.min_close_pct:
                        continue

                    # 2. Volume Participation Filter (>= min_vol_ratio x 10-bar SMA)
                    vol_sma = row['Vol_SMA10']
                    if vol_sma > 0 and (row['Volume'] / vol_sma) < self.min_vol_ratio:
                        continue

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
            'Initial Stop': entry_price - (unit / 3.0),
            'Exit Price': exit_price,
            'Reason': reason,
            'Unit': unit,
            'Unit Pct': unit_pct
        })

    def run_portfolio_simulation(self):
        # Sort all theoretical setups chronologically to mimic live market execution
        self.raw_signals.sort(key=lambda x: x['Entry Time'])
        
        # 1. Load Index Gate Data (QQQ Daily 50 EMA + SPY/QQQ Intraday 1m VWAP)
        qqq_regime = {}
        spy_vwap_map = {}
        qqq_vwap_map = {}
        
        if self.index_gate:
            print("Applying Index Gate (QQQ Daily 50 EMA Regime + Intraday SPY/QQQ VWAP)...")
            try:
                import yfinance as yf
                qqq_df = yf.download("QQQ", period="3y", interval="1d", progress=False)
                if isinstance(qqq_df.columns, pd.MultiIndex):
                    qqq_df.columns = qqq_df.columns.get_level_values(0)
                qqq_df["EMA_50"] = qqq_df["Close"].ewm(span=50, adjust=False).mean()
                qqq_df["Above_50EMA"] = qqq_df["Close"].shift(1) > qqq_df["EMA_50"].shift(1)
                qqq_regime = dict(zip(pd.to_datetime(qqq_df.index).date, qqq_df["Above_50EMA"]))
            except Exception as e:
                print(f"Warning: Could not fetch QQQ daily regime data ({e})")

            try:
                import sqlite3
                db_path = os.path.join(ROOT_DIR, "data", "intraday_1m.db")
                con = sqlite3.connect(db_path)
                bench_df = pd.read_sql("""
                    SELECT ticker, datetime, open, high, low, close, volume 
                    FROM bars_1m 
                    WHERE ticker IN ('SPY', 'QQQ')
                    ORDER BY datetime ASC
                """, con)
                con.close()
                bench_df['Date'] = pd.to_datetime(bench_df['datetime']).dt.date
                bench_df['Typical_Price'] = (bench_df['high'] + bench_df['low'] + bench_df['close']) / 3.0
                bench_df['Vol_x_TP'] = bench_df['Typical_Price'] * bench_df['volume']

                spy_df = bench_df[bench_df['ticker'] == 'SPY'].copy()
                spy_vol = spy_df.groupby('Date')['volume'].cumsum()
                spy_tp = spy_df.groupby('Date')['Vol_x_TP'].cumsum()
                spy_df['SPY_VWAP'] = spy_tp / spy_vol
                spy_df['SPY_Above_VWAP'] = spy_df['close'] >= spy_df['SPY_VWAP']
                spy_vwap_map = dict(zip(spy_df['datetime'], spy_df['SPY_Above_VWAP']))

                qqq_df = bench_df[bench_df['ticker'] == 'QQQ'].copy()
                qqq_vol = qqq_df.groupby('Date')['volume'].cumsum()
                qqq_tp = qqq_df.groupby('Date')['Vol_x_TP'].cumsum()
                qqq_df['QQQ_VWAP'] = qqq_tp / qqq_vol
                qqq_df['QQQ_Below_VWAP'] = qqq_df['close'] < qqq_df['QQQ_VWAP']
                qqq_vwap_map = dict(zip(qqq_df['datetime'], qqq_df['QQQ_Below_VWAP']))
                print(f"Loaded intraday VWAP tide for {len(spy_vwap_map)} SPY bars and {len(qqq_vwap_map)} QQQ bars.")
            except Exception as e:
                print(f"Warning: Could not load index intraday VWAP from vault ({e})")

        # 2. Load 20-Day Relative Strength Data if enabled
        rs_map = {}
        if self.require_rs:
            print("Applying Relative Strength Filter (20-day return >= SPY)...")
            try:
                import yfinance as yf
                spy_d = yf.download("SPY", period="3y", interval="1d", progress=False)
                if isinstance(spy_d.columns, pd.MultiIndex):
                    spy_d.columns = spy_d.columns.get_level_values(0)
                spy_ret = spy_d["Close"].pct_change(20).shift(1)
                spy_ret.index = pd.to_datetime(spy_ret.index).date

                for t in self.tickers:
                    if t in ['SPY', 'QQQ', 'PSQ', 'SH']:
                        continue
                    t_d = yf.download(t, period="3y", interval="1d", progress=False)
                    if isinstance(t_d.columns, pd.MultiIndex):
                        t_d.columns = t_d.columns.get_level_values(0)
                    t_ret = t_d["Close"].pct_change(20).shift(1)
                    t_ret.index = pd.to_datetime(t_ret.index).date
                    merged_rs = (t_ret - spy_ret).dropna()
                    rs_map[t] = dict(merged_rs)
            except Exception as e:
                print(f"Warning: Could not compute relative strength ({e})")

        equity = self.start_capital
        peak_equity = equity
        max_drawdown = 0.0
        locked_until = None
        trades_per_day = {}
        
        for sig in self.raw_signals:
            sig_dt = sig['Entry Time']
            sig_date = sig_dt.date() if hasattr(sig_dt, 'date') else sig_dt
            sig_time = sig_dt.time() if hasattr(sig_dt, 'time') else sig_dt
            ticker = sig['Ticker']
            dt_key = str(sig_dt)[:19]

            # Enforce 1 Trade Per Day (Cash Account Limit)
            if trades_per_day.get(sig_date, 0) >= self.max_trades_per_day:
                continue

            # Enforce Market Regime & Index Gate Logic
            regime = "BULL"
            if self.index_gate:
                if qqq_regime:
                    is_bull = qqq_regime.get(sig_date, True)
                    regime = "BULL" if is_bull else "BEAR"
                    if not is_bull:
                        # Bear / Correction Regime (QQQ <= 50 EMA):
                        # Block high-beta growth stocks that drag during pullbacks
                        if ticker in ['ARM', 'HOOD']:
                            continue
                    else:
                        # Bull Expansion Regime (QQQ > 50 EMA):
                        # Block inverse ETFs (don't short in a bull market)
                        if ticker in ['PSQ', 'SH']:
                            continue

                # Intraday Index VWAP Tide Gate
                if ticker in ['PSQ', 'SH']:
                    # Inverse trades require QQQ dropping below VWAP intraday
                    if qqq_vwap_map and not qqq_vwap_map.get(dt_key, True):
                        continue
                else:
                    # Long trades require SPY lifting above VWAP intraday
                    if spy_vwap_map and not spy_vwap_map.get(dt_key, True):
                        continue

            # Enforce 20-Day Relative Strength (RS >= 0)
            if self.require_rs and ticker not in ['PSQ', 'SH']:
                ticker_rs = rs_map.get(ticker, {}).get(sig_date, 0.0)
                if ticker_rs < 0.0:
                    continue

            # Enforce Afternoon Tight Unit Filter
            if sig_time > self.morning_cutoff:
                if sig.get('Unit Pct', 0.0) > self.midday_max_unit_pct:
                    continue

            if locked_until is not None and sig['Entry Time'] < locked_until:
                continue
                
            # Initial stop distance (1/3 unit) for position sizing
            stop_dist = sig['Unit'] / 3.0
            if stop_dist <= 0: continue
            
            # Position Sizing Math
            risk_amount = equity * self.risk_pct
            if self.fractional:
                shares = round(risk_amount / stop_dist, 4)
                max_shares_cash = round(equity / sig['Entry Price'], 4)
                shares = min(shares, max_shares_cash)
            else:
                shares = int(risk_amount / stop_dist)
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
                'Ticker': ticker,
                'Entry Time': sig['Entry Time'],
                'Exit Time': sig['Exit Time'],
                'Shares': shares,
                'Entry Price': round(sig['Entry Price'], 2),
                'Exit Price': round(sig['Exit Price'], 2),
                'Net PnL': round(gross_pnl, 2),
                'Reason': sig['Reason'],
                'Regime': regime,
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
        
        if 'Regime' in df.columns:
            print("\n--- PERFORMANCE BY REGIME ---")
            for reg, grp in df.groupby('Regime'):
                r_wins = grp[grp['Net PnL'] > 0]['Net PnL'].sum()
                r_loss = abs(grp[grp['Net PnL'] < 0]['Net PnL'].sum())
                r_pf = round(r_wins / r_loss, 2) if r_loss > 0 else float('inf')
                r_wr = (len(grp[grp['Net PnL'] > 0]) / len(grp)) * 100
                print(f" {reg:4s} Regime: {len(grp):3d} Trades | Net: ${grp['Net PnL'].sum():+7.2f} | Win Rate: {r_wr:4.1f}% | Profit Factor: {r_pf:4.2f}")

        print("\n--- PERFORMANCE BY TICKER ---")
        for t, grp in df.groupby('Ticker'):
            t_wins = grp[grp['Net PnL'] > 0]['Net PnL'].sum()
            t_loss = abs(grp[grp['Net PnL'] < 0]['Net PnL'].sum())
            t_pf = round(t_wins / t_loss, 2) if t_loss > 0 else float('inf')
            t_wr = (len(grp[grp['Net PnL'] > 0]) / len(grp)) * 100
            t_3r = (grp['Reason'] == 'TARGET_3R').sum()
            print(f" {t:5s}: {len(grp):2d} Trades | Net: ${grp['Net PnL'].sum():+6.2f} | Win Rate: {t_wr:4.1f}% | PF: {t_pf:4.2f} | 3R Targets: {t_3r:2d}")

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
        from config import (
            DAYTRADE_TICKERS as tickers,
            DEFAULT_DAYTRADE_MIN_CLOSE_PCT,
            DEFAULT_DAYTRADE_MIN_VOL_RATIO,
            DEFAULT_DAYTRADE_RATCHET_1_5R,
        )
    except ImportError:
        # Curated Elite Day-Trading Universe
        tickers = ["ARM", "HOOD", "PLTR", "AMZN", "AAPL", "GOOGL"]
        DEFAULT_DAYTRADE_MIN_CLOSE_PCT = 0.60
        DEFAULT_DAYTRADE_MIN_VOL_RATIO = 0.80
        DEFAULT_DAYTRADE_RATCHET_1_5R = True
    
    sim = FashionablyLatePortfolio(
        tickers,
        start_capital=1000.0,
        risk_pct=0.02,
        min_close_pct=DEFAULT_DAYTRADE_MIN_CLOSE_PCT,
        min_vol_ratio=DEFAULT_DAYTRADE_MIN_VOL_RATIO,
        ratchet_1_5r=DEFAULT_DAYTRADE_RATCHET_1_5R
    )
    sim.generate_signals()
    sim.run_portfolio_simulation()
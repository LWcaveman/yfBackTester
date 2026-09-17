import os
import sys
import pandas as pd
from datetime import datetime, time

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

try:
    from data_engine_portfolio import get_strategy_data
except ImportError:
    from late_entry_day_trade.data_engine_portfolio import get_strategy_data

try:
    from config import (
        DEFAULT_DAYTRADE_ENABLE_DUAL_ENGINE,
        DEFAULT_DAYTRADE_ENABLE_PARTIAL_SCALE,
        DEFAULT_DAYTRADE_PARTIAL_SCALE_R,
        DEFAULT_DAYTRADE_PARTIAL_SCALE_PCT,
        DEFAULT_DAYTRADE_RUNNER_R,
        MIDDAY_REVERSION_TICKERS,
    )
except ImportError:
    DEFAULT_DAYTRADE_ENABLE_DUAL_ENGINE = False
    DEFAULT_DAYTRADE_ENABLE_PARTIAL_SCALE = False
    DEFAULT_DAYTRADE_PARTIAL_SCALE_R = 1.5
    DEFAULT_DAYTRADE_PARTIAL_SCALE_PCT = 0.33
    DEFAULT_DAYTRADE_RUNNER_R = 4.0
    MIDDAY_REVERSION_TICKERS = ["TQQQ", "CONL", "SOXL", "AAPL", "PLTR"]

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
        enable_chop_stop: bool = False,
        enable_dual_engine: bool = False,
        enable_partial_scale: bool = False,
        partial_scale_r: float = 1.5,
        partial_scale_pct: float = 0.33,
        runner_r: float = 4.0,
        midday_tickers: list = None
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
        self.enable_dual_engine = enable_dual_engine
        self.enable_partial_scale = enable_partial_scale
        self.partial_scale_r = partial_scale_r
        self.partial_scale_pct = partial_scale_pct
        self.runner_r = runner_r
        self.midday_tickers = midday_tickers if midday_tickers is not None else MIDDAY_REVERSION_TICKERS
        
        self.raw_signals = []
        self.executed_trades = []
        
    def generate_signals(self):
        print(f"Scanning {len(self.tickers)} tickers for historical morning momentum setups (Engine 1)...")
        for ticker in self.tickers:
            try:
                df = get_strategy_data(ticker, days=self.days)
                self._scan_ticker(ticker, df)
            except Exception:
                pass # Silently skip tickers with missing data
                
        if self.enable_dual_engine:
            print(f"Scanning {len(self.midday_tickers)} tickers for Midday VWAP 2-SD Reversion setups (Engine 2)...")
            for ticker in self.midday_tickers:
                try:
                    df = get_strategy_data(ticker, days=self.days)
                    self._scan_midday_reversion(ticker, df)
                except Exception:
                    pass
                
    def _scan_ticker(self, ticker, df):
        df['Prev_EMA_9'] = df['EMA_9'].shift(1)
        df['Prev_VWAP'] = df['VWAP'].shift(1)
        df['Vol_SMA10'] = df['Volume'].rolling(10).mean()
        
        highs = df['High'].values
        lows = df['Low'].values
        closes = df['Close'].values
        times = df['Time'].values
        dts = df['Datetime'].values
        ema9s = df['EMA_9'].values
        p_ema9s = df['Prev_EMA_9'].values
        vwaps = df['VWAP'].values
        p_vwaps = df['Prev_VWAP'].values
        sma5s = df['SMA_5'].values
        sma10s = df['SMA_10'].values
        vol_smas = df['Vol_SMA10'].values
        vols = df['Volume'].values
        lods = df['LOD'].values
        dates = df['Date'].values
        n = len(df)
        
        in_trade = False
        took_partial = False
        partial_price = 0.0
        entry_price = stop_loss = target = unit = 0.0
        entry_time = None
        entry_clock = None
        entry_date = None
        entry_idx = 0
        
        for i in range(10, n):
            current_time = times[i]
            current_dt = dts[i]
            
            if in_trade:
                mins_in_trade = i - entry_idx
                
                # Check Partial Scaling or Breakeven Ratchet
                if self.enable_partial_scale:
                    partial_tgt = entry_price + (unit / 3.0) * self.partial_scale_r
                    if not took_partial and highs[i] >= partial_tgt:
                        took_partial = True
                        partial_price = partial_tgt
                        stop_loss = entry_price  # Lock in partial and ratchet stop to Breakeven
                elif self.ratchet_1_5r and stop_loss < entry_price:
                    half_target = entry_price + (unit * 0.5)  # +1.5R in 3:1 geometry
                    if highs[i] >= half_target:
                        stop_loss = entry_price  # Ratchet stop to Breakeven
                
                # Stop Loss check
                if lows[i] <= stop_loss:
                    if took_partial:
                        exit_price = (partial_price * self.partial_scale_pct) + (stop_loss * (1.0 - self.partial_scale_pct))
                        reason = f"PARTIAL_{self.partial_scale_r}R_AND_BE"
                    else:
                        exit_price = stop_loss
                        reason = "BREAKEVEN" if abs(stop_loss - entry_price) < 0.02 else "STOP_LOSS"
                    self._record_signal(ticker, entry_time, current_dt, entry_price, stop_loss, exit_price, reason, unit, strategy="MORNING_MOMENTUM", entry_date=entry_date, entry_clock=entry_clock)
                    in_trade = False
                    continue
                    
                # Target check
                if highs[i] >= target:
                    if took_partial:
                        exit_price = (partial_price * self.partial_scale_pct) + (target * (1.0 - self.partial_scale_pct))
                        reason = f"PARTIAL_AND_RUNNER_{self.runner_r}R"
                    else:
                        exit_price = target
                        reason = f"TARGET_{self.runner_r}R" if self.enable_partial_scale else "TARGET_3R"
                    self._record_signal(ticker, entry_time, current_dt, entry_price, stop_loss, exit_price, reason, unit, strategy="MORNING_MOMENTUM", entry_date=entry_date, entry_clock=entry_clock)
                    in_trade = False
                    continue
                    
                # 15-Minute Chop Time-Stop (disabled by default under NO_CHOP_STOP policy)
                if self.enable_chop_stop and mins_in_trade >= 15:
                    progress_thresh = entry_price + ((target - entry_price) * 0.3)
                    if closes[i] < progress_thresh:
                        exit_price = (partial_price * self.partial_scale_pct + closes[i] * (1.0 - self.partial_scale_pct)) if took_partial else closes[i]
                        self._record_signal(ticker, entry_time, current_dt, entry_price, stop_loss, exit_price, "CHOP_TIME_STOP", unit, strategy="MORNING_MOMENTUM", entry_date=entry_date, entry_clock=entry_clock)
                        in_trade = False
                        continue
                        
                # End of Day Flush
                if current_time >= time(15, 58):
                    exit_price = (partial_price * self.partial_scale_pct + closes[i] * (1.0 - self.partial_scale_pct)) if took_partial else closes[i]
                    self._record_signal(ticker, entry_time, current_dt, entry_price, stop_loss, exit_price, "EOD_EXIT", unit, strategy="MORNING_MOMENTUM", entry_date=entry_date, entry_clock=entry_clock)
                    in_trade = False
                continue
                
            # Scan for Entry Parameters (Morning Window: 10:00 AM to morning_cutoff)
            valid_time = (time(10, 0) <= current_time <= self.morning_cutoff)
            if not valid_time: continue
            
            cross_up = (ema9s[i] > vwaps[i]) and (p_ema9s[i] <= p_vwaps[i])
            
            if cross_up and (ema9s[i] > p_ema9s[i]):
                near_5 = abs(closes[i] - sma5s[i]) / sma5s[i] < 0.03
                near_10 = abs(closes[i] - sma10s[i]) / sma10s[i] < 0.03
                
                if near_5 or near_10:
                    # 1. Bullish Close Filter (Must close in upper 40% of candle range)
                    c_range = highs[i] - lows[i]
                    cpct = (closes[i] - lows[i]) / c_range if c_range > 0 else 0.5
                    if cpct < self.min_close_pct:
                        continue

                    # 2. Volume Participation Filter (>= min_vol_ratio x 10-bar SMA)
                    vol_sma = vol_smas[i]
                    if vol_sma > 0 and (vols[i] / vol_sma) < self.min_vol_ratio:
                        continue

                    lod = lods[i]
                    entry_price = closes[i]
                    if entry_price <= lod: continue # Data anomaly failsafe
                    
                    unit = entry_price - lod
                    in_trade = True
                    took_partial = False
                    partial_price = 0.0
                    stop_loss = entry_price - (unit / 3.0)
                    if self.enable_partial_scale:
                        target = entry_price + (unit / 3.0) * self.runner_r
                    else:
                        target = entry_price + unit
                    entry_time = current_dt
                    entry_clock = times[i]
                    entry_date = dates[i]
                    entry_idx = i

    def _scan_midday_reversion(self, ticker, df):
        """Engine 2: Midday VWAP 2-SD Mean Reversion (11:30 AM - 1:30 PM)."""
        df['Vol_SMA10'] = df['Volume'].rolling(10).mean()
        highs = df['High'].values
        lows = df['Low'].values
        closes = df['Close'].values
        opens = df['Open'].values
        times = df['Time'].values
        dates = df['Date'].values
        dts = df['Datetime'].values
        vwaps = df['VWAP'].values
        lower_bands = df['VWAP_Lower_2SD'].values
        adxs = df['ADX_5m'].values
        atrs = df['ATR_1m'].values
        vols = df['Volume'].values
        vol_smas = df['Vol_SMA10'].values
        n = len(df)
        
        in_trade = False
        entry_price = stop_loss = target = unit = 0.0
        entry_time = None
        entry_clock = None
        entry_date = None
        entry_idx = 0
        
        for i in range(15, n):
            current_time = times[i]
            current_dt = dts[i]
            
            if in_trade:
                # Stop Loss check
                if lows[i] <= stop_loss:
                    self._record_signal(ticker, entry_time, current_dt, entry_price, stop_loss, stop_loss, "MIDDAY_STOP_LOSS", unit, strategy="MIDDAY_VWAP_REVERSION", entry_date=entry_date, entry_clock=entry_clock)
                    in_trade = False
                    continue
                    
                # Target check: Central VWAP
                if highs[i] >= vwaps[i]:
                    self._record_signal(ticker, entry_time, current_dt, entry_price, stop_loss, vwaps[i], "MIDDAY_TARGET_VWAP", unit, strategy="MIDDAY_VWAP_REVERSION", entry_date=entry_date, entry_clock=entry_clock)
                    in_trade = False
                    continue
                    
                # End of Day Flush
                if current_time >= time(15, 58):
                    self._record_signal(ticker, entry_time, current_dt, entry_price, stop_loss, closes[i], "MIDDAY_EOD", unit, strategy="MIDDAY_VWAP_REVERSION", entry_date=entry_date, entry_clock=entry_clock)
                    in_trade = False
                continue
                
            # Entry Window: 11:30 AM to 1:30 PM
            if not (time(11, 30) <= current_time <= time(13, 30)):
                continue
                
            # ADX < 25 Filter (Rangebound / Non-trending requirement)
            if adxs[i] > 25.0:
                continue
                
            # Setup: Price extended below Lower 2-SD Band
            if not (lows[i] <= lower_bands[i] or lows[i-1] <= lower_bands[i-1]):
                continue
                
            # Reversal Trigger: Hammer or Green Engulfing with volume
            rng = highs[i] - lows[i]
            if rng <= 0: continue
            is_hammer = (min(opens[i], closes[i]) - lows[i]) / rng >= 0.40 and closes[i] > opens[i]
            is_green_engulf = closes[i] > opens[i] and closes[i] > highs[i-1]
            vol_ok = vols[i] >= vol_smas[i] * 0.75
            
            if (is_hammer or is_green_engulf) and vol_ok:
                entry_price = closes[i]
                atr = atrs[i]
                stop_loss = lows[i] - (1.5 * atr)
                sd = entry_price - stop_loss
                if sd <= 0 or sd / entry_price < 0.001: continue
                
                # Unit definition for sizing compatibility (unit = 3 * stop_dist)
                unit = sd * 3.0
                in_trade = True
                entry_time = current_dt
                entry_clock = times[i]
                entry_date = dates[i]
                entry_idx = i

    def _record_signal(self, ticker, entry_dt, exit_dt, entry_price, stop_loss, exit_price, reason, unit=0.0, strategy="MORNING_MOMENTUM", entry_date=None, entry_clock=None):
        unit_pct = (unit / entry_price) if entry_price > 0 else 0.0
        self.raw_signals.append({
            'Ticker': ticker,
            'Entry Time': pd.to_datetime(entry_dt),
            'Exit Time': pd.to_datetime(exit_dt),
            'Date': entry_date if entry_date is not None else pd.to_datetime(entry_dt).date(),
            'Time': entry_clock if entry_clock is not None else pd.to_datetime(entry_dt).time(),
            'Entry Price': entry_price,
            'Stop Loss': stop_loss,
            'Initial Stop': entry_price - (unit / 3.0),
            'Exit Price': exit_price,
            'Reason': reason,
            'Unit': unit,
            'Unit Pct': unit_pct,
            'Strategy': strategy
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
        # Sort raw signals chronologically across all tickers and engines
        self.raw_signals.sort(key=lambda x: pd.to_datetime(x['Entry Time']))
        
        for sig in self.raw_signals:
            sig_dt = pd.to_datetime(sig['Entry Time'])
            sig_date = sig.get('Date', sig_dt.date())
            sig_time = sig.get('Time', sig_dt.time())
            ticker = sig['Ticker']
            strategy = sig.get('Strategy', 'MORNING_MOMENTUM')
            dt_key = str(sig['Entry Time'])[:19]

            # Enforce 1 Trade Per Day (Cash Account Limit)
            if trades_per_day.get(sig_date, 0) >= self.max_trades_per_day:
                continue

            # Enforce Market Regime & Index Gate Logic
            regime = "BULL"
            if self.index_gate and qqq_regime:
                is_bull = qqq_regime.get(sig_date, True)
                regime = "BULL" if is_bull else "BEAR"
                if not is_bull:
                    # Bear / Correction Regime (QQQ <= 50 EMA):
                    # Block high-beta growth stocks that drag during pullbacks
                    if ticker in ['ARM', 'HOOD', 'CONL', 'SOXL']:
                        continue
                else:
                    # Bull Expansion Regime (QQQ > 50 EMA):
                    # Block inverse ETFs (don't short in a bull market)
                    if ticker in ['PSQ', 'SH']:
                        continue

            # Engine-Specific Filters
            if strategy == 'MORNING_MOMENTUM':
                # Intraday Index VWAP Tide Gate
                if self.index_gate:
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

                # Enforce Afternoon Tight Unit Filter (for late morning momentum entries)
                if sig_time > self.morning_cutoff:
                    if sig.get('Unit Pct', 0.0) > self.midday_max_unit_pct:
                        continue
            elif strategy == 'MIDDAY_VWAP_REVERSION':
                # Midday VWAP Reversion is mean-reversion, already filtered by ADX < 25 and -2 SD touch!
                pass

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
                'Strategy': sig.get('Strategy', 'MORNING_MOMENTUM'),
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

        if 'Strategy' in df.columns and self.enable_dual_engine:
            print("\n--- PERFORMANCE BY ENGINE ---")
            for strat, grp in df.groupby('Strategy'):
                s_wins = grp[grp['Net PnL'] > 0]['Net PnL'].sum()
                s_loss = abs(grp[grp['Net PnL'] < 0]['Net PnL'].sum())
                s_pf = round(s_wins / s_loss, 2) if s_loss > 0 else float('inf')
                s_wr = (len(grp[grp['Net PnL'] > 0]) / len(grp)) * 100
                print(f" {strat:<24}: {len(grp):3d} Trades | Net: ${grp['Net PnL'].sum():+7.2f} | Win Rate: {s_wr:4.1f}% | PF: {s_pf:4.2f}")
        
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
            t_3r = (grp['Reason'].str.contains('TARGET')).sum()
            print(f" {t:5s}: {len(grp):2d} Trades | Net: ${grp['Net PnL'].sum():+6.2f} | Win Rate: {t_wr:4.1f}% | PF: {t_pf:4.2f} | Targets: {t_3r:2d}")

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
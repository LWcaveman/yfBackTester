import yfinance as yf
import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings('ignore')

# Curated Universe: Mega-caps, High-Beta Tech, Broad Indices, and Sector Leaders
TICKERS = [
    "SPY", "QQQ", "DIA", "IWM", "SMH", "XLF", "XLE", "XLV", 
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", 
    "AVGO", "NFLX", "AMD", "QCOM", "NOW", "PANW", "CRWD",
    "COST", "WMT", "JPM", "V", "MA", "UNH", "LLY", "XOM"
]

def analyze_ticker(ticker):
    try:
        df = yf.download(ticker, period="2y", interval="1d", progress=False)
        if df.empty or 'Close' not in df.columns:
            return None
            
        # Flatten MultiIndex columns if present (yfinance latest version fix)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
            
        df = df.dropna(subset=['Close']).copy()
        
        # Calculate Indicators
        df['EMA_20'] = df['Close'].ewm(span=20, adjust=False).mean()
        df['EMA_50'] = df['Close'].ewm(span=50, adjust=False).mean()
        df['EMA_200'] = df['Close'].ewm(span=200, adjust=False).mean()
        
        delta = df['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=4).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=4).mean()
        rs = gain / loss
        df['RSI_4'] = 100 - (100 / (1 + rs))
        
        high_low = df['High'] - df['Low']
        high_close = np.abs(df['High'] - df['Close'].shift())
        low_close = np.abs(df['Low'] - df['Close'].shift())
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        df['ATR_14'] = tr.rolling(window=14).mean()
        
        # Regimes
        df['Uptrend'] = (df['EMA_20'] > df['EMA_50']) & (df['EMA_50'] > df['EMA_200'])
        df['Sideways_Down'] = ~df['Uptrend']
        
        # Setup Definitions
        df['Entry_Trend'] = (df['Low'] <= df['EMA_20']) & (df['Close'] > df['EMA_20']) & df['Uptrend']
        df['Dist_from_50'] = (df['EMA_50'] - df['Close']) / df['EMA_50']
        df['Entry_Reversion'] = (df['RSI_4'] < 30) & df['Sideways_Down'] & (df['Dist_from_50'] > 0.03)

        last = df.iloc[-1]
        setup_triggered = None
        
        if last['Entry_Trend']:
            setup_triggered = 'Trend (1.5R)'
            target_mult = 1.5
            signal_col = 'Entry_Trend'
        elif last['Entry_Reversion']:
            setup_triggered = 'Rubber Band (2.0R)'
            target_mult = 2.0
            signal_col = 'Entry_Reversion'
        else:
            return None # Skip tickers with no setup today

        # Backtest the specific setup that triggered today
        trades = 0
        wins = 0
        signals = df[df[signal_col]].index
        
        for idx in signals:
            row_num = df.index.get_loc(idx)
            if row_num >= len(df) - 1:
                continue
                
            entry_price = df.iloc[row_num + 1]['Open']
            atr = df.iloc[row_num]['ATR_14']
            if pd.isna(atr): continue
            
            risk = 1.5 * atr
            stop = entry_price - risk
            target = entry_price + (target_mult * risk)
            
            hit = False
            for fwd in range(row_num + 1, len(df)):
                if df.iloc[fwd]['Low'] <= stop:
                    break
                if df.iloc[fwd]['High'] >= target:
                    hit = True
                    break
                    
            trades += 1
            if hit: wins += 1

        win_rate = (wins / trades) * 100 if trades > 0 else 0
        
        return {
            'Ticker': ticker,
            'Setup': setup_triggered,
            'Close': last['Close'],
            'Win Rate': f"{win_rate:.1f}%",
            'Hist Trades': trades
        }
        
    except Exception as e:
        return None

print("Scanning 30 high-quality institutional tickers for today's setups...")
results = []
for ticker in TICKERS:
    res = analyze_ticker(ticker)
    if res:
        # Only keep setups with a historical edge > 50%
        if float(res['Win Rate'].strip('%')) >= 50.0:
            results.append(res)

if results:
    df_res = pd.DataFrame(results).sort_values(by='Win Rate', ascending=False)
    print("\n=== HIGH PROBABILITY SETUPS FOUND TODAY ===")
    print(df_res.to_string(index=False))
else:
    print("\nNo high-probability setups triggered today. Keep your capital safe in cash.")
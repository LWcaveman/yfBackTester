import yfinance as yf
import pandas as pd

def get_strategy_data(ticker_symbol: str) -> pd.DataFrame:
    print(f"Fetching data for {ticker_symbol}...")
    
    # 1. Fetch Daily Data for Daily SMAs
    daily = yf.download(ticker_symbol, period="60d", interval="1d", progress=False)
    if daily.empty:
        raise ValueError(f"Failed to fetch daily data for {ticker_symbol}.")
    
    # Handle yfinance multi-index column formatting
    if isinstance(daily.columns, pd.MultiIndex):
        daily.columns = daily.columns.get_level_values(0)
        
    daily['SMA_5'] = daily['Close'].rolling(window=5).mean()
    daily['SMA_10'] = daily['Close'].rolling(window=10).mean()
    
    # Shift daily data so today's intraday logic relies on YESTERDAY'S closing SMA
    # This completely eliminates lookahead bias in the backtest.
    daily['SMA_5'] = daily['SMA_5'].shift(1)
    daily['SMA_10'] = daily['SMA_10'].shift(1)
    
    daily = daily[['SMA_5', 'SMA_10']]
    daily.index = pd.to_datetime(daily.index).date
    
    # 2. Fetch 1-Minute Intraday Data (Hard limited to 7 days by yfinance)
    intraday = yf.download(ticker_symbol, period="7d", interval="1m", progress=False)
    if intraday.empty:
        raise ValueError(f"Failed to fetch 1m intraday data for {ticker_symbol}.")
        
    if isinstance(intraday.columns, pd.MultiIndex):
        intraday.columns = intraday.columns.get_level_values(0)
        
    intraday = intraday.tz_convert('America/New_York')
    
    # 3. Calculate Intraday Indicators
    intraday['Date'] = intraday.index.date
    intraday['Time'] = intraday.index.time
    
    # VWAP Calculation (Resets every day)
    intraday['Typical_Price'] = (intraday['High'] + intraday['Low'] + intraday['Close']) / 3
    intraday['Vol_x_TP'] = intraday['Typical_Price'] * intraday['Volume']
    
    cum_vol = intraday.groupby('Date')['Volume'].cumsum()
    cum_vol_x_tp = intraday.groupby('Date')['Vol_x_TP'].cumsum()
    intraday['VWAP'] = cum_vol_x_tp / cum_vol
    
    # 9 EMA Calculation
    intraday['EMA_9'] = intraday['Close'].ewm(span=9, adjust=False).mean()
    
    # Track the Low of the Day (LOD) dynamically as the day progresses
    intraday['LOD'] = intraday.groupby('Date')['Low'].cummin()
    
    # 4. Merge Daily SMAs into the 1-Minute DataFrame
    intraday = intraday.merge(daily, left_on='Date', right_index=True, how='left')
    
    # Drop rows until moving averages stabilize
    intraday.dropna(subset=['EMA_9', 'VWAP', 'SMA_5'], inplace=True)
    
    return intraday
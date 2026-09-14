import yfinance as yf
import pandas as pd

def get_strategy_data(ticker_symbol: str) -> pd.DataFrame:
    # 1. Fetch Daily Data
    daily = yf.download(ticker_symbol, period="60d", interval="1d", progress=False)
    if daily.empty:
        raise ValueError(f"No daily data for {ticker_symbol}.")
        
    if isinstance(daily.columns, pd.MultiIndex):
        daily.columns = daily.columns.get_level_values(0)
        
    daily['SMA_5'] = daily['Close'].rolling(window=5).mean()
    daily['SMA_10'] = daily['Close'].rolling(window=10).mean()
    
    # Shift daily data so today's intraday logic relies on YESTERDAY'S closing SMA
    daily['SMA_5'] = daily['SMA_5'].shift(1)
    daily['SMA_10'] = daily['SMA_10'].shift(1)
    
    daily = daily[['SMA_5', 'SMA_10']]
    daily.index = pd.to_datetime(daily.index).date
    
    # 2. Fetch 1-Minute Intraday Data
    intraday = yf.download(ticker_symbol, period="7d", interval="1m", progress=False)
    if intraday.empty:
        raise ValueError(f"No 1m data for {ticker_symbol}.")
        
    if isinstance(intraday.columns, pd.MultiIndex):
        intraday.columns = intraday.columns.get_level_values(0)
        
    intraday = intraday.tz_convert('America/New_York')
    
    # Preserve full datetime for chronological portfolio sorting
    intraday['Datetime'] = intraday.index 
    intraday['Date'] = intraday.index.date
    intraday['Time'] = intraday.index.time
    
    # 3. Calculate Intraday Indicators
    intraday['Typical_Price'] = (intraday['High'] + intraday['Low'] + intraday['Close']) / 3
    intraday['Vol_x_TP'] = intraday['Typical_Price'] * intraday['Volume']
    
    cum_vol = intraday.groupby('Date')['Volume'].cumsum()
    cum_vol_x_tp = intraday.groupby('Date')['Vol_x_TP'].cumsum()
    intraday['VWAP'] = cum_vol_x_tp / cum_vol
    
    intraday['EMA_9'] = intraday['Close'].ewm(span=9, adjust=False).mean()
    intraday['LOD'] = intraday.groupby('Date')['Low'].cummin()
    
    # Merge Daily SMAs
    intraday = intraday.merge(daily, left_on='Date', right_index=True, how='inner')
    intraday.dropna(subset=['EMA_9', 'VWAP', 'SMA_5'], inplace=True)
    
    return intraday
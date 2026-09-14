import argparse
import sys
import yfinance as yf

def fetch_stock_data(ticker):
    # Using Ticker.history() guarantees a flat, clean dataframe for single tickers
    stock = yf.Ticker(ticker)
    df = stock.history(period="2y", interval="1d")
    
    if df.empty:
        print(f"Error: No data found for '{ticker}'. Please verify the ticker symbol.")
        sys.exit(1)
        
    # 1. Reset the index so 'Date' becomes a standard column (critical for AI/spreadsheet parsing)
    df = df.reset_index()
    
    # 2. Strip timezone metadata from the Date column to prevent formatting errors in Excel/Pandas
    if 'Date' in df.columns:
        df['Date'] = df['Date'].dt.tz_localize(None)
        
    # 3. Keep only the core price and volume columns, dropping 'Dividends' and 'Stock Splits'
    core_columns = ['Date', 'Open', 'High', 'Low', 'Close', 'Volume']
    df = df[[col for col in core_columns if col in df.columns]]
    
    # Export to a flat CSV
    filename = f"{ticker}_2y_chart_data.csv"
    df.to_csv(filename, index=False)
    print(f"Success: 2 years of daily data exported to {filename}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download 2 years of clean daily price and volume data.")
    parser.add_argument("ticker", type=str, help="The stock ticker symbol (e.g., AAPL, NVDA)")
    
    args = parser.parse_args()
    fetch_stock_data(args.ticker.upper())
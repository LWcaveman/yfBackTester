import argparse
import pandas as pd
from data_loader import get_historical_data
from strategies.vwap_shelf import VWAPEMAShelfStrategy
from portfolio_engine import PortfolioBacktester

# The Exact 30-Ticker High-Profit Universe
EXPANDED_UNIVERSE = [
    # Core Index & Sector ETFs (Liquidity Foundation)
    "SPY", "QQQ", "IWM", "SMH", "XLV", "XLI", "XLE",
    # MegaCap Tech & Cloud Momentum
    "AAPL", "NVDA", "META", "AMZN", "GOOGL",
    # High-Performance Semiconductor Leaders (Removed QCOM)
    "AMAT", "LRCX", "AVGO", "ADI", "MU",
    # Healthcare & Biotech Trends
    "LLY", "MRK", "UNH", "AMGN", "TMO",
    # Financial Leaders (Kept V, GS, BLK; Removed AXP, MA, JPM)
    "BLK", "GS", "V",
    # Industrials & Defense (Kept CAT, UNP, PH; Removed ETN, GE)
    "CAT", "UNP", "PH",
    # Quality Retail & Consumer
    "COST", "HD"
]

def main():
    parser = argparse.ArgumentParser(description="VWAP Simulation")
    parser.add_argument("--slots", type=int, default=2)
    parser.add_argument("--stale", type=int, default=14)
    args = parser.parse_args()

    print(f"Loading historical bars and earnings data for {len(EXPANDED_UNIVERSE)} symbols...")
    data = {}
    for idx, sym in enumerate(EXPANDED_UNIVERSE, 1):
        print(f"[{idx:02d}/{len(EXPANDED_UNIVERSE)}] Processing {sym}...", end="\r", flush=True)
        df = get_historical_data(sym, start_date="2023-01-01")
        if not df.empty and len(df) >= 50:
            data[sym] = df
    print(f"\nSuccessfully loaded {len(data)} tickers.")

    sim = PortfolioBacktester(
        VWAPEMAShelfStrategy(),
        starting_capital=1000,
        risk_pct=0.02,
        max_positions=args.slots,
        allow_fractional=True,
        min_risk_pct=0.02,
        require_spy_regime=True,
        stale_bars=args.stale
    )

    res = sim.run(data)

    print("\n=======================================================")
    print(f" CURATED UNIVERSE ({args.slots} SLOTS | STALE STOP: {args.stale} BARS)")
    print("=======================================================")
    print(f"Starting Balance: ${res['starting_capital']:.2f}")
    print(f"Final Balance:    ${res['final_balance']:.2f}")
    print(f"Net Profit:       ${res['net_profit']:+.2f} ({res['total_return_pct']})")
    print(f"Total Trades:     {res['total_trades']}")
    print(f"Win Rate:         {res['win_rate']}")
    print(f"Profit Factor:    {res['profit_factor']}")
    print(f"Max Drawdown:     {res['max_drawdown']}")
    print("\n--- EXIT REASONS ---")
    print(res["trades_df"]["reason"].value_counts())

    res["trades_df"].to_csv("backtest_vwap_detailed.csv", index=False)
    print("\nTrade log saved to backtest_vwap_detailed.csv")

if __name__ == "__main__":
    main()
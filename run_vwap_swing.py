import argparse
import pandas as pd
from data_loader import get_historical_data
from strategies.vwap_shelf import VWAPEMAShelfStrategy
from portfolio_engine_swing import PortfolioBacktesterSwing

EXPANDED_UNIVERSE = [
    "SPY", "QQQ", "IWM", "SMH", "XLV", "XLI", "XLE",
    "AAPL", "NVDA", "META", "AMZN", "GOOGL",
    "AMAT", "LRCX", "AVGO", "ADI", "MU",
    "LLY", "MRK", "UNH", "AMGN", "TMO",
    "BLK", "GS", "V",
    "CAT", "UNP", "PH",
    "COST", "HD"
]

def main():
    parser = argparse.ArgumentParser(description="VWAP Structural Swing Simulation")
    parser.add_argument("--slots", type=int, default=1)
    parser.add_argument("--stale", type=int, default=12)
    parser.add_argument("--risk", type=float, default=2.0, help="Account risk percentage per trade (e.g., 2.0 for 2%)")
    parser.add_argument("--reward", type=float, default=2.0, help="Target reward multiple (e.g., 2.0 for 2R)")
    parser.add_argument("--buffer", type=float, default=0.8, help="Stop buffer percentage below candle low (e.g., 0.8 for 0.8%)")
    args = parser.parse_args()

    risk_decimal = args.risk / 100.0
    buffer_decimal = args.buffer / 100.0

    print(f"Loading historical bars and earnings data for {len(EXPANDED_UNIVERSE)} symbols...")
    data = {}
    for idx, sym in enumerate(EXPANDED_UNIVERSE, 1):
        print(f"[{idx:02d}/{len(EXPANDED_UNIVERSE)}] Processing {sym}...", end="\r", flush=True)
        df = get_historical_data(sym, start_date="2023-01-01")
        if not df.empty and len(df) >= 50:
            data[sym] = df
    print(f"\nSuccessfully loaded {len(data)} tickers.")

    sim = PortfolioBacktesterSwing(
        VWAPEMAShelfStrategy(),
        starting_capital=1000,
        risk_pct=risk_decimal,
        target_r=args.reward,
        max_positions=args.slots,
        allow_fractional=True,
        require_spy_regime=True,
        stale_bars=args.stale,
        stop_buffer_pct=buffer_decimal
    )

    res = sim.run(data)

    print("\n=======================================================")
    print(f" STRUCTURAL SWING UNIVERSE ({args.slots} SLOTS | STALE STOP: {args.stale} BARS | RISK: {args.risk}% | REWARD: {args.reward}R | BUFFER: {args.buffer}%)")
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

    res["trades_df"].to_csv("backtest_vwap_swing_detailed.csv", index=False)
    print("\nTrade log saved to backtest_vwap_swing_detailed.csv")

if __name__ == "__main__":
    main()
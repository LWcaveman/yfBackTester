import pandas as pd
import numpy as np
from strategies.base import BaseStrategy

class EMAShelfStrategy(BaseStrategy):
    def __init__(self, ema_period: int = 20, sma_period: int = 50, min_close_pct: float = 0.60):
        super().__init__(name="20EMA_50SMA_Pullback_Pure2R")
        self.ema_period = ema_period
        self.sma_period = sma_period
        self.min_close_pct = min_close_pct

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        data = df.copy()

        # Indicators
        data["ema20"] = data["close"].ewm(span=self.ema_period, adjust=False).mean()
        data["sma50"] = data["close"].rolling(window=self.sma_period).mean()

        # Setup Conditions:
        # 1. Structural Uptrend: Close > 50 SMA and 20 EMA > 50 SMA
        uptrend = (data["close"] > data["sma50"]) & (data["ema20"] > data["sma50"])

        # 2. Pullback test: Candle low penetrates or tags within 0.3% of 20 EMA
        touches_ema = (data["low"] <= data["ema20"] * 1.003) & (data["close"] >= data["ema20"] * 0.990)

        # 3. Bullish Defense: Close finishes in the top 40% of the daily range (>= 60th percentile)
        candle_range = data["high"] - data["low"]
        strong_close = np.where(candle_range > 0, (data["close"] - data["low"]) / candle_range >= self.min_close_pct, False)

        data["setup_valid"] = uptrend & touches_ema & strong_close

        # Signal geometry calculated on signal bar close
        # Entry assumed at next bar open; initial stop at signal bar low
        data["planned_stop"] = data["low"]
        
        return data
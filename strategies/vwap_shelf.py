import pandas as pd
import numpy as np
from strategies.base import BaseStrategy

class VWAPEMAShelfStrategy(BaseStrategy):
    def __init__(self, ema_period: int = 20, sma_period: int = 50):
        super().__init__(name="20EMA_WeeklyVWAP_RS_System")
        self.ema_period = ema_period
        self.sma_period = sma_period

    def generate_signals(self, df: pd.DataFrame, spy_df: pd.DataFrame = None) -> pd.DataFrame:
        data = df.copy()

        # Moving Averages
        data["ema20"] = data["close"].ewm(span=self.ema_period, adjust=False).mean()
        data["sma50"] = data["close"].rolling(window=self.sma_period).mean()

        # -------------------------------------------------------------
        # 1. WEEKLY ANCHORED VWAP
        # -------------------------------------------------------------
        # Identify start of each week (grouping key: year + week number)
        if not isinstance(data.index, pd.DatetimeIndex):
            data.index = pd.to_datetime(data.index)

        data["week_id"] = data.index.to_period("W")
        typical_price = (data["high"] + data["low"] + data["close"]) / 3.0
        pv = typical_price * data["volume"]

        # Cumulative volume and price-volume resetting weekly
        cum_pv = pv.groupby(data["week_id"]).cumsum()
        cum_vol = data["volume"].groupby(data["week_id"]).cumsum()
        data["weekly_vwap"] = np.where(cum_vol > 0, cum_pv / cum_vol, data["close"])

        # -------------------------------------------------------------
        # 2. RELATIVE STRENGTH VS SPY (20-bar lookback)
        # -------------------------------------------------------------
        if spy_df is not None:
            spy_data = spy_df.copy()
            if not isinstance(spy_data.index, pd.DatetimeIndex):
                spy_data.index = pd.to_datetime(spy_data.index)

            data["ret_20"] = data["close"] / data["close"].shift(20)
            spy_ret_20 = spy_data["close"] / spy_data["close"].shift(20)
            data["spy_ret_20"] = spy_ret_20.reindex(data.index).ffill()
            data["rs_positive"] = data["ret_20"] >= data["spy_ret_20"]
        else:
            data["rs_positive"] = True

        # -------------------------------------------------------------
        # 3. SETUP CONDITIONS
        # -------------------------------------------------------------
        # Structural Uptrend: Above 50 SMA and 20 EMA > 50 SMA
        uptrend = (data["close"] > data["sma50"]) & (data["ema20"] > data["sma50"])

        # EMA Pullback Test
        touches_ema = (data["low"] <= data["ema20"] * 1.003) & (data["close"] >= data["ema20"] * 0.990)

        # Bullish Rejection: Close in upper 40% of bar
        candle_range = data["high"] - data["low"]
        strong_close = np.where(candle_range > 0, (data["close"] - data["low"]) / candle_range >= 0.40, False)

        # Institutional Defense: Close must hold above Weekly VWAP
        above_wvwap = data["close"] >= data["weekly_vwap"]

        data["setup_valid"] = uptrend & touches_ema & strong_close & above_wvwap & data["rs_positive"]
        data["planned_stop"] = data["low"]

        return data
"""
VWAP Reclaim Engine
Detects institutional morning liquidity sweeps below VWAP followed by high-volume bullish reclaims.
Designed for high capital velocity and pristine risk geometry in small cash accounts.
"""

from datetime import datetime, time
from typing import List, Dict, Any, Optional
import pandas as pd
import numpy as np


class VWAPReclaimEngine:
    def __init__(
        self,
        start_time: str = "09:40",
        end_time: str = "11:15",
        min_sweep_bars: int = 2,
        min_depth_pct: float = 0.003,    # Minimum 0.3% dip below VWAP to constitute a sweep
        max_depth_pct: float = 0.025,    # Maximum 2.5% dip below VWAP (avoids collapsing breakdowns)
        min_close_pct: float = 0.60,     # Candle close in upper 40% of candle range
        min_vol_ratio: float = 0.80,     # Volume >= 0.8x 10-bar SMA
        min_stop_pct: float = 0.006,     # Minimum 0.6% stop distance
        max_stop_pct: float = 0.025,     # Maximum 2.5% stop distance
        target_r: float = 4.0,           # Runner target in R units
        ratchet_r: float = 1.5,          # Partial exit / breakeven ratchet trigger
        partial_scale_pct: float = 0.33, # Scale 33% at ratchet_r
        enable_partial_scale: bool = True
    ):
        self.start_time = datetime.strptime(start_time, "%H:%M").time() if isinstance(start_time, str) else start_time
        self.end_time = datetime.strptime(end_time, "%H:%M").time() if isinstance(end_time, str) else end_time
        self.min_sweep_bars = min_sweep_bars
        self.min_depth_pct = min_depth_pct
        self.max_depth_pct = max_depth_pct
        self.min_close_pct = min_close_pct
        self.min_vol_ratio = min_vol_ratio
        self.min_stop_pct = min_stop_pct
        self.max_stop_pct = max_stop_pct
        self.target_r = target_r
        self.ratchet_r = ratchet_r
        self.partial_scale_pct = partial_scale_pct
        self.enable_partial_scale = enable_partial_scale

    def scan_dataframe(self, ticker: str, df: pd.DataFrame) -> List[Dict[str, Any]]:
        """
        Scans a 1-minute intraday dataframe for VWAP Reclaim setups.
        Expected columns: ['Datetime', 'Date', 'Time', 'Open', 'High', 'Low', 'Close', 'Volume', 'VWAP']
        Optional: ['Vol_SMA10']
        """
        # Ensure standard column naming
        col_map = {c.lower(): c for c in df.columns}
        high_col = col_map.get('high', 'High')
        low_col = col_map.get('low', 'Low')
        close_col = col_map.get('close', 'Close')
        vol_col = col_map.get('volume', 'Volume')
        vwap_col = col_map.get('vwap', 'VWAP')
        time_col = col_map.get('time', 'Time')
        date_col = col_map.get('date', 'Date')
        dt_col = col_map.get('datetime', 'Datetime')

        if 'Vol_SMA10' not in df.columns:
            df = df.copy()
            df['Vol_SMA10'] = df[vol_col].rolling(10).mean()
        vol_sma_col = 'Vol_SMA10'

        signals = []

        # Process day by day
        for date_val, day_df in df.groupby(date_col):
            if len(day_df) < 30:
                continue
            day_df = day_df.reset_index(drop=True)
            n = len(day_df)

            highs = day_df[high_col].values
            lows = day_df[low_col].values
            closes = day_df[close_col].values
            vols = day_df[vol_col].values
            vwaps = day_df[vwap_col].values
            vol_smas = day_df[vol_sma_col].values
            times = day_df[time_col].values
            dts = day_df[dt_col].values

            in_trade = False
            bars_below_vwap = 0
            sweep_low = float('inf')

            entry_price = stop_loss = target = 0.0
            entry_time = None
            entry_clock = None
            entry_date = date_val
            took_partial = False
            partial_price = 0.0
            unit = 0.0

            for i in range(10, n):
                curr_t = times[i]
                curr_dt = dts[i]
                c = closes[i]
                h = highs[i]
                l = lows[i]
                v = vols[i]
                vwap = vwaps[i]
                vol_sma = vol_smas[i]

                if in_trade:
                    # Target & Stop Management
                    partial_tgt = entry_price + (unit / 3.0) * self.ratchet_r
                    runner_tgt = entry_price + (unit / 3.0) * self.target_r

                    # Partial Scale & Breakeven Ratchet
                    if self.enable_partial_scale and not took_partial and h >= partial_tgt:
                        took_partial = True
                        partial_price = partial_tgt
                        stop_loss = entry_price  # Ratchet stop to Breakeven

                    # Stop Loss Hit
                    if l <= stop_loss:
                        if took_partial:
                            exit_price = (partial_price * self.partial_scale_pct) + (stop_loss * (1.0 - self.partial_scale_pct))
                            reason = f"PARTIAL_{self.ratchet_r}R_AND_BE"
                        else:
                            exit_price = stop_loss
                            reason = "BREAKEVEN" if abs(stop_loss - entry_price) < 0.02 else "STOP_LOSS"

                        signals.append({
                            'Ticker': ticker,
                            'Entry Time': pd.to_datetime(entry_time),
                            'Exit Time': pd.to_datetime(curr_dt),
                            'Date': entry_date,
                            'Time': entry_clock,
                            'Entry Price': entry_price,
                            'Stop Loss': stop_loss,
                            'Initial Stop': entry_price - (unit / 3.0),
                            'Exit Price': exit_price,
                            'Reason': reason,
                            'Unit': unit,
                            'Unit Pct': (unit / entry_price) if entry_price > 0 else 0.0,
                            'Strategy': 'VWAP_RECLAIM'
                        })
                        in_trade = False
                        continue

                    # Runner Target Hit
                    if h >= runner_tgt:
                        if took_partial:
                            exit_price = (partial_price * self.partial_scale_pct) + (runner_tgt * (1.0 - self.partial_scale_pct))
                            reason = f"PARTIAL_AND_RUNNER_{self.target_r}R"
                        else:
                            exit_price = runner_tgt
                            reason = f"TARGET_{self.target_r}R"

                        signals.append({
                            'Ticker': ticker,
                            'Entry Time': pd.to_datetime(entry_time),
                            'Exit Time': pd.to_datetime(curr_dt),
                            'Date': entry_date,
                            'Time': entry_clock,
                            'Entry Price': entry_price,
                            'Stop Loss': stop_loss,
                            'Initial Stop': entry_price - (unit / 3.0),
                            'Exit Price': exit_price,
                            'Reason': reason,
                            'Unit': unit,
                            'Unit Pct': (unit / entry_price) if entry_price > 0 else 0.0,
                            'Strategy': 'VWAP_RECLAIM'
                        })
                        in_trade = False
                        continue

                    # End of Day Exit (15:58)
                    if curr_t >= time(15, 58):
                        exit_price = (partial_price * self.partial_scale_pct + c * (1.0 - self.partial_scale_pct)) if took_partial else c
                        signals.append({
                            'Ticker': ticker,
                            'Entry Time': pd.to_datetime(entry_time),
                            'Exit Time': pd.to_datetime(curr_dt),
                            'Date': entry_date,
                            'Time': entry_clock,
                            'Entry Price': entry_price,
                            'Stop Loss': stop_loss,
                            'Initial Stop': entry_price - (unit / 3.0),
                            'Exit Price': exit_price,
                            'Reason': "EOD_EXIT",
                            'Unit': unit,
                            'Unit Pct': (unit / entry_price) if entry_price > 0 else 0.0,
                            'Strategy': 'VWAP_RECLAIM'
                        })
                        in_trade = False
                    continue

                # Not currently in trade: Track below-VWAP liquidity sweeps
                if c < vwap:
                    bars_below_vwap += 1
                    sweep_low = min(sweep_low, l)
                else:
                    # Potential VWAP Reclaim Trigger
                    valid_window = (self.start_time <= curr_t <= self.end_time)
                    valid_sweep = (bars_below_vwap >= self.min_sweep_bars) and (sweep_low < vwap)

                    sweep_depth = (vwap - sweep_low) / vwap if vwap > 0 else 0.0
                    valid_depth = (self.min_depth_pct <= sweep_depth <= self.max_depth_pct)

                    prev_c = closes[i - 1]
                    prev_vwap = vwaps[i - 1]
                    cross_up = (prev_c <= prev_vwap) and (c > vwap)

                    c_range = h - l
                    cpct = (c - l) / c_range if c_range > 0 else 0.5
                    valid_candle = (cpct >= self.min_close_pct)

                    valid_vol = (vol_sma > 0) and (v >= self.min_vol_ratio * vol_sma)

                    if valid_window and valid_sweep and valid_depth and cross_up and valid_candle and valid_vol:
                        entry_price = c
                        # Stop loss placed at sweep low or clamp within min/max bounds
                        raw_stop_dist = entry_price - sweep_low
                        clamped_stop_dist = max(
                            entry_price * self.min_stop_pct,
                            min(raw_stop_dist, entry_price * self.max_stop_pct)
                        )
                        stop_loss = entry_price - clamped_stop_dist
                        # Unit in standard 3:1 geometry (unit = 3 * stop_dist)
                        unit = clamped_stop_dist * 3.0

                        in_trade = True
                        entry_time = curr_dt
                        entry_clock = curr_t
                        entry_date = date_val
                        took_partial = False
                        partial_price = 0.0

                    # Reset sweep tracker once above VWAP
                    bars_below_vwap = 0
                    sweep_low = float('inf')

        return signals


def scan_vwap_reclaim(ticker: str, df: pd.DataFrame, **kwargs) -> List[Dict[str, Any]]:
    """Convenience helper to scan a DataFrame with VWAPReclaimEngine."""
    engine = VWAPReclaimEngine(**kwargs)
    return engine.scan_dataframe(ticker, df)

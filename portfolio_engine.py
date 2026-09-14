import pandas as pd
import numpy as np

class PortfolioBacktester:
    def __init__(
        self,
        strategy,
        starting_capital: float = 1000.0,
        risk_pct: float = 0.02,
        max_positions: int = 2,
        allow_fractional: bool = True,
        min_risk_pct: float = 0.020,
        require_spy_regime: bool = True,
        stale_bars: int = 14
    ):
        self.strategy = strategy
        self.starting_capital = starting_capital
        self.risk_pct = risk_pct
        self.max_positions = max_positions
        self.allow_fractional = allow_fractional
        self.min_risk_pct = min_risk_pct
        self.require_spy_regime = require_spy_regime
        self.stale_bars = stale_bars

    def run(self, ticker_data: dict[str, pd.DataFrame]) -> dict:
        spy_df = ticker_data.get("SPY")
        if spy_df is not None:
            spy_sma50 = spy_df["close"].rolling(50).mean()
        else:
            spy_sma50 = None

        signals = {}
        for sym, df in ticker_data.items():
            if len(df) >= 50:
                try:
                    signals[sym] = self.strategy.generate_signals(df, spy_df=spy_df)
                except TypeError:
                    signals[sym] = self.strategy.generate_signals(df)

        if not signals: return {"error": "No valid data"}

        all_dates = sorted(list(set.union(*[set(df.index) for df in signals.values()])))

        cash = self.starting_capital
        settled_cash = self.starting_capital
        open_positions = {}
        trades = []
        equity_curve = []
        pending_settlements = []

        for i in range(1, len(all_dates)):
            curr_date = all_dates[i]
            prev_date = all_dates[i - 1]

            new_settlements = []
            for avail_date, amt in pending_settlements:
                if curr_date >= avail_date: settled_cash += amt
                else: new_settlements.append((avail_date, amt))
            pending_settlements = new_settlements

            closed_this_bar = []
            for sym, pos in list(open_positions.items()):
                df = signals[sym]
                if curr_date not in df.index:
                    pos["bars_held"] += 1
                    continue

                curr_row = df.loc[curr_date]
                entry_p = pos["entry_price"]
                stop_p = pos["stop_loss"]
                r1_p = pos["target_1r"]
                r2_p = pos["target_2r"]
                r_val = pos["risk_per_share"]
                shares = pos["shares"]

                if curr_row["open"] <= stop_p:
                    pos["lowest_price"] = min(pos["lowest_price"], float(curr_row["open"]))
                    pos["highest_price"] = max(pos["highest_price"], float(curr_row["open"]))
                    t = self._record_exit(pos, float(curr_row["open"]), curr_date, "STOP_GAP", entry_p, r_val, shares)
                    trades.append(t)
                    pending_settlements.append((curr_date, t["exit"] * shares))
                    closed_this_bar.append(sym)
                    continue

                pos["highest_price"] = max(pos["highest_price"], float(curr_row["high"]))
                pos["lowest_price"] = min(pos["lowest_price"], float(curr_row["low"]))

                if curr_row["low"] <= stop_p:
                    reason = "BREAKEVEN" if abs(stop_p - entry_p) < 0.02 else "STOP_LOSS"
                    t = self._record_exit(pos, stop_p, curr_date, reason, entry_p, r_val, shares)
                    trades.append(t)
                    pending_settlements.append((curr_date, stop_p * shares))
                    closed_this_bar.append(sym)
                    continue

                if curr_row["high"] >= r2_p:
                    t = self._record_exit(pos, r2_p, curr_date, "TARGET_2R", entry_p, r_val, shares)
                    trades.append(t)
                    pending_settlements.append((curr_date, r2_p * shares))
                    closed_this_bar.append(sym)
                    continue

                if curr_row["high"] >= r1_p and stop_p < entry_p:
                    pos["stop_loss"] = entry_p

                if self.stale_bars > 0 and pos["bars_held"] >= self.stale_bars and pos["stop_loss"] < entry_p:
                    t = self._record_exit(pos, float(curr_row["close"]), curr_date, "STALE_EXIT", entry_p, r_val, shares)
                    trades.append(t)
                    pending_settlements.append((curr_date, t["exit"] * shares))
                    closed_this_bar.append(sym)
                    continue

                # Earnings Defense Exit
                if curr_row.get("earnings_exit", False):
                    t = self._record_exit(pos, float(curr_row["close"]), curr_date, "EARNINGS_EXIT", entry_p, r_val, shares)
                    trades.append(t)
                    pending_settlements.append((curr_date, t["exit"] * shares))
                    closed_this_bar.append(sym)
                    continue

                pos["bars_held"] += 1

            for sym in closed_this_bar:
                del open_positions[sym]

            open_val = sum(signals[s].loc[curr_date]["close"] * p["shares"] for s, p in open_positions.items() if curr_date in signals[s].index)
            current_equity = round(settled_cash + open_val + sum(amt for _, amt in pending_settlements), 2)
            equity_curve.append({"date": curr_date, "equity": current_equity})

            is_market_bullish = True
            if self.require_spy_regime and spy_sma50 is not None:
                if prev_date in spy_sma50.index and prev_date in spy_df.index:
                    is_market_bullish = spy_df.loc[prev_date, "close"] > spy_sma50.loc[prev_date]

            available_slots = self.max_positions - len(open_positions)
            if available_slots > 0 and settled_cash > 10.0 and is_market_bullish:
                candidates = []
                for sym, df in signals.items():
                    if sym in open_positions: continue
                    if prev_date in df.index and curr_date in df.index:
                        prev_bar = df.loc[prev_date]
                        curr_bar = df.loc[curr_date]

                        # Verify setup and ensure no impending earnings announcement
                        if prev_bar.get("setup_valid", False) and not curr_bar.get("earnings_blackout", False):
                            dist = abs(prev_bar["close"] - prev_bar["ema20"]) / prev_bar["ema20"]
                            candidates.append((sym, dist, curr_bar, prev_bar))

                # Sort by tightest pullback to the 20 EMA
                candidates.sort(key=lambda x: x[1])

                for sym, _, curr_bar, prev_bar in candidates[:available_slots]:
                    entry_p = curr_bar["open"]
                    
                    # Original Strict Sizing Logic (No ATR Widening)
                    raw_stop = prev_bar.get("planned_stop", entry_p * 0.98)
                    dist_raw = entry_p - raw_stop
                    dist_floor = entry_p * self.min_risk_pct
                    
                    risk_per_share = max(dist_raw, dist_floor)
                    stop_p = entry_p - risk_per_share

                    max_pos_cost = current_equity / self.max_positions
                    target_risk_dollars = current_equity * (self.risk_pct / self.max_positions)
                    ideal_shares = target_risk_dollars / risk_per_share
                    position_cost = ideal_shares * entry_p

                    allowed_cost = min(position_cost, max_pos_cost, settled_cash)
                    if allowed_cost < 5.0: continue

                    actual_shares = allowed_cost / entry_p
                    if not self.allow_fractional: actual_shares = int(actual_shares)
                    if actual_shares <= 0 or (actual_shares * entry_p) < 5.0: continue

                    settled_cash -= actual_shares * entry_p
                    open_positions[sym] = {
                        "symbol": sym, "entry_date": curr_date, "entry_price": entry_p,
                        "stop_loss": stop_p, "risk_per_share": risk_per_share,
                        "target_1r": round(entry_p + risk_per_share, 2), 
                        "target_2r": round(entry_p + (2.0 * risk_per_share), 2),
                        "shares": round(actual_shares, 4) if self.allow_fractional else actual_shares,
                        "bars_held": 0, "highest_price": float(curr_bar["high"]), "lowest_price": float(curr_bar["low"])
                    }

        return self._generate_portfolio_report(trades, equity_curve)

    def _record_exit(self, pos, exit_p, date, reason, entry_p, r_val, shares):
        r_mult = round((exit_p - entry_p) / r_val, 2)
        pnl = round((exit_p - entry_p) * shares, 2)
        mfe_r = round((pos["highest_price"] - entry_p) / r_val, 2)
        mae_r = round((entry_p - pos["lowest_price"]) / r_val, 2)
        entry_date_str = pos["entry_date"].strftime("%Y-%m-%d") if hasattr(pos["entry_date"], "strftime") else str(pos["entry_date"])[:10]
        exit_date_str = date.strftime("%Y-%m-%d") if hasattr(date, "strftime") else str(date)[:10]
        return {
            "symbol": pos["symbol"], "entry_date": entry_date_str, "exit_date": exit_date_str,
            "entry": round(pos["entry_price"], 2), "exit": round(exit_p, 2), "risk_per_share": round(r_val, 2),
            "reason": reason, "r_multiple": r_mult, "mfe_r": mfe_r, "mae_r": mae_r, "pnl": pnl, "bars": pos["bars_held"]
        }

    def _generate_portfolio_report(self, trades, equity_curve):
        if not trades: return {"total_trades": 0, "final_balance": self.starting_capital}
        tdf = pd.DataFrame(trades)
        eq_df = pd.DataFrame(equity_curve)
        wins = tdf[tdf["r_multiple"] > 0]
        losses = tdf[tdf["r_multiple"] < 0]
        be = tdf[tdf["r_multiple"] == 0]
        final_eq = eq_df["equity"].iloc[-1] if not eq_df.empty else self.starting_capital
        net_profit = final_eq - self.starting_capital
        total_return_pct = (net_profit / self.starting_capital) * 100
        eq_df["peak"] = eq_df["equity"].cummax()
        eq_df["drawdown"] = (eq_df["equity"] - eq_df["peak"]) / eq_df["peak"]
        max_dd = eq_df["drawdown"].min() * 100
        win_rate = (len(wins) / len(tdf)) * 100
        gross_gains = wins["pnl"].sum()
        gross_losses = abs(losses["pnl"].sum())
        profit_factor = round(gross_gains / gross_losses, 2) if gross_losses > 0 else np.nan
        return {
            "starting_capital": self.starting_capital, "final_balance": round(final_eq, 2),
            "net_profit": round(net_profit, 2), "total_return_pct": f"{total_return_pct:+.2f}%",
            "max_drawdown": f"{max_dd:.2f}%", "total_trades": len(tdf), "wins": len(wins),
            "losses": len(losses), "breakeven": len(be), "win_rate": f"{win_rate:.1f}%",
            "profit_factor": profit_factor, "trades_df": tdf
        }
# yfBackTester

Standalone algorithmic backtesting and quantitative market scanning framework designed to simulate, validate, and screen swing trading and intraday setups using historical OHLCV data via `yfinance`. Operates entirely locally without broker authentication or live API keys.

---

## Environment Setup

Because `yfinance` requires modern async TLS libraries (`curl_cffi>=0.15`), the virtual environment must run on **Python 3.11+**.

```bash
cd ~/yfBackTester

# 1. Activate existing virtual environment
source .venv/bin/activate

# 2. Or initialize a new one with Python 3.11+
python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

---

## Directory Structure

```text
yfBackTester/
├── cache/                             # Local CSV cache of downloaded Yahoo Finance daily bars
│   └── earnings/                      # Cached historical company earnings calendar dates
├── config.py                          # Centralized universes, paths, and default parameters
├── data_loader.py                     # Historical OHLCV fetcher with disk cache & earnings blackout
├── portfolio_engine.py                # Multi-position portfolio backtest simulator (T+1 settlement, ratcheting stops)
├── portfolio_engine_swing.py          # Structural swing simulator with stop buffer & target tuning
├── run.py                             # Unified multi-strategy CLI runner with trade ledger display
├── run_vwap.py                        # Benchmark runner for 30-ticker VWAP + RS system
├── run_vwap_swing.py                  # Benchmark runner for single-slot structural swing system
├── market_scanner.py                  # Real-time scanner for today's high-probability setups
├── requirements.txt                   # yfinance, pandas, numpy, tabulate
├── strategies/
│   ├── __init__.py
│   ├── base.py                        # Abstract base class for strategy plugins
│   ├── ema_shelf.py                   # 20 EMA / 50 SMA Pullback Pure 2R strategy
│   └── vwap_shelf.py                  # 20 EMA + Weekly Anchored VWAP + Relative Strength vs SPY
├── late_entry_day_trade/              # Intraday 1-minute 9 EMA / VWAP crossover trading system
│   ├── data_engine.py                 # Single-ticker 1m data loader with lagged daily SMAs
│   ├── data_engine_portfolio.py       # Multi-ticker portfolio 1m data loader
│   ├── backtest.py                    # Single-ticker intraday simulation engine
│   └── backtest_portfolio.py          # Chronological multi-ticker portfolio intraday simulator
├── vwap_reclaim/                      # Morning VWAP Liquidity Sweep & Reclaim system
│   ├── engine.py                      # Core VWAP Reclaim detection engine
│   └── backtest.py                    # Standalone VWAP Reclaim runner with compounding support
└── charts/
    └── download_chart.py              # CLI utility for downloading clean 2-year daily CSV charts
```

---

## Supported Strategies

### 1. Weekly VWAP + 20 EMA Pullback + RS (`strategies/vwap_shelf.py`)
* **Trend Filter:** Price > 50-day SMA and 20-day EMA > 50-day SMA.
* **Relative Strength Filter:** 20-day return must outperform `SPY` over the same window.
* **Institutional Support:** Candle Close must hold above the current week's Anchored VWAP.
* **Pullback / Coil:** Daily Low penetrates or comes within 0.3% of the rising 20 EMA.
* **Bullish Defense:** Candle Close finishes in the top 40% of the daily range (`min_close_pct=0.60`, defending the rejection tail).
* **Exit Rules:**
  - Initial stop at signal bar Low ($1R$ Risk).
  - Intraday $+1R$ ratchet: moves stop loss to Breakeven ($Entry$).
  - Intraday $+2R$ fixed profit target hit.
  - Stale exit: liquidated at market close if stagnant for 14 bars without achieving $+1R$.
  - Earnings defense: closes before market close prior to an earnings release (with a 5-day pre-earnings entry blackout).

### 2. Pure 20 EMA / 50 SMA Pullback (`strategies/ema_shelf.py`)
* **Trend Filter:** Price > 50 SMA and 20 EMA > 50 SMA.
* **Pullback:** Low tags within 0.3% of 20 EMA.
* **Defense:** Close in the upper 40% of the daily candle range.
* **Sizing & Exits:** Same geometry as VWAP system with $+1R$ breakeven ratchet and $+2R$ target.

### 3. "Fashionably Late" Intraday Momentum (`late_entry_day_trade/`)
* **Execution Windows:** 10:00 AM – 10:45 AM (Morning flush recovery) and 10:46 AM – 1:30 PM (Midday continuation).
* **Trigger:** Intraday 1-minute 9 EMA crosses strictly above intraday VWAP with positive EMA slope.
* **Macro Context:** Intraday price is within 3% of the Daily 5-period or 10-period SMA (calculated using yesterday's close to eliminate lookahead bias).
* **Target & Risk:** Measured move targeting $3R$ reward ($Unit = Entry - LOD$, $Stop = Entry - Unit / 3$).
* **15-Minute Chop Bailout:** If price fails to advance at least 30% towards target within 15 minutes, exits at market close.
* **Curated Elite Universe:** Optimized for institutional follow-through and orderly momentum (`TSLL`, `NVDL`, `CONL`, `TQQQ`, `PLTR`, `RBLX`, `MARA`, `SOFI`).

### 4. Morning VWAP Liquidity Sweep & Reclaim (`vwap_reclaim/`)
* **Execution Window:** 9:40 AM – 11:15 AM (Catches early liquidity flush recoveries before the 10:00 AM momentum crossover).
* **The Sweep:** Price dips below intraday VWAP for $\ge 2$ consecutive bars, establishing a shallow sweep low (between 0.3% and 2.5% below VWAP).
* **The Reclaim Trigger:** 1-minute candle forcefully crosses back above VWAP with bullish range defense ($\ge 60\%$ close) and institutional volume ($\ge 0.8\times$ 10-bar SMA).
* **Market Tide Confirmation:** Requires `SPY` trading above intraday VWAP for long entries (or `QQQ` below VWAP for inverse short entries).
* **Risk & Exits:** Initial stop at the sweep low (capped between 0.6% and 2.5%), partial scale of 33% at $+1.5R$ with stop ratcheted to Breakeven, and runner targeting $+4.0R$.

---

## CLI Usage

Ensure the virtual environment is active:
```bash
source .venv/bin/activate
```

### 1. Unified Multi-Strategy Runner (`run.py`)

Run the default VWAP strategy across the curated 30-ticker universe:
```bash
python run.py
```

Run the Pure EMA Pullback strategy on specific tickers with trade ledger output:
```bash
python run.py --strategy ema --tickers SPY QQQ NVDA AAPL --start 2023-01-01 --show-trades
```

Run the Small Cash Account Compounding simulation ($70 start + $15/week deposits):
```bash
# Native Regime-Routed Mode (Bull: Late Entry Momentum / Bear: VWAP Reclaim) - 1 Trade/Day T+1 Safe
python run.py --strategy daytrade --capital 1000

# Small Cash Account Compounding ($70 Start + $15/wk deposit, 1 Trade/Day, Zero GFV)
python run.py --strategy daytrade --capital 70 --deposit 15 --show-trades

# Standalone Pure Late Entry Momentum (Original 8 tickers: TSLL, NVDL, CONL, TQQQ, PLTR, RBLX, AAPL, AMZN)
python run.py --strategy daytrade --capital 1000 --no-enable-vwap-reclaim

# Standalone Pure VWAP Reclaim simulation (Sweeper tickers: CONL, SOFI, MARA, PLTR)
python run.py --strategy vwap-reclaim --capital 1000 --show-trades

# Direct VWAP Reclaim module runner
python vwap_reclaim/backtest.py --capital 70 --deposit 15 --days 250
```

Run the 1-minute Intraday Day-Trading strategy with custom universes:
```bash
# Override universes explicitly via CLI
python run.py --strategy daytrade --tickers TSLL NVDL CONL TQQQ PLTR RBLX AAPL AMZN --reclaim-tickers CONL SOFI MARA PLTR
```

View Data Vault database status and coverage:
```bash
python run.py --vault-stats
```

### 2. Multi-Year Alpaca Data Vault (`alpaca_vault.py`)

The local Data Vault stores continuous, high-resolution 1-minute time series from Alpaca Market Data API v2 (SIP feed with 100% consolidated market volume) in `data/intraday_1m.db` (SQLite):

```bash
# 1. Bulk sync 2 years of 1m bars for the 10-ticker universe (ARM, HOOD, PLTR, AMZN, AAPL, GOOGL, SPY, QQQ, SH, PSQ)
python run.py --bulk-sync --years 2

# 2. Add or sync any individual ticker for N years
python run.py --add-ticker NVDA --years 2

# 3. Check storage stats, date ranges, and bar counts
python run.py --vault-stats

# Direct alpaca_vault CLI
python alpaca_vault.py --bulk-sync --years 2
python alpaca_vault.py --add-ticker TSLA --years 2
python alpaca_vault.py --stats
```

### 3. Standalone Swing Runners

Run the 2-slot VWAP benchmark runner:
```bash
python run_vwap.py --slots 2 --stale 14
```

Run the 1-slot Structural Swing runner with configurable stop buffer and reward:
```bash
python run_vwap_swing.py --slots 1 --risk 2.0 --reward 2.0 --buffer 0.8
```

### 3. Intraday 1-Minute Data Vault (`data_vault.py`)

The local Data Vault stores continuous, high-resolution 1-minute time series in `data/intraday_1m.db` (SQLite):
```bash
# Sync recent 7-day 1m bars for the day-trade universe (automatically deduplicated)
python data_vault.py --sync

# Check storage stats, date ranges, and bar counts
python data_vault.py --stats
```

### 4. Real-Time Market Scanner (`market_scanner.py`)

Scan 30 institutional leaders for active setups today (Trend 1.5R or Rubber Band Reversion) backed by historical win rate verification:
```bash
python market_scanner.py
```

### 5. Intraday Day Trade Portfolio Backtest

Run the chronological multi-ticker portfolio simulation across momentum leaders:
```bash
python late_entry_day_trade/backtest_portfolio.py
```

---

## Metric Definitions

* **Win Rate:** Percentage of executed trades closing at $> 0.0R$.
* **Profit Factor:** Gross profits divided by gross losses. Values $> 1.50$ indicate a robust statistical edge.
* **Expectancy ($R$):** Average return per trade in units of risk.
* **Max Drawdown:** Maximum peak-to-trough account equity contraction experienced during the simulation.
* **Exit Reasons:**
  - `TARGET_2R` / `TARGET_3R`: Intraday price reached target.
  - `BREAKEVEN`: Position stopped out at initial entry price after reaching $+1R$.
  - `STOP_LOSS`: Position hit initial risk stop.
  - `STOP_GAP`: Overnight price opened below stop level; filled at Open.
  - `STALE_EXIT`: Closed at market close due to exceeding maximum allowed holding days without hitting $+1R$.
  - `EARNINGS_EXIT`: Closed defensively on the trading day before corporate earnings announcements.
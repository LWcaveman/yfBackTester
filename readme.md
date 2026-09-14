# yfBackTester

Standalone backtesting framework designed to simulate and validate swing trading setups against historical OHLCV data via `yfinance`. Operates locally without broker authentication or live API keys.

---

## Environment Setup

Because `yfinance` requires `curl_cffi>=0.15`, the virtual environment must run on **Python 3.11+**.

```bash
cd ~/yfBackTester

# 1. Create .venv using the Python 3.11 binary from rhTrader
~/rhTrader/venv/bin/python -m venv .venv

# 2. Activate virtual environment
source .venv/bin/activate

# 3. Upgrade pip and install dependencies
pip install --upgrade pip
pip install -r requirements.txt

```

---

## Directory Structure

```text
yfBackTester/
├── .cache/                 # Local CSV cache of downloaded Yahoo Finance bars
├── .venv/                  # Python 3.11 virtual environment
├── strategies/
│   ├── __init__.py
│   ├── base.py             # Abstract base class for strategy plugins
│   └── ema_shelf.py        # 20 EMA / 50 SMA Pullback strategy
├── config.py               # Global parameters and path configs
├── data_loader.py          # OHLCV data fetcher with disk caching
├── engine.py               # Bar-by-bar backtest simulation & performance metrics
├── requirements.txt        # yfinance, pandas, numpy, tabulate
└── run.py                  # CLI runner and report generator

```

---

## Core Strategy Logic (`20EMA_50SMA_Pullback_Pure2R`)

The default strategy mirrors the `rhTrader` autonomous swing execution rules:

* **Trend Filter:** Price > 50-day SMA and 20-day EMA > 50-day SMA.
* **Pullback / Coil:** Daily low penetrates or comes within 0.3% of the rising 20 EMA, and close finishes in the top 40% of the daily range (defended tail).
* **Signal Geometry:**
* **Entry:** Next bar's Open price.
* **Initial Stop ($1R$ Risk):** Signal bar Low ($Risk = Entry - Stop$).
* **Breakeven Milestone ($+1R$):** $Entry + 1R$. Ratchets stop loss to $Entry$ to eliminate downside risk.
* **Take Profit ($+2R$ Fixed):** $Entry + 2R$. Closes the position completely. No trailing stop.


* **Execution Order of Operations:**
1. Gap-down check at Open below stop floor.
2. Intraday Stop Loss / Breakeven breach check at Low.
3. Intraday $+1R$ Breakeven ratchet check at High.
4. Intraday $+2R$ Profit Target hit check at High.



---

## CLI Usage

Ensure your virtual environment is active before running commands:

```bash
source .venv/bin/activate

```

### 1. Default Multi-Ticker Test

Runs the backtest across default index and sector ETFs (`SPY`, `QQQ`, `XLK`, `SMH`, `IWM`, `AAPL`, `MSFT`, `NVDA`) from January 1, 2023 to present:

```bash
python run.py

```

### 2. Custom Date Range & Specific Tickers

Pass custom start dates and a targeted basket of symbols:

```bash
python run.py --tickers SPY QQQ XLK DIA --start 2022-01-01

```

### 3. Detailed Trade Ledger

Add `--show-trades` to print the individual trade log (entry/exit dates, price fills, exit reason, and individual $R$-multiples) for each ticker:

```bash
python run.py --tickers SPY --start 2023-01-01 --show-trades

```

---

## Metric Definitions

* **Win Rate:** Percentage of trades closing at $> 0.0R$.
* **Total Return ($R$):** Cumulative $R$-multiple earned ($+2.0R$ per win, $-1.0R$ per initial stop-out, $0.0R$ per breakeven scratch).
* **Expectancy ($E$):** Average return per trade in units of risk ($R$). A positive expectancy (e.g., $+0.20R$) indicates long-term mathematical edge.
* **Profit Factor:** Gross profits divided by gross losses. Values $> 1.50$ indicate robust strategy edge.
* **Avg Bars:** Average number of trading days a position was held open from entry to target or stop.
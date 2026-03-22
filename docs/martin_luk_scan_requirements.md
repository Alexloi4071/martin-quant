# Martin Luk Scan System Requirements

## Goal

Build a scan and execution stack that matches the practical flow of a Martin Luk style process:

1. Start with market regime and sector/theme leadership.
2. Build a leader watchlist from liquid, volatile U.S. equities.
3. Detect high-quality daily setups.
4. Confirm intraday entries on 15m.
5. Manage risk with small predefined stops and R-multiple exits.
6. Review results weekly and refine the process.

## Strategy Layers

### 1. Market Regime

Required outputs:
- `BULL / WEAK_BULL / CHOPPY / BEAR`
- benchmark trend state for `SPY`, `QQQ`, `IWM`
- optional breadth and sector leadership confirmation

Current repo coverage:
- Partial
- `src/martin_quant/filters/market_regime.py`
- `src/martin_quant/regime/sector_regime_filter.py`

Still needed:
- stable end-to-end wiring into scan runners
- optional breadth data if you want better exposure control

### 2. Universe / Watchlist

Required filters:
- U.S. equities only
- price floor
- average dollar volume
- ADR / ATR expansion
- RS versus benchmark
- sector/theme grouping
- earnings and catalyst awareness

Current repo coverage:
- Good concept coverage
- `Finviz` candidate screens
- `WatchlistBuilder`
- theme / momentum helpers

Still needed:
- reliable watchlist pipeline integration
- better sector/theme labeling source

### 3. Daily Setups

Core setups to support:
- pullback
- breakout
- episodic pivot / earnings gap
- AVWAP reclaim
- first pullback after expansion

Current repo coverage:
- pullback
- breakout
- EPS setup
- AVWAP anchor/scoring
- premarket gap scanner

Still needed:
- tighter alignment between setup modules and live scan entrypoints
- more explicit "first pullback" and "event-driven leader" ranking rules

### 4. Intraday Timing

Required trigger families:
- 15m ORB
- reclaim entry
- AVWAP reclaim
- optional 1h context filter

Current repo coverage:
- Good module coverage
- `ORBTrigger`
- reclaim triggers
- intraday entry detector

Still needed:
- reliable 15m intraday data source
- consistent scan runner integration

### 5. Risk and Execution

Required:
- fixed per-trade risk
- stop distance validation
- position sizing
- bracket orders
- open-position monitoring
- partial profit and trailing rules

Current repo coverage:
- Good conceptual coverage
- position sizing
- exit manager
- partial take profit
- IBKR bridge

Still needed:
- production-hardening of broker integration
- clean handoff from scan signals to orders

### 6. Review / Feedback

Required:
- trade logging
- weekly review
- by-setup / by-sector / by-regime stats
- markdown or CSV output

Current repo coverage:
- present but API surface is inconsistent in places

## Data Requirements

### Must-Have Data

1. Daily OHLCV
- needed for structure, EMA, ADR, RS, bases, pullbacks

2. 15m OHLCV
- needed for ORB and reclaim entries

3. Earnings calendar
- needed for episodic pivot / catalyst filtering

4. Company metadata
- sector, industry, market cap

5. Premarket price and volume
- needed for gap scans

### Strongly Recommended Data

1. News / catalyst feed
- earnings alone is not enough
- helps detect product launches, guidance, FDA, analyst actions, major contracts

2. Sector / theme classification
- semis, AI, software, cyber, crypto equity, biotech, etc.

3. Benchmark and sector ETF data
- `SPY`, `QQQ`, `IWM`, `SMH`, `XLK`, `XBI`, `ARKK`, etc.

### Optional but Useful Data

1. Breadth data
- `% above 50D`, new highs/lows, advance/decline

2. 1m or 5m OHLCV
- only if you want finer intraday triggers later

3. Float / shares outstanding / short interest
- helpful for event-driven and momentum ranking

## API Checklist

### Already Expected by This Repo

1. `FINNHUB_API_KEY`
- Status: used now
- Purpose: earnings, metadata, OHLCV download scripts
- Priority: required

2. `POLYGON_API_KEY`
- Status: partially used now
- Purpose: stronger premarket and intraday support
- Priority: strongly recommended

3. `TELEGRAM_BOT_TOKEN`
- Status: used now
- Purpose: alerts
- Priority: optional

4. `TELEGRAM_CHAT_ID`
- Status: used now
- Purpose: alerts
- Priority: optional

5. `IBKR_HOST`, `IBKR_PORT`, `IBKR_CLIENT_ID`
- Status: not consistently wired from env yet, but clearly needed for live trading
- Purpose: broker connection
- Priority: required only for execution

### Recommended to Add Even if Not Wired Yet

1. `BENZINGA_API_KEY`
- Best fit for catalyst/news workflow
- Use for news headlines, analyst notes, FDA, guidance, unusual events

2. `FMP_API_KEY`
- Good backup for earnings, company profile, historical ratios, float/share stats

3. `TWELVEDATA_API_KEY`
- Optional backup intraday feed if Polygon is unavailable

4. `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`
- Optional fallback if you later want paper execution or market data experiments

## Finviz

`Finviz` does not need an API key in this repo.

It is currently handled as a scraper / screen reader:
- candidate generation
- not a full market data source

Risk:
- layout changes or anti-bot protections can break it

## Minimum Viable External Stack

If you want the leanest usable version:

1. `Finviz`
- candidate pool

2. `Finnhub`
- metadata + earnings + backup market data

3. `Polygon`
- premarket + intraday bars

4. `IBKR`
- execution only

## Best Practical Stack

If you want the closest fit to a Martin-style workflow:

1. `Finviz`
- leader candidate generation

2. `Finnhub`
- earnings and basic metadata

3. `Polygon`
- premarket and intraday bars

4. `Benzinga`
- catalyst/news layer

5. `IBKR`
- order routing and position monitoring

## Priority Build Order

1. Fix repo integration issues so the existing scan path actually runs.
2. Standardize provider selection around `Finviz + Finnhub + Polygon`.
3. Make `Polygon` the primary premarket / intraday source.
4. Add a catalyst/news source.
5. Harden IBKR live/paper execution.

## Current Recommendation

Fill these first:
- `FINNHUB_API_KEY`
- `POLYGON_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `IBKR_HOST`
- `IBKR_PORT`
- `IBKR_CLIENT_ID`
- `ACCOUNT_EQUITY`

Reserve these now for future wiring:
- `BENZINGA_API_KEY`
- `FMP_API_KEY`
- `TWELVEDATA_API_KEY`
- `ALPACA_API_KEY`
- `ALPACA_SECRET_KEY`

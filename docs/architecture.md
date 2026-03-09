# Architecture

## 1. Goal

This project is a standalone quantitative research and timing framework inspired by Martin-style discretionary stock trading.

The initial objective is not full automation. The objective is to convert the most repeatable parts of the process into a researchable and reviewable system that can:
- scan U.S. stocks
- identify high-quality trend and pullback candidates
- monitor intraday timing conditions
- generate reviewable trade candidates
- support manual validation before deeper automation

## 2. V1 Scope

V1 focuses on:
- U.S. equities only
- Daily structure analysis
- 15-minute trigger execution layer
- 1-hour context confirmation layer
- Pullback as the main setup
- Breakout as the secondary setup
- Manual review and labeling workflow

V1 does not focus on:
- options execution
- fully automated live order routing
- portfolio optimization
- complex parabolic short automation

## 3. Design Principles

### 3.1 Research-first
The repository is built to answer whether the stock-selection and timing logic has edge before introducing downstream complexity.

### 3.2 Config-driven
Thresholds, windows, toggles, and ranking weights should live in config files whenever possible.

### 3.3 Traceable
Every candidate, setup, trigger, and final signal should be explainable from stored features and rule outputs.

### 3.4 Multi-timeframe
The system separates structure, context, and trigger logic across daily, hourly, and 15-minute layers.

## 4. System Layers

### 4.1 Data Layer
Responsibilities:
- ingest OHLCV data
- normalize timestamps and symbols
- adjust for splits if required
- align multi-timeframe data
- enrich with earnings, sector, and event metadata

Suggested sources:
- Finviz for universe and stock context
- Finnhub for earnings and market/event data
- local parquet/csv cache for repeatable research runs

### 4.2 Universe Layer
Responsibilities:
- filter symbols by liquidity, price, and activity
- rank hot sectors and strong groups
- rank candidate leaders by relative strength and dollar volume
- build watchlists for daily research and intraday monitoring

### 4.3 Feature Layer
Responsibilities:
- compute EMA features
- compute ATR, ADR, RVOL, dollar volume
- compute AVWAP from chosen anchors
- compute relative strength and structure features
- build market context features for regime awareness

### 4.4 Anchor Layer
Responsibilities:
- detect potential AVWAP anchor points
- support swing-low, swing-high, breakout, and gap anchors
- rank anchors when multiple valid anchors exist

V1 note:
Anchor selection remains semi-subjective, so the design should allow multiple anchor candidates and review-time inspection.

### 4.5 Setup Layer
Responsibilities:
- detect pullback opportunities
- detect breakout opportunities
- invalidate weak or late structures
- assign a setup score

The setup layer should answer:
- Is this stock structurally interesting?
- Is the trend healthy?
- Is price near a meaningful support/resistance cluster?

### 4.6 Timing Layer
Responsibilities:
- convert setup candidates into executable timing signals
- detect reclaim, opening-range, breakout, or reversal triggers on 15-minute bars
- reject low-quality intraday entries

The timing layer should answer:
- Is now the right moment to act?
- Is the stop distance still acceptable?
- Is the signal late, stretched, or invalid?

### 4.7 Risk Layer
Responsibilities:
- place initial stop logic
- compute stop distance and R multiple
- size positions from risk budget
- support partial profit-taking and trailing exits
- enforce maximum stop-width skip rules

### 4.8 Review Layer
Responsibilities:
- export candidate snapshots
- generate charts for manual review
- store labels such as valid, invalid, late, clean, or noisy
- support error analysis and research iteration

## 5. Primary Pipeline

### 5.1 Daily Scan Pipeline
1. Load daily market data.
2. Build tradeable universe.
3. Compute structure and trend features.
4. Detect pullback and breakout setup candidates.
5. Rank candidates and write watchlist outputs.

### 5.2 Intraday Monitor Pipeline
1. Load watchlist from daily scan.
2. Sync 15-minute and 1-hour data.
3. Recalculate trigger features.
4. Check trigger and invalidation rules.
5. Export reviewable signal package.

### 5.3 Review Pipeline
1. Generate daily and intraday charts.
2. Export feature snapshot.
3. Store manual labels.
4. Compare future outcomes to original signal quality.

## 6. Package Structure

### `src/martin_quant/core`
Shared enums, datatypes, constants, and registry logic.

### `src/martin_quant/data`
Providers, loaders, cleaners, and timeframe alignment logic.

### `src/martin_quant/universe`
Liquidity, relative-strength, and watchlist construction logic.

### `src/martin_quant/features`
Reusable feature generators such as EMA, AVWAP, ATR, and RVOL.

### `src/martin_quant/anchors`
Anchor detection and ranking for AVWAP calculations.

### `src/martin_quant/setups`
Structural setup detection such as pullback and breakout.

### `src/martin_quant/timing`
Intraday entry triggers and timing confirmation rules.

### `src/martin_quant/risk`
Stop logic, position sizing, exit rules, and portfolio limits.

### `src/martin_quant/pipeline`
High-level orchestration for scan, monitor, and review flows.

### `src/martin_quant/backtest`
Research-only event simulation, performance analytics, and walk-forward modules.

### `src/martin_quant/review`
Manual review exporters, chart builders, and label storage.

### `src/martin_quant/cli`
Command-line entry points such as scan, monitor, backtest, and review.

## 7. Suggested V1 Implementation Order

1. Data ingestion and normalization.
2. Universe filters and watchlist builder.
3. EMA, AVWAP, ATR, RVOL, and relative-strength features.
4. Pullback setup engine.
5. 15-minute trigger engine.
6. Risk and exit engine.
7. Review exports.
8. Backtest and walk-forward evaluation.

## 8. Success Criteria for V1

The project should be considered successful when it can:
- produce a stable daily watchlist
- detect repeatable pullback and breakout candidates
- emit reviewable 15-minute timing signals
- export enough context for manual validation
- support later transition into systematic testing and selective automation

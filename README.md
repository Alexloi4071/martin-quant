# Martin Quant

Independent quantitative research and execution framework for Martin-style U.S. stock selection and timing.

## Purpose

This repository is designed to build a standalone Martin project first, with manual review and research validation before any options integration.

Core v1 direction:
- U.S. equities only
- Daily timeframe for structure and watchlist building
- 15-minute timeframe for trigger and execution timing
- 1-hour timeframe as supporting context
- Pullback setup as the primary setup
- Breakout setup as the secondary setup
- Manual review and labeling before automation

## Repository Structure

```text
martin-quant/
├── README.md
├── pyproject.toml
├── requirements.txt
├── .env.example
├── .gitignore
├── config/
│   ├── app.yaml
│   ├── data_sources.yaml
│   ├── universe.yaml
│   ├── setups.yaml
│   └── risk.yaml
├── docs/
│   ├── architecture.md
│   ├── strategy_spec.md
│   └── roadmap.md
├── scripts/
│   └── run_daily_scan.py
└── src/
    └── martin_quant/
        ├── __init__.py
        ├── core/
        │   └── __init__.py
        ├── data/
        │   └── __init__.py
        ├── universe/
        │   └── __init__.py
        ├── features/
        │   └── __init__.py
        ├── anchors/
        │   └── __init__.py
        ├── setups/
        │   └── __init__.py
        ├── timing/
        │   └── __init__.py
        ├── risk/
        │   └── __init__.py
        ├── pipeline/
        │   └── __init__.py
        ├── backtest/
        │   └── __init__.py
        ├── review/
        │   └── __init__.py
        └── cli/
            └── __init__.py
```

## Architecture

The main architecture document is in `docs/architecture.md`.

It defines:
- design principles
- module boundaries
- pipeline flow
- research and review workflow
- v1 implementation scope

## Suggested Next Build Order

1. Implement data ingestion and normalization.
2. Build universe filters and watchlist generation.
3. Implement pullback setup detection.
4. Add 15-minute timing triggers.
5. Add stop, R-multiple, and exit logic.
6. Add manual review export and backtesting.

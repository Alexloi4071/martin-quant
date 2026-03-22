# Project Status and Next Steps (2026-03-19)

## 1. Current verified state

As of 2026-03-19, the following items were verified locally:

### API and service connectivity
- `Finnhub`: OK
- `Polygon`: OK
- `FMP`: OK
- `Benzinga`: OK
- `TelegramBot`: OK
- `TelegramChat`: OK
- `IBKR Gateway paper` at `127.0.0.1:4002`: OK

Verification source:
- `py -3.11 scripts\check_connections.py`

### Local tooling that is working
- `.env` can be loaded by local scripts.
- `scripts/check_connections.py` works.
- `scripts/check_connections.py --telegram-updates` was previously added and can be used to help confirm the Telegram `chat_id`.
- `scripts/update_universe_and_data.py` can download and write local parquet data.
- `martin_quant.scripts.run_daily_scan_v2` now runs end-to-end without the previous `AVWAPReclaimTrigger` import crash.

### Latest verified scan run
Command:
```powershell
$env:PYTHONPATH='src'
py -3.11 -m martin_quant.scripts.run_daily_scan_v2 --no-alerts --symbols SPY,YUM,YOU,XPO
```

Observed result on 2026-03-19:
- scan process completed
- regime detected as `BULL`
- `0` symbols passed base setup detection
- `0` final signals found
- no runtime crash in the V2 scan entrypoint

This means the scan runner is executable now, but the strategy output is not yet validated.

## 2. Fixes already completed

### Runtime / integration fixes already done
- Added a local connection test script: `scripts/check_connections.py`
- Added Telegram `getUpdates` helper support for chat-id checking
- Added `.env` placeholders and project-specific env wiring
- Reworked the Finviz provider because the original parser was extracting bad symbol values from page text
- Adjusted data update flow to use:
  - `Finviz` for candidate generation
  - `Finnhub` for metadata and earnings
  - `Polygon` for daily OHLCV
  - `yfinance` fallback for `1h` and `15m` OHLCV when Polygon intraday history returned empty delayed responses
- Added throttling/retry behavior around Polygon daily requests to reduce `429` failures
- Fixed one blocking package compatibility issue in `src/martin_quant/timing/__init__.py` by restoring the missing `AVWAPReclaimTrigger` compatibility export
- Updated the V2 scan runner so it can load env vars, pull local parquet data, detect regime, compute setup candidates, and call `DailyScannerV2`

## 3. Current known problems

### A. Strategy currently runs, but output quality is not proven
Symptoms:
- the V2 scan finishes, but the small verified sample produces no setups and no signals

Likely causes:
- current sample size is too small
- setup thresholds may be too strict for the imported data
- the Martin-style logic is only partially aligned in the ranking / setup linkage

Impact:
- system is runnable, but not yet trustworthy as a stock-selection engine

### B. Universe refresh is still operationally weak
Symptoms:
- a larger refresh attempt timed out after about 304 seconds under rate limiting / slow provider response
- local cache ended up only partially expanded

Current locally visible daily cache symbols after that attempt:
- `SPY`
- `VIRT`
- `VRT`
- `WWD`
- `XIFR`
- `XOM`
- `XPO`
- `YOU`
- `YUM`

Impact:
- scans are currently being run on a tiny and possibly incomplete universe
- this makes zero-signal runs hard to interpret

### C. Data provider stack is still mixed and not fully normalized
Current behavior:
- daily bars: mainly `Polygon`
- metadata / earnings: `Finnhub`
- intraday fallback: `yfinance`
- candidates: `Finviz` scraping

Risks:
- timestamps, session boundaries, adjusted prices, and volume definitions may differ across providers
- intraday and daily data may not line up perfectly
- strategy rules based on AVWAP / ORB / reclaim logic can become inconsistent if data sources disagree

### D. Finviz is still a scraping dependency
Facts:
- no API key needed
- parser was already broken once and had to be fixed

Risk:
- layout changes or bot protection can break candidate generation again

### E. Some package surfaces are still fragile
Facts:
- one stale import/export mismatch was found and fixed in `timing`
- based on the repo structure, similar stale `__init__` exports may still exist in other packages

Impact:
- additional runtime errors may appear when deeper modules are exercised

### F. IBKR execution path is connected but not validated end-to-end
Facts:
- gateway paper port is reachable
- actual scan-to-order handoff has not been proven

Not yet verified:
- contract resolution
- bracket order creation
- partial take profit logic
- stop updates
- live position monitoring
- error handling on broker rejects or reconnects

## 4. What has not been tested yet

The following areas still need explicit testing:

### Scan and strategy behavior
- running the scanner on a larger real universe
- validating that setup detectors produce reasonable candidates
- validating that `DailyScannerV2` ranking matches the intended Martin Luk workflow
- validating that regime changes affect ranking and filtering correctly
- validating sector / theme leadership logic end-to-end

### Data integrity
- consistency between `1d`, `1h`, and `15m` parquet outputs
- timezone normalization across providers
- corporate action effects such as splits / adjusted history
- data gaps and partial-day handling
- API retry / backoff behavior under rate limits or temporary failures

### Alerts and automation
- actual Telegram alert send from a run that produces real signals
- scheduler / daily batch execution
- failure reporting when the scan crashes or data update fails

### Execution / broker layer
- `run_live.py` or equivalent live/paper workflow
- scan signal to order object conversion
- paper order placement against IBKR
- fills, cancel / replace, stop-loss, take-profit, and trailing behavior
- reconnection handling if IBKR Gateway restarts

### Reporting and review
- output CSV validity when signals do exist
- trade log persistence
- weekly review statistics by setup / sector / regime
- downstream report formatting and consistency

## 5. Current landing / production-readiness issues

The project is not production-ready yet. The main blockers are:

### 1. No validated large-universe daily workflow
The system can run, but it has not been proven on a realistic daily universe size with stable refresh times.

### 2. No proven signal quality yet
A runnable scan is not the same as a useful scan. Setup quality, ranking quality, and final trade selection still need real validation.

### 3. Data-source inconsistency
The current mixed-source approach is practical for now, but it is not yet clean enough for robust production behavior.

### 4. Broker path is only partially landed
Connectivity is there, but order lifecycle management has not been validated in paper trading from end to end.

### 5. Low automated test coverage on the real workflow
The project still relies too much on manual runtime checks. Critical paths need smoke tests and integration tests.

### 6. Weak operational resilience
Timeouts, partial refreshes, and provider fragility are still too manual to manage.

## 6. Recommended improvement plan

### Phase 1: stabilize the data refresh path
Priority: highest

Tasks:
- make `update_universe_and_data.py` resumable
- write progress logs per symbol
- persist success / failure state cleanly
- cap provider concurrency more conservatively
- add clearer backoff handling for Polygon
- verify that interrupted runs can continue without corrupting existing parquet files

Expected outcome:
- a reliable local market cache for a realistic watchlist size

### Phase 2: validate strategy output on a broader universe
Priority: highest

Tasks:
- refresh a larger watchlist
- run `run_daily_scan_v2` without limiting to 4 symbols
- inspect why base setup detection is returning `0`
- tune thresholds or fix input assumptions if the detectors are too strict
- compare scan output with the intended Martin Luk workflow

Expected outcome:
- evidence that the scan produces sensible candidate names

### Phase 3: unify and normalize provider behavior
Priority: high

Tasks:
- document which provider is authoritative for each timeframe
- normalize timestamp handling and market session assumptions
- validate AVWAP / ORB / reclaim calculations on the chosen intraday feed
- reduce hidden differences between `Polygon` and `yfinance`

Expected outcome:
- more consistent scan results and fewer hidden data-quality bugs

### Phase 4: harden scan-to-alert and scan-to-execution handoff
Priority: high

Tasks:
- test Telegram send on real signal output
- validate CSV/report output on non-empty results
- wire scan results into the broker execution layer in paper mode
- test bracket order flow and position management with IBKR paper

Expected outcome:
- usable daily workflow from scan to action

### Phase 5: add regression protection
Priority: medium

Tasks:
- add smoke tests for:
  - env loading
  - data pipeline fetch
  - daily scan entrypoint
  - at least one setup detector
  - Telegram alert formatting
  - IBKR bridge initialization
- add compatibility tests for package exports that the runners rely on

Expected outcome:
- future code changes are less likely to break runtime imports and integrations

## 7. Suggested next-session task order

When work resumes, use this order:

1. finish stabilizing `update_universe_and_data.py`
2. build a larger local cache successfully without timeout
3. run `run_daily_scan_v2` on the larger cache
4. inspect why setup detection is empty or too sparse
5. adjust / fix setup logic until the scan produces reasonable candidates
6. test Telegram alerts on real scan output
7. test IBKR paper order flow end-to-end
8. add smoke tests around the now-working path

## 8. Useful commands for the next session

### Reload `.env` into the current PowerShell session
```powershell
Get-Content -LiteralPath '.env' | ForEach-Object {
  if ($_ -and -not $_.TrimStart().StartsWith('#') -and $_.Contains('=')) {
    $parts = $_ -split '=',2
    [Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1].Trim().Trim('"').Trim("'"), 'Process')
  }
}
$env:PYTHONPATH='src'
```

### Check API and service connectivity
```powershell
py -3.11 scripts\check_connections.py
```

### Check Telegram updates / discover chat id
```powershell
py -3.11 scripts\check_connections.py --telegram-updates
```

### Run the V2 scan on a small known sample
```powershell
py -3.11 -m martin_quant.scripts.run_daily_scan_v2 --no-alerts --symbols SPY,YUM,YOU,XPO
```

### Refresh local market data
```powershell
$env:POLYGON_PAUSE_SECONDS='12'
py -3.11 scripts\update_universe_and_data.py --max-symbols 10 --per-screen-cap 5
```

## 9. Bottom line

As of 2026-03-19, the project has moved from "cannot run reliably" to "core scan path can run locally".

What is still missing is the hard part:
- stable large-universe refresh
- validated scan quality
- consistent data behavior
- proven IBKR paper execution workflow
- automated regression coverage

That is the correct point to continue from in the next session.

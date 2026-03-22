# Martin Luk Workflow Gap Analysis and Implementation Plan (2026-03-19)

## Purpose

This document captures two things:

1. The current verified problems in the repo and why the scanner is not yet answering the right question.
2. A detailed implementation plan for translating the workflow described in the article below into code and operational workflow for this project.

Primary source:
- Trading Resource Hub, "Martin Luk's Low-Friction Study Process"
  - https://tradingresourcehub.substack.com/p/martin-luk-study-process

This is a planning document only. No strategy changes in this file are implemented yet.

## Executive Summary

The current system is not yet performing a Martin Luk-style market analysis in the area that matters most right now: bear-market and correction-phase opportunity finding.

The present V2 path behaves more like:

- build a long-biased candidate list
- run long-biased setup detectors
- apply a bear-regime penalty that mostly suppresses action

That is not the same as:

- detect a weak market backdrop
- identify short-side resistance on higher timeframes
- wait for lower-timeframe confirmation
- execute tight-risk short entries
- manage them as quick swings

So the current "0 setups / 0 signals" result does not prove there were no opportunities. It proves the current implementation is structurally misaligned with the workflow we actually want to test.

## Current Verified State

### 1. Data refresh path is now more stable

Completed before this document:

- `scripts/update_universe_and_data.py` now supports resumable refresh state.
- Per-symbol progress logs are persisted.
- Report files for success, skip, and failure are written cleanly.
- Parquet writes are now atomic, reducing the chance of corrupted partial files after interruption.

### 2. Real refresh run completed successfully

Command run:

```powershell
$env:PYTHONPATH='src'
$env:POLYGON_PAUSE_SECONDS='12'
py -3.11 scripts\update_universe_and_data.py --resume --max-symbols 10 --per-screen-cap 5
```

Observed result:

- Finviz candidates: 11
- Finnhub metadata rows: 11
- Finnhub earnings rows: 5
- OHLCV success: 0
- OHLCV skipped: 11
- OHLCV failed: 0

Interpretation:

- the local cache for that small watchlist was already complete
- the resumable refresh behavior worked as intended
- data-refresh stability is no longer the immediate blocker for scan diagnosis on this sample

### 3. Current auto-generated candidate universe

Current symbols:

- `YUM`
- `YOU`
- `XPO`
- `XOM`
- `XIFR`
- `WWD`
- `VRT`
- `VIRT`
- `VG`
- `TSSI`
- `SPY`

This list is small and strongly biased toward stocks that passed long-oriented screening logic.

## Current Verified Problems

## A. The V2 scan is not implementing a full Martin Luk bear-market workflow

The most important issue is architectural, not just threshold tuning.

Current V2 behavior:

- `run_daily_scan_v2.py` builds setup scores only from `PullbackSetupDetector` and `BreakoutSetupDetector`
- both are long-side detectors
- symbols that do not pass those detectors are discarded before V2 ranking

Practical impact:

- the V2 entry path does not actively search for short candidates
- in a weak or bearish tape, the scanner is effectively asking:
  - "Which long setups still look good?"
  instead of:
  - "What is the best long or short opportunity given market context?"

This is the single biggest reason the current output is not trustworthy.

## B. The short-side code path is not production-usable yet

The repo does contain `src/martin_quant/setups/short_setup.py`, but it is not integrated into the V2 scan path and also contains a hard runtime bug.

Verified issue:

- `short_setup.py` references `SetupType.BREAKDOWN`
- `src/martin_quant/core/enums.py` currently defines only:
  - `PULLBACK`
  - `BREAKOUT`

Result:

- directly calling `ShortSetupDetector.detect()` raises `AttributeError`
- even exploratory diagnostics for short setups currently fail without code fixes

Practical impact:

- the short workflow is not just missing from ranking
- it is not even internally coherent enough to test safely

## C. Bear-regime handling currently behaves more like "go to cash" than "find shorts"

`SectorRegimeFilter` currently defines bear regime as:

- preferred: none
- allowed: none
- avoid: all known sectors

That makes sense for a conservative long-only regime filter, but not for a long-short or short-capable workflow.

Practical impact:

- in `BEAR`, sector logic suppresses participation broadly
- the scanner is not set up to say:
  - "avoid weak long sectors"
  and separately:
  - "prefer short setups in weak sectors"

Instead, it mostly says:

- "avoid everything"

That is inconsistent with the workflow described in the Martin article.

## D. The universe-generation step is long-biased

`config/research.yaml` currently uses these Finviz screens:

- `core_leaders`
- `growth_momentum`
- `pullback_candidates`
- `breakout_candidates`

The screen filters also lean long by design:

- `ta_sma50_pa`
- `ta_sma200_pa`
- `ta_highlow52w_a10h`
- positive RS and momentum bias

Practical impact:

- even before setup detection begins, the universe is filtered toward relative-strength and trend-intact names
- this is useful for long scans
- it is not sufficient for bear-market reversal or short-side opportunity finding

In other words, we are starting from the wrong watchlist for the current market question.

## E. Long-side detectors are functioning, but the current sample does not pass them

I ran local diagnostics on the cached sample and checked where each symbol failed.

### Pullback diagnostics

Observed first-failure reasons across 10 non-benchmark symbols:

- `depth_too_shallow`: 5
- `ema_stack`: 2
- `not_first_pullback`: 1
- `no_vud`: 1
- `too_far_from_support`: 1

Per-symbol examples:

- `YUM`: pullback too shallow at 4.8%
- `YOU`: pullback too shallow at 2.8%
- `XOM`: pullback too shallow at 0.6%
- `VRT`: pullback too shallow at 2.3%
- `VG`: pullback too shallow at 0.6%
- `XPO`: failed EMA stack
- `WWD`: failed EMA stack
- `XIFR`: not first pullback
- `VIRT`: no volume dry-up
- `TSSI`: too far from support

Interpretation:

- on this sample, the long pullback detector is rejecting names for understandable reasons
- this does not look like a simple runtime bug
- it does show that the long-side setup rules are strict enough to produce zero hits on a weak-market sample

### Breakout diagnostics

Observed first-failure reasons across 10 non-benchmark symbols:

- `below_resistance`: 9
- `low_rvol`: 1

Per-symbol examples:

- many names were still below 20-day resistance
- `VG` had only about `0.68x` relative volume

Interpretation:

- for this sample, the breakout detector is also behaving consistently with its own logic
- again, this does not mean the market had no opportunity
- it means this watchlist did not contain names in valid breakout posture

## F. The current result is not answering the real question

The question we care about is:

- why were there no actionable long or short opportunities?

What the current system actually answered was:

- why did none of these long-biased candidates pass strict long pullback / breakout conditions?

That is a narrower and different question.

## What The Martin Luk Article Actually Suggests

## A. Start from market behavior, not from pre-fixed setup taxonomies

The article starts with market context:

- more breakouts failing
- more stocks losing key moving averages
- expectation of a correction

This implies the first system question should be:

- what market phase are we in, and what opportunity class should dominate?

Current repo gap:

- regime exists, but it is not yet driving a proper long-vs-short opportunity architecture

## B. In weak markets, shorting at resistance matters more than shorting breakdowns

The article's core insight is that stocks often decline right after rejecting from declining moving averages, especially:

- the 21 EMA
- the 50 EMA

It also notes:

- the tighter the EMAs, the higher the rejection rate

Current repo gap:

- there is no properly integrated "short at resistance" detector in the main daily scan path

## C. Higher timeframe defines the area, lower timeframe confirms the entry

The article converges on a multi-timeframe process:

- higher timeframe identifies resistance
- lower timeframe confirms the reversal

Two higher-timeframe resistance types are emphasized:

1. declining moving averages near tightening EMAs
2. declining AVWAPs near tightening EMAs

Two lower-timeframe entry tactics are emphasized:

1. breakdown below the previous hourly bar low
2. intraday failed retest of VWAP or HOD AVWAP

Current repo gap:

- the existing V2 scan has AVWAP scoring, but not this specific short-side resistance-confirmation workflow
- ORB logic exists, but it is not the same as the entry logic described in the article

## D. Shorts are managed as quick swings with tight risk

The article states a short-management approach that is materially different from how many people treat longs:

- stop at HOD or at the breakdown candle high
- typical stop range around 1.5% to 5%
- risk per trade around 0.5%
- max position size around 35%
- trim at around +3R
- trim more at support such as prior lows, EMAs, or AVWAP
- exit if the hourly bar closes above the 50 EMA

Current repo gap:

- some risk modules exist in generic form
- this specific short-side management model is not yet wired into a real scan-to-execution or scan-to-paper-trade path

## E. Study questions must be specific

The article repeatedly emphasizes specificity:

- which EMA matters most?
- when exactly should the short be entered?
- what lower-timeframe confirmation defines the reversal?

It also distinguishes:

- pattern recognition from just a few charts
- execution refinement from reviewing 200+ charts

Current repo gap:

- we have code artifacts, but not yet a diagnostic framework that exposes exact failure reasons in a way that supports this style of refinement

## Translation Into A Concrete System Design

## Phase 0. Clarify system intent

Before code changes, the intended mode of the scanner must be explicit.

Recommended target mode:

- context-aware long-short idea generation
- not long-only
- not "always long-short equally"
- rather:
  - in `BULL`, prioritize long continuation / pullback setups
  - in `CHOPPY`, reduce aggressiveness and focus on defensive or special-case setups
  - in `BEAR`, prioritize short-at-resistance and reversal setups

Required design decision:

- whether `BEAR` should allow selective longs at all
- or whether `BEAR` becomes:
  - "short-first, long-rare"

My recommendation:

- `BEAR` should be short-first, not all-cash and not symmetric with `BULL`

## Phase 1. Rebuild the setup taxonomy around direction

Current problem:

- setup taxonomy is long-only in the main path

Required change:

- scanner setup candidates should become direction-aware

Proposed structure:

- long setups
  - pullback
  - breakout
  - EPS / catalyst continuation if kept
- short setups
  - short_resistance_reversal
  - failed_breakout_short
  - parabolic_short
  - breakdown_continuation_short

Implementation consequences:

- add new setup enum values
- normalize `SetupSignal` handling so long and short signals use the same interface cleanly
- make ranking direction-aware instead of assuming one scoring shape fits both sides

## Phase 2. Add bear-market candidate generation

Current problem:

- Finviz screens mostly generate long-biased names

Required change:

- add bear-side watchlist generation

Recommended new screens:

- `failed_breakout_shorts`
  - recent weakness after prior leadership
  - below key moving averages
  - elevated ATR / ADR
- `bounce_into_resistance`
  - under 50 or 200 SMA
  - recent rebound after a sharp decline
  - liquid and high-ADR
- `parabolic_short_candidates`
  - extended names with reversal risk
- `broken_leaders`
  - former RS leaders now under key MAs

Important note:

- the exact Finviz filters need pragmatic iteration
- they should not be treated as the final truth
- they are only universe builders for deeper strategy logic

## Phase 3. Implement Martin-style higher-timeframe short detectors

Required detector families:

### 3.1 Declining EMA resistance short

Core idea:

- stock rallies into a declining and tightening EMA cluster
- rejection risk increases when 21 and 50 are both nearby and sloping down

Suggested detector fields:

- EMA 21 slope
- EMA 50 slope
- EMA distance / tightness
- distance from current price to EMA cluster
- whether price approached from below
- whether recent bounce duration is acceptable
- whether price remains under major resistance

### 3.2 Declining AVWAP plus EMA resistance short

Core idea:

- stock rallies into AVWAP anchored from a swing high or other relevant reference
- AVWAP aligns with declining EMAs

Suggested detector fields:

- anchor type
- AVWAP slope / direction proxy
- AVWAP overlap with EMA 21 / 50 zone
- distance into resistance
- rejection quality

### 3.3 Failed breakout / broken leader short

Core idea:

- stock used to be strong
- breakout failed
- now rallying weakly into resistance

Suggested detector fields:

- prior leadership / RS history
- failed breakout event flag
- reclaim failure
- weak bounce structure

## Phase 4. Implement lower-timeframe confirmation rules for shorts

This is the most important missing piece relative to the article.

Required additions:

### 4.1 Previous-hourly-low breakdown trigger

Conditions to study and encode:

- higher-timeframe resistance is already identified
- hourly structure weakens
- short trigger occurs when price breaks the prior hourly low

Potential module:

- `timing/hourly_breakdown_trigger.py`

### 4.2 VWAP / HOD AVWAP failed retest trigger

Conditions to study and encode:

- stock has initial crack lower
- rebounds intraday into VWAP or HOD AVWAP
- first red confirmation at that retest becomes the short trigger

Potential module:

- `timing/vwap_fail_trigger.py`

Inputs likely needed:

- 1-minute or 5-minute intraday data
- session VWAP
- HOD AVWAP
- HOD and intraday structure state

Important consequence:

- current local data refresh may need lower intraday granularity than just `15m`
- likely `5m` becomes necessary for faithful implementation

## Phase 5. Redesign regime logic so bear mode prefers shorts instead of suppressing everything

Current problem:

- bear regime currently avoids all sectors

Required change:

- separate long-side and short-side regime logic

Recommended redesign:

- `allow_long(sector, regime)`
- `allow_short(sector, regime)`
- `sector_bonus_long(...)`
- `sector_bonus_short(...)`

Example behavior:

- `BULL`
  - prefer long tech / semi / discretionary
  - allow selective short only in broken / climactic names
- `CHOPPY`
  - reduce all aggression
  - focus on high-quality edge only
- `BEAR`
  - longs heavily restricted
  - shorts preferred in weak groups and broken leaders

## Phase 6. Rework scoring so it reflects direction-specific logic

Current issue:

- one generic score formula is used:
  - setup score times regime weight plus AVWAP bonus plus sector bonus

That is too generic.

Recommended redesign:

- split scoring into:
  - setup quality
  - contextual alignment
  - execution confirmation
  - risk efficiency

For shorts, candidate factors should include:

- quality of resistance cluster
- quality of lower-timeframe confirmation
- distance to stop
- available downside to next support
- market context alignment
- whether the name is a broken former leader or just random weakness

## Phase 7. Encode short-side risk and management rules explicitly

The article suggests a practical, not theoretical, short management framework.

Recommended implementation:

- stop basis:
  - HOD
  - breakdown candle high
  - or VWAP reclaim depending on trigger type
- sizing:
  - default 0.5% account risk
  - hard max position size cap
- partials:
  - trim at around +3R
  - trim at prior low / EMA / AVWAP support
- invalidation:
  - hourly close above 50 EMA

Potential code impact:

- `risk/partial_take_profit.py`
- `risk/exit_manager.py`
- `broker/order_manager.py`
- `broker/position_monitor.py`

## Phase 8. Build diagnostic tooling, not just signals

This is essential if we want to refine the strategy the way Martin describes.

New recommended outputs:

- per-symbol rejection reason logs
- detector-level pass/fail breakdown
- direction split:
  - long candidates
  - short candidates
- regime context snapshot
- top near-misses

This should answer questions like:

- how many symbols were discarded because of universe bias?
- how many because of higher-timeframe setup failure?
- how many because lower-timeframe trigger never confirmed?
- how many because stop distance was too wide?

Without this layer, further refinement becomes guesswork.

## Phase 9. Add review workflow and study support

To align with the article's study method, this project should support not only signal generation but also iterative review.

Recommended additions:

- save chart cases for:
  - accepted short setups
  - rejected near-miss setups
  - failed trades
- produce structured review rows with:
  - market regime
  - higher-timeframe resistance type
  - lower-timeframe trigger type
  - stop basis
  - initial R multiple outcome

This enables the "five charts to see a pattern, 200 charts to refine execution" loop described in the article.

## Proposed Implementation Sequence

## Step 1. Fix broken short-side foundations

Scope:

- fix enum mismatch
- normalize short `SetupSignal` payloads
- make short detectors runnable without runtime errors

Deliverable:

- short detector can be executed in isolation and in tests

## Step 2. Add short-biased universe screens

Scope:

- extend `config/research.yaml`
- add bearish candidate generation
- keep long and short candidate lists distinguishable

Deliverable:

- `auto_candidates_long.txt`
- `auto_candidates_short.txt`
- or a unified file with direction tags

## Step 3. Integrate short detectors into the daily scan pipeline

Scope:

- update `run_daily_scan_v2.py`
- update scanner interfaces
- allow long and short setup score maps

Deliverable:

- scan produces:
  - long setup candidates
  - short setup candidates
  - reasoned rejection counts

## Step 4. Implement higher-timeframe short resistance detectors

Scope:

- declining EMA resistance detector
- AVWAP plus EMA resistance detector
- failed-breakout / broken-leader detector

Deliverable:

- short candidates become strategy-specific instead of generic weakness names

## Step 5. Implement lower-timeframe short confirmation

Scope:

- hourly previous-bar-low break trigger
- VWAP / HOD AVWAP fail trigger

Deliverable:

- actual Martin-style short entries instead of placeholder short classification

## Step 6. Redesign regime and scoring logic

Scope:

- separate long and short regime behavior
- separate long and short sector preferences
- direction-aware scoring

Deliverable:

- `BEAR` behaves as "short-first" instead of "avoid all"

## Step 7. Add diagnostics and regression tests

Scope:

- unit tests
- smoke tests
- per-symbol rejection reporting
- sample-run assertions

Deliverable:

- changes become inspectable and harder to break silently

## Recommended First Coding Batch

If implementation starts, the first batch should stay narrow and testable.

Recommended Batch 1:

1. fix short enum and signal compatibility
2. integrate `ShortSetupDetector` into the scan path
3. split long-vs-short setup counts in reporting
4. add rejection-reason reporting
5. add one or two bearish universe screens

Why this first:

- it addresses the biggest false-negative source immediately
- it gives visibility into whether the market currently has short candidates
- it avoids prematurely coding complex lower-timeframe logic before the scanner can even surface bear-side names

## Risks and Open Questions

## Risk 1. Data granularity may be insufficient

The article's short entry workflow relies on:

- previous hourly low
- VWAP fail
- HOD AVWAP fail

Current pipeline mainly persists:

- `1d`
- `1h`
- `15m`

This may be sufficient for hourly-breakdown prototypes, but likely insufficient for faithful VWAP-fail implementation.

Likely requirement:

- add `5m` data

## Risk 2. Finviz may not express the exact short universe cleanly

Finviz is useful for coarse candidate generation, but bear-side screening may require approximation.

Implication:

- some short universe logic may need to be derived from local data instead of only from Finviz screens

## Risk 3. The article is principle-rich but not rule-complete

The article gives:

- strong process guidance
- strong structural clues
- strong risk-management clues

It does not provide:

- one finalized fully mechanical rule set

Implication:

- some translation decisions must be made explicitly
- those decisions should be documented and tested, not implied

## Risk 4. Overfitting execution details too early

Martin explicitly distinguishes:

- seeing the pattern quickly
- refining execution through many charts

Implication:

- the first implementation should aim for usable, inspectable logic
- not over-optimized micro rules from day one

## Recommended Decision Before Coding

Before implementation proceeds, these decisions should be approved:

1. Should the scanner become explicitly long-short?
2. Should bear regime become short-first rather than all-cash?
3. Should we add `5m` data to support VWAP-fail short entries?
4. Should long and short universes be generated separately?
5. Should Batch 1 focus on surfacing short candidates before coding full intraday entry logic?

## My Recommendation

Yes to all five.

Most important immediate conclusion:

- the current zero-signal behavior is not enough evidence to conclude "no trades"
- the system must first be rebuilt so it can look for the right kind of trade in the right kind of market

Only after that should we evaluate whether the market truly offered no opportunity.

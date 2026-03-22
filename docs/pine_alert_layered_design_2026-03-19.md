# Pine Alert Layered Design (Martin Luk Workflow) (2026-03-19)

## Purpose

This document defines the recommended layered Pine alert architecture for this project.

Goal:

- keep TradingView alert usage efficient
- stay as close as possible to Martin Luk workflow
- separate broad monitoring from precise execution confirmation
- make webhook payloads clean and consistent

This is the design specification, not the final Pine code.

## Core Principle

Do not put the entire Martin Luk workflow into one Pine script.

Split the logic into layers.

Reason:

- broad filters and precise triggers serve different purposes
- alert count should be used where it creates the most value
- Martin Luk workflow is naturally multi-stage
- scan, context, setup, and trigger should not all live in the same alert condition

## Recommended Layer Stack

Use four layers.

### Layer 0. Python Candidate Layer

This is outside Pine.

Purpose:

- decide which symbols deserve attention today
- produce daily long and short candidate lists
- reduce TradingView workload

Current source:

- `scan-v2`
- exported files in `outputs/signals`

Output:

- `ML_LONG` watchlist symbols
- `ML_SHORT` watchlist symbols

Pine should never replace this layer.

### Layer 1. Broad Watchlist Monitoring Layer

Purpose:

- notify when a symbol in today's watchlist starts behaving correctly
- low alert count
- broad coverage

TradingView object:

- watchlist alerts

Use only simple shared logic here.

Long examples:

- price reclaims short-term EMA zone
- price moves back above session VWAP
- price breaks intraday consolidation high

Short examples:

- price stays below key intraday average
- price breaks local intraday support
- price loses VWAP after weak bounce

This layer is not for exact execution.
It is only for:

- attention routing
- ranking what needs a chart look now

### Layer 2. Higher Timeframe Setup Validation Layer

Purpose:

- confirm the symbol still matches the intended Martin Luk structure
- reduce false alerts from random intraday noise

TradingView object:

- technical alert or Pine alertcondition

This layer should validate daily / hourly context.

Examples:

Long:

- daily bullish EMA stack
- price above daily EMA21 or EMA50
- recent pullback depth acceptable
- relative volume or contraction structure acceptable

Short:

- daily bearish EMA stack
- price below daily EMA9 or EMA21 cluster
- rally still below resistance
- short-side bounce into resistance rather than random breakdown extension

This layer should not be used alone.
It should gate Layer 3 triggers.

### Layer 3. Intraday Execution Trigger Layer

Purpose:

- define the actual Martin Luk-style timing event
- webhook this layer aggressively

TradingView object:

- technical alert or Pine alertcondition per symbol or per selected watchlist logic

This is the highest-value alert layer.

Recommended short triggers:

- previous hourly low break
- VWAP failed retest
- lower high then breakdown of micro support
- rejection at EMA / AVWAP cluster followed by first red confirmation

Recommended long triggers:

- reclaim of key intraday level after pullback
- break above opening range / local high
- first higher low above VWAP or EMA support

This layer is where webhook events should mainly come from.

## Recommended Operational Split

### Broad coverage

Use watchlist alerts for Layer 1 only.

Recommended count:

- `1` watchlist alert for `ML_LONG`
- `1` watchlist alert for `ML_SHORT`

This uses your two watchlist alert slots efficiently.

### Precision alerts

Use technical alerts for Layer 2 plus Layer 3 combinations on the highest-priority names.

Recommended count:

- `5` to `10` short names
- `3` to `5` long names only when regime supports them

This is still far below your `400 technical alerts` capacity.

## Script Architecture Recommendation

Use three Pine scripts, not one giant script.

### Script A. Broad Attention Router

Purpose:

- lightweight watchlist-level signal
- broad alert routing only

Recommended conditions:

Long mode:

- close above VWAP
- close above short EMA
- intraday momentum expansion

Short mode:

- close below VWAP
- close below short EMA
- intraday weakness after bounce

Recommended outputs:

- `LONG_ATTENTION`
- `SHORT_ATTENTION`

Use case:

- watchlist alerts only

### Script B. Martin Setup Validator

Purpose:

- validate that higher timeframe structure still matches the intended setup
- reduce garbage alerts

Recommended long checks:

- daily EMA stack bullish
- price near EMA21 or EMA50 support
- not too extended from support
- optional contraction or tightness behavior

Recommended short checks:

- daily EMA stack bearish
- price below resistance cluster
- rally into EMA resistance
- optional bearish candle structure
- optional hourly lower-high context

Recommended outputs:

- `LONG_SETUP_VALID`
- `SHORT_SETUP_VALID`

Use case:

- chart overlay
- optional alert
- mostly used as a prerequisite in Script C

### Script C. Martin Execution Trigger

Purpose:

- exact webhook event generation
- only fire when setup validation is already true

Recommended long triggers:

- reclaim trigger
- opening range / pivot break
- higher low confirmation

Recommended short triggers:

- previous hour low break
- VWAP fail
- intraday lower-high breakdown
- first red bar after resistance retest

Recommended outputs:

- `LONG_RECLAIM`
- `LONG_ORB`
- `SHORT_PREV_HOUR_LOW_BREAK`
- `SHORT_VWAP_FAIL`
- `SHORT_RETEST_FAIL`

Use case:

- webhook source of truth

## Martin Luk Mapping By Alert Layer

### Long side

#### Layer 1 long attention

Use for:

- symbol comes alive intraday
- price starts reclaiming levels

Not enough to enter by itself.

#### Layer 2 long setup valid

Use for:

- daily pullback structure still intact
- support has not failed
- long is still worth watching

#### Layer 3 long execution

Use for:

- reclaim trigger
- break of intraday pivot
- low-risk entry is available now

### Short side

#### Layer 1 short attention

Use for:

- weak intraday action begins
- symbol is losing intraday support

Not enough to enter by itself.

#### Layer 2 short setup valid

Use for:

- daily short-at-resistance structure still valid
- bounce remains below resistance
- higher timeframe context still matches Martin short idea

#### Layer 3 short execution

Use for:

- previous-hour low break
- VWAP fail
- retest rejection
- actual timing event worth sending to webhook

## Alert Allocation Plan

### Minimal version

- Script A only for watchlist alerts
- Script C only for top symbols

Allocation:

- `2` watchlist alerts total
- `10` to `20` technical alerts

This is the recommended starting point.

### Expanded version

- Script A for watchlist alerts
- Script B visual only or optional alerts
- Script C for execution alerts

Allocation:

- `2` watchlist alerts
- `20` to `40` technical alerts

Still well within Premium limits.

## Recommended Daily Rotation Logic

### In bull regime

Focus:

- more long names
- fewer short names

Suggested allocation:

- `8` to `12` long names
- `3` to `5` short names

Priority triggers:

- `LONG_RECLAIM`
- `LONG_ORB`
- selective short only in broken names

### In choppy regime

Focus:

- smaller lists
- best quality only

Suggested allocation:

- `4` to `8` long names
- `4` to `8` short names

Priority triggers:

- simple attention alerts first
- execution triggers only on A-tier names

### In bear regime

Focus:

- short-first
- longs rare

Suggested allocation:

- `8` to `12` short names
- `0` to `4` long names

Priority triggers:

- `SHORT_PREV_HOUR_LOW_BREAK`
- `SHORT_VWAP_FAIL`
- `SHORT_RETEST_FAIL`

## Recommended Condition Hierarchy

### Long hierarchy

1. symbol is in daily candidate list
2. daily structure is still valid
3. intraday reclaim or break confirms entry
4. webhook fires

### Short hierarchy

1. symbol is in daily candidate list
2. rally into resistance remains valid
3. intraday failure confirms weakness
4. webhook fires

This matches Martin Luk much better than alerting on raw intraday movement alone.

## Suggested Alert Names

### Watchlist layer

- `ML_LONG_ATTENTION_WL`
- `ML_SHORT_ATTENTION_WL`

### Setup validation layer

- `ML_LONG_SETUP_VALID`
- `ML_SHORT_SETUP_VALID`

### Execution layer

- `ML_LONG_RECLAIM`
- `ML_LONG_ORB`
- `ML_SHORT_PREV_HOUR_LOW_BREAK`
- `ML_SHORT_VWAP_FAIL`
- `ML_SHORT_RETEST_FAIL`

## Suggested Webhook Payload Strategy

Keep one common JSON shape across all Pine alerts.

Only change these fields:

- `direction`
- `setup`
- `trigger`
- optional `notes`

Everything should post to the same webhook URL.

Reason:

- simplifies receiver logic
- avoids per-alert integration drift
- makes review logs consistent

## What Should Not Be Put Into Pine First

Avoid these in the first Pine version:

- full universe selection logic
- complicated relative-strength ranking
- complete multi-anchor AVWAP research engine
- detailed risk sizing rules
- full trade management engine

Those belong in Python or later refinement.

Pine should focus on:

- structure visibility
- alert timing
- clean payload output

## Best First Implementation Order

### Phase 1

Build Script A and Script C only.

That gives you:

- broad watchlist monitoring
- precise execution webhook alerts

### Phase 2

Add Script B validation layer for better filtering.

### Phase 3

Refine short triggers with better hourly and VWAP logic.

## Concrete Recommendation For Your Current Setup

Start with this exact stack:

- Python `scan-v2` decides today's names
- TradingView watchlists: `ML_LONG`, `ML_SHORT`
- Script A on both watchlists for broad attention
- Script C on top-priority names for webhook execution alerts
- same webhook URL for everything

Use these trigger priorities first:

1. `SHORT_PREV_HOUR_LOW_BREAK`
2. `SHORT_VWAP_FAIL`
3. `LONG_RECLAIM`

This is the most practical and most Martin-aligned version for your current tools.

## Next Step

After reviewing this design, the next coding step should be:

1. write `Script A` specification in Pine terms
2. write `Script C` specification in Pine terms
3. implement the first Pine indicator/alertcondition set

That is the correct order.

# Martin Transcript Engineering Spec

Date: 2026-03-20
Corpus: `docs/youtube/*.txt`

## Scope

This spec is built from the full transcript corpus currently under `docs/youtube`.
The folder contains 33 `.txt` files, but `$AEVA $30 _ 9 Jan 2026 (1).txt` appears to be a duplicate of `$AEVA $30 _ 9 Jan 2026.txt`, so the effective corpus is 32 unique videos/transcripts.

The goal is not to clone every discretionary nuance immediately.
The goal is to separate:
1. high-confidence repeated rules that should become code now
2. medium-confidence rules that need parameterized experimentation
3. low-confidence discretionary commentary that should stay as notes until more evidence accumulates

## Corpus-Level Findings

Repeated themes across the full corpus:
- `weekly` context is everywhere and is one of the strongest recurring filters
- `EMA9` is Martin's most repeated tactical line for both longs and shorts
- `EMA50` is the major daily trend/support-resistance divider
- `EMA21` appears as a secondary but important support/resistance line
- gap context matters a lot: gap up into resistance, gap down into support, gap fill, previous-day close
- AVWAP/anchored VWAP is used as contextual support/resistance, not as a single magic anchor
- many sessions are explicitly treated as low-quality or no-trade days
- for shorts, the dominant repeated pattern is still: wait, bounce into declining resistance, then break down
- for intraday timing, Martin repeatedly avoids acting aggressively in the first 15 minutes
- "wait for the close" appears often enough that close-confirmation logic should become a formal part of the system

## High-Confidence Rules To Engineer

### 1. Market First

The general market is the primary filter.
Repeated rule cluster:
- if QQQ/IWM/SPY are weak, long follow-through degrades sharply
- if the market is gapping down hard, do not chase fresh shorts immediately at the open
- if the market is mixed/choppy, trade less or skip
- if indices are reclaiming support, some shorts should be avoided or covered faster

Engineering implication:
- market context must drive trade aggressiveness, setup allowance, and position sizing
- scanner/regime should not just output `BULL/CHOPPY/BEAR`
- it should also output flags like:
  - `breakout_friendly`
  - `trade_less`
  - `short_bias_ok`
  - `avoid_new_shorts_on_open`

Status:
- basic version already implemented
- next version should add breadth proxies, gap-fill proximity, and benchmark reclaim/failure states

### 2. Weekly Before Daily

Repeated rule cluster:
- weekly chart quality is a major precondition
- many daily pullbacks are only considered attractive if the weekly is constructive
- many shorts are preferred when weekly structure is rolling over or rejecting weekly EMA levels

Engineering implication:
- every setup should ingest a weekly context object
- weekly context should include:
  - weekly EMA9 / EMA21 / EMA50 alignment
  - weekly distance to key EMA support/resistance
  - inside week / prior swing high-low / major base structure flags
  - whether weekly is extended or compressing

Status:
- partial weekly context exists in repo
- next step should make weekly context mandatory in setup scoring, not optional decoration

### 3. EMA Framework

High-confidence repeated moving average framework:
- daily EMA9: tactical trend / immediate support-resistance / exit line
- daily EMA21: deeper pullback support or stronger short resistance
- daily EMA50: major trend line and more important structural level
- daily EMA150: broader trend regime / major support-resistance / watchlist classification
- weekly EMA9 and EMA21 are repeatedly cited for context

Engineering implication:
- all setup modules should standardize on `9 / 21 / 50 / 150`
- exit logic should explicitly understand "close back above EMA9" for shorts and "close below EMA9" for longs when relevant

Status:
- partly implemented already
- daily short setup was aligned to EMA21 in this pass
- full exit-manager integration still missing

### 4. Watchlist Structure

Repeated rule cluster:
- strong separation between leaders and laggards
- watchlist discussion repeatedly uses trend state, sector leadership, and relative strength
- transcript on `12 Mar 2026` explicitly defines:
  - leading = `price > EMA50 and EMA50 > EMA150`
  - lagging = `price < EMA50 and EMA50 < EMA150`
  - mediocre = in between

Engineering implication:
- watchlist should not be a single long-only ranked list
- it should maintain parallel buckets:
  - `leading_longs`
  - `mediocre_monitor`
  - `lagging_shorts`
- sector and theme labels should remain attached for later regime routing

Status:
- transcript bucket builder already implemented
- next step is integrating those buckets into scanner/routing instead of only exposing helpers

### 5. Short The Bounce

This is one of the clearest repeated rule clusters in the corpus.
Repeated rule cluster:
- do not love initiating fresh shorts after a hard flush at the open
- the better short is usually the first or early bounce into declining resistance
- preferred resistance references:
  - declining daily/intraday EMA9
  - daily/intraday EMA21
  - daily EMA50 in larger retracements
  - AVWAP from swing high / major event
  - unfilled gap / prior close / prior support turned resistance
- if price closes back above EMA9, often that is a cover or stop signal

Engineering implication:
- short logic must be multi-stage:
  1. context says short bias is valid
  2. price bounces into resistance reference
  3. lower timeframe confirms failure/breakdown
  4. cover rule tracks EMA9 close/reclaim

Status:
- first executable short retest trigger is implemented
- next version should add:
  - first-bounce detection
  - ranking of resistance confluence
  - benchmark-relative weakness filter
  - cover/exit integration

### 6. First 15 Minutes Are Usually Noisy

Repeated rule cluster:
- many mentions of waiting the first 15 minutes
- spreads and whipsaws are repeatedly cited
- Martin often regrets acting too early

Engineering implication:
- intraday triggers should default to `wait_first_15m = True`
- opening-range logic should be filtered by market-state and setup quality
- the first 15 minutes should be allowed only for exceptional A+ cases if enabled at all

Status:
- long ORB already effectively waits
- short retest trigger now explicitly waits
- next step is making this a global intraday timing policy instead of trigger-specific behavior

### 7. Wait For The Close / Close Confirmation

Repeated rule cluster:
- many references to not trusting intrabar pokes
- close below or above EMA9 matters
- waiting for close is often used to avoid fake reclaim or fake breakdown

Engineering implication:
- signals need a `confirmation_mode`:
  - `intrabar`
  - `bar_close`
  - `daily_close`
- exits also need close-confirmed modes
- review labels should distinguish between anticipatory vs confirmed entries

Status:
- not fully engineered yet
- this is a top-priority next module

### 8. No-Trade / Trade-Less Days Are Real

Repeated rule cluster:
- explicit "no trade", "skip", "do nothing", and "stop trading until comfortable" comments
- low-quality market conditions are treated as an actionable state, not just commentary

Engineering implication:
- add a `trade_quality_state` layer on top of regime:
  - `A_GO`
  - `SELECTIVE`
  - `OBSERVE_ONLY`
- this should be driven by:
  - benchmark alignment
  - breadth / leader participation
  - first-hour behavior
  - range compression vs whipsaw
  - conflicting sector rotations

Status:
- only partially represented today via `trade_less`
- should become a first-class engine output

## Medium-Confidence Rules To Engineer Next

### 9. Sector Rotation / Sector Selection

Repeated rule cluster:
- healthy markets rotate into other sectors
- Martin repeatedly comments on whether only semis are holding, whether defensives are leading, whether beaten-down names are bouncing, etc.
- he often avoids shorts in names stronger than their sector ETF
- he likes longs more when sector leadership supports the setup

Engineering implication:
- sector logic needs to compare stock relative strength to sector ETF, not just a static sector allow/avoid table
- future module should compute:
  - stock vs sector relative strength
  - sector vs QQQ/SPY relative strength
  - whether the setup is with or against current sector rotation

Status:
- current sector regime filter is still too static
- needs a dynamic sector-relative-strength layer

### 10. Gap Taxonomy

Repeated rule cluster:
- gap up into declining EMA = potential short
- gap down into support = not an automatic short
- gap fill and prior close are major intraday levels
- opening gap context strongly affects whether to act or wait

Engineering implication:
- create a dedicated gap-context feature block:
  - gap size
  - gap direction
  - gap into support vs resistance
  - gap fill distance
  - prior day close distance
  - overnight extension state

Status:
- pieces exist in comments and trigger logic, but no dedicated feature module yet

### 11. Relative Strength / Relative Weakness Routing

Repeated rule cluster:
- leaders should not be shorted casually
- weak names in weak sectors are better short candidates
- strong names in strong sectors can still hold support even on rough days

Engineering implication:
- scanner routing should be direction-aware using:
  - stock vs benchmark RS
  - stock vs sector ETF RS
  - whether price is holding key EMA levels better or worse than peers

Status:
- only partially represented through old RS ranking and static sector config

## Low-Confidence Or Hard-To-Code Discretionary Areas

These should be documented, not overfit immediately:
- "feel" for whether a gap is too obvious or too crowded
- judgement on whether a right side of base is long enough
- deciding whether a bounce is only a bounce or an actual character change
- visual interpretation of leader participation when transcripts are conversational rather than formal

These should eventually become review overlays or analyst notes, not hard filters right away.

## Recommended Architecture Next

### A. Market / Trade Quality Layer
- extend current market context into a richer `MartinTradeQualityState`
- include benchmark trend, gap context, breadth proxy, and sector participation proxy
- output both regime and participation quality

### B. Weekly Context Layer
- unify weekly context into a reusable object consumed by every setup detector
- mandatory for scoring longs and shorts

### C. Gap Context Layer
- standalone feature module for gap classification and distance to fill / prior close / major EMA / AVWAP

### D. Directional Routing Layer
- use watchlist bucket + relative strength + sector-relative-strength to route symbols to long or short engines

### E. Close-Confirmation Layer
- a generic trigger/exit confirmation policy shared by reclaim, ORB, short retest, and future modules

### F. Exit Layer
- formalize:
  - long fail = close below tactical EMA / support
  - short fail = close back above EMA9 or reclaim of resistance line
  - partial profits at logical higher-timeframe targets rather than only fixed R

## Coding Priority Order

1. `close_confirmation.py`
- shared confirmation policy for entries and exits

2. `gap_context.py`
- gap up/down into support/resistance taxonomy

3. `weekly_context` integration pass
- make weekly context required in setup scoring

4. dynamic sector-relative-strength module
- stock vs sector ETF and sector vs benchmark

5. trade-quality / no-trade engine
- convert "skip/do nothing/trade less" into explicit engine state

6. exit manager upgrade
- implement EMA9 close-based short cover and long fail rules

## Already Reflected In Code

The repo now has first-pass implementations for:
- transcript watchlist buckets using `price / EMA50 / EMA150`
- market-first context evaluation using benchmarks
- Martin-style short retest breakdown trigger
- scanner integration for short timing trigger
- daily short setup aligned to `9 / 21 / 50`

## Practical Conclusion

The corpus says the missing gap is not "code cannot do it".
The missing gap is that the repo still lacks several orchestration layers around an already valid setup core:
- trade-quality state
- weekly gating
- gap taxonomy
- close-confirmation
- dynamic sector/relative-strength routing
- exit behavior that matches how Martin actually manages trades

That is the correct next design direction.

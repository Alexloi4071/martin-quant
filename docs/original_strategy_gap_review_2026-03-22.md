# Original Strategy Gap Review

Date: 2026-03-22

This document summarizes the main remaining gaps between the current `martin-quant` project and the Martin Luk strategy/process described in the source transcripts, notes, and internal engineering docs.

The point is not that the project is incomplete in a generic sense. The point is that several parts are still simplified relative to the original trading process, especially in how regime, sector rotation, directional routing, and exits are handled.

## Summary Table

| Priority | Gap Area | Current State | Main Problem | Recommended Direction |
|---|---|---|---|---|
| P0 | Formal strategy spec | Still placeholder | No single source of truth | Write a real executable spec |
| P0 | Main scan flow vs bear-market logic | Legacy flow still skips too much | `BEAR` still behaves too defensively in V1 | Promote V2 logic to primary path |
| P1 | CLI / operator workflow completeness | Some commands are placeholder / stub | Strategy modules exist but are not fully exposed | Replace placeholders with real diagnostics |
| P1 | RS / sector routing | Mixed static + partial dynamic logic | Still not fully Martin-style direction-aware routing | Make dynamic RS the core routing layer |
| P1 | Exit / partial profit logic | Mostly fixed-R based | Too mechanical vs original structure-based exits | Move to higher-timeframe target logic |
| P2 | Discretionary review overlays | Mostly documented, lightly modeled | Important judgment layer still outside the engine | Build structured review overlays instead of hard rules |

## 1. Formal Strategy Specification Is Still Missing

### Current state

The formal strategy spec file is still only a placeholder:

- `docs/strategy_spec.md`

It currently lists planned sections only:

- universe definition
- market regime logic
- setup definitions
- trigger definitions
- stop and exit logic
- scoring and ranking
- manual review criteria

### Why this matters

Without a fully written spec, the codebase can pass tests and still drift from the original process. Different modules may each implement a reasonable interpretation, but there is no final reference describing:

- which rules are canonical
- which are heuristic
- which are transcript-derived but still provisional
- which scanner path is the official one

### Improvement plan

1. Replace `docs/strategy_spec.md` with a real strategy contract.
2. Split every rule into:
   - canonical rule
   - engineering approximation
   - known deviation from source material
3. Mark which path is official:
   - legacy V1
   - V2 scan
   - live execution path
4. Add a cross-reference table from spec sections to code modules.

### Expected result

The project gets a stable reference point. Future changes can be evaluated as:

- aligned with spec
- intentional deviation
- regression

## 2. Main Scan Flow Still Does Not Fully Reflect Bear-Market Martin Logic

### Current state

The legacy daily scanner still gates out symbols too aggressively in bear markets. In the current V1 flow, if regime is `BEAR`, the loop can skip before meaningful long/short routing occurs.

This is materially weaker than the project’s own internal gap-analysis target, which explicitly says bear markets should become short-first, not no-trade-by-default.

### Why this matters

Martin’s original process is not simply:

- bull = long
- bear = avoid everything

It is much closer to:

- strong bull = long-friendly
- mixed = selective, confirmation-heavy
- bear / weak participation = short-first or observe-only depending on context

### Improvement plan

1. Treat V2 regime/trade-quality logic as the canonical path.
2. Deprecate the legacy V1 regime gate once V2 parity is complete.
3. Ensure main CLI scan path uses:
   - market context
   - trade quality
   - breadth state
   - direction-aware allow/deny rules
4. Make `BEAR` behavior explicitly branch to:
   - selective shorting
   - observe-only
   - no new longs

### Expected result

The engine will behave closer to the original process:

- not overtrading weak environments
- not disabling valid short-side behavior
- expressing genuine directional bias instead of generic caution

## 3. Operator-Facing Workflow Is Still Partly Placeholder

### Current state

Some user-facing CLI entry points remain incomplete:

- `sectors` prints a placeholder message
- `orb` is still a stub / not implemented command

This means some lower-level modules exist, but the workflow exposed to the user still lags the actual strategy model.

### Why this matters

A strategy system is not only its internal modules. It also needs operator-facing tools that let the user inspect:

- sector state
- regime state
- ORB readiness
- directional routing reasons
- why a symbol was accepted or rejected

### Improvement plan

1. Replace `sectors` placeholder with a real sector diagnostics report:
   - static sector category
   - dynamic sector relative strength
   - sector ETF mapping
   - long/short preference by regime
2. Replace `orb` stub with real diagnostics:
   - opening range
   - confirmation state
   - failure / breakout notes
   - benchmark context
3. Add an explain mode for scan results:
   - accepted because
   - rejected because
   - waiting for confirmation because

### Expected result

The project becomes inspectable in a Martin-style workflow rather than just producing output files.

## 4. Relative Strength And Sector Routing Are Not Yet Fully Original-Style

### Current state

There are two layers today:

1. Older simplified logic:
   - static sector preference tables
   - placeholder-style RS usage in older scanner code
2. Newer V2 logic:
   - dynamic sector strength bonuses
   - breadth and trade-quality overlays

The problem is that dynamic logic exists, but it has not completely replaced the older simplified routing model everywhere.

### Why this matters

Martin’s process is highly relative:

- stock vs benchmark
- stock vs sector
- sector vs market
- strong names should not be casually shorted
- weak names in weak sectors are higher-quality shorts

Static sector allow/avoid tables are useful, but they are not the full model.

### Improvement plan

1. Move dynamic sector RS from “bonus layer” to “core routing layer”.
2. Make direction routing depend on:
   - stock vs benchmark RS
   - stock vs sector RS
   - sector vs benchmark RS
   - weekly context alignment
3. Reduce reliance on hardcoded preferred/avoid lists.
4. Emit routing diagnostics per symbol:
   - long-routable
   - short-routable
   - neither

### Expected result

The scanner will look much more like the original discretionary process and much less like a static filter stack.

## 5. Exit Logic Is Still Too Fixed-R Relative To The Original Process

### Current state

The exit and scaling logic still leans heavily on mechanical R targets such as:

- 3R partial
- 5R partial
- fixed R multiple targets in simulation / planning

That is clean for testing and automation, but it is still more mechanical than the original approach.

### Why this matters

Martin’s exit process is often structural, not just numeric. In practice that means:

- partial into logical extension
- respect of higher-timeframe resistance/support
- adaptation to market participation
- sometimes more aggressive profit-taking in poor environments
- sometimes more patient holding in trend conditions

### Improvement plan

1. Add structural target providers:
   - prior weekly high/low
   - major AVWAP cluster
   - gap-fill / prior-close zones
   - higher-timeframe resistance/support
2. Let exit planning choose between:
   - fixed-R fallback
   - structural target
   - hybrid target stack
3. Tie exit aggression to:
   - trade quality state
   - breadth
   - regime
4. Keep fixed R only as:
   - backtest baseline
   - fallback when no structure is available

### Expected result

The system will stop looking like a generic systematic swing model and start acting more like the original discretionary framework.

## 6. Discretionary Review Layer Is Not Yet Fully Engineered

### Current state

The codebase already documents several hard-to-code discretionary areas, but most of them still sit outside the operational engine.

Examples:

- whether a gap is too obvious / crowded
- whether a base is mature enough
- whether a bounce is only a reflex bounce
- whether leader participation is convincing

### Why this matters

These are not necessarily good candidates for immediate hard rules. But if they remain totally external, the system misses a large part of the original edge.

### Improvement plan

1. Build structured review overlays instead of forcing hard filters too early.
2. Add analyst-note style outputs for:
   - crowded gap risk
   - weak right-side base development
   - low-quality rebound character
   - leader participation mismatch
3. Store these as review features in reports and scan exports.
4. Use them first as:
   - notes
   - score caps
   - confirmation requirements
   not immediate binary filters.

### Expected result

The system preserves the discretionary edge without pretending that every nuance is already ready for hard automation.

## Recommended Execution Order

1. Write the formal strategy spec and define the official scan path.
2. Promote V2 scan logic to primary workflow and retire conflicting V1 behavior.
3. Replace CLI placeholders with real diagnostics.
4. Rebuild routing around dynamic RS and sector-relative-strength.
5. Upgrade exits from fixed-R-first to structure-aware.
6. Add structured discretionary review overlays.

## Suggested Definition Of Done

The project is materially closer to the original content when all of the following are true:

- there is one canonical strategy spec
- the default scan path is direction-aware and bear-capable
- sector and ORB diagnostics are real tools, not placeholders
- symbol routing is based on relative strength relationships, not mostly static sector tables
- exits can use structure-aware targets
- discretionary transcript insights appear in review outputs and score logic

## Practical Read

Current status in one sentence:

The project already has many of the right building blocks, especially in the V2 path, but it still needs consolidation, promotion of the newer logic to the main workflow, and replacement of several simplifications that are still less nuanced than the original Martin process.

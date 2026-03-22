# Martin Luk Scan to TradingView Workflow (2026-03-19)

## Purpose

This document explains the practical workflow for this project:

1. how stocks are selected each day
2. how that differs from a full Martin Luk workflow
3. how to use TradingView Premium without running into alert limits
4. how scan results should flow into webhook, Telegram, and review logs

This is the operational design document, not the Pine Script implementation.

## Short Answer

Yes, the current repo can now generate both long and short candidates.

But no, it is still not a full Martin Luk workflow yet.

Current state:

- universe generation is still mostly long-biased
- scan engine now supports both long and short setup candidates
- TradingView should be used as the intraday confirmation layer
- one webhook URL can be shared by all alerts
- the real resource limit is alert count, not webhook URL count

## What The Repo Currently Scans

Current stock universe comes mainly from `config/research.yaml`.

Primary screens still in use:

- `core_leaders`
- `growth_momentum`
- `pullback_candidates`
- `breakout_candidates`

That means the watchlist source is still mostly built from:

- stocks above key moving averages
- stocks near highs
- relative-strength / momentum names
- long-oriented pullback and breakout candidates

So the answer is:

- current universe selection is still not fully Martin Luk bear-market style
- it is still too long-biased at the stock-selection layer

## What The Repo Currently Checks After Universe Selection

The current V2 runner now evaluates three base setup families:

- `pullback`
- `breakout`
- `short_resistance_reversal`

### Pullback conditions

Main ideas:

- pullback depth roughly `5%` to `30%`
- near `EMA21` or `EMA50`
- bullish EMA stack
- volume dry-up
- first pullback logic

### Breakout conditions

Main ideas:

- near or through resistance
- breakout bar relative volume at least `1.5x`
- VCP / contraction quality
- EMA structure confirmation

### Short conditions

Main ideas:

- `EMA9 < EMA20 < EMA50`
- price below `EMA9`
- bounce into short-term resistance within `3%`
- `EMA20` declining
- bearish candle confirmation
- default minimum reward/risk around `2R`

After setup detection, V2 applies:

- regime weight
- AVWAP contribution
- sector bonus
- long/short direction-aware sector logic

## What Is Still Missing Versus Full Martin Luk

The current repo is only partially aligned.

Still missing or incomplete:

- bear-market universe generation built specifically for short ideas
- broken leader and failed-breakout short universe logic
- proper `1H` previous-bar-low short trigger workflow
- proper `VWAP fail` / `HOD AVWAP fail` intraday trigger workflow
- `5m` data integration for intraday confirmation
- full review loop around accepted setups, near misses, and failed trades

So the correct mental model is:

- Python repo chooses what deserves attention
- TradingView decides whether intraday confirmation actually happens

## Key Design Principle

Do not use TradingView to search the whole market.

Use the repo to narrow the market first.

Then use TradingView only for:

- chart confirmation
- intraday trigger confirmation
- webhook firing

That is much closer to Martin Luk's process.

## Why Webhook URL Count Is Not The Real Problem

A single webhook URL can receive alerts for many symbols and many setups.

Example:

- all long alerts can hit the same webhook URL
- all short alerts can also hit the same webhook URL
- both can still go to the same Cloudflare Worker endpoint

The webhook URL is shared.

What matters is TradingView alert count.

## TradingView Premium Limits That Matter

TradingView Premium currently provides these relevant limits:

- `400` price alerts
- `400` technical alerts
- `2` watchlist alerts

Implication:

- you should not create one workflow per webhook URL
- you should design around alert count and watchlist structure

## Best Practical Workflow For Your Account

The best version for your setup is a hybrid model.

### Layer 1. Python decides the candidate list

Every day, run `scan-v2`.

Output should create:

- long candidate list
- short candidate list
- ranked scores
- levels and notes
- exported symbol files

Current outputs already support:

- `outputs/signals/candidates_YYYY-MM-DD.csv`
- `outputs/signals/candidates/candidates_YYYY-MM-DD.json`
- `outputs/signals/long_symbols_YYYY-MM-DD.txt`
- `outputs/signals/short_symbols_YYYY-MM-DD.txt`

### Layer 2. TradingView holds only today's watchlists

Maintain two TradingView watchlists:

- `ML_LONG`
- `ML_SHORT`

Every day, replace the contents using the latest scan results.

Recommended size:

- `5` to `20` long names
- `5` to `20` short names

Do not send the full market into TradingView alerts.

Martin Luk does not need the full market in real time. He needs the best names in context.

### Layer 3. Use watchlist alerts for broad coverage

Use the two TradingView watchlist alerts like this:

- one alert for `ML_LONG`
- one alert for `ML_SHORT`

These are the most alert-efficient broad monitoring tools.

Use them for simple shared conditions only.

Examples:

- broad long reclaim condition
- broad short weakness / breakdown condition

This gives you a very cheap first notification layer.

### Layer 4. Use technical alerts for the highest-priority names

For the top names only, add more specific technical or Pine alerts.

Recommended use:

- top `5` short names get `prev_hour_low_break`
- top `5` short names get `vwap_fail`
- top `5` long names get `reclaim`

If you do this:

- `5` short names x `2` alerts = `10`
- `5` long names x `1` alert = `5`

That is only `15` technical alerts.

Your Premium limit is far above that.

So the practical answer is:

- symbol rotation is not a problem if you keep the daily candidate list small

## Recommended Daily Workflow

### Daily Prep

1. Run the Python scan.
2. Review the long and short ranked candidates.
3. Select only the best names for the day.
4. Update TradingView watchlists `ML_LONG` and `ML_SHORT`.
5. Keep the candidate count small and intentional.

### During Market Hours

1. TradingView monitors only today's selected names.
2. Watchlist alerts provide broad monitoring.
3. Technical alerts provide specific intraday triggers.
4. All alerts go to the same webhook URL.
5. Webhook writes events and optionally sends Telegram.

### End of Day

1. Review which names triggered.
2. Compare triggered names with scan ranking.
3. Log accepted setups, near misses, and failed ideas.
4. Refine the universe logic and trigger logic separately.

## Recommended Alert Architecture

### Option A. Minimal Setup

Best when you want low maintenance.

- `2` watchlist alerts total
- one for `ML_LONG`
- one for `ML_SHORT`
- same webhook URL for both

Pros:

- easiest to maintain
- very low alert usage

Cons:

- less specific than full Martin intraday logic

### Option B. Recommended Setup

Best balance for your account.

- `2` watchlist alerts for broad coverage
- `10` to `20` technical alerts for top-priority names
- same webhook URL for all alerts

Pros:

- efficient
- closer to Martin style
- easy to rotate names daily

Cons:

- requires daily watchlist maintenance

### Option C. Maximum Precision

Use only if you later automate watchlist sync and Pine logic.

- daily top candidates from Python
- many per-symbol technical alerts
- finer intraday trigger logic on `1H`, `15m`, and later `5m`

Pros:

- closest to full Martin execution workflow

Cons:

- highest maintenance cost
- not necessary yet

## Recommended Alert Allocation

For your current phase, use this:

- watchlist alerts: `2`
  - `ML_LONG_WATCHLIST`
  - `ML_SHORT_WATCHLIST`
- technical alerts: `10` to `20`
  - top short names first
  - long names only if market context supports them

Recommended priority order:

1. short `prev_hour_low_break`
2. short `vwap_fail`
3. long `reclaim`

That ordering is closer to the current Martin Luk study focus.

## How Notification Flow Should Work

### Flow

```text
Python scan
-> candidate files
-> TradingView watchlists
-> TradingView alerts
-> shared webhook URL
-> Worker / receiver
-> outputs/signals/events.jsonl
-> Telegram
-> review / journal
```

### Shared Webhook Principle

All of these can use the same webhook URL:

- short alerts
- long alerts
- test alerts
- reclaim alerts
- VWAP fail alerts
- previous-hour low break alerts

The payload tells the receiver what type of event it is.

So symbol rotation does not require a new webhook URL.

## How To Decide Which Stocks Enter TradingView Each Day

This is the practical decision model I recommend.

### Short list first in weak markets

In `BEAR` or weak conditions:

- prioritize short names first
- only include long names if they are exceptional

Recommended short candidate filters for TradingView list:

- top total score in scan
- clear daily resistance structure
- broken leader behavior
- tight stop distance
- enough downside room
- liquid enough to trade cleanly

### Long list only when justified

In `BULL` or stronger tape:

- keep long pullback / breakout names
- reduce short list size

### Suggested final daily list size

Do not overload yourself.

Recommended:

- `5` to `10` primary short names
- `3` to `8` secondary short names
- `5` to `10` primary long names only if regime supports it

## What Needs To Be Improved Later

To get closer to full Martin Luk workflow, the next major upgrades should be:

1. add bearish universe screens
2. add broken leader / failed breakout universe logic
3. add `5m` data support
4. add explicit `prev_hour_low_break` timing module
5. add explicit `VWAP fail` timing module
6. build a better review output for near-miss and triggered intraday cases

## Current Recommended Operating Mode

Use this for now:

- Python = market narrowing engine
- TradingView = intraday trigger engine
- webhook = event transport
- Telegram = notification layer
- review logs = study layer

That is the most practical version of Martin Luk style execution for your current tools.

## My Recommendation

Use the `Option B` architecture now.

That means:

- Python scan every day
- two TradingView watchlists only
- two watchlist alerts for broad coverage
- add specific technical alerts only for top names
- all alerts share one webhook URL

This solves the alert limit issue and keeps the workflow close to Martin Luk's actual process.

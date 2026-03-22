# Pine Script C1 Setup Notes (2026-03-19)

## File

- `tradingview/ml_script_c1_short_prev_hour_low_break.pine`

## Purpose

Script C1 is the first actual execution-timing script.

It is designed to answer one question only:

- has the stock broken below the previous hourly bar low under bearish context?

This is the first real trading trigger layer after:

- Python candidate selection
- Script A attention routing
- Script B setup validation

## Intended Sequence

Use the workflow in this order:

1. stock is on today's Python short candidate list
2. `ML_SHORT_ATTENTION` appears from Script A
3. `ML_SHORT_SETUP_VALID` appears from Script B
4. `ML_SHORT_PREV_HOUR_LOW_BREAK` appears from Script C1

That fourth event is the first actual short execution trigger candidate.

## What Script C1 Checks

Primary trigger:

- `close` crosses below the previous hourly low

Optional gating filters:

- daily bearish EMA stack
- hourly weak structure
- close below VWAP
- close below intraday EMA
- red trigger bar
- minimum bar expansion relative to ATR

## Alert Name

The script exposes one main alertcondition:

- `ML_SHORT_PREV_HOUR_LOW_BREAK`

## Recommended Settings

For first use, keep these enabled:

- `Require Daily Short Structure = true`
- `Require Hourly Weak Structure = true`
- `Require Close Below VWAP = true`
- `Require Close Below Intraday EMA = true`
- `Require Red Trigger Bar = true`

Keep this moderate at first:

- `Minimum Trigger Bar Range / ATR = 0.25`

If too many alerts fire:

- raise `Minimum Trigger Bar Range / ATR`
- keep VWAP gate on
- keep hourly weak structure on

If too few alerts fire:

- lower `Minimum Trigger Bar Range / ATR`
- temporarily disable intraday EMA gate

## Suggested Webhook Payload

```json
{
  "secret": "YOUR_SECRET",
  "source": "tradingview",
  "symbol": "{{ticker}}",
  "exchange": "{{exchange}}",
  "timeframe": "{{interval}}",
  "bar_time": "{{time}}",
  "fired_at": "{{timenow}}",
  "price": "{{close}}",
  "direction": "short",
  "setup": "short_resistance_reversal",
  "trigger": "prev_hour_low_break",
  "notes": ["script_c1"]
}
```

## Where To Use It

Best use case:

- top `5` to `10` short names only

Do not run this on a huge random watchlist.

This is meant for names that already passed Python + A + B.

## How To Read It

### If C1 fires but B did not validate

Treat it as low quality.

### If B validated earlier and C1 now fires

This is a proper short timing candidate.

### If A, B, and C1 all align

That is the cleanest version of the current workflow.

## Important Limitation

Script C1 is still a first execution layer.

It does not yet include:

- VWAP failed retest trigger
- HOD AVWAP retest failure
- multi-bar lower-high pattern logic
- full stop/target logic inside Pine

Those belong to later scripts.

## Recommended Next Step

After validating C1 in live use, the next script should be:

- `Script C2 = SHORT_VWAP_FAIL`

That will pair naturally with C1.

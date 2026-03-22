# Pine Script A and B Setup Notes (2026-03-19)

## Files

- `tradingview/ml_script_a_attention_router.pine`
- `tradingview/ml_script_b_setup_validator.pine`

## Intended Use

### Script A

Use Script A for broad attention routing.

Best use cases:

- watchlist alerts
- broad scan-to-chart routing
- fast notification that a symbol is waking up intraday

Alert names exposed by the script:

- `ML_LONG_ATTENTION`
- `ML_SHORT_ATTENTION`

### Script B

Use Script B for higher-timeframe setup validation.

Best use cases:

- chart confirmation
- technical alerts on top-priority names
- checking whether the symbol still matches the intended Martin context

Alert names exposed by the script:

- `ML_LONG_SETUP_VALID`
- `ML_SHORT_SETUP_VALID`

## Recommended Order

1. add Script A to TradingView first
2. create one or two test alerts from Script A
3. verify webhook logging works
4. add Script B next
5. use Script B as the higher-timeframe gate before later adding Script C execution triggers

## Practical Mapping

### Broad watchlist layer

Use Script A on:

- `ML_LONG`
- `ML_SHORT`

### Precision validation layer

Use Script B on:

- top `5` to `10` short names
- top `3` to `5` long names when market regime supports longs

## Suggested Alert Messages

### Script A long attention

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
  "direction": "long",
  "setup": "attention_router",
  "trigger": "long_attention",
  "notes": ["script_a"]
}
```

### Script A short attention

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
  "setup": "attention_router",
  "trigger": "short_attention",
  "notes": ["script_a"]
}
```

### Script B long setup valid

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
  "direction": "long",
  "setup": "setup_validator",
  "trigger": "long_setup_valid",
  "notes": ["script_b"]
}
```

### Script B short setup valid

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
  "setup": "setup_validator",
  "trigger": "short_setup_valid",
  "notes": ["script_b"]
}
```

## Important Note

Script A and Script B are not execution timing scripts.

They are:

- attention routing
- setup validation

Actual Martin-style execution triggers like:

- previous hour low break
- VWAP fail
- retest fail

should be implemented in Script C later.

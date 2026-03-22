# TradingView Webhook Setup Guide (2026-03-19)

## Purpose

This document is the practical setup guide for connecting TradingView alerts to the local Martin Quant signal pipeline.

Current receiver assumptions:

- Webhook endpoint accepts `POST`
- Request body should be valid JSON
- Shared secret is validated from the `secret` field
- Incoming events are written to `outputs/signals/events.jsonl`
- Optional Telegram forwarding is handled by the webhook processor

## Important Clarification

The current URL:

```text
https://tradeai.jy03220354.workers.dev/
```

is a Cloudflare Worker URL, not a Cloudflare Quick Tunnel URL.

That is fine. TradingView can send webhooks to a `workers.dev` endpoint as long as:

- the Worker accepts `POST`
- the Worker returns quickly
- the Worker does not redirect to auth/login
- the URL is publicly reachable over HTTPS

## TradingView Alert Setup

In TradingView alert creation:

1. Enable webhook notifications.
2. Paste this URL into `Webhook URL`:

```text
https://tradeai.jy03220354.workers.dev/
```

3. Paste a valid JSON payload into `Message`.
4. Use `Once Per Bar Close` unless you specifically need intrabar alerts.
5. Make sure TradingView account `2FA` is enabled, otherwise webhook alerts will not work.

## Why JSON Format Matters

TradingView sends `application/json` only if the alert message is valid JSON.

If the message is not valid JSON, TradingView will send plain text instead. That creates unnecessary parsing issues.

Use double quotes only. Do not add trailing commas. Do not add comments inside JSON.

## Required Fields For Current Receiver

The current receiver is designed to work with these fields:

- `secret`
- `source`
- `symbol`
- `direction`
- `setup`
- `trigger`

Recommended additional fields:

- `exchange`
- `timeframe`
- `bar_time`
- `fired_at`
- `price`
- `notes`

## Recommended Base Payload

Use this as the default template:

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
  "notes": ["tv_alert"]
}
```

## TradingView Variables Used

These placeholders are intended to be replaced by TradingView:

- `{{ticker}}`
- `{{exchange}}`
- `{{interval}}`
- `{{time}}`
- `{{timenow}}`
- `{{close}}`

## Recommended Alert Payloads

### 1. Short: Previous Hour Low Break

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
  "notes": ["hourly_breakdown"]
}
```

Recommended alert name:

```text
ML_SHORT_PREV_HOUR_LOW
```

### 2. Short: VWAP Fail

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
  "trigger": "vwap_fail",
  "notes": ["intraday_retest_fail"]
}
```

Recommended alert name:

```text
ML_SHORT_VWAP_FAIL
```

### 3. Long: Reclaim

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
  "setup": "pullback",
  "trigger": "reclaim",
  "notes": ["tv_reclaim"]
}
```

Recommended alert name:

```text
ML_LONG_RECLAIM
```

### 4. Minimal Test Payload

Use this first before configuring the real strategy alerts:

```json
{
  "secret": "YOUR_SECRET",
  "source": "tradingview",
  "symbol": "{{ticker}}",
  "timeframe": "{{interval}}",
  "price": "{{close}}",
  "direction": "short",
  "setup": "test_signal",
  "trigger": "manual_test"
}
```

Recommended alert name:

```text
ML_TEST_SIGNAL
```

## Receiver Mapping

The current webhook processor maps these fields into the event log:

- `symbol` -> event symbol
- `direction` -> long or short
- `setup` -> setup type
- `trigger` -> trigger type
- `timeframe` -> timeframe
- `price` -> event price

Saved destination:

```text
outputs/signals/events.jsonl
```

## Recommended Deployment Order

1. Start the local or deployed receiver.
2. Test with the minimal payload.
3. Confirm the event is written to `outputs/signals/events.jsonl`.
4. Confirm no Cloudflare error is returned.
5. Then configure the real short and long alerts.

## Quick Checklist

Before testing:

- TradingView `2FA` enabled
- Worker URL publicly reachable
- Worker accepts `POST`
- JSON is valid
- `secret` matches receiver configuration
- No login/auth wall in front of the Worker
- Worker returns quickly

## Common Failure Modes

### TradingView sends `text/plain`

Cause:

- alert message is not valid JSON

Fix:

- use valid JSON only
- use double quotes only
- remove trailing commas

### `401 invalid_secret`

Cause:

- `secret` in TradingView does not match the receiver secret

Fix:

- update TradingView payload or receiver config so both are identical

### `400 missing_symbol`

Cause:

- `symbol` field missing or empty

Fix:

- include `"symbol": "{{ticker}}"`

### Cloudflare `530`

Cause:

- the Worker is trying to reach an invalid upstream hostname
- or the Worker route itself is misconfigured

Fix:

- first make sure the Worker can accept and return `200/202` without proxying anywhere else
- remove broken internal `fetch(...)` forwarding until the base webhook path is stable

### Timeout

Cause:

- webhook handler does too much work before responding

Fix:

- return immediately
- do logging or Telegram forwarding after the response path if possible

## Suggested Next Step For You

Configure one TradingView alert first using `ML_TEST_SIGNAL` and the minimal test payload.

After that succeeds, configure these in order:

1. `ML_SHORT_PREV_HOUR_LOW`
2. `ML_SHORT_VWAP_FAIL`
3. `ML_LONG_RECLAIM`

## Reference Links

- TradingView webhook alerts:
  - https://www.tradingview.com/support/solutions/43000529348-how-to-configure-webhook-alerts/
- TradingView alert variables:
  - https://www.tradingview.com/support/solutions/43000531021-how-to-use-a-variable-value-in-alert/
- Cloudflare Workers on `workers.dev`:
  - https://developers.cloudflare.com/workers/configuration/routing/workers-dev/
- Cloudflare error 530:
  - https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-5xx-errors/error-530/

# Risk Controls

oneFill enforces pre-trade guardrails before any order reaches the exchange. The `RiskValidator` runs after Validate (balance / qty / listing checks) but before Executor (order dispatch), so a rejected risk check never sends an order.

## Guardrails

Configure all guardrails in `config/risk.yaml`.

### `max_notional_per_intent`

**Default:** `100000` USD

Reject any single intent whose total notional exceeds this cap. This is a hard upper bound — it protects against fat-finger errors (e.g., accidentally submitting $10M instead of $10K).

```yaml
max_notional_per_intent: 100000
```

### `daily_loss_limit_usd`

**Default:** `10000` USD

Reject if cumulative filled-leg PnL for the current UTC day exceeds this loss. Tracks all intents whose legs have filled and computes realized PnL from fill prices.

```yaml
daily_loss_limit_usd: 10000
```

!!! tip
    This is a circuit breaker, not a precise risk measure. When it trips, all new intents are rejected for the rest of the UTC day. Use it to cap worst-day losses.

### `max_venue_exposure_usd`

**Default:** `50000` USD

Reject if any single venue has too much filled-but-uncompensated notional outstanding. This protects against venue-specific operational risk (exchange downtime, withdrawal freezes, etc.).

```yaml
max_venue_exposure_usd: 50000
```

### Rate limiting

**Defaults:** 10 orders per 60-second sliding window

Prevents runaway order submission. The sliding window means early orders expire and free up capacity naturally.

```yaml
rate_limit:
  max_orders: 10
  window_seconds: 60
```

## Disabling checks

Set any guard value to `null` to skip that check entirely:

```yaml
daily_loss_limit_usd: null    # no daily loss limit
max_venue_exposure_usd: null  # no venue exposure cap
```

`max_notional_per_intent` and `rate_limit` can also be set to `null`.

## Failure output

Risk failures appear in two places:

**Terminal (human-readable):**
```
Risk check FAILED:
  - Daily loss limit exceeded: -12345 USD > -10000 USD
```

**JSON output** (`--json` flag):
```json
{
  "status": "REJECTED",
  "risk_failures": [
    "Daily loss limit exceeded: -12345 USD > -10000 USD"
  ]
}
```

Add `risk_failures` to your monitoring or scripts to distinguish risk rejections from validation failures.

## Execution order

```
Intent → Planner → Validator → RiskValidator → Executor → Reconciler
                                   ↑
                           (no orders sent
                            if rejected here)
```

If `RiskValidator` rejects, the intent transitions to `REJECTED` — same terminal state as a validation failure, but the rejection reason clearly identifies it as a risk block.

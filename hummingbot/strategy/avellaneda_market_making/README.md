# Avellaneda Market Making Extensions

## Toxicity Gate

The toxicity gate measures post-fill adverse selection and can widen spreads, shrink size, or briefly pause quoting when recent maker fills look toxic.

### How it works

1. For each maker fill, the gate evaluates adverse selection at configured horizons `h`:
   - BUY fill: `adv_bps(h) = 1e4 * (mid(t+h) - p_fill) / p_fill`
   - SELL fill: `adv_bps(h) = 1e4 * (p_fill - mid(t+h)) / p_fill`
2. Each horizon keeps an EWMA of `adv_bps`.
3. Toxicity score is aggregated as:
   - `tox_bps = sum_h weight_h * max(0, -ewma_adv_bps(h))`
4. A hysteresis state machine toggles between `NORMAL` and `TOXIC`.

### Key configuration

- `toxicity_enabled`
- `toxicity_horizons_secs`
- `toxicity_ewma_halflife_secs`
- `toxicity_weights`
- `toxicity_trigger_bps`
- `toxicity_release_bps`
- `toxicity_confirm_secs`
- `toxicity_hysteresis_secs`
- `toxicity_hold_secs`
- `toxicity_action_mode` (`widen_only`, `widen_and_shrink`, `pause_quote`)
- `toxicity_spread_mult_min`, `toxicity_spread_mult_max`
- `toxicity_size_mult_min`, `toxicity_size_mult_max`
- `toxicity_curve_power`
- `toxicity_debug`

### Integration behavior

- Reservation price logic is unchanged.
- Side-specific Avellaneda spread offsets are multiplied by the toxicity spread multiplier.
- Order sizes are multiplied by the toxicity size multiplier when mode is `widen_and_shrink`.
- In `pause_quote` mode, new order creation is suppressed for `toxicity_hold_secs` after entering `TOXIC`.

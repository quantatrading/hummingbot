# Avellaneda Drift Regime Extension

This change adds an optional short-horizon drift term to Avellaneda reservation price:

- Classic: `r_AS = s - inventory_term`
- Extended: `r = r_AS + bias`

Where `bias` is computed from the same reference price series used for `s` (`mid_price` or `vamp`).

## Drift math

Using log returns `r_i = ln(p_i / p_{i-1})`:

- `mu_60`: mean returns over `drift_window_short_secs`
- `mu_300`: mean returns over `drift_window_long_secs`
- `sig_300`: stdev returns over `drift_window_vol_secs`
- `z = mu_60 / (sig_300 + 1e-12)`

Regime is activated with confirmation and hysteresis:

- UP: `z > threshold` and `mu_300 > 0`
- DOWN: `z < -threshold` and `mu_300 < 0`
- otherwise NEUTRAL

Bias (default, vol-scaled):

- `bias_bps = drift_kappa * clip(z, -z_clip, z_clip) * (sig_300 * 1e4)`
- capped by `drift_bias_max_bps` (or `defensive_bias_max_bps` in defensive mode)
- converted to price units before adding to reservation price

## Defensive inventory override

If `abs(net_base_inventory * reference_price) > inventory_risk_cap_quote`:

- temporarily enforce bias direction that flattens inventory
- long inventory -> negative bias; short inventory -> positive bias
- bounded and held for `defensive_hold_secs`

## Optional spread widening

If `drift_spread_adjust_enabled = true`, optimal spread can be widened modestly up to
`drift_spread_multiplier_max` under strong trend score.

## New config fields

- `drift_enabled`
- `drift_z_threshold`
- `drift_confirm_secs`
- `drift_hysteresis_secs`
- `drift_kappa`
- `drift_z_clip`
- `drift_bias_max_bps`
- `drift_window_short_secs`
- `drift_window_long_secs`
- `drift_window_vol_secs`
- `drift_spread_adjust_enabled`
- `drift_spread_multiplier_max`
- `inventory_risk_cap_quote`
- `defensive_bias_max_bps`
- `defensive_hold_secs`

Set `drift_enabled: false` to revert to classic upstream behavior (no drift bias added).

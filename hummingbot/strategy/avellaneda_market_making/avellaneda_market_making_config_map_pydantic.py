import ast
from datetime import datetime, time
from decimal import Decimal
from typing import Dict, List, Optional, Union

from pydantic import ConfigDict, Field, field_validator, model_validator

from hummingbot.client.config.config_data_types import BaseClientModel
from hummingbot.client.config.config_validators import (
    validate_bool,
    validate_datetime_iso_string,
    validate_decimal,
    validate_int,
    validate_time_iso_string,
)
from hummingbot.client.config.strategy_config_data_types import BaseTradingStrategyConfigMap
from hummingbot.client.settings import required_exchanges
from hummingbot.connector.utils import split_hb_trading_pair


class InfiniteModel(BaseClientModel):
    model_config = ConfigDict(title="infinite")


class FromDateToDateModel(BaseClientModel):
    start_datetime: datetime = Field(
        default=...,
        description="The start date and time for date-to-date execution timeframe.",
        json_schema_extra={
            "prompt": "Please enter the start date and time (YYYY-MM-DD HH:MM:SS)", "prompt_on_new": True
        }
    )
    end_datetime: datetime = Field(
        default=...,
        description="The end date and time for date-to-date execution timeframe.",
        json_schema_extra={
            "prompt": "Please enter the end date and time (YYYY-MM-DD HH:MM:SS)", "prompt_on_new": True
        }
    )
    model_config = ConfigDict(title="from_date_to_date")

    @field_validator("start_datetime", "end_datetime", mode="before")
    @classmethod
    def validate_execution_time(cls, v: Union[str, datetime]) -> Optional[str]:
        if not isinstance(v, str):
            v = v.strftime("%Y-%m-%d %H:%M:%S")
        ret = validate_datetime_iso_string(v)
        if ret is not None:
            raise ValueError(ret)
        return v


class DailyBetweenTimesModel(BaseClientModel):
    start_time: time = Field(
        default=...,
        description="The start time for daily-between-times execution timeframe.",
        json_schema_extra={"prompt": "Please enter the start time (HH:MM:SS)", "prompt_on_new": True},
    )
    end_time: time = Field(
        default=...,
        description="The end time for daily-between-times execution timeframe.",
        json_schema_extra={"prompt": "Please enter the end time (HH:MM:SS)", "prompt_on_new": True},
    )
    model_config = ConfigDict(title="daily_between_times")

    @field_validator("start_time", "end_time", mode="before")
    @classmethod
    def validate_execution_time(cls, v: Union[str, datetime]) -> Optional[str]:
        if not isinstance(v, str):
            v = v.strftime("%H:%M:%S")
        ret = validate_time_iso_string(v)
        if ret is not None:
            raise ValueError(ret)
        return v


EXECUTION_TIMEFRAME_MODELS = {
    InfiniteModel.model_config["title"]: InfiniteModel,
    FromDateToDateModel.model_config["title"]: FromDateToDateModel,
    DailyBetweenTimesModel.model_config["title"]: DailyBetweenTimesModel,
}


class SingleOrderLevelModel(BaseClientModel):
    model_config = ConfigDict(title="single_order_level")


class MultiOrderLevelModel(BaseClientModel):
    order_levels: int = Field(
        default=2,
        description="The number of orders placed on either side of the order book.",
        ge=2,
        json_schema_extra={"prompt": "How many orders do you want to place on both sides?", "prompt_on_new": True},
    )
    level_distances: Decimal = Field(
        default=Decimal("0"),
        description="The spread between order levels, expressed in % of optimal spread.",
        ge=0,
        json_schema_extra={"prompt": "How far apart in % of optimal spread should orders on one side be?", "prompt_on_new": True},
    )
    model_config = ConfigDict(title="multi_order_level")

    @field_validator("order_levels", mode="before")
    @classmethod
    def validate_int_zero_or_above(cls, v: str):
        ret = validate_int(v, min_value=2)
        if ret is not None:
            raise ValueError(ret)
        return v

    @field_validator("level_distances", mode="before")
    @classmethod
    def validate_decimal_zero_or_above(cls, v: str):
        ret = validate_decimal(v, min_value=Decimal("0"), inclusive=True)
        if ret is not None:
            raise ValueError(ret)
        return v


ORDER_LEVEL_MODELS = {
    SingleOrderLevelModel.model_config["title"]: SingleOrderLevelModel,
    MultiOrderLevelModel.model_config["title"]: MultiOrderLevelModel,
}


class TrackHangingOrdersModel(BaseClientModel):
    hanging_orders_cancel_pct: Decimal = Field(
        default=Decimal("10"),
        description="The spread percentage at which hanging orders will be cancelled.",
        gt=0,
        lt=100,
        json_schema_extra={
            "prompt": "At what spread percentage (from mid price) will hanging orders be canceled? (Enter 1 to indicate 1%)",
        }
    )
    model_config = ConfigDict(title="track_hanging_orders")

    @field_validator("hanging_orders_cancel_pct", mode="before")
    @classmethod
    def validate_pct_exclusive(cls, v: str):
        ret = validate_decimal(v, min_value=Decimal("0"), max_value=Decimal("100"), inclusive=False)
        if ret is not None:
            raise ValueError(ret)
        return v


class IgnoreHangingOrdersModel(BaseClientModel):
    model_config = ConfigDict(title="ignore_hanging_orders")


HANGING_ORDER_MODELS = {
    TrackHangingOrdersModel.model_config["title"]: TrackHangingOrdersModel,
    IgnoreHangingOrdersModel.model_config["title"]: IgnoreHangingOrdersModel,
}


class AvellanedaMarketMakingConfigMap(BaseTradingStrategyConfigMap):
    strategy: str = Field(default="avellaneda_market_making")
    execution_timeframe_mode: Union[InfiniteModel, FromDateToDateModel, DailyBetweenTimesModel] = Field(
        default=...,
        description="The execution timeframe.",
        json_schema_extra={
            "prompt": f"Select the execution timeframe ({'/'.join(EXECUTION_TIMEFRAME_MODELS.keys())})",
            "prompt_on_new": True,
        }
    )
    order_amount: Decimal = Field(
        default=...,
        description="The strategy order amount.",
        gt=0,
        json_schema_extra={
            "prompt": lambda mi: AvellanedaMarketMakingConfigMap.order_amount_prompt(mi),
            "prompt_on_new": True,
        }
    )
    reference_price_source: str = Field(
        default="mid_price",
        description="Reference price source used as s in Avellaneda equations (mid_price or vamp).",
        json_schema_extra={
            "prompt": "Select reference price source (mid_price/vamp)",
            "prompt_on_new": True,
        },
    )
    vamp_volume: Decimal = Field(
        default=Decimal("0"),
        description="Base volume used to compute VAMP. Set 0 to use order_amount.",
        ge=0,
        json_schema_extra={"prompt": "Enter VAMP volume in base asset units (0 uses order_amount)"},
    )
    vamp_auto_q_enabled: bool = Field(
        default=False,
        description="If enabled, derive VAMP Q from full visible order book depth.",
        json_schema_extra={"prompt": "Enable auto VAMP Q from full order book depth? (Yes/No)"},
    )
    vamp_q_min: Decimal = Field(
        default=Decimal("0"),
        description="Optional minimum Q clamp for auto VAMP Q (0 disables min clamp).",
        ge=0,
        json_schema_extra={"prompt": "Enter minimum auto VAMP Q (0 disables)"},
    )
    vamp_q_max: Decimal = Field(
        default=Decimal("0"),
        description="Optional maximum Q clamp for auto VAMP Q (0 disables max clamp).",
        ge=0,
        json_schema_extra={"prompt": "Enter maximum auto VAMP Q (0 disables)"},
    )
    drift_enabled: bool = Field(
        default=False,
        description="Enable non-zero drift extension in reservation price (HJB-consistent).",
        json_schema_extra={"prompt": "Enable drift term extension? (Yes/No)"},
    )
    drift_z_threshold: Decimal = Field(
        default=Decimal("0.6"),
        description="Regime activation threshold for z-score of drift.",
        ge=0,
        json_schema_extra={"prompt": "Enter drift z-score threshold"},
    )
    drift_confirm_secs: int = Field(
        default=30,
        description="Seconds a regime signal must persist before switching.",
        ge=0,
        json_schema_extra={"prompt": "Enter drift confirmation window (seconds)"},
    )
    drift_hysteresis_secs: int = Field(
        default=180,
        description="Minimum seconds between regime switches.",
        ge=0,
        json_schema_extra={"prompt": "Enter drift hysteresis window (seconds)"},
    )
    drift_kappa: Decimal = Field(
        default=Decimal("0.25"),
        description="Effective horizon factor: tau = drift_kappa * drift_window_short_secs.",
        ge=0,
        json_schema_extra={"prompt": "Enter drift horizon factor (kappa)"},
    )
    drift_bias_max_bps: Decimal = Field(
        default=Decimal("20"),
        description="Maximum absolute drift term in bps under normal mode.",
        ge=0,
        json_schema_extra={"prompt": "Enter max drift bias (bps)"},
    )
    drift_window_short_secs: int = Field(
        default=60,
        description="Short window (seconds) for mu_60.",
        ge=1,
        json_schema_extra={"prompt": "Enter short drift window (seconds)"},
    )
    drift_window_long_secs: int = Field(
        default=300,
        description="Long window (seconds) for mu_300 confirmation.",
        ge=1,
        json_schema_extra={"prompt": "Enter long drift window (seconds)"},
    )
    drift_window_vol_secs: int = Field(
        default=300,
        description="Volatility window (seconds) for sig_300.",
        ge=1,
        json_schema_extra={"prompt": "Enter volatility window (seconds)"},
    )
    inventory_risk_cap_quote: Decimal = Field(
        default=Decimal("200"),
        description="Inventory risk cap in quote units for defensive drift mode.",
        ge=0,
        json_schema_extra={"prompt": "Enter inventory risk cap (quote)"},
    )
    defensive_bias_max_bps: Decimal = Field(
        default=Decimal("35"),
        description="Maximum absolute drift bias in bps under defensive mode.",
        ge=0,
        json_schema_extra={"prompt": "Enter defensive max drift bias (bps)"},
    )
    defensive_hold_secs: int = Field(
        default=300,
        description="Seconds defensive mode stays active after trigger.",
        ge=0,
        json_schema_extra={"prompt": "Enter defensive hold duration (seconds)"},
    )
    side_intensity_enabled: bool = Field(
        default=False,
        description="Enable side-specific arrival intensity estimation (k_b, k_a).",
        json_schema_extra={"prompt": "Enable side-specific intensity estimation? (Yes/No)"},
    )
    side_intensity_window_secs: int = Field(
        default=900,
        description="Rolling window (seconds) for side intensity estimation.",
        ge=60,
        json_schema_extra={"prompt": "Enter side intensity window (seconds)"},
    )
    side_intensity_update_interval_secs: int = Field(
        default=30,
        description="Refit cadence (seconds) for side intensity parameters.",
        ge=1,
        json_schema_extra={"prompt": "Enter side intensity update interval (seconds)"},
    )
    side_intensity_smoothing_beta: Decimal = Field(
        default=Decimal("0.2"),
        description="EMA smoothing factor for side intensity updates.",
        ge=0,
        le=1,
        json_schema_extra={"prompt": "Enter side intensity smoothing beta (0-1)"},
    )
    side_intensity_k_min: Decimal = Field(
        default=Decimal("10"),
        description="Lower bound for side-specific k search.",
        gt=0,
        json_schema_extra={"prompt": "Enter minimum side intensity k"},
    )
    side_intensity_k_max: Decimal = Field(
        default=Decimal("20000"),
        description="Upper bound for side-specific k search.",
        gt=0,
        json_schema_extra={"prompt": "Enter maximum side intensity k"},
    )
    side_intensity_min_events: int = Field(
        default=5,
        description="Minimum fill events per side to refit.",
        ge=0,
        json_schema_extra={"prompt": "Enter minimum fill events per side"},
    )
    side_intensity_use_censoring: bool = Field(
        default=True,
        description="Use right-censoring in side intensity likelihood.",
        json_schema_extra={"prompt": "Use censoring-aware side intensity fit? (Yes/No)"},
    )
    side_intensity_delta_mode: str = Field(
        default="relative_to_r",
        description="Delta definition for side intensity estimator.",
        json_schema_extra={"prompt": "Select side intensity delta mode (relative_to_r/absolute_price)"},
    )
    side_intensity_debug_logging: bool = Field(
        default=True,
        description="Enable side intensity telemetry logging.",
        json_schema_extra={"prompt": "Enable side intensity debug logging? (Yes/No)"},
    )
    side_intensity_a_skew_enabled: bool = Field(
        default=True,
        description="Enable A_b/A_a baseline-intensity asymmetry skew on reservation price center.",
        json_schema_extra={"prompt": "Enable A-asymmetry center skew? (Yes/No)"},
    )
    side_intensity_a_skew_max_bps: Decimal = Field(
        default=Decimal("5.0"),
        description="Maximum absolute A-skew impact in bps of reservation price.",
        ge=0,
        json_schema_extra={"prompt": "Enter max A-skew impact (bps)"},
    )
    side_intensity_a_skew_ewma_alpha: Decimal = Field(
        default=Decimal("0.2"),
        description="EWMA alpha for A-skew smoothing.",
        ge=0,
        le=1,
        json_schema_extra={"prompt": "Enter A-skew smoothing alpha (0-1)"},
    )
    side_intensity_a_skew_hold_secs: int = Field(
        default=60,
        description="Minimum hold time before allowing A-skew sign flip.",
        ge=0,
        json_schema_extra={"prompt": "Enter A-skew sign hold (seconds)"},
    )
    side_intensity_a_skew_deadband_bps: Decimal = Field(
        default=Decimal("0.2"),
        description="Deadband on ln(A_b/A_a) ratio expressed in bps.",
        ge=0,
        json_schema_extra={"prompt": "Enter A-skew deadband (bps)"},
    )
    side_intensity_a_eps: Decimal = Field(
        default=Decimal("1e-9"),
        description="Numerical epsilon for A-ratio stability.",
        gt=0,
        json_schema_extra={"prompt": "Enter A-skew epsilon"},
    )
    toxicity_enabled: bool = Field(
        default=False,
        description="Enable post-fill adverse-selection toxicity gate.",
        json_schema_extra={"prompt": "Enable toxicity gate? (Yes/No)"},
    )
    toxicity_horizons_secs: List[int] = Field(
        default_factory=lambda: [5, 10, 30],
        description="Post-fill adverse-selection horizons (seconds).",
        json_schema_extra={"prompt": "Enter toxicity horizons in seconds (e.g. [5,10,30])"},
    )
    toxicity_ewma_halflife_secs: float = Field(
        default=120.0,
        description="EWMA half-life for adverse selection estimates.",
        gt=0,
        json_schema_extra={"prompt": "Enter toxicity EWMA half-life (seconds)"},
    )
    toxicity_weights: Dict[int, Decimal] = Field(
        default_factory=lambda: {5: Decimal("0.5"), 10: Decimal("0.3"), 30: Decimal("0.2")},
        description="Weights by horizon for toxicity score aggregation.",
        json_schema_extra={"prompt": "Enter toxicity weights by horizon (e.g. {5:0.5,10:0.3,30:0.2})"},
    )
    toxicity_trigger_bps: Decimal = Field(
        default=Decimal("1.5"),
        description="Toxicity trigger threshold in bps.",
        ge=0,
        json_schema_extra={"prompt": "Enter toxicity trigger threshold (bps)"},
    )
    toxicity_release_bps: Decimal = Field(
        default=Decimal("0.8"),
        description="Toxicity release threshold in bps.",
        ge=0,
        json_schema_extra={"prompt": "Enter toxicity release threshold (bps)"},
    )
    toxicity_confirm_secs: float = Field(
        default=20.0,
        description="Confirmation time for trigger/release transitions.",
        ge=0,
        json_schema_extra={"prompt": "Enter toxicity confirmation time (seconds)"},
    )
    toxicity_hysteresis_secs: float = Field(
        default=120.0,
        description="Minimum time between toxicity regime switches.",
        ge=0,
        json_schema_extra={"prompt": "Enter toxicity hysteresis time (seconds)"},
    )
    toxicity_hold_secs: float = Field(
        default=60.0,
        description="Pause hold duration after entering TOXIC in pause mode.",
        ge=0,
        json_schema_extra={"prompt": "Enter toxicity hold time (seconds)"},
    )
    toxicity_action_mode: str = Field(
        default="widen_only",
        description="Action mode when toxicity is active.",
        json_schema_extra={"prompt": "Choose toxicity action mode (widen_only/widen_and_shrink/pause_quote)"},
    )
    toxicity_spread_mult_min: Decimal = Field(
        default=Decimal("1.0"),
        description="Minimum spread multiplier under toxicity gate.",
        ge=0,
        json_schema_extra={"prompt": "Enter toxicity minimum spread multiplier"},
    )
    toxicity_spread_mult_max: Decimal = Field(
        default=Decimal("4.0"),
        description="Maximum spread multiplier under toxicity gate.",
        ge=0,
        json_schema_extra={"prompt": "Enter toxicity maximum spread multiplier"},
    )
    toxicity_size_mult_min: Decimal = Field(
        default=Decimal("0.2"),
        description="Minimum size multiplier under toxicity shrink mode.",
        ge=0,
        json_schema_extra={"prompt": "Enter toxicity minimum size multiplier"},
    )
    toxicity_size_mult_max: Decimal = Field(
        default=Decimal("1.0"),
        description="Maximum size multiplier under toxicity shrink mode.",
        ge=0,
        json_schema_extra={"prompt": "Enter toxicity maximum size multiplier"},
    )
    toxicity_curve_power: Decimal = Field(
        default=Decimal("1.0"),
        description="Convexity power for toxicity action scaling.",
        gt=0,
        json_schema_extra={"prompt": "Enter toxicity curve power"},
    )
    toxicity_debug: bool = Field(
        default=False,
        description="Enable toxicity debug logging.",
        json_schema_extra={"prompt": "Enable toxicity debug logs? (Yes/No)"},
    )
    inventory_gate_enabled: bool = Field(
        default=True,
        description="Enable inventory-aware gating on A-skew.",
        json_schema_extra={"prompt": "Enable inventory gate on A-skew? (Yes/No)"},
    )
    inventory_gate_scale_pct: Decimal = Field(
        default=Decimal("0.35"),
        description="Scale as fraction of inventory_risk_cap_quote for gate decay.",
        gt=0,
        json_schema_extra={"prompt": "Enter inventory gate scale pct"},
    )
    inventory_gate_mode: str = Field(
        default="exp",
        description="Inventory gate mode.",
        json_schema_extra={"prompt": "Select inventory gate mode (exp/linear)"},
    )
    inventory_gate_min: Decimal = Field(
        default=Decimal("0.05"),
        description="Minimum gate factor floor.",
        ge=0,
        le=1,
        json_schema_extra={"prompt": "Enter inventory gate minimum (0-1)"},
    )
    inventory_cross_suppress_enabled: bool = Field(
        default=True,
        description="Enable suppression after inventory sign crossing.",
        json_schema_extra={"prompt": "Enable inventory crossing suppression? (Yes/No)"},
    )
    inventory_cross_deadband_base: Decimal = Field(
        default=Decimal("0.0002"),
        description="Deadband on base inventory for crossing detection.",
        ge=0,
        json_schema_extra={"prompt": "Enter crossing deadband in base units"},
    )
    inventory_cross_suppress_factor: Decimal = Field(
        default=Decimal("0.2"),
        description="Maximum gate factor during crossing suppression hold.",
        ge=0,
        le=1,
        json_schema_extra={"prompt": "Enter crossing suppress factor (0-1)"},
    )
    inventory_cross_hold_secs: int = Field(
        default=120,
        description="Hold duration for crossing suppression.",
        ge=0,
        json_schema_extra={"prompt": "Enter crossing suppress hold (seconds)"},
    )
    inventory_size_scaling_enabled: bool = Field(
        default=True,
        description="Enable inventory-aware dynamic order-size scaling.",
        json_schema_extra={"prompt": "Enable inventory-aware size scaling? (Yes/No)"},
    )
    inventory_size_min_mult: Decimal = Field(
        default=Decimal("0.15"),
        description="Minimum size multiplier under maximum inventory stress.",
        ge=0,
        le=1,
        json_schema_extra={"prompt": "Enter minimum size multiplier (0-1)"},
    )
    inventory_size_beta: Decimal = Field(
        default=Decimal("3.0"),
        description="Exponential decay coefficient for inventory size scaling.",
        ge=0,
        json_schema_extra={"prompt": "Enter inventory size scaling beta"},
    )
    inv_stress_ema_halflife_secs: int = Field(
        default=60,
        description="EMA half-life (seconds) for inventory risk smoothing in quote terms.",
        gt=0,
        json_schema_extra={"prompt": "Enter inventory stress EMA half-life (seconds)"},
    )
    size_scaling_beta: Decimal = Field(
        default=Decimal("3.0"),
        description="Convex size scaling beta for exp(-beta * stress^power).",
        ge=0,
        json_schema_extra={"prompt": "Enter convex size scaling beta"},
    )
    size_scaling_min_mult: Decimal = Field(
        default=Decimal("0.20"),
        description="Minimum symmetric size multiplier under maximum stress.",
        ge=0,
        le=1,
        json_schema_extra={"prompt": "Enter minimum size multiplier (0-1)"},
    )
    size_scaling_power: Decimal = Field(
        default=Decimal("2.0"),
        description="Convex power for stress in size scaling exp(-beta * stress^power).",
        gt=0,
        json_schema_extra={"prompt": "Enter convex power for size scaling"},
    )
    size_dir_bias_enabled: bool = Field(
        default=True,
        description="Enable directional per-side size bias to favor inventory flattening side.",
        json_schema_extra={"prompt": "Enable directional size bias? (Yes/No)"},
    )
    size_dir_bias_k: Decimal = Field(
        default=Decimal("0.50"),
        description="Directional size bias strength k.",
        ge=0,
        json_schema_extra={"prompt": "Enter directional size bias strength k"},
    )
    order_optimization_enabled: bool = Field(
        default=True,
        description=(
            "Allows the bid and ask order prices to be adjusted based on"
            " the current top bid and ask prices in the market."
        ),
        json_schema_extra={"prompt": "Do you want to enable order optimization? (Yes/No)"}
    )
    risk_factor: Decimal = Field(
        default=Decimal("1"),
        description="The risk factor (\u03B3).",
        gt=0,
        json_schema_extra={"prompt": "Enter risk factor (\u03B3)", "prompt_on_new": True},
    )
    order_amount_shape_factor: Decimal = Field(
        default=Decimal("0"),
        description="The amount shape factor (\u03b7)",
        ge=0,
        le=1,
        json_schema_extra={"prompt": "Enter order amount shape factor (\u03B7)"},
    )
    min_spread: Decimal = Field(
        default=Decimal("0"),
        description="The minimum spread limit as percentage of the mid price.",
        ge=0,
        json_schema_extra={"prompt": "Enter minimum spread limit (as % of mid price)"},
    )
    order_refresh_time: float = Field(
        default=...,
        description="The frequency at which the orders' spreads will be re-evaluated.",
        gt=0.,
        json_schema_extra={"prompt": "How often do you want to refresh orders (in seconds)?", "prompt_on_new": True},
    )
    max_order_age: float = Field(
        default=1800.,
        description="A given order's maximum lifetime irrespective of spread.",
        gt=0.,
        json_schema_extra={"prompt": "How long do you want to cancel and replace bids and asks with the same price (in seconds)?"}
    )
    order_refresh_tolerance_pct: Decimal = Field(
        default=Decimal("0"),
        description="The range of spreads tolerated on refresh cycles. Orders over that range are cancelled and re-submitted.",
        ge=-10, le=10,
        json_schema_extra={"prompt": "Enter the percent change in price needed to refresh orders at each cycle (Enter 1 to indicate 1%)"},
    )
    filled_order_delay: float = Field(
        default=60.,
        description="The delay before placing a new order after an order fill.",
        gt=0.,
        json_schema_extra={"prompt": "How long do you want to wait before placing the next order if your order gets filled (in seconds)"},
    )
    inventory_target_base_pct: Decimal = Field(
        default=Decimal("50"),
        description="Defines the inventory target for the base asset.",
        ge=0,
        le=100,
        json_schema_extra={"prompt": "Enter the inventory target for the base asset (Enter 50 for 50%)", "prompt_on_new": True},
    )
    add_transaction_costs: bool = Field(
        default=False,
        description="If activated, transaction costs will be added to order prices.",
        json_schema_extra={"prompt": "Do you want to add transaction costs automatically to order prices? (Yes/No)"},
    )
    volatility_buffer_size: int = Field(
        default=200,
        description="The number of ticks that will be stored to calculate volatility.",
        ge=1,
        le=10_000,
        json_schema_extra={"prompt": "Enter amount of ticks that will be stored to estimate order book liquidity"},
    )
    trading_intensity_buffer_size: int = Field(
        default=200,
        description="The number of ticks that will be stored to calculate order book liquidity.",
        ge=1,
        le=10_000,
        json_schema_extra={"prompt": "Enter amount of ticks that will be stored to estimate order book liquidity"},
    )
    order_levels_mode: Union[SingleOrderLevelModel, MultiOrderLevelModel] = Field(
        default=SingleOrderLevelModel.model_construct(),
        description="Allows activating multi-order levels.",
        json_schema_extra={"prompt": f"Select the order levels mode ({'/'.join(list(ORDER_LEVEL_MODELS.keys()))})"},
    )
    order_override: Optional[Dict] = Field(
        default=None,
        description="Allows custom specification of the order levels and their spreads and amounts.",
    )
    hanging_orders_mode: Union[IgnoreHangingOrdersModel, TrackHangingOrdersModel] = Field(
        default=IgnoreHangingOrdersModel(),
        description="When tracking hanging orders, the orders on the side opposite to the filled orders remain active.",
        json_schema_extra={"prompt": f"Select the hanging orders mode ({'/'.join(list(HANGING_ORDER_MODELS.keys()))})"},
    )
    should_wait_order_cancel_confirmation: bool = Field(
        default=True,
        description="If activated, the strategy will await cancellation confirmation from the exchange before placing a new order.",
        json_schema_extra={
            "prompt": "Should the strategy wait to receive a confirmation for orders cancellation before creating a new set of orders? (Yes/No)",
        }
    )
    model_config = ConfigDict(title="avellaneda_market_making")

    # === prompts ===

    @classmethod
    def order_amount_prompt(cls, model_instance: 'AvellanedaMarketMakingConfigMap') -> str:
        trading_pair = model_instance.market
        base_asset, quote_asset = split_hb_trading_pair(trading_pair)
        return f"What is the amount of {base_asset} per order?"

    # === specific validations ===

    @field_validator("execution_timeframe_mode", mode="before")
    @classmethod
    def validate_execution_timeframe(
        cls, v: Union[str, InfiniteModel, FromDateToDateModel, DailyBetweenTimesModel]
    ):
        if isinstance(v, (InfiniteModel, FromDateToDateModel, DailyBetweenTimesModel, Dict)):
            sub_model = v
        elif v not in EXECUTION_TIMEFRAME_MODELS:
            raise ValueError(
                f"Invalid timeframe, please choose value from {list(EXECUTION_TIMEFRAME_MODELS.keys())}"
            )
        else:
            sub_model = EXECUTION_TIMEFRAME_MODELS[v].model_construct()
        return sub_model

    @field_validator("order_refresh_tolerance_pct", mode="before")
    @classmethod
    def validate_order_refresh_tolerance_pct(cls, v: str):
        """Used for client-friendly error output."""
        ret = validate_decimal(v, min_value=Decimal("-10"), max_value=Decimal("10"), inclusive=True)
        if ret is not None:
            raise ValueError(ret)
        return v

    @field_validator("volatility_buffer_size", "trading_intensity_buffer_size", mode="before")
    @classmethod
    def validate_buffer_size(cls, v: str):
        """Used for client-friendly error output."""
        ret = validate_int(v, 1, 10_000)
        if ret is not None:
            raise ValueError(ret)
        return v

    @field_validator("order_levels_mode", mode="before")
    @classmethod
    def validate_order_levels_mode(cls, v: Union[str, SingleOrderLevelModel, MultiOrderLevelModel]):
        if isinstance(v, (SingleOrderLevelModel, MultiOrderLevelModel, Dict)):
            sub_model = v
        elif v not in ORDER_LEVEL_MODELS:
            raise ValueError(
                f"Invalid order levels mode, please choose value from {list(ORDER_LEVEL_MODELS.keys())}."
            )
        else:
            sub_model = ORDER_LEVEL_MODELS[v].model_construct()
        return sub_model

    @field_validator("hanging_orders_mode", mode="before")
    @classmethod
    def validate_hanging_orders_mode(cls, v: Union[str, IgnoreHangingOrdersModel, TrackHangingOrdersModel]):
        if isinstance(v, (TrackHangingOrdersModel, IgnoreHangingOrdersModel, Dict)):
            sub_model = v
        elif v not in HANGING_ORDER_MODELS:
            raise ValueError(
                f"Invalid hanging order mode, please choose value from {list(HANGING_ORDER_MODELS.keys())}."
            )
        else:
            sub_model = HANGING_ORDER_MODELS[v].model_construct()
        return sub_model

    # === generic validations ===

    @field_validator(
        "order_optimization_enabled",
        "add_transaction_costs",
        "should_wait_order_cancel_confirmation",
        "vamp_auto_q_enabled",
        "drift_enabled",
        "side_intensity_enabled",
        "side_intensity_use_censoring",
        "side_intensity_debug_logging",
        "side_intensity_a_skew_enabled",
        "toxicity_enabled",
        "toxicity_debug",
        "inventory_gate_enabled",
        "inventory_cross_suppress_enabled",
        "inventory_size_scaling_enabled",
        "size_dir_bias_enabled",
        mode="before")
    @classmethod
    def validate_bool(cls, v: str):
        """Used for client-friendly error output."""
        if isinstance(v, str):
            ret = validate_bool(v)
            if ret is not None:
                raise ValueError(ret)
        return v

    @field_validator("order_amount_shape_factor", mode="before")
    @classmethod
    def validate_decimal_from_zero_to_one(cls, v: str):
        """Used for client-friendly error output."""
        ret = validate_decimal(v, min_value=Decimal("0"), max_value=Decimal("1"), inclusive=True)
        if ret is not None:
            raise ValueError(ret)
        return v

    @field_validator(
        "order_amount",
        "risk_factor",
        "order_refresh_time",
        "max_order_age",
        "filled_order_delay",
        mode="before")
    @classmethod
    def validate_decimal_above_zero(cls, v: str):
        """Used for client-friendly error output."""
        ret = validate_decimal(v, min_value=Decimal("0"), inclusive=False)
        if ret is not None:
            raise ValueError(ret)
        return v

    @field_validator("min_spread", mode="before")
    @classmethod
    def validate_decimal_zero_or_above(cls, v: str):
        """Used for client-friendly error output."""
        ret = validate_decimal(v, min_value=Decimal("0"), inclusive=True)
        if ret is not None:
            raise ValueError(ret)
        return v

    @field_validator("reference_price_source", mode="before")
    @classmethod
    def validate_reference_price_source(cls, v: str):
        value = str(v).lower()
        valid_values = {"mid_price", "vamp"}
        if value not in valid_values:
            raise ValueError("Invalid price source, please choose value from ['mid_price', 'vamp']")
        return value

    @field_validator("vamp_volume", mode="before")
    @classmethod
    def validate_vamp_volume(cls, v: str):
        ret = validate_decimal(v, min_value=Decimal("0"), inclusive=True)
        if ret is not None:
            raise ValueError(ret)
        return v

    @field_validator("vamp_q_min", "vamp_q_max", mode="before")
    @classmethod
    def validate_vamp_q_bounds(cls, v: str):
        ret = validate_decimal(v, min_value=Decimal("0"), inclusive=True)
        if ret is not None:
            raise ValueError(ret)
        return v

    @field_validator(
        "drift_z_threshold",
        "drift_kappa",
        "drift_bias_max_bps",
        "inventory_risk_cap_quote",
        "defensive_bias_max_bps",
        mode="before")
    @classmethod
    def validate_drift_decimals_non_negative(cls, v: str):
        ret = validate_decimal(v, min_value=Decimal("0"), inclusive=True)
        if ret is not None:
            raise ValueError(ret)
        return v

    @field_validator(
        "drift_confirm_secs",
        "drift_hysteresis_secs",
        "drift_window_short_secs",
        "drift_window_long_secs",
        "drift_window_vol_secs",
        "defensive_hold_secs",
        mode="before")
    @classmethod
    def validate_drift_ints_non_negative(cls, v: str):
        ret = validate_int(v, min_value=0)
        if ret is not None:
            raise ValueError(ret)
        return v

    @field_validator("side_intensity_smoothing_beta", mode="before")
    @classmethod
    def validate_side_intensity_beta(cls, v: str):
        ret = validate_decimal(v, min_value=Decimal("0"), max_value=Decimal("1"), inclusive=True)
        if ret is not None:
            raise ValueError(ret)
        return v

    @field_validator("side_intensity_a_skew_ewma_alpha", mode="before")
    @classmethod
    def validate_side_intensity_a_skew_alpha(cls, v: str):
        ret = validate_decimal(v, min_value=Decimal("0"), max_value=Decimal("1"), inclusive=True)
        if ret is not None:
            raise ValueError(ret)
        return v

    @field_validator("side_intensity_k_min", "side_intensity_k_max", mode="before")
    @classmethod
    def validate_side_intensity_k_bounds(cls, v: str):
        ret = validate_decimal(v, min_value=Decimal("0"), inclusive=False)
        if ret is not None:
            raise ValueError(ret)
        return v

    @field_validator(
        "side_intensity_window_secs",
        "side_intensity_update_interval_secs",
        "side_intensity_min_events",
        "side_intensity_a_skew_hold_secs",
        mode="before")
    @classmethod
    def validate_side_intensity_ints(cls, v: str):
        ret = validate_int(v, min_value=0)
        if ret is not None:
            raise ValueError(ret)
        return v

    @field_validator("side_intensity_a_skew_max_bps", "side_intensity_a_skew_deadband_bps", mode="before")
    @classmethod
    def validate_side_intensity_a_skew_non_negative(cls, v: str):
        ret = validate_decimal(v, min_value=Decimal("0"), inclusive=True)
        if ret is not None:
            raise ValueError(ret)
        return v

    @field_validator("side_intensity_a_eps", mode="before")
    @classmethod
    def validate_side_intensity_a_eps(cls, v: str):
        ret = validate_decimal(v, min_value=Decimal("0"), inclusive=False)
        if ret is not None:
            raise ValueError(ret)
        return v

    @field_validator("toxicity_horizons_secs", mode="before")
    @classmethod
    def validate_toxicity_horizons_secs(cls, v):
        parsed = v
        if isinstance(v, str):
            parsed = ast.literal_eval(v)
        if not isinstance(parsed, (list, tuple, set)):
            raise ValueError("toxicity_horizons_secs must be a list of positive integers")
        horizons = []
        for item in parsed:
            ret = validate_int(str(item), min_value=0, inclusive=False)
            if ret is not None:
                raise ValueError(ret)
            horizons.append(int(item))
        if len(horizons) == 0:
            raise ValueError("toxicity_horizons_secs cannot be empty")
        return sorted(set(horizons))

    @field_validator("toxicity_weights", mode="before")
    @classmethod
    def validate_toxicity_weights(cls, v):
        parsed = v
        if isinstance(v, str):
            parsed = ast.literal_eval(v)
        if not isinstance(parsed, dict):
            raise ValueError("toxicity_weights must be a mapping of horizon->weight")
        weights: Dict[int, Decimal] = {}
        for key, value in parsed.items():
            ret_h = validate_int(str(key), min_value=0, inclusive=False)
            if ret_h is not None:
                raise ValueError(ret_h)
            ret_w = validate_decimal(str(value), min_value=Decimal("0"), inclusive=True)
            if ret_w is not None:
                raise ValueError(ret_w)
            weights[int(key)] = Decimal(str(value))
        if len(weights) == 0:
            raise ValueError("toxicity_weights cannot be empty")
        return weights

    @field_validator("toxicity_ewma_halflife_secs", mode="before")
    @classmethod
    def validate_toxicity_halflife(cls, v: str):
        ret = validate_decimal(v, min_value=Decimal("0"), inclusive=False)
        if ret is not None:
            raise ValueError(ret)
        return v

    @field_validator("toxicity_confirm_secs", "toxicity_hysteresis_secs", "toxicity_hold_secs", mode="before")
    @classmethod
    def validate_toxicity_non_negative_times(cls, v: str):
        ret = validate_decimal(v, min_value=Decimal("0"), inclusive=True)
        if ret is not None:
            raise ValueError(ret)
        return v

    @field_validator(
        "toxicity_trigger_bps",
        "toxicity_release_bps",
        "toxicity_spread_mult_min",
        "toxicity_spread_mult_max",
        "toxicity_size_mult_min",
        "toxicity_size_mult_max",
        mode="before",
    )
    @classmethod
    def validate_toxicity_non_negative_decimals(cls, v: str):
        ret = validate_decimal(v, min_value=Decimal("0"), inclusive=True)
        if ret is not None:
            raise ValueError(ret)
        return v

    @field_validator("toxicity_curve_power", mode="before")
    @classmethod
    def validate_toxicity_curve_power(cls, v: str):
        ret = validate_decimal(v, min_value=Decimal("0"), inclusive=False)
        if ret is not None:
            raise ValueError(ret)
        return v

    @field_validator("toxicity_action_mode", mode="before")
    @classmethod
    def validate_toxicity_action_mode(cls, v: str):
        value = str(v).lower()
        valid_values = {"widen_only", "widen_and_shrink", "pause_quote"}
        if value not in valid_values:
            raise ValueError("Invalid toxicity_action_mode, choose from ['widen_only', 'widen_and_shrink', 'pause_quote']")
        return value

    @field_validator("inventory_gate_scale_pct", mode="before")
    @classmethod
    def validate_inventory_gate_scale_pct(cls, v: str):
        ret = validate_decimal(v, min_value=Decimal("0"), inclusive=False)
        if ret is not None:
            raise ValueError(ret)
        return v

    @field_validator("inventory_gate_min", "inventory_cross_suppress_factor", mode="before")
    @classmethod
    def validate_inventory_gate_unit_interval(cls, v: str):
        ret = validate_decimal(v, min_value=Decimal("0"), max_value=Decimal("1"), inclusive=True)
        if ret is not None:
            raise ValueError(ret)
        return v

    @field_validator("inventory_size_min_mult", mode="before")
    @classmethod
    def validate_inventory_size_min_mult(cls, v: str):
        ret = validate_decimal(v, min_value=Decimal("0"), max_value=Decimal("1"), inclusive=True)
        if ret is not None:
            raise ValueError(ret)
        return v

    @field_validator("inventory_size_beta", mode="before")
    @classmethod
    def validate_inventory_size_beta(cls, v: str):
        ret = validate_decimal(v, min_value=Decimal("0"), inclusive=True)
        if ret is not None:
            raise ValueError(ret)
        return v

    @field_validator("inv_stress_ema_halflife_secs", mode="before")
    @classmethod
    def validate_inv_stress_ema_halflife_secs(cls, v: str):
        ret = validate_int(v, min_value=0, inclusive=False)
        if ret is not None:
            raise ValueError(ret)
        return v

    @field_validator("size_scaling_min_mult", mode="before")
    @classmethod
    def validate_size_scaling_min_mult(cls, v: str):
        ret = validate_decimal(v, min_value=Decimal("0"), max_value=Decimal("1"), inclusive=True)
        if ret is not None:
            raise ValueError(ret)
        return v

    @field_validator("size_scaling_beta", "size_dir_bias_k", mode="before")
    @classmethod
    def validate_non_negative_scaling_decimals(cls, v: str):
        ret = validate_decimal(v, min_value=Decimal("0"), inclusive=True)
        if ret is not None:
            raise ValueError(ret)
        return v

    @field_validator("size_scaling_power", mode="before")
    @classmethod
    def validate_size_scaling_power(cls, v: str):
        ret = validate_decimal(v, min_value=Decimal("0"), inclusive=False)
        if ret is not None:
            raise ValueError(ret)
        return v

    @field_validator("inventory_cross_deadband_base", mode="before")
    @classmethod
    def validate_inventory_cross_deadband(cls, v: str):
        ret = validate_decimal(v, min_value=Decimal("0"), inclusive=True)
        if ret is not None:
            raise ValueError(ret)
        return v

    @field_validator("inventory_cross_hold_secs", mode="before")
    @classmethod
    def validate_inventory_cross_hold_secs(cls, v: str):
        ret = validate_int(v, min_value=0)
        if ret is not None:
            raise ValueError(ret)
        return v

    @field_validator("inventory_gate_mode", mode="before")
    @classmethod
    def validate_inventory_gate_mode(cls, v: str):
        value = str(v).lower()
        valid_values = {"exp", "linear"}
        if value not in valid_values:
            raise ValueError("Invalid inventory_gate_mode, choose from ['exp', 'linear']")
        return value

    @field_validator("side_intensity_delta_mode", mode="before")
    @classmethod
    def validate_side_intensity_delta_mode(cls, v: str):
        value = str(v).lower()
        valid_values = {"relative_to_r", "absolute_price"}
        if value not in valid_values:
            raise ValueError("Invalid side intensity delta mode, choose from ['relative_to_r', 'absolute_price']")
        return value

    @field_validator("inventory_target_base_pct", mode="before")
    @classmethod
    def validate_pct_inclusive(cls, v: str):
        """Used for client-friendly error output."""
        ret = validate_decimal(v, min_value=Decimal("0"), max_value=Decimal("100"), inclusive=True)
        if ret is not None:
            raise ValueError(ret)
        return v

    # === post-validations ===

    @model_validator(mode="after")
    def post_validations(self):
        if self.side_intensity_k_min >= self.side_intensity_k_max:
            raise ValueError("side_intensity_k_min must be less than side_intensity_k_max")
        required_exchanges.add(self.exchange)
        return self

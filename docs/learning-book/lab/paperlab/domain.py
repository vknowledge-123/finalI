from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR


def positive(value: Decimal) -> Decimal:
    if not value.is_finite() or value <= 0:
        raise ValueError("Expected a finite positive number")
    return value


def strict_quantity(capital: Decimal, price: Decimal) -> int:
    positive(price)
    if not capital.is_finite() or capital < 0:
        raise ValueError("Invalid capital")
    return int(capital // price)


def align_price(price: Decimal, tick: Decimal, side: str) -> Decimal:
    positive(price)
    positive(tick)
    if side not in {"BUY", "SELL"}:
        raise ValueError("Invalid side")
    rounding = ROUND_CEILING if side == "BUY" else ROUND_FLOOR
    result = (price / tick).to_integral_value(rounding=rounding) * tick
    return positive(result)


@dataclass(frozen=True)
class RiskState:
    side: str
    entry: Decimal
    target: Decimal
    stop: Decimal
    high_water: Decimal
    low_water: Decimal
    initial_stop_pct: Decimal
    trailing_enabled: bool
    trailing_pct: Decimal
    cost_enabled: bool
    cost_rr: Decimal


def advance(state: RiskState, price: Decimal) -> tuple[RiskState, str | None]:
    positive(price)
    if state.side not in {"BUY", "SELL"}:
        raise ValueError("Invalid side")
    high = max(state.high_water, price)
    low = min(state.low_water, price)
    stop = state.stop
    hundred = Decimal("100")
    if state.side == "BUY":
        if state.trailing_enabled:
            stop = max(stop, high * (1 - state.trailing_pct / hundred))
        if state.cost_enabled and high >= state.entry * (
            1 + state.initial_stop_pct * state.cost_rr / hundred
        ):
            stop = max(stop, state.entry)
        reason = "TARGET" if price >= state.target else "STOP" if price <= stop else None
    else:
        if state.trailing_enabled:
            stop = min(stop, low * (1 + state.trailing_pct / hundred))
        if state.cost_enabled and low <= state.entry * (
            1 - state.initial_stop_pct * state.cost_rr / hundred
        ):
            stop = min(stop, state.entry)
        reason = "TARGET" if price <= state.target else "STOP" if price >= stop else None
    updated = RiskState(
        state.side, state.entry, state.target, stop, high, low,
        state.initial_stop_pct, state.trailing_enabled, state.trailing_pct,
        state.cost_enabled, state.cost_rr,
    )
    return updated, reason


def marked_pnl(side: str, entry: Decimal, price: Decimal, quantity: int) -> Decimal:
    if side not in {"BUY", "SELL"}:
        raise ValueError("Invalid side")
    if quantity <= 0:
        raise ValueError("Invalid quantity")
    positive(entry)
    positive(price)
    return (price - entry) * quantity * (1 if side == "BUY" else -1)

"""Paper-only teaching model. No brokerage or network dependencies."""
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR


def positive(value):
    number = Decimal(str(value))
    if not number.is_finite() or number <= 0:
        raise ValueError("Expected a finite positive value")
    return number


def tick_price(price, tick, side):
    price, tick = positive(price), positive(tick)
    if side not in {"BUY", "SELL"}:
        raise ValueError("Invalid side")
    mode = ROUND_CEILING if side == "BUY" else ROUND_FLOOR
    result = (price / tick).to_integral_value(rounding=mode) * tick
    return positive(result)


def quantity(capital, price, minimum_one=False):
    capital, price = positive(capital), positive(price)
    result = int(capital // price)
    return max(1, result) if minimum_one else result


def sector_change(ltp, previous_close):
    return (positive(ltp) / positive(previous_close) - 1) * 100


@dataclass
class Position:
    strategy: str
    symbol: str
    side: str
    qty: int
    entry: Decimal
    target: Decimal
    stop: Decimal
    ltp: Decimal
    status: str = "OPEN"
    exit_reason: str = ""

    @classmethod
    def from_fill(cls, strategy, symbol, side, qty, price, target_pct, sl_pct):
        entry = positive(price)
        target_pct, sl_pct = positive(target_pct), positive(sl_pct)
        if type(qty) is not int or qty <= 0 or side not in {"BUY", "SELL"}:
            raise ValueError("Invalid fill")
        if target_pct >= 100 or sl_pct >= 100:
            raise ValueError("Percentages must be below 100")
        sign = 1 if side == "BUY" else -1
        return cls(strategy, symbol, side, qty, entry,
                   entry * (1 + sign * target_pct / 100),
                   entry * (1 - sign * sl_pct / 100), entry)

    @property
    def pnl(self):
        sign = 1 if self.side == "BUY" else -1
        return (self.ltp - self.entry) * self.qty * sign

    def mark(self, price):
        if self.status != "OPEN":
            return
        self.ltp = positive(price)
        target_hit = self.ltp >= self.target if self.side == "BUY" else self.ltp <= self.target
        stop_hit = self.ltp <= self.stop if self.side == "BUY" else self.ltp >= self.stop
        if target_hit or stop_hit:
            # A lab assumes a fill at the supplied tick, not a live execution promise.
            self.status = "CLOSED"
            self.exit_reason = "TARGET" if target_hit else "STOP_LOSS"

    def as_dict(self):
        return {"strategy": self.strategy, "symbol": self.symbol,
                "side": self.side, "qty": self.qty, "status": self.status,
                "entry": str(self.entry), "ltp": str(self.ltp),
                "target": str(self.target), "stop": str(self.stop),
                "pnl": str(self.pnl), "exit_reason": self.exit_reason}

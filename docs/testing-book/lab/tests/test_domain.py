from decimal import Decimal
import pytest
from domain import Position, quantity, sector_change, tick_price


@pytest.mark.parametrize("side,expected", [("BUY", "100.05"), ("SELL", "100.00")])
def test_directional_tick_rounding(side, expected):
    assert tick_price("100.03", ".05", side) == Decimal(expected)


@pytest.mark.parametrize("bad", ["0", "-1", "NaN", "Infinity"])
def test_invalid_price_rejected(bad):
    with pytest.raises(ValueError):
        quantity("10000", bad)


def test_capital_policy_is_explicit():
    assert quantity("10000", "12000") == 0
    assert quantity("10000", "12000", minimum_one=True) == 1


def test_sector_denominator_and_sign():
    assert sector_change("990", "1000") == Decimal("-1")


@pytest.mark.parametrize("side,tick,reason,pnl", [
    ("BUY", "101", "TARGET", "2"), ("BUY", "99.5", "STOP_LOSS", "-1"),
    ("SELL", "99", "TARGET", "2"), ("SELL", "100.5", "STOP_LOSS", "-1")])
def test_exit_at_exact_threshold(side, tick, reason, pnl):
    position = Position.from_fill("demo", "TEST", side, 2, "100", "1", ".5")
    position.mark(tick)
    assert position.status == "CLOSED"
    assert position.exit_reason == reason
    assert position.pnl == Decimal(pnl)
    position.mark("1000")
    assert position.pnl == Decimal(pnl)

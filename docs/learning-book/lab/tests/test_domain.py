from decimal import Decimal as D

import pytest

from paperlab.domain import RiskState, advance, align_price, marked_pnl, strict_quantity


@pytest.mark.parametrize("capital,price,expected", [
    ("10000", "2000", 5), ("10000", "12000", 0), ("0", "100", 0),
])
def test_strict_quantity(capital, price, expected):
    assert strict_quantity(D(capital), D(price)) == expected


@pytest.mark.parametrize("value", ["0", "-1", "NaN", "Infinity"])
def test_invalid_price_rejected(value):
    with pytest.raises(ValueError):
        strict_quantity(D("100"), D(value))


@pytest.mark.parametrize("side,expected", [("BUY", "100.05"), ("SELL", "100.00")])
def test_tick_alignment(side, expected):
    assert align_price(D("100.03"), D("0.05"), side) == D(expected)


def state(side="BUY", trailing=False, cost=False):
    return RiskState(side, D("100"), D("110") if side == "BUY" else D("90"),
                     D("99") if side == "BUY" else D("101"), D("100"), D("100"),
                     D("1"), trailing, D("1"), cost, D("2"))


def test_long_trailing_never_decreases():
    a, reason = advance(state(trailing=True), D("104"))
    b, reason = advance(a, D("103.5"))
    assert a.stop == b.stop == D("102.96")
    assert reason is None


def test_short_trailing_never_increases():
    a, _ = advance(state("SELL", trailing=True), D("96"))
    b, reason = advance(a, D("96.5"))
    assert a.stop == b.stop == D("96.96")
    assert reason is None


def test_disabled_trailing_retains_initial_stop():
    updated, _ = advance(state(), D("105"))
    assert updated.stop == D("99")


def test_cost_stop_independent_of_trailing():
    updated, _ = advance(state(cost=True), D("102"))
    assert updated.stop == D("100")
    _, reason = advance(updated, D("100"))
    assert reason == "STOP"


@pytest.mark.parametrize("side,price,reason", [
    ("BUY", "110", "TARGET"), ("BUY", "99", "STOP"),
    ("SELL", "90", "TARGET"), ("SELL", "101", "STOP"),
])
def test_exit_equality(side, price, reason):
    assert advance(state(side), D(price))[1] == reason


def test_short_pnl():
    assert marked_pnl("SELL", D("100"), D("95"), 2) == D("10")


def test_unknown_side_rejected():
    with pytest.raises(ValueError):
        align_price(D("100"), D("0.05"), "OTHER")

from decimal import Decimal
import pytest
from jevloop.assets import AssetSpec
from jevloop.ladder import Rung, select_rung
from jevloop.limits import Limits
from jevloop.policy import (
    KILL,
    PULL_QUOTES,
    QUOTE_BOTH_SIDES,
    QUOTE_WIDE,
    STAND_DOWN,
    WIDEN,
    compose_action,
    fallback_action,
)
from jevloop.pricing import avellaneda_stoikov_quotes, bounded_quote_prices
from jevloop.risk import check


def answers(**kw):
    a = {
        "toxic_flow": {"noul": 0.1},
        "liquidity_stressed": {"noul": 0.1},
        "quote_environment": {"score": 2.5, "confidence": 0.9},
        "direction": {"choice": "up", "confidence": 0.9},
        "inventory_pressure": {"score": 0.0},
        "execution_health": {"score": 2.0},
    }
    for k, v in kw.items():
        a[k].update(v)
    return a


def snap(**kw):
    s = {
        "drawdown_pct": 0.0,
        "inventory_usd": 0.0,
        "inventory_qty": 0.0,
        "daily_loss_usd": 0.0,
        "position_age_s": 0.0,
        "data_age_s": 0.0,
        "mid": 100.0,
        "best_bid": 99.9,
        "best_ask": 100.1,
        "inventory_utilization": 0.0,
    }
    s.update(kw)
    return s


def test_policy_kills_on_drawdown():
    assert compose_action(answers(), snap(drawdown_pct=0.2), Limits()).kind == KILL


def test_policy_pulls_toxic():
    assert compose_action(answers(toxic_flow={"noul": 0.9}), snap(), Limits()).kind == PULL_QUOTES


def test_policy_widens_liquidity_stress():
    assert compose_action(answers(liquidity_stressed={"noul": 0.9}), snap(), Limits()).kind == WIDEN


def test_policy_quotes_both_on_strong_env():
    assert compose_action(answers(), snap(), Limits()).kind == QUOTE_BOTH_SIDES


def test_policy_quotes_wide_on_marginal_env():
    assert (
        compose_action(
            answers(quote_environment={"score": 1.5, "confidence": 0.4}), snap(), Limits()
        ).kind
        == QUOTE_WIDE
    )


def test_policy_stands_down_below_floor():
    assert (
        compose_action(
            answers(quote_environment={"score": 0.2, "confidence": 0.9}), snap(), Limits()
        ).kind
        == STAND_DOWN
    )


def test_direction_leg_disabled_by_default():
    assert compose_action(answers(), snap(), Limits()).direction_leg is None


def test_fallback_fails_closed():
    assert fallback_action(snap(), Limits()).kind == STAND_DOWN


@pytest.mark.parametrize(
    "field,value,kill",
    [
        ("drawdown_pct", 0.2, True),
        ("inventory_usd", 100, True),
        ("daily_loss_usd", 100, True),
        ("data_age_s", 20, False),
    ],
)
def test_risk_hard_conditions(field, value, kill):
    v = check(
        snap(**{field: value}),
        0,
        Limits(),
        api_error_streak=0,
        decision_latency_ms=0,
        session_open_orders=0,
    )
    assert not v.ok and v.kill is kill


def test_risk_checks_projected_position_before_order():
    v = check(
        snap(inventory_usd=45),
        10,
        Limits(),
        api_error_streak=0,
        decision_latency_ms=0,
        session_open_orders=0,
        projected_inventory_usd=55,
    )
    assert not v.ok and "projected" in v.veto


def test_risk_checks_session_order_cap():
    v = check(snap(), 1, Limits(), api_error_streak=0, decision_latency_ms=0, session_open_orders=2)
    assert not v.ok


def test_ladder_priority():
    base = dict(
        decision_confidence=0.9,
        low_confidence_threshold=0.5,
        execution_health_score=2,
        execution_health_floor=1,
    )
    assert select_rung(risk_kill=True, decision_late=True, jev_down=True, **base) == Rung.KILL
    assert select_rung(risk_kill=False, decision_late=True, jev_down=True, **base) == Rung.HOLD_LATE
    assert (
        select_rung(risk_kill=False, decision_late=False, jev_down=True, **base) == Rung.RULES_ONLY
    )
    assert (
        select_rung(risk_kill=False, decision_late=False, jev_down=False, risk_ok=False, **base)
        == Rung.HOLD_BLOCKED
    )


def test_ladder_reduces_low_confidence_or_health():
    assert (
        select_rung(
            risk_kill=False,
            decision_late=False,
            jev_down=False,
            decision_confidence=0.2,
            low_confidence_threshold=0.5,
            execution_health_score=2,
            execution_health_floor=1,
        )
        == Rung.REDUCE
    )
    assert (
        select_rung(
            risk_kill=False,
            decision_late=False,
            jev_down=False,
            decision_confidence=0.9,
            low_confidence_threshold=0.5,
            execution_health_score=0.2,
            execution_health_floor=1,
        )
        == Rung.REDUCE
    )


def test_bounded_quotes_do_not_cross_top_of_book():
    spec = AssetSpec("BTC/USD", "crypto", True, True, price_increment=Decimal("0.01"))
    bid, ask = bounded_quote_prices(
        mid=100, best_bid=99.9, best_ask=100.1, inventory_utilization=0, limits=Limits(), spec=spec
    )
    assert bid <= 99.9 and ask >= 100.1


def test_inventory_skew_moves_long_quotes_lower():
    spec = AssetSpec("BTC/USD", "crypto", True, True, price_increment=Decimal("0.01"))
    flat = bounded_quote_prices(
        mid=100, best_bid=99.9, best_ask=100.1, inventory_utilization=0, limits=Limits(), spec=spec
    )
    long = bounded_quote_prices(
        mid=100, best_bid=99.9, best_ask=100.1, inventory_utilization=1, limits=Limits(), spec=spec
    )
    assert long[0] <= flat[0]


def test_as_helper_rejects_bad_units():
    with pytest.raises(ValueError):
        avellaneda_stoikov_quotes(100, 1, 0, 1, 1, 1)


def test_as_helper_matches_paper_total_spread_not_double():
    # Avellaneda & Stoikov (2008): reservation r = mid - q*gamma*sigma^2*(T-t); the
    # *total* optimal spread is delta = gamma*sigma^2*(T-t) + (2/gamma)*ln(1+gamma/kappa),
    # split evenly around r. A prior revision applied the full delta on each side,
    # doubling the quoted width -- this pins the corrected, undoubled behavior.
    import math

    mid, inventory, gamma, sigma, horizon, kappa = 100.0, 0.0, 0.1, 0.02, 5.0, 1.5
    bid, ask = avellaneda_stoikov_quotes(mid, inventory, gamma, sigma, horizon, kappa)
    variance = sigma**2
    reservation = mid - inventory * gamma * variance * horizon
    full_spread = gamma * variance * horizon + (2 / gamma) * math.log1p(gamma / kappa)
    assert ask - bid == pytest.approx(full_spread)
    assert bid == pytest.approx(reservation - full_spread / 2)
    assert ask == pytest.approx(reservation + full_spread / 2)


def test_as_helper_reservation_shifts_with_inventory():
    # Long inventory should push the reservation price (and therefore both quotes)
    # below mid, all else equal -- and the total spread should be unaffected by
    # inventory (inventory affects skew via `reservation`, not `delta`).
    flat_bid, flat_ask = avellaneda_stoikov_quotes(100.0, 0.0, 0.1, 0.02, 5.0, 1.5)
    long_bid, long_ask = avellaneda_stoikov_quotes(100.0, 5.0, 0.1, 0.02, 5.0, 1.5)
    assert long_bid < flat_bid
    assert long_ask < flat_ask
    assert (flat_ask - flat_bid) == pytest.approx(long_ask - long_bid)

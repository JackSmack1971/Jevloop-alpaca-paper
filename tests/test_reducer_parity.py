from decimal import Decimal

import pytest

from jevloop.assets import AssetSpec
from jevloop.limits import Limits
from jevloop.loop import _apply_intentions
from jevloop.reducer import TickIdentity, TickInput, reduce_tick
from jevloop.state import RuntimeState


def _snapshot():
    return {
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


def _answers():
    return {
        "toxic_flow": {"noul": 0.1},
        "liquidity_stressed": {"noul": 0.1},
        "quote_environment": {"score": 2.5, "confidence": 0.9},
        "direction": {"choice": "neutral", "confidence": 0.9},
        "inventory_pressure": {"score": 0.0},
        "execution_health": {"score": 2.0},
        "regime": {"choice": "range"},
    }


def _asset():
    return AssetSpec(
        "BTC/USD",
        "crypto",
        True,
        True,
        tradable=True,
        status="active",
        min_order_size=Decimal("0.0001"),
        min_trade_increment=Decimal("0.0001"),
        price_increment=Decimal("0.01"),
        metadata_source="alpaca:/v2/assets",
    )


def _identity(mode):
    return TickIdentity("same-run", mode, "same-snapshot", "BTC/USD", "route", "model")


def test_real_dry_and_simulation_have_identical_deterministic_decisions():
    common = dict(snapshot=_snapshot(), answers=_answers(), asset=_asset(), limits=Limits())
    dry = reduce_tick(TickInput(identity=_identity("dry-real"), **common))
    simulation = reduce_tick(TickInput(identity=_identity("simulation"), **common))

    assert dry.action == simulation.action
    assert dry.rung == simulation.rung
    assert dry.risk == simulation.risk
    assert dry.effects == simulation.effects
    assert dry.identity.symbol == simulation.identity.symbol == "BTC/USD"
    assert dry.identity.run_id == simulation.identity.run_id == "same-run"


class _NoSubmissionBroker:
    def submit_limit_order(self, **kwargs):  # pragma: no cover - prohibited path
        raise AssertionError("dry and simulation modes cannot submit")

    def submit_market_order(self, **kwargs):  # pragma: no cover - prohibited path
        raise AssertionError("dry and simulation modes cannot flatten")


def test_dry_adapter_cannot_turn_intentions_into_orders():
    decision = reduce_tick(
        TickInput(
            identity=_identity("dry-real"),
            snapshot=_snapshot(),
            answers=_answers(),
            asset=_asset(),
            limits=Limits(),
        )
    )
    execution = _apply_intentions(
        decision=decision,
        alpaca=_NoSubmissionBroker(),
        spec=_asset(),
        runtime=RuntimeState(),
        pending_orders={},
        dry=True,
    )
    assert "dry buy" in execution


def test_simulation_module_has_no_broker_execution_adapter():
    import jevloop.simulate as simulation

    assert not hasattr(simulation, "_apply_intentions")
    assert not hasattr(simulation, "AlpacaClient")


def test_simulation_intentions_are_rejected_by_paper_adapter():
    decision = reduce_tick(TickInput(
        identity=_identity("simulation"), snapshot=_snapshot(), answers=_answers(),
        asset=_asset(), limits=Limits(),
    ))
    with pytest.raises(PermissionError, match="only paper-real"):
        _apply_intentions(
            decision=decision, alpaca=_NoSubmissionBroker(), spec=_asset(),
            runtime=RuntimeState(), pending_orders={}, dry=False,
        )


def test_mock_provider_is_rejected_before_paper_authority(monkeypatch, capsys):
    from jevloop.loop import run
    from jevloop.preflight import PaperPreflightResult

    class MockProvider:
        name = "MOCK"
        model = "mock"

    monkeypatch.setattr(
        "jevloop.loop.paper_preflight",
        lambda **kwargs: PaperPreflightResult((), object(), _asset(), {}, MockProvider()),
    )
    assert run(symbol="BTC/USD", ticks=1, mock=True, dry_execution=False, limits=Limits()) == 2
    assert "may not drive broker orders" in capsys.readouterr().out

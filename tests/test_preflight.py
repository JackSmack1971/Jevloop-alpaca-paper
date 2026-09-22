import json

import pytest

from jevloop.assets import AssetNotTradableError, AssetSpec
from jevloop.client import MockDecisionClient
from jevloop.doctor import main as doctor_main
from jevloop.limits import Limits
from jevloop.loop import run
from jevloop.preflight import (
    ACCOUNT_NOT_TRADABLE,
    ACCOUNT_READ_FAILURE,
    ASSET_NOT_TRADABLE,
    FOREIGN_SESSION_ORDERS,
    INVALID_PROVIDER_CONFIGURATION,
    NON_PAPER_ENDPOINT,
    PaperPreflightReason,
    PaperPreflightResult,
    paper_preflight,
)


class Broker:
    base_url = "https://paper-api.alpaca.markets"

    def __init__(self, *, account=None, foreign=None):
        self.account = account or {"status": "ACTIVE", "trading_blocked": False}
        self.foreign = foreign or []
        self.submit_calls = 0
        self.cancel_calls = 0

    def get_account(self):
        return self.account

    def load_asset_spec(self):
        return AssetSpec(
            "BTC/USD", "crypto", True, True, status="active", tradable=True,
            min_order_size=1, min_trade_increment=1, price_increment=1,
            metadata_source="alpaca:/v2/assets",
        )

    def get_foreign_session_open_orders(self):
        return self.foreign

    def submit_limit_order(self, **kwargs):
        self.submit_calls += 1

    def cancel_session_orders(self):
        self.cancel_calls += 1
        return []


class RealProvider(MockDecisionClient):
    def __init__(self):
        super().__init__()
        self.name = "test-real-provider"


def codes(result):
    return {reason.code for reason in result.reasons}


def test_preflight_ready():
    assert paper_preflight(symbol="BTC/USD", alpaca=Broker(), decision_client=RealProvider()).ready


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda broker: setattr(broker, "base_url", "https://api.alpaca.markets"), NON_PAPER_ENDPOINT),
        (lambda broker: broker.account.update(status="INACTIVE"), ACCOUNT_NOT_TRADABLE),
        (lambda broker: broker.account.update(trading_blocked=True), ACCOUNT_NOT_TRADABLE),
        (lambda broker: broker.foreign.append({"id": "old-1"}), FOREIGN_SESSION_ORDERS),
    ],
)
def test_preflight_stable_broker_reason_codes(mutate, code):
    broker = Broker()
    mutate(broker)
    assert code in codes(paper_preflight(symbol="BTC/USD", alpaca=broker, decision_client=RealProvider()))


def test_preflight_account_read_failure_code(monkeypatch):
    broker = Broker()
    monkeypatch.setattr(broker, "get_account", lambda: (_ for _ in ()).throw(ValueError("bad account")))
    assert ACCOUNT_READ_FAILURE in codes(
        paper_preflight(symbol="BTC/USD", alpaca=broker, decision_client=RealProvider())
    )


def test_preflight_asset_failure_code(monkeypatch):
    broker = Broker()
    monkeypatch.setattr(
        broker, "load_asset_spec", lambda: (_ for _ in ()).throw(AssetNotTradableError("inactive"))
    )
    assert ASSET_NOT_TRADABLE in codes(
        paper_preflight(symbol="BTC/USD", alpaca=broker, decision_client=RealProvider())
    )


def test_preflight_invalid_provider_code():
    assert INVALID_PROVIDER_CONFIGURATION in codes(
        paper_preflight(symbol="BTC/USD", alpaca=Broker(), decision_client=MockDecisionClient())
    )


def test_doctor_and_runtime_use_identical_readiness(monkeypatch, capsys, tmp_path):
    result = PaperPreflightResult((PaperPreflightReason(ACCOUNT_NOT_TRADABLE, "blocked"),))
    monkeypatch.setattr("jevloop.doctor.paper_preflight", lambda **kwargs: result)
    monkeypatch.setattr("jevloop.loop.paper_preflight", lambda **kwargs: result)
    monkeypatch.setattr("jevloop.loop.LOG_DIR", tmp_path)
    assert doctor_main([]) == 2
    doctor_output = capsys.readouterr().out
    assert run(symbol="BTC/USD", ticks=0, mock=False, dry_execution=False, limits=Limits()) == 2
    runtime_output = capsys.readouterr().out
    assert ACCOUNT_NOT_TRADABLE in doctor_output
    assert ACCOUNT_NOT_TRADABLE in runtime_output


def test_foreign_orders_block_paper_without_submit_or_cancel(monkeypatch, tmp_path):
    broker = Broker(foreign=[{"id": "foreign-1", "client_order_id": "jevloop-old-1"}])
    result = paper_preflight(symbol="BTC/USD", alpaca=broker, decision_client=RealProvider())
    monkeypatch.setattr("jevloop.loop.paper_preflight", lambda **kwargs: result)
    monkeypatch.setattr("jevloop.loop.LOG_DIR", tmp_path)
    assert run(symbol="BTC/USD", ticks=0, mock=False, dry_execution=False, limits=Limits()) == 2
    assert broker.submit_calls == 0
    assert broker.cancel_calls == 0


def test_dry_mode_emits_structured_foreign_order_warning(monkeypatch, capsys, tmp_path):
    broker = Broker(foreign=[{"id": "foreign-1", "client_order_id": "jevloop-old-1"}])
    result = paper_preflight(symbol="BTC/USD", alpaca=broker, decision_client=RealProvider())
    monkeypatch.setattr("jevloop.loop.paper_preflight", lambda **kwargs: result)
    monkeypatch.setattr("jevloop.loop.LOG_DIR", tmp_path)
    assert run(symbol="BTC/USD", ticks=0, mock=False, dry_execution=True, limits=Limits()) == 0
    warning = next(line for line in capsys.readouterr().out.splitlines() if line.startswith("{"))
    assert json.loads(warning) == {
        "event": "paper_preflight_warning",
        "code": FOREIGN_SESSION_ORDERS,
        "details": {"order_ids": ["foreign-1"]},
    }
    assert broker.submit_calls == 0
    assert broker.cancel_calls == 0

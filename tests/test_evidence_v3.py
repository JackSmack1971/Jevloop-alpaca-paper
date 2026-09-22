import json

import pytest

from jevloop.evidence import (
    EvidenceContext,
    PROCESS_RUN_ID,
    classify_loaded_record,
    serialize_record,
    stable_digest,
)
from jevloop.execution.alpaca import AlpacaClient
from jevloop.limits import Limits


def _record() -> dict:
    context = EvidenceContext.create(
        runtime_mode="simulation",
        data_source="test-synthetic",
        symbol="BTC/USD",
        route="MOCK",
        model="test",
        limits=Limits(),
    )
    return {**context.fields(), "ts": 1.0}


def test_v3_serialization_is_explicit_and_round_trips():
    encoded = serialize_record(_record())
    assert json.loads(encoded)["schema_version"] == 3
    assert json.loads(encoded)["run_id"] == PROCESS_RUN_ID


def test_digest_is_stable_across_mapping_order():
    assert stable_digest({"a": 1, "b": 2}) == stable_digest({"b": 2, "a": 1})


def test_legacy_rows_are_loaded_but_excluded_with_reason():
    row = classify_loaded_record({"schema_version": 2, "symbol": "BTC/USD"})
    assert row["cohort_eligible"] is False
    assert row["cohort_exclusion_reason"] == "legacy schema v2"


@pytest.mark.parametrize("key", ["Authorization", "APCA-API-KEY-ID", "secret_key", "token"])
def test_credentials_and_authorization_are_rejected_at_any_depth(key):
    with pytest.raises(ValueError, match="credential field forbidden"):
        serialize_record({**_record(), "nested": {key: "never-log-me"}})


def test_request_id_is_correlated_without_headers_or_credentials(monkeypatch):
    class Response:
        status_code = 200
        text = '{"id":"order-1","client_order_id":"jevloop-session-1","status":"accepted"}'
        headers = {"X-Request-ID": "request-123", "Authorization": "secret"}

        def json(self):
            return json.loads(self.text)

    monkeypatch.setattr("jevloop.execution.alpaca.requests.request", lambda *a, **k: Response())
    client = AlpacaClient("key-secret", "secret-secret", "BTC/USD")
    client._request(
        "POST",
        f"{client.base_url}/v2/orders",
        json={"client_order_id": "jevloop-session-1"},
    )
    event = client.drain_request_evidence()[0]
    assert event["run_id"] == PROCESS_RUN_ID
    assert event["request_id"] == "request-123"
    assert event["client_order_id"] == "jevloop-session-1"
    assert event["order_id"] == "order-1"
    assert event["operation"] == "submission"
    assert "headers" not in event
    assert "key-secret" not in json.dumps(event)
    assert "secret-secret" not in json.dumps(event)

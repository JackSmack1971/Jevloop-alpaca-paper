import pytest
import requests
from jevloop.battery import build_questions, run_battery, validate_answers
from jevloop.client import (
    DecisionClientError,
    DecisionLateResponseError,
    DecisionSchemaError,
    MockDecisionClient,
    TypeSafeDirectClient,
    resolve_decision_client,
    _post,
)
from jevloop.split import SplitViolation, assert_split_respected, render_split_table


def test_real_battery_has_exact_allow_list_and_seven_questions():
    q = build_questions()
    assert len(q) == 7
    assert_split_respected(q)


def test_unknown_question_rejected():
    q = build_questions()
    q["calculate_vwap"] = {"type": "noul", "instructions": "judge"}
    with pytest.raises(SplitViolation):
        assert_split_respected(q)


def test_arithmetic_delegation_rejected():
    q = build_questions()
    q["regime"]["instructions"] = "calculate the exact value"
    with pytest.raises(SplitViolation):
        assert_split_respected(q)


def test_split_table_names_both_owners():
    text = render_split_table()
    assert "DETERMINISTIC" in text and "PROBABILISTIC" in text


def test_mock_shape_validates():
    client = MockDecisionClient(seed=1)
    state = {"return_1m": 0.0, "imbalance": 0.0, "spread_bps": 2, "inventory_utilization": 0.0}
    answers, meta = run_battery(client, state, 1)
    assert len(answers) == 7 and meta["route"] == "MOCK"


def test_answer_probability_keys_must_match():
    client = MockDecisionClient(seed=1)
    answers, _ = client.ask(
        {"return_1m": 0.0, "imbalance": 0.0, "spread_bps": 2, "inventory_utilization": 0.0},
        build_questions(),
        1,
    )
    answers["direction"]["probabilities"].pop("up")
    with pytest.raises(DecisionSchemaError):
        validate_answers(answers)


def test_answer_probability_range_checked():
    client = MockDecisionClient(seed=1)
    answers, _ = client.ask(
        {"return_1m": 0.0, "imbalance": 0.0, "spread_bps": 2, "inventory_utilization": 0.0},
        build_questions(),
        1,
    )
    answers["toxic_flow"]["noul"] = 1.5
    with pytest.raises(DecisionSchemaError):
        validate_answers(answers)


def _valid_answers():
    client = MockDecisionClient(seed=1)
    answers, _ = client.ask(
        {"return_1m": 0.0, "imbalance": 0.0, "spread_bps": 2, "inventory_utilization": 0.0},
        build_questions(),
        1,
    )
    return answers


@pytest.mark.parametrize("payload", [None, 3, [], "response"])
def test_provider_outer_response_must_be_mapping(monkeypatch, payload):
    monkeypatch.setattr("jevloop.client._post", lambda *args, **kwargs: (payload, 0.01))
    with pytest.raises(DecisionSchemaError, match="response must be a mapping"):
        TypeSafeDirectClient("secret", "model").ask({}, build_questions(), 1)


@pytest.mark.parametrize("answers", [None, 3, [], "answers"])
def test_answer_collection_must_be_mapping(answers):
    class Client:
        def ask(self, **kwargs):
            return answers, {}

    with pytest.raises(DecisionSchemaError, match="answers must be a mapping"):
        run_battery(Client(), {}, 1)


@pytest.mark.parametrize("bad_answer", [None, 3, [], "answer"])
def test_each_answer_must_be_mapping(bad_answer):
    answers = _valid_answers()
    answers["direction"] = bad_answer
    with pytest.raises(DecisionSchemaError, match="'direction' must be a mapping"):
        validate_answers(answers)


@pytest.mark.parametrize("key", ["regime", "direction"])
@pytest.mark.parametrize("probabilities", [None, 1, [], "probabilities"])
def test_probability_container_must_be_mapping(key, probabilities):
    answers = _valid_answers()
    answers[key]["probabilities"] = probabilities
    with pytest.raises(DecisionSchemaError, match="probabilities must be a mapping"):
        validate_answers(answers)


@pytest.mark.parametrize("change", ["missing", "extra"])
def test_answer_keys_must_be_exact(change):
    answers = _valid_answers()
    if change == "missing":
        answers.pop("direction")
    else:
        answers["unexpected"] = {}
    with pytest.raises(DecisionSchemaError, match="exactly match"):
        validate_answers(answers)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_probability_values_must_be_finite(value):
    answers = _valid_answers()
    answers["direction"]["probabilities"].update(up=value)
    with pytest.raises(DecisionSchemaError, match="finite"):
        validate_answers(answers)


@pytest.mark.parametrize("value", ["0.5", None, True, -0.1, 1.1])
def test_probability_values_must_be_numeric_and_in_range(value):
    answers = _valid_answers()
    answers["direction"]["probabilities"].update(up=value)
    with pytest.raises(DecisionSchemaError):
        validate_answers(answers)


def test_choice_must_be_one_of_the_requested_options():
    answers = _valid_answers()
    answers["direction"]["choice"] = "sideways"
    with pytest.raises(DecisionSchemaError, match="choice is not allowed"):
        validate_answers(answers)


@pytest.mark.parametrize("values", [(0.1, 0.1, 0.1), (0.6, 0.6, 0.1)])
def test_probability_totals_outside_tolerance_are_rejected(values):
    answers = _valid_answers()
    answers["direction"]["probabilities"] = dict(zip(("up", "down", "neutral"), values))
    with pytest.raises(DecisionSchemaError, match="sum to 1"):
        validate_answers(answers)


def test_missing_provider_key_fails_closed(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.delenv("AI_GATEWAY_API_KEY", raising=False)
    with pytest.raises(DecisionClientError):
        resolve_decision_client()


def test_direct_provider_requires_explicit_model(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "x")
    monkeypatch.delenv("TYPESAFE_MODEL", raising=False)
    monkeypatch.delenv("AI_GATEWAY_API_KEY", raising=False)
    with pytest.raises(DecisionClientError):
        resolve_decision_client()


def test_mock_requires_explicit_flag(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.delenv("AI_GATEWAY_API_KEY", raising=False)
    assert resolve_decision_client(mock=True).name == "MOCK"


class _Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class _Response:
    status_code = 200
    headers = {}
    text = ""

    def __init__(self, payload=None):
        self.payload = payload or {"answers": {}}

    def json(self):
        return self.payload


def test_post_rejects_success_returned_after_total_deadline(monkeypatch):
    clock = _Clock()

    def post(*args, **kwargs):
        clock.advance(1.01)
        return _Response({"ok": True})

    monkeypatch.setattr("jevloop.client.requests.post", post)
    with pytest.raises(DecisionLateResponseError) as raised:
        _post("https://example.test", {}, {}, 1.0, monotonic=clock.monotonic)
    assert raised.value.configured_deadline_s == 1.0
    assert raised.value.measured_latency_ms == 1010.0
    assert raised.value.disposition == "rejected_late_response"


def test_post_retry_exhaustion_uses_total_clock(monkeypatch):
    clock = _Clock()
    calls = 0

    def post(*args, **kwargs):
        nonlocal calls
        calls += 1
        clock.advance(0.1)
        raise requests.ConnectionError("offline")

    monkeypatch.setattr("jevloop.client.requests.post", post)
    monkeypatch.setattr("jevloop.client.random.uniform", lambda *_: 0.0)
    with pytest.raises(DecisionClientError, match="transport failure"):
        _post(
            "https://example.test",
            {},
            {},
            10.0,
            monotonic=clock.monotonic,
            sleep=clock.advance,
        )
    assert calls == 3
    assert clock.now == pytest.approx(1.05)


def test_post_rejects_backoff_that_would_cross_deadline(monkeypatch):
    clock = _Clock()

    def post(*args, **kwargs):
        clock.advance(0.8)
        raise requests.ConnectionError("offline")

    monkeypatch.setattr("jevloop.client.requests.post", post)
    monkeypatch.setattr("jevloop.client.random.uniform", lambda *_: 0.0)
    with pytest.raises(DecisionLateResponseError, match="retry backoff") as raised:
        _post(
            "https://example.test",
            {},
            {},
            1.0,
            monotonic=clock.monotonic,
            sleep=clock.advance,
        )
    assert raised.value.measured_latency_ms == 800.0


def test_post_accepts_on_time_success_and_reports_total_latency(monkeypatch):
    clock = _Clock()

    def post(*args, **kwargs):
        clock.advance(0.4)
        return _Response({"ok": True})

    monkeypatch.setattr("jevloop.client.requests.post", post)
    payload, latency_s = _post("https://example.test", {}, {}, 1.0, monotonic=clock.monotonic)
    assert payload == {"ok": True}
    assert latency_s == pytest.approx(0.4)

import pytest
from jevloop.battery import build_questions, run_battery, validate_answers
from jevloop.client import DecisionClientError, MockDecisionClient, resolve_decision_client
from jevloop.split import SplitViolation, assert_split_respected, render_split_table


def test_real_battery_has_exact_allow_list_and_seven_questions():
    q=build_questions(); assert len(q)==7; assert_split_respected(q)


def test_unknown_question_rejected():
    q=build_questions(); q["calculate_vwap"]={"type":"noul","instructions":"judge"}
    with pytest.raises(SplitViolation): assert_split_respected(q)


def test_arithmetic_delegation_rejected():
    q=build_questions(); q["regime"]["instructions"]="calculate the exact value"
    with pytest.raises(SplitViolation): assert_split_respected(q)


def test_split_table_names_both_owners():
    text=render_split_table(); assert "DETERMINISTIC" in text and "PROBABILISTIC" in text


def test_mock_shape_validates():
    client=MockDecisionClient(seed=1)
    state={"return_1m":0.0,"imbalance":0.0,"spread_bps":2,"inventory_utilization":0.0}
    answers,meta=run_battery(client,state,1)
    assert len(answers)==7 and meta["route"]=="MOCK"


def test_answer_probability_keys_must_match():
    client=MockDecisionClient(seed=1)
    answers,_=client.ask({"return_1m":0.0,"imbalance":0.0,"spread_bps":2,"inventory_utilization":0.0},build_questions(),1)
    answers["direction"]["probabilities"].pop("up")
    with pytest.raises(ValueError): validate_answers(answers)


def test_answer_probability_range_checked():
    client=MockDecisionClient(seed=1)
    answers,_=client.ask({"return_1m":0.0,"imbalance":0.0,"spread_bps":2,"inventory_utilization":0.0},build_questions(),1)
    answers["toxic_flow"]["noul"]=1.5
    with pytest.raises(ValueError): validate_answers(answers)


def test_missing_provider_key_fails_closed(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY",raising=False); monkeypatch.delenv("AI_GATEWAY_API_KEY",raising=False)
    with pytest.raises(DecisionClientError): resolve_decision_client()


def test_direct_provider_requires_explicit_model(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY","x"); monkeypatch.delenv("TYPESAFE_MODEL",raising=False); monkeypatch.delenv("AI_GATEWAY_API_KEY",raising=False)
    with pytest.raises(DecisionClientError): resolve_decision_client()


def test_mock_requires_explicit_flag(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY",raising=False); monkeypatch.delenv("AI_GATEWAY_API_KEY",raising=False)
    assert resolve_decision_client(mock=True).name=="MOCK"

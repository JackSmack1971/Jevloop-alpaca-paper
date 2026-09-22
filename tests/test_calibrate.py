import math
from jevloop.calibrate import (
    Observation, accuracy, adaptive_classwise_ece, confidence_ece, multiclass_brier,
    negative_log_loss, pair_observations, summarize
)


def test_perfect_multiclass_brier_is_zero():
    obs=[Observation({"up":1,"down":0,"neutral":0},"up",0)]
    assert multiclass_brier(obs)==0


def test_uniform_three_class_brier_is_two_thirds():
    obs=[Observation({"up":1/3,"down":1/3,"neutral":1/3},"down",0)]
    assert multiclass_brier(obs)==pytest.approx(2/3)


def test_pairing_uses_direction_probability_vector_and_time_horizon():
    ticks=[
      {"ts":0,"mid":100,"symbol":"BTC/USD","direction_probabilities":{"up":.8,"down":.1,"neutral":.1},"route":"real"},
      {"ts":5,"mid":100.01,"symbol":"BTC/USD"},
      {"ts":15,"mid":101,"symbol":"BTC/USD"},
    ]
    obs=pair_observations(ticks,symbol="BTC/USD",horizon_seconds=10,neutral_bps=1)
    assert len(obs)==1 and obs[0].outcome=="up" and obs[0].probs["up"]==.8


def test_mock_excluded_by_default():
    ticks=[{"ts":0,"mid":100,"symbol":"BTC/USD","direction_probabilities":{"up":.8,"down":.1,"neutral":.1},"route":"MOCK"},{"ts":20,"mid":101,"symbol":"BTC/USD"}]
    assert pair_observations(ticks,symbol=None,horizon_seconds=10,neutral_bps=1)==[]


def test_small_sample_marked_inconclusive():
    obs=[Observation({"up":.8,"down":.1,"neutral":.1},"up",i) for i in range(20)]
    report=summarize(obs,bootstrap_draws=0)
    assert report["status"]=="DESCRIPTIVE_ONLY"
    assert report["support_warnings"]


def test_metrics_finite_for_valid_sample():
    obs=[Observation({"up":.8,"down":.1,"neutral":.1},"up",0),Observation({"up":.2,"down":.7,"neutral":.1},"down",1)]
    assert accuracy(obs)==1
    assert math.isfinite(negative_log_loss(obs))
    ece,_=confidence_ece(obs); assert 0<=ece<=1

import pytest


def test_sample_climatology_is_reported_as_reference_not_readiness_claim():
    obs = [
        Observation({"up": .7, "down": .2, "neutral": .1}, "up", 0),
        Observation({"up": .2, "down": .7, "neutral": .1}, "down", 1),
        Observation({"up": .2, "down": .2, "neutral": .6}, "neutral", 2),
    ]
    report = summarize(obs, bootstrap_draws=0)
    assert report["sample_climatology_probabilities"] == pytest.approx({"up": 1/3, "down": 1/3, "neutral": 1/3})
    assert report["status"] == "DESCRIPTIVE_ONLY"


def test_adaptive_classwise_ece_is_bounded():
    obs = [
        Observation({"up": .8, "down": .1, "neutral": .1}, "up", 0),
        Observation({"up": .2, "down": .7, "neutral": .1}, "down", 1),
        Observation({"up": .2, "down": .2, "neutral": .6}, "neutral", 2),
    ]
    ece, table = adaptive_classwise_ece(obs, bins=3)
    assert 0 <= ece <= 1
    assert set(table) == {"up", "down", "neutral"}

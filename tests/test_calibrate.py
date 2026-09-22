import math
import pytest
from jevloop.calibrate import (
    CohortIdentity, Observation, accuracy, adaptive_classwise_ece, confidence_ece,
    main, moving_block_brier_ci, multiclass_brier, negative_log_loss, pair_observations, summarize
)


COHORT = CohortIdentity("run-a", "alpaca-market-data", "responses", "model-a",
                        "config-a", "limits", "battery")


def _obs(probs, outcome, ts, symbol="BTC/USD"):
    return Observation(probs, outcome, ts, symbol, COHORT)


def test_observation_rejects_empty_symbol_identity():
    with pytest.raises(ValueError, match="symbol must be non-empty"):
        _obs(PROBS, "up", 0, "  ")


def test_cli_requires_symbol_when_bootstrap_is_requested(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--bootstrap-draws", "10"])
    assert exc.value.code == 2
    assert "--symbol is required" in capsys.readouterr().err


def test_perfect_multiclass_brier_is_zero():
    obs=[_obs({"up":1,"down":0,"neutral":0},"up",0)]
    assert multiclass_brier(obs)==0


def test_uniform_three_class_brier_is_two_thirds():
    obs=[_obs({"up":1/3,"down":1/3,"neutral":1/3},"down",0)]
    assert multiclass_brier(obs)==pytest.approx(2/3)


def test_pairing_uses_direction_probability_vector_and_time_horizon():
    ticks=[_row(0, probs=PROBS), _row(5, mid=100.01), _row(15, mid=101)]
    obs=pair_observations(ticks,symbol="BTC/USD",horizon_seconds=10,neutral_bps=1)
    assert len(obs)==1 and obs[0].outcome=="up" and obs[0].probs["up"]==.8


def test_mock_excluded_by_default():
    ticks=[_row(0, probs=PROBS, mode="simulation", source="seeded-synthetic", route="MOCK"),
           _row(10, mode="simulation", source="seeded-synthetic", route="MOCK")]
    assert not pair_observations(ticks,symbol=None,horizon_seconds=10,neutral_bps=1)


def test_small_sample_marked_inconclusive():
    obs=[_obs({"up":.8,"down":.1,"neutral":.1},"up",i) for i in range(20)]
    report=summarize(obs,bootstrap_draws=0)
    assert report["status"]=="DESCRIPTIVE_ONLY"
    assert report["support_warnings"]


def test_metrics_finite_for_valid_sample():
    obs=[_obs({"up":.8,"down":.1,"neutral":.1},"up",0),_obs({"up":.2,"down":.7,"neutral":.1},"down",1)]
    assert accuracy(obs)==1
    assert math.isfinite(negative_log_loss(obs))
    ece, _ = confidence_ece(obs)
    assert 0 <= ece <= 1

def test_sample_climatology_is_reported_as_reference_not_readiness_claim():
    obs = [
        _obs({"up": .7, "down": .2, "neutral": .1}, "up", 0),
        _obs({"up": .2, "down": .7, "neutral": .1}, "down", 1),
        _obs({"up": .2, "down": .2, "neutral": .6}, "neutral", 2),
    ]
    report = summarize(obs, bootstrap_draws=0)
    assert report["sample_climatology_probabilities"] == pytest.approx({"up": 1/3, "down": 1/3, "neutral": 1/3})
    assert report["status"] == "DESCRIPTIVE_ONLY"


def test_adaptive_classwise_ece_is_bounded():
    obs = [
        _obs({"up": .8, "down": .1, "neutral": .1}, "up", 0),
        _obs({"up": .2, "down": .7, "neutral": .1}, "down", 1),
        _obs({"up": .2, "down": .2, "neutral": .6}, "neutral", 2),
    ]
    ece, table = adaptive_classwise_ece(obs, bins=3)
    assert 0 <= ece <= 1
    assert set(table) == {"up", "down", "neutral"}


def test_interleaved_symbols_cannot_produce_pooled_moving_block_interval():
    observations = [
        _obs(PROBS, "up", i, "BTC/USD" if i % 2 == 0 else "ETH/USD")
        for i in range(24)
    ]
    with pytest.raises(ValueError, match="exactly one symbol"):
        moving_block_brier_ci(observations, block_size=2, draws=20)

    report = summarize(observations, bootstrap_draws=0)
    assert report["n"] == 24
    assert report["brier_95pct_moving_block_bootstrap_ci"] is None
    assert report["moving_block_interval_metadata"]["status"] == "omitted"
    assert report["moving_block_interval_metadata"]["selected_symbol"] is None


def test_single_symbol_moving_block_interval_is_deterministic_and_described():
    observations = [
        _obs({"up": .6 + (i % 3) / 10, "down": .3 - (i % 3) / 10,
              "neutral": .1}, "up" if i % 2 else "down", i)
        for i in range(30)
    ]
    first = moving_block_brier_ci(observations, block_size=3, draws=100, seed=91)
    second = moving_block_brier_ci(observations, block_size=3, draws=100, seed=91)
    assert first == second

    report = summarize(observations, block_size=3, bootstrap_draws=100,
                       selected_symbol="BTC/USD")
    metadata = report["moving_block_interval_metadata"]
    assert metadata["selected_symbol"] == "BTC/USD"
    assert metadata["cohort_identity"]["run_id"] == "run-a"
    assert metadata["block_size"] == 3
    assert metadata["sensitivity_values"] == report["bootstrap_block_sensitivity"]
    assert metadata["observation_count"] == 30


def _row(ts, *, run="run-a", source="alpaca-market-data", mode="dry-real",
         route="responses", model="model-a", config="config-a", probs=None,
         mid=100, provider_ts=None):
    return {
        "schema_version": 3, "package_version": "test", "run_id": run,
        "runtime_mode": mode, "data_source": source, "symbol": "BTC/USD",
        "git_commit": "abc", "process_started_at": 0, "skill_digest": "skill",
        "battery_schema_digest": "battery", "strategy_config_digest": config,
        "limits_digest": "limits", "provider_route": route, "provider_model": model,
        "route": route, "ts": ts, "mid": mid,
        "quote_provider_ts": ts if provider_ts is None else provider_ts,
        "direction_probabilities": probs,
    }


PROBS = {"up": .8, "down": .1, "neutral": .1}


def _pair(rows, **kwargs):
    return pair_observations(
        rows, symbol="BTC/USD", horizon_seconds=10, neutral_bps=1,
        max_label_lag_seconds=5, **kwargs,
    )


def test_schema_v3_pair_records_horizon_and_actual_lag():
    result = _pair([_row(0, probs=PROBS), _row(12, mid=101)])
    assert len(result) == 1
    assert result[0].requested_horizon_seconds == 10
    assert result[0].actual_label_lag_seconds == 12
    assert result.exclusion_counts == {reason: 0 for reason in result.exclusion_counts}


@pytest.mark.parametrize("field,value", [("run", "run-b"), ("model", "model-b"),
                                          ("config", "config-b")])
def test_incompatible_cohort_label_is_rejected(field, value):
    result = _pair([_row(0, probs=PROBS), _row(10, **{field: value})])
    assert not result
    assert result.exclusion_counts["cohort_mismatch"] == 1


def test_cross_source_label_is_rejected():
    result = _pair([_row(0, probs=PROBS), _row(10, source="other-real-source")])
    assert not result
    assert result.exclusion_counts["source_mismatch"] >= 1


@pytest.mark.parametrize("forecast_mode,label_mode,simulation_only", [
    ("simulation", "dry-real", False),
    ("dry-real", "simulation", True),
])
def test_real_and_simulation_evidence_never_mix(forecast_mode, label_mode, simulation_only):
    forecast_source = "seeded-synthetic" if forecast_mode == "simulation" else "alpaca-market-data"
    label_source = "seeded-synthetic" if label_mode == "simulation" else "alpaca-market-data"
    rows = [
        _row(0, probs=PROBS, mode=forecast_mode, source=forecast_source,
             route="MOCK" if forecast_mode == "simulation" else "responses"),
        _row(10, mode=label_mode, source=label_source,
             route="MOCK" if label_mode == "simulation" else "responses"),
    ]
    result = _pair(rows, simulation_only=simulation_only)
    assert not result
    assert result.exclusion_counts["source_mismatch"] >= 1


def test_stale_real_label_is_rejected():
    result = _pair([_row(0, probs=PROBS), _row(10, provider_ts=4)],
                   max_provider_age_seconds=5)
    assert not result
    assert result.exclusion_counts["stale_label"] == 1


def test_label_lag_and_freshness_exact_boundaries_are_inclusive():
    result = _pair([_row(0, probs=PROBS), _row(15, mid=101, provider_ts=10)],
                   max_provider_age_seconds=5)
    assert len(result) == 1
    assert result[0].actual_label_lag_seconds == 15


def test_excessive_label_lag_is_rejected():
    result = _pair([_row(0, probs=PROBS), _row(15.001, mid=101)])
    assert not result
    assert result.exclusion_counts["excessive_lag"] == 1


def test_legacy_and_missing_provider_timestamp_have_stable_counts():
    label = _row(10)
    label["quote_provider_ts"] = None
    result = _pair([{"schema_version": 2}, _row(0, probs=PROBS), label])
    assert result.exclusion_counts["legacy_schema"] == 1
    assert result.exclusion_counts["missing_timestamps"] == 1

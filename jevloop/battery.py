"""Seven bounded Jev questions plus strict response validation."""
from __future__ import annotations

import math
from collections.abc import Mapping
from numbers import Real

from .client import DecisionSchemaError
from .split import assert_split_respected

PROBABILITY_SUM_TOLERANCE = 0.02


def build_questions() -> dict:
    questions = {
        "regime": {
            "type": "choice",
            "instructions": "Which market regime best describes the supplied state?",
            "criteria": {
                "trending": "directional persistence dominates",
                "mean_reverting": "short-horizon reversals dominate",
                "high_vol": "volatility dominates without a stable directional regime",
                "crisis": "dislocated or unusually unstable conditions",
            },
        },
        "direction": {
            "type": "choice",
            "instructions": "What is the directional price bias over the next few decision intervals?",
            "criteria": {"up": None, "down": None, "neutral": None},
        },
        "toxic_flow": {
            "type": "noul",
            "instructions": "Does the observed flow look more like informed/adverse flow than ordinary noise?",
        },
        "liquidity_stressed": {
            "type": "noul",
            "instructions": "Does the supplied spread, depth, and activity state indicate liquidity stress?",
        },
        "quote_environment": {
            "type": "score",
            "instructions": "How suitable is this state for passive-intent liquidity provision?",
            "criteria": ["Do not quote", "Marginal", "Standard", "Excellent"],
        },
        "inventory_pressure": {
            "type": "score",
            "instructions": "How urgent is reducing the current normalized inventory exposure?",
            "criteria": ["None", "Mild", "High", "Reduce now"],
        },
        "execution_health": {
            "type": "score",
            "instructions": "How healthy are recent execution conditions given rejects, fills, slippage, and latency?",
            "criteria": ["Broken", "Degraded", "Normal", "Optimal"],
        },
    }
    assert_split_respected(questions)
    return questions


def _finite01(value, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise DecisionSchemaError(f"{label} must be numeric")
    x = float(value)
    if not math.isfinite(x) or not 0.0 <= x <= 1.0:
        raise DecisionSchemaError(f"{label} must be finite and in [0,1]")
    return x


def _validate_probabilities(probabilities: object, expected: set[str], label: str) -> None:
    if not isinstance(probabilities, Mapping):
        raise DecisionSchemaError(f"answer {label!r} probabilities must be a mapping")
    if set(probabilities) != expected:
        raise DecisionSchemaError(f"answer {label!r} probability keys do not match allowed choices")
    vals = [_finite01(v, f"{label}.{k}") for k, v in probabilities.items()]
    if abs(sum(vals) - 1.0) > PROBABILITY_SUM_TOLERANCE:
        raise DecisionSchemaError(
            f"answer {label!r} probabilities must sum to 1 "
            f"within tolerance {PROBABILITY_SUM_TOLERANCE}"
        )


def validate_answers(answers: object) -> None:
    questions = build_questions()
    if not isinstance(answers, Mapping):
        raise DecisionSchemaError("decision answers must be a mapping")
    if set(answers) != set(questions):
        raise DecisionSchemaError("decision answer keys must exactly match requested question keys")
    for key, q in questions.items():
        ans = answers[key]
        if not isinstance(ans, Mapping):
            raise DecisionSchemaError(f"answer {key!r} must be a mapping")
        if ans.get("type") != q["type"]:
            raise DecisionSchemaError(f"answer {key!r} has an unexpected type")
        if q["type"] == "noul":
            _finite01(ans.get("noul"), key)
        elif q["type"] == "choice":
            options = set(q["criteria"])
            if ans.get("choice") not in options:
                raise DecisionSchemaError(f"answer {key!r} choice is not allowed")
            _validate_probabilities(ans.get("probabilities"), options, key)
            _finite01(ans.get("confidence"), f"{key}.confidence")
        elif q["type"] == "score":
            n = len(q["criteria"])
            raw_score = ans.get("score")
            if isinstance(raw_score, bool) or not isinstance(raw_score, Real):
                raise DecisionSchemaError(f"answer {key!r} score must be numeric")
            score = float(raw_score)
            if not math.isfinite(score) or not 0 <= score <= n - 1:
                raise DecisionSchemaError(f"answer {key!r} score must be finite and in range")
            expected = {str(i) for i in range(n)}
            _validate_probabilities(ans.get("probabilities"), expected, key)
            _finite01(ans.get("confidence"), f"{key}.confidence")


def run_battery(client, state: dict, timeout: float) -> tuple[dict, dict]:
    questions = build_questions()
    result = client.ask(state=state, questions=questions, timeout=timeout)
    try:
        answers, meta = result
    except (TypeError, ValueError) as exc:
        raise DecisionSchemaError("decision client result must contain answers and metadata") from exc
    if not isinstance(meta, Mapping):
        raise DecisionSchemaError("decision response metadata must be a mapping")
    validate_answers(answers)
    return answers, meta

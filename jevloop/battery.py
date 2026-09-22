"""Seven bounded Jev questions plus strict response validation."""
from __future__ import annotations

import math
from .split import assert_split_respected


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
    x = float(value)
    if not math.isfinite(x) or not 0.0 <= x <= 1.0:
        raise ValueError(f"{label} must be finite in [0,1]")
    return x


def _validate_probabilities(probabilities: dict, expected: set[str], label: str) -> None:
    if set(probabilities) != expected:
        raise ValueError(f"{label} probability keys do not match criteria")
    vals = [_finite01(v, f"{label}.{k}") for k, v in probabilities.items()]
    if abs(sum(vals) - 1.0) > 0.02:
        raise ValueError(f"{label} probabilities sum to {sum(vals):.4f}, expected approximately 1")


def validate_answers(answers: dict) -> None:
    questions = build_questions()
    if set(answers) != set(questions):
        raise ValueError("battery response keys do not exactly match request keys")
    for key, q in questions.items():
        ans = answers[key]
        if ans.get("type") != q["type"]:
            raise ValueError(f"answer {key!r} has wrong type")
        if q["type"] == "noul":
            _finite01(ans.get("noul"), key)
        elif q["type"] == "choice":
            options = set(q["criteria"])
            if ans.get("choice") not in options:
                raise ValueError(f"answer {key!r} chose an unknown option")
            _validate_probabilities(ans.get("probabilities", {}), options, key)
            _finite01(ans.get("confidence"), f"{key}.confidence")
        elif q["type"] == "score":
            n = len(q["criteria"])
            score = float(ans.get("score"))
            if not math.isfinite(score) or not 0 <= score <= n - 1:
                raise ValueError(f"answer {key!r} score out of range")
            expected = {str(i) for i in range(n)}
            _validate_probabilities(ans.get("probabilities", {}), expected, key)
            _finite01(ans.get("confidence"), f"{key}.confidence")


def run_battery(client, state: dict, timeout: float) -> tuple[dict, dict]:
    questions = build_questions()
    answers, meta = client.ask(state=state, questions=questions, timeout=timeout)
    validate_answers(answers)
    return answers, meta

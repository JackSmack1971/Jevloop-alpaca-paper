"""Enforce the deterministic/probabilistic boundary."""
from __future__ import annotations

ALLOWED_QUESTIONS: dict[str, str] = {
    "regime": "fuzzy market regime",
    "direction": "short-horizon directional bias",
    "toxic_flow": "whether observed flow appears informed/toxic",
    "liquidity_stressed": "whether current liquidity appears stressed",
    "quote_environment": "quality of the current state for passive quoting",
    "inventory_pressure": "urgency of reducing current inventory",
    "execution_health": "quality of recent execution conditions",
}

_ARITHMETIC_MARKERS = (
    "calculate", "compute the", "sum of", "average of", "mean of", "multiply", "divide by",
    "exact value", "precise value", "vwap", "standard deviation", "variance", "spread in bps",
    "mid price", "drawdown percentage", "position value",
)


class SplitViolation(ValueError):
    pass


def _text(value) -> str:
    if isinstance(value, dict):
        return " ".join(_text(v) for v in value.values())
    if isinstance(value, list):
        return " ".join(_text(v) for v in value)
    return str(value)


def assert_split_respected(questions: dict) -> None:
    if set(questions) != set(ALLOWED_QUESTIONS):
        missing = set(ALLOWED_QUESTIONS) - set(questions)
        extra = set(questions) - set(ALLOWED_QUESTIONS)
        raise SplitViolation(f"battery keys differ from allow-list; missing={sorted(missing)}, extra={sorted(extra)}")
    for qid, q in questions.items():
        text = _text(q.get("instructions", "")).lower()
        for marker in _ARITHMETIC_MARKERS:
            if marker in text:
                raise SplitViolation(f"question {qid!r} appears to delegate deterministic arithmetic: {marker!r}")


def render_split_table() -> str:
    return """DETERMINISTIC (code)                 PROBABILISTIC (Jev)\n--------------------                 -------------------\ntimestamps / returns / volatility    regime + direction judgments\nspread / depth / inventory USD       toxic-flow + liquidity judgments\naccount / position reconciliation    quote-environment judgment\npolicy / sizing / risk / execution   inventory-pressure judgment\norder ownership / cancellation       execution-health judgment"""

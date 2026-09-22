"""Offline probability diagnostics for logged Jev direction forecasts.

The report is descriptive evidence, not a deployment-readiness or profitability gate.
It uses proper scoring rules, a sample-climatology reference, calibration diagnostics,
and block-bootstrap uncertainty that preserves short-range serial dependence.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
from dataclasses import dataclass
from pathlib import Path

LOG_DIR = Path(os.getenv("JEV_LOOP_HOME", str(Path.home() / ".jev-loop")))
LOG_FILE = LOG_DIR / "log.jsonl"
CLASSES = ("up", "down", "neutral")


@dataclass(frozen=True)
class Observation:
    probs: dict[str, float]
    outcome: str
    ts: float


def load_ticks(path: Path = LOG_FILE) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            rows.append(row)
    return rows


def _valid_probs(value) -> dict[str, float] | None:
    if not isinstance(value, dict) or set(value) != set(CLASSES):
        return None
    try:
        probs = {k: float(value[k]) for k in CLASSES}
    except (TypeError, ValueError):
        return None
    if any((not math.isfinite(v) or v < 0 or v > 1) for v in probs.values()):
        return None
    total = sum(probs.values())
    if total <= 0 or abs(total - 1.0) > 0.03:
        return None
    return {k: v / total for k, v in probs.items()}


def pair_observations(
    ticks: list[dict],
    *,
    symbol: str | None,
    horizon_seconds: float,
    neutral_bps: float,
    include_mock: bool = False,
) -> list[Observation]:
    rows = [r for r in ticks if r.get("mid") is not None and r.get("ts") is not None]
    if symbol:
        rows = [r for r in rows if str(r.get("symbol", "")).upper() == symbol.upper()]
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(str(row.get("symbol") or ""), []).append(row)

    observations: list[Observation] = []
    for group_rows in groups.values():
        group_rows.sort(key=lambda r: float(r["ts"]))
        j = 0
        for i, row in enumerate(group_rows):
            probs = _valid_probs(row.get("direction_probabilities"))
            if probs is None:
                continue
            if not include_mock and str(row.get("route") or "").upper() == "MOCK":
                continue
            target_ts = float(row["ts"]) + horizon_seconds
            j = max(j, i + 1)
            while j < len(group_rows) and float(group_rows[j]["ts"]) < target_ts:
                j += 1
            if j >= len(group_rows):
                break
            start = float(row["mid"])
            end = float(group_rows[j]["mid"])
            if start <= 0:
                continue
            return_bps = (end / start - 1.0) * 10_000
            if return_bps > neutral_bps:
                outcome = "up"
            elif return_bps < -neutral_bps:
                outcome = "down"
            else:
                outcome = "neutral"
            observations.append(Observation(probs, outcome, float(row["ts"])))
    observations.sort(key=lambda obs: obs.ts)
    return observations


def multiclass_brier(observations: list[Observation]) -> float:
    if not observations:
        return float("nan")
    return sum(
        sum((obs.probs[k] - (1.0 if obs.outcome == k else 0.0)) ** 2 for k in CLASSES)
        for obs in observations
    ) / len(observations)


def negative_log_loss(observations: list[Observation]) -> float:
    if not observations:
        return float("nan")
    eps = 1e-12
    return -sum(math.log(max(eps, obs.probs[obs.outcome])) for obs in observations) / len(observations)


def accuracy(observations: list[Observation]) -> float:
    if not observations:
        return float("nan")
    return sum(max(obs.probs, key=obs.probs.get) == obs.outcome for obs in observations) / len(observations)


def sample_climatology(observations: list[Observation]) -> dict[str, float]:
    if not observations:
        return {k: float("nan") for k in CLASSES}
    n = len(observations)
    return {k: sum(obs.outcome == k for obs in observations) / n for k in CLASSES}


def constant_forecast_brier(observations: list[Observation], probs: dict[str, float]) -> float:
    synthetic = [Observation(probs, obs.outcome, obs.ts) for obs in observations]
    return multiclass_brier(synthetic)


def confidence_ece(observations: list[Observation], bins: int = 10) -> tuple[float, list[dict]]:
    """Legacy top-label, equal-width ECE retained as a diagnostic only."""
    if not observations:
        return float("nan"), []
    groups: list[list[tuple[float, int]]] = [[] for _ in range(bins)]
    for obs in observations:
        label = max(obs.probs, key=obs.probs.get)
        conf = obs.probs[label]
        idx = min(bins - 1, int(conf * bins))
        groups[idx].append((conf, int(label == obs.outcome)))
    rows: list[dict] = []
    ece = 0.0
    n = len(observations)
    for i, group in enumerate(groups):
        if group:
            mean_conf = sum(x for x, _ in group) / len(group)
            empirical = sum(y for _, y in group) / len(group)
            ece += len(group) / n * abs(mean_conf - empirical)
        else:
            mean_conf = empirical = None
        rows.append({
            "bin": f"{i/bins:.1f}-{(i+1)/bins:.1f}",
            "n": len(group),
            "mean_confidence": mean_conf,
            "empirical_accuracy": empirical,
        })
    return ece, rows


def adaptive_classwise_ece(
    observations: list[Observation], bins: int = 10
) -> tuple[float, dict[str, list[dict]]]:
    """Classwise ECE using equal-count bins to reduce empty-bin sensitivity."""
    if not observations:
        return float("nan"), {}
    bins = max(1, min(bins, len(observations)))
    by_class: dict[str, list[dict]] = {}
    class_eces: list[float] = []
    for klass in CLASSES:
        points = sorted((obs.probs[klass], int(obs.outcome == klass)) for obs in observations)
        rows: list[dict] = []
        weighted_error = 0.0
        n = len(points)
        for i in range(bins):
            lo = i * n // bins
            hi = (i + 1) * n // bins
            group = points[lo:hi]
            if not group:
                continue
            mean_prob = sum(p for p, _ in group) / len(group)
            event_rate = sum(y for _, y in group) / len(group)
            weighted_error += len(group) / n * abs(mean_prob - event_rate)
            rows.append({
                "n": len(group),
                "min_probability": group[0][0],
                "max_probability": group[-1][0],
                "mean_probability": mean_prob,
                "event_rate": event_rate,
            })
        by_class[klass] = rows
        class_eces.append(weighted_error)
    return sum(class_eces) / len(class_eces), by_class


def _default_block_size(n: int) -> int:
    # Conservative transparent heuristic, not an estimated optimum. Sensitivity is
    # reported around it rather than presenting a single block length as authoritative.
    return max(2, int(round(n ** (1.0 / 3.0)))) if n > 1 else 1


def moving_block_brier_ci(
    observations: list[Observation], *, block_size: int, draws: int = 1000, seed: int = 17
) -> tuple[float, float] | None:
    """Approximate 95% CI using a moving-block bootstrap for serial dependence."""
    n = len(observations)
    if block_size < 1:
        raise ValueError("block_size must be positive")
    if n < max(20, block_size * 2) or draws <= 0:
        return None
    rng = random.Random(seed)
    scores: list[float] = []
    starts = list(range(0, n - block_size + 1))
    for _ in range(draws):
        sample: list[Observation] = []
        while len(sample) < n:
            start = rng.choice(starts)
            sample.extend(observations[start : start + block_size])
        scores.append(multiclass_brier(sample[:n]))
    scores.sort()
    return scores[int(0.025 * (draws - 1))], scores[int(0.975 * (draws - 1))]


def summarize(
    observations: list[Observation], *, block_size: int | None = None, bootstrap_draws: int = 1000
) -> dict:
    brier = multiclass_brier(observations)
    uniform_probs = {k: 1.0 / len(CLASSES) for k in CLASSES}
    uniform_brier = constant_forecast_brier(observations, uniform_probs) if observations else float("nan")
    climatology = sample_climatology(observations)
    climatology_brier = constant_forecast_brier(observations, climatology) if observations else float("nan")
    top_ece, top_table = confidence_ece(observations)
    classwise_ece, classwise_table = adaptive_classwise_ece(observations)
    counts = {k: sum(obs.outcome == k for obs in observations) for k in CLASSES}

    resolved_block = block_size if block_size is not None else _default_block_size(len(observations))
    if resolved_block <= 0:
        raise ValueError("block_size must be positive")
    ci = moving_block_brier_ci(observations, block_size=resolved_block, draws=bootstrap_draws)
    sensitivity: dict[str, list[float] | None] = {}
    for candidate in sorted({max(1, resolved_block // 2), resolved_block, resolved_block * 2}):
        candidate_ci = moving_block_brier_ci(observations, block_size=candidate, draws=bootstrap_draws)
        sensitivity[str(candidate)] = list(candidate_ci) if candidate_ci else None

    support_warnings: list[str] = []
    if len(observations) < 100:
        support_warnings.append("fewer than 100 paired observations")
    sparse = [k for k, count in counts.items() if count < 10]
    if sparse:
        support_warnings.append("fewer than 10 outcomes for: " + ", ".join(sparse))

    return {
        "n": len(observations),
        "status": "DESCRIPTIVE_ONLY",
        "support_warnings": support_warnings,
        "multiclass_brier": brier,
        "uniform_brier": uniform_brier,
        "sample_climatology_probabilities": climatology,
        "sample_climatology_brier": climatology_brier,
        "brier_improvement_vs_uniform": uniform_brier - brier if observations else float("nan"),
        "brier_improvement_vs_sample_climatology": climatology_brier - brier if observations else float("nan"),
        "brier_95pct_moving_block_bootstrap_ci": list(ci) if ci else None,
        "bootstrap_block_size": resolved_block,
        "bootstrap_block_size_method": "explicit" if block_size is not None else "n^(1/3) transparent heuristic",
        "bootstrap_block_sensitivity": sensitivity,
        "negative_log_loss": negative_log_loss(observations),
        "accuracy": accuracy(observations),
        "top_label_equal_width_ece": top_ece,
        "adaptive_classwise_ece": classwise_ece,
        "outcome_counts": counts,
        "top_label_reliability": top_table,
        "classwise_reliability": classwise_table,
        "interpretation": (
            "Probability-quality diagnostics only. Do not infer trading profitability, deployment readiness, "
            "or calibration from one metric or one sample. Preserve temporal out-of-sample evaluation and "
            "report sensitivity to horizon, neutral band, and bootstrap block length."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jev-loop calibrate")
    parser.add_argument("--symbol")
    parser.add_argument("--horizon-seconds", type=float, default=15.0)
    parser.add_argument("--neutral-bps", type=float, default=1.0)
    parser.add_argument("--include-mock", action="store_true")
    parser.add_argument("--block-size", type=int, default=None, help="moving-block length; default reports an n^(1/3) heuristic plus sensitivity")
    parser.add_argument("--bootstrap-draws", type=int, default=1000)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.horizon_seconds <= 0 or args.neutral_bps < 0:
        parser.error("horizon must be positive and neutral band non-negative")
    if args.block_size is not None and args.block_size <= 0:
        parser.error("block size must be positive")

    ticks = load_ticks()
    observations = pair_observations(
        ticks,
        symbol=args.symbol,
        horizon_seconds=args.horizon_seconds,
        neutral_bps=args.neutral_bps,
        include_mock=args.include_mock,
    )
    if not observations:
        print("no eligible real-provider direction probability observations")
        return 2
    report = summarize(observations, block_size=args.block_size, bootstrap_draws=args.bootstrap_draws)
    report.update({"horizon_seconds": args.horizon_seconds, "neutral_bps": args.neutral_bps, "symbol": args.symbol})
    if args.json:
        print(json.dumps(report, indent=2, allow_nan=False))
    else:
        print(f"status: {report['status']} (n={report['n']})")
        print(f"multiclass Brier: {report['multiclass_brier']:.4f}")
        print(f"sample-climatology Brier: {report['sample_climatology_brier']:.4f}")
        print(f"Brier improvement vs sample climatology: {report['brier_improvement_vs_sample_climatology']:.4f}")
        print(f"negative log loss: {report['negative_log_loss']:.4f}")
        print(f"accuracy: {report['accuracy']:.3f}")
        print(f"top-label equal-width ECE (diagnostic): {report['top_label_equal_width_ece']:.3f}")
        print(f"adaptive classwise ECE: {report['adaptive_classwise_ece']:.3f}")
        if report["brier_95pct_moving_block_bootstrap_ci"]:
            lo, hi = report["brier_95pct_moving_block_bootstrap_ci"]
            print(f"Brier 95% moving-block bootstrap CI: [{lo:.4f}, {hi:.4f}] (block={report['bootstrap_block_size']})")
        print("outcomes:", report["outcome_counts"])
        for warning in report["support_warnings"]:
            print(f"support warning: {warning}")
        print("interpretation:", report["interpretation"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Fully offline component simulation. No network and no broker orders."""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import time
from pathlib import Path

from .assets import classify_symbol
from .battery import run_battery
from .client import MockDecisionClient
from .ladder import select_rung
from .limits import Limits
from .policy import compose_action
from .state import RuntimeState, TradeTick, build_snapshot, observe_trades

LOG_DIR = Path(os.getenv("JEV_LOOP_HOME", str(Path.home() / ".jev-loop")))
LOG_FILE = LOG_DIR / "log.jsonl"
LATEST_FILE = LOG_DIR / "latest.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jev-loop simulate")
    parser.add_argument("--ticks", type=int, default=60)
    parser.add_argument("--symbol", default="BTC/USD")
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--no-log", action="store_true")
    args = parser.parse_args(argv)
    if args.ticks < 1:
        parser.error("--ticks must be >= 1")

    limits = Limits()
    # Synthetic simulation deliberately never claims broker metadata.
    hint = classify_symbol(args.symbol)
    rng = random.Random(args.seed)
    client = MockDecisionClient(seed=args.seed)
    runtime = RuntimeState(account_equity_usd=10_000.0, last_equity_usd=10_000.0, high_water_mark_usd=10_000.0)
    trades: list[TradeTick] = []
    records: list[dict] = []
    base = time.time() - args.ticks * limits.tick_seconds
    price = 100.0

    for i in range(args.ticks):
        ts = base + i * limits.tick_seconds
        shock = rng.gauss(0.0, 0.0008)
        price *= math.exp(shock)
        half_spread = max(0.01, price * 0.0001)
        bid, ask = price - half_spread, price + half_spread
        side = "buy" if shock >= 0 else "sell"
        trade = TradeTick(ts=ts, price=price, size=1.0 + rng.random(), side=side, trade_id=f"sim-{i}")
        trades.append(trade)
        observe_trades(runtime, [trade])
        depth_bias = max(-0.8, min(0.8, shock * 300))
        bid_size = 10.0 * (1 + depth_bias)
        ask_size = 10.0 * (1 - depth_bias)
        snapshot = build_snapshot(
            as_of=ts,
            mid=(bid + ask) / 2,
            best_bid=bid,
            best_ask=ask,
            bid_depth=[(bid, bid_size)],
            ask_depth=[(ask, ask_size)],
            trades=trades,
            runtime=runtime,
            market_data_ts=ts,
            max_position_usd=limits.max_position_usd,
            has_depth=hint.has_depth,
        )
        answers, meta = run_battery(client, snapshot, timeout=1.0)
        action = compose_action(answers, snapshot, limits)
        rung = select_rung(
            risk_kill=False,
            decision_late=False,
            jev_down=False,
            decision_confidence=answers["quote_environment"]["confidence"],
            low_confidence_threshold=limits.low_confidence_threshold,
            execution_health_score=answers["execution_health"]["score"],
            execution_health_floor=limits.execution_health_floor,
        )
        record = {
            "schema_version": 2,
            "tick": i + 1,
            "ts": ts,
            "symbol": hint.symbol,
            "asset_class": hint.asset_class,
            "mid": snapshot["mid"],
            "spread_bps": snapshot["spread_bps"],
            "observed_vwap": snapshot["observed_vwap"],
            "inventory_qty": 0.0,
            "inventory_usd": 0.0,
            "drawdown_pct": 0.0,
            "regime": answers["regime"]["choice"],
            "direction": answers["direction"]["choice"],
            "direction_probabilities": answers["direction"]["probabilities"],
            "toxic_flow": answers["toxic_flow"]["noul"],
            "liquidity_stressed": answers["liquidity_stressed"]["noul"],
            "quote_environment": answers["quote_environment"]["score"],
            "quote_environment_conf": answers["quote_environment"]["confidence"],
            "execution_health": answers["execution_health"]["score"],
            "action": action.kind,
            "action_reason": action.reason,
            "rung": rung.value,
            "execution": "simulation-no-broker",
            "latency_ms": meta["latency_ms"],
            "route": meta["route"],
            "model": meta["model"],
        }
        records.append(record)
        print(f"sim {i+1}: mid={price:.4f} action={action.kind} rung={rung.value}")

    if not args.no_log:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            for row in records:
                fh.write(json.dumps(row, separators=(",", ":")) + "\n")
        payload = {
            "schema_version": 2,
            "generated_at": time.time(),
            "symbol": hint.symbol,
            "ticks": records[-120:],
            "stats": {
                "calls": len(records),
                "avg_ms": sum(r["latency_ms"] for r in records) / len(records),
                "late_count": 0,
                "uptime_s": 0.0,
                "mode": "simulation",
            },
        }
        tmp = LATEST_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        tmp.replace(LATEST_FILE)
    print("SIMULATION_OK: offline wiring exercised; no claim about provider quality or strategy edge")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

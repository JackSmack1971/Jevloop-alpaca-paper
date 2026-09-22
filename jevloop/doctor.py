"""Operational preflight: prove configuration, broker asset authority, and Jev response shape."""
from __future__ import annotations

import argparse
import os

from .battery import run_battery
from .client import DecisionClientError, resolve_decision_client
from .execution.alpaca import AlpacaAPIError, AlpacaConfigError, client_from_env


def _probe_state() -> dict:
    return {
        "as_of": 0.0,
        "mid": 100.0,
        "best_bid": 99.99,
        "best_ask": 100.01,
        "observed_vwap": 100.0,
        "return_1m": 0.0,
        "return_5m": 0.0,
        "return_30m": 0.0,
        "realized_vol_5m": 0.001,
        "spread_bps": 2.0,
        "has_depth": True,
        "bid_depth_3": [[99.99, 1.0]],
        "ask_depth_3": [[100.01, 1.0]],
        "imbalance": 0.0,
        "aggressive_buy_ratio": 0.5,
        "trade_intensity_30s": 1.0,
        "inventory_qty": 0.0,
        "inventory_usd": 0.0,
        "inventory_utilization": 0.0,
        "avg_entry_price": None,
        "position_age_s": 0.0,
        "account_equity_usd": 10_000.0,
        "daily_pnl_usd": 0.0,
        "daily_loss_usd": 0.0,
        "drawdown_pct": 0.0,
        "orders_submitted": 0,
        "orders_rejected": 0,
        "broker_fills_seen": 0,
        "last_10_latencies_ms": [],
        "last_10_slippage_bps": [],
        "data_age_s": 0.0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jev-loop doctor")
    parser.add_argument("--symbol", default=os.getenv("DEFAULT_SYMBOL", "BTC/USD"))
    parser.add_argument("--offline", action="store_true", help="syntax/import checks only; no runtime verification")
    parser.add_argument("--mock", action="store_true", help="use mock only to test local battery plumbing")
    args = parser.parse_args(argv)

    print("jev-loop doctor")
    print("  package imports: OK")
    if args.offline:
        print("  broker: UNVERIFIED_RUNTIME (--offline)")
        print("  Jev provider: UNVERIFIED_RUNTIME (--offline)")
        return 0

    try:
        alpaca = client_from_env(symbol=args.symbol, live=False)
        spec = alpaca.load_asset_spec()
        account = alpaca.get_account()
        market_open = alpaca.is_market_open(spec)
        print(
            f"  broker asset: OK symbol={spec.symbol} class={spec.asset_class} "
            f"status={spec.status} tradable={spec.tradable} source={spec.metadata_source}"
        )
        blocked = bool(account.get("trading_blocked"))
        print(f"  paper account: OK status={account.get('status')} trading_blocked={blocked}")
        if blocked:
            print("  broker: BLOCKED: account reports trading_blocked=true")
            return 2
        print(f"  session: {'open/24x7' if market_open else 'closed'}")
        foreign = alpaca.get_foreign_session_open_orders()
        if foreign:
            ids = [str(o.get("id")) for o in foreign]
            print(
                f"  prior-session orders: NOTICE {len(foreign)} open order(s) from a different "
                f"jev-loop session remain open (ids={ids}); a new run's automatic cancellation is "
                "scoped to its own session and will not touch them -- reconcile manually before "
                "relying on session-owned cancellation to clear the account"
            )
        else:
            print("  prior-session orders: none found")
    except (AlpacaConfigError, AlpacaAPIError, ValueError) as exc:
        print(f"  broker: BLOCKED: {exc}")
        return 2

    try:
        client = resolve_decision_client(mock=args.mock)
        answers, meta = run_battery(client, _probe_state(), timeout=5.0)
        print(
            f"  decision provider: OK route={meta.get('route')} model={meta.get('model')} "
            f"latency_ms={meta.get('latency_ms')} questions={len(answers)}"
        )
        if client.name == "MOCK":
            print("  note: mock proves local wiring only; it is not valid for broker-order execution")
    except (DecisionClientError, ValueError, KeyError, TypeError) as exc:
        print(f"  decision provider: BLOCKED: {exc}")
        return 2

    print("DOCTOR_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

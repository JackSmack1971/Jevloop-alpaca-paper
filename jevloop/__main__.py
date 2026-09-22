"""CLI dispatcher."""
from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env", override=False)

USAGE = "usage: jev-loop <doctor|run|simulate|calibrate|serve|validate-symbol|explain-split> [options]"


def _validate_symbol(argv: list[str]) -> int:
    import argparse
    from .assets import classify_symbol
    from .execution.alpaca import AlpacaAPIError, AlpacaConfigError, client_from_env

    parser = argparse.ArgumentParser(prog="jev-loop validate-symbol")
    parser.add_argument("symbol")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args(argv)
    try:
        hint = classify_symbol(args.symbol)
    except ValueError as exc:
        print(f"invalid symbol syntax: {exc}")
        return 2
    if args.offline:
        print(f"syntax-only: {hint.symbol} class_hint={hint.asset_class}; tradability=UNVERIFIED_RUNTIME")
        return 0
    try:
        client = client_from_env(symbol=hint.symbol, live=False)
        spec = client.load_asset_spec()
    except (AlpacaConfigError, AlpacaAPIError, ValueError) as exc:
        print(f"broker validation failed: {exc}")
        return 2
    print(f"symbol: {spec.symbol}")
    print(f"asset_class: {spec.asset_class}")
    print(f"status: {spec.status}")
    print(f"tradable: {spec.tradable}")
    print(f"fractionable: {spec.fractionable}")
    print(f"shortable: {spec.shortable}")
    print(f"min_order_size: {spec.min_order_size}")
    print(f"min_trade_increment: {spec.min_trade_increment}")
    print(f"price_increment: {spec.price_increment}")
    print(f"metadata_source: {spec.metadata_source}")
    return 0


def _explain_split() -> int:
    from .split import render_split_table
    print(render_split_table())
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        print(USAGE)
        return 1
    command, rest = sys.argv[1], sys.argv[2:]
    if command == "doctor":
        from .doctor import main as command_main
    elif command == "run":
        from .loop import main as command_main
    elif command == "simulate":
        from .simulate import main as command_main
    elif command == "calibrate":
        from .calibrate import main as command_main
    elif command == "serve":
        from .serve import main as command_main
    elif command == "validate-symbol":
        return _validate_symbol(rest)
    elif command == "explain-split":
        return _explain_split()
    else:
        print(f"unknown command {command!r}. {USAGE}")
        return 1
    return command_main(rest)


if __name__ == "__main__":
    sys.exit(main())

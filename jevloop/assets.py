"""Asset syntax hints plus broker-authoritative asset metadata.

Ticker syntax is useful for choosing a market-data route offline. It is never
proof that Alpaca currently supports or allows trading the asset.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

_CRYPTO_PATTERN = re.compile(r"^[A-Z0-9]{2,12}/[A-Z0-9]{2,12}$")
_EQUITY_PATTERN = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


class UnknownSymbolError(ValueError):
    pass


class AssetNotTradableError(ValueError):
    pass


@dataclass(frozen=True)
class AssetSpec:
    symbol: str
    asset_class: str  # crypto | us_equity
    is_24_7: bool
    has_depth: bool
    tradable: bool | None = None
    status: str | None = None
    fractionable: bool | None = None
    shortable: bool | None = None
    min_order_size: Decimal | None = None
    min_trade_increment: Decimal | None = None
    price_increment: Decimal | None = None
    metadata_source: str = "syntax-only"


def classify_symbol(raw: str) -> AssetSpec:
    sym = raw.strip().upper()
    if _CRYPTO_PATTERN.fullmatch(sym):
        return AssetSpec(sym, "crypto", True, True, shortable=False)
    if _EQUITY_PATTERN.fullmatch(sym):
        return AssetSpec(sym, "us_equity", False, False, shortable=False)
    raise UnknownSymbolError(
        f"{raw!r} is neither a crypto pair such as BTC/USD nor a US-equity-style symbol such as AAPL"
    )


def _decimal(value) -> Decimal | None:
    if value in (None, ""):
        return None
    return Decimal(str(value))


def from_alpaca_asset(raw_symbol: str, payload: dict) -> AssetSpec:
    hint = classify_symbol(raw_symbol)
    asset_class = payload.get("class") or hint.asset_class
    if asset_class not in {"crypto", "us_equity"}:
        raise AssetNotTradableError(f"unsupported Alpaca asset class: {asset_class!r}")
    symbol = str(payload.get("symbol") or hint.symbol).upper()
    spec = AssetSpec(
        symbol=symbol,
        asset_class=asset_class,
        is_24_7=(asset_class == "crypto"),
        has_depth=(asset_class == "crypto"),
        tradable=bool(payload.get("tradable")),
        status=payload.get("status"),
        fractionable=payload.get("fractionable"),
        shortable=bool(payload.get("shortable", False)),
        min_order_size=_decimal(payload.get("min_order_size")),
        min_trade_increment=_decimal(payload.get("min_trade_increment")),
        price_increment=_decimal(payload.get("price_increment")),
        metadata_source="alpaca:/v2/assets",
    )
    return spec


def require_tradable(spec: AssetSpec) -> None:
    if spec.metadata_source != "alpaca:/v2/assets":
        raise AssetNotTradableError("broker-authoritative asset metadata was not loaded")
    if spec.status != "active":
        raise AssetNotTradableError(f"asset status is {spec.status!r}, not 'active'")
    if spec.tradable is not True:
        raise AssetNotTradableError("asset is not currently marked tradable by Alpaca")
    if spec.asset_class == "crypto":
        missing = [
            name for name in ("min_order_size", "min_trade_increment", "price_increment")
            if getattr(spec, name) is None
        ]
        if missing:
            raise AssetNotTradableError(f"crypto asset metadata missing execution increments: {missing}")


def _quantize_up(value: Decimal, increment: Decimal) -> Decimal:
    if increment <= 0:
        raise ValueError("increment must be positive")
    steps = (value / increment).to_integral_value(rounding=ROUND_CEILING)
    return steps * increment


def _quantize_down(value: Decimal, increment: Decimal) -> Decimal:
    if increment <= 0:
        raise ValueError("increment must be positive")
    steps = (value / increment).to_integral_value(rounding=ROUND_FLOOR)
    return steps * increment


def quantity_for_notional(notional_usd: float, price: float, spec: AssetSpec) -> float:
    if notional_usd <= 0 or price <= 0:
        raise ValueError("notional and price must be positive")
    raw = Decimal(str(notional_usd)) / Decimal(str(price))
    inc = spec.min_trade_increment or (Decimal("0.000000001") if spec.asset_class == "crypto" else Decimal("0.000000001"))
    qty = _quantize_up(raw, inc)
    if spec.min_order_size is not None:
        qty = max(qty, spec.min_order_size)
    return float(qty)


def quantize_price(price: float, spec: AssetSpec, *, side: str) -> float:
    if price <= 0:
        raise ValueError("price must be positive")
    # Alpaca equities reject sub-penny prices >= $1; crypto metadata is authoritative when present.
    fallback = Decimal("0.01") if spec.asset_class == "us_equity" and price >= 1 else Decimal("0.0001")
    inc = spec.price_increment or fallback
    value = Decimal(str(price))
    q = _quantize_down(value, inc) if side == "buy" else _quantize_up(value, inc)
    return float(q)


def quantize_quantity(qty: float, spec: AssetSpec, *, up: bool = False) -> float:
    """Quantize an existing quantity to broker metadata without increasing it by default."""
    if qty < 0:
        raise ValueError("quantity must be non-negative")
    inc = spec.min_trade_increment or Decimal("0.000000001")
    value = Decimal(str(qty))
    q = _quantize_up(value, inc) if up else _quantize_down(value, inc)
    return float(q)

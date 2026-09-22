from decimal import Decimal
import pytest

from jevloop.assets import (
    AssetNotTradableError,
    AssetSpec,
    UnknownSymbolError,
    classify_symbol,
    from_alpaca_asset,
    quantize_price,
    quantize_quantity,
    quantity_for_notional,
    require_tradable,
)


def crypto_spec(**kwargs):
    base = dict(symbol="BTC/USD", asset_class="crypto", is_24_7=True, has_depth=True,
                tradable=True, status="active", min_order_size=Decimal("0.0001"),
                min_trade_increment=Decimal("0.0001"), price_increment=Decimal("0.01"),
                metadata_source="alpaca:/v2/assets")
    base.update(kwargs)
    return AssetSpec(**base)


@pytest.mark.parametrize("raw,kind", [("btc/usd","crypto"),("ETH/USDT","crypto"),("aapl","us_equity"),("BRK.B","us_equity")])
def test_classify_symbol_is_syntax_hint(raw, kind):
    spec = classify_symbol(raw)
    assert spec.asset_class == kind
    assert spec.metadata_source == "syntax-only"


def test_unknown_symbol_rejected():
    with pytest.raises(UnknownSymbolError):
        classify_symbol("not a symbol !")


def test_broker_payload_overrides_capabilities():
    spec = from_alpaca_asset("BTC/USD", {"symbol":"BTC/USD","class":"crypto","status":"active","tradable":True,
                                                 "min_order_size":"0.001","min_trade_increment":"0.0001","price_increment":"0.05"})
    assert spec.min_order_size == Decimal("0.001")
    assert spec.price_increment == Decimal("0.05")
    assert spec.metadata_source == "alpaca:/v2/assets"

@pytest.mark.parametrize("status,tradable", [("inactive",True),("active",False),(None,True)])
def test_require_tradable_fails_closed(status, tradable):
    spec = crypto_spec(status=status, tradable=tradable)
    with pytest.raises(AssetNotTradableError):
        require_tradable(spec)


def test_require_tradable_rejects_syntax_only():
    with pytest.raises(AssetNotTradableError):
        require_tradable(classify_symbol("BTC/USD"))


def test_quantity_uses_broker_increment_and_minimum():
    spec = crypto_spec(min_order_size=Decimal("0.002"), min_trade_increment=Decimal("0.001"))
    assert quantity_for_notional(1.0, 1000.0, spec) == 0.002
    assert quantity_for_notional(2.1, 1000.0, spec) == 0.003


def test_quantity_rejects_nonpositive():
    with pytest.raises(ValueError):
        quantity_for_notional(0, 100, crypto_spec())
    with pytest.raises(ValueError):
        quantity_for_notional(10, 0, crypto_spec())


def test_quantize_quantity_does_not_increase_by_default():
    spec = crypto_spec(min_trade_increment=Decimal("0.01"))
    assert quantize_quantity(1.019, spec) == 1.01
    assert quantize_quantity(1.011, spec, up=True) == 1.02


def test_price_quantization_is_side_safe():
    spec = crypto_spec(price_increment=Decimal("0.05"))
    assert quantize_price(100.023, spec, side="buy") == 100.0
    assert quantize_price(100.023, spec, side="sell") == 100.05


def test_equity_fallback_prevents_subpenny_at_or_above_one_dollar():
    spec = AssetSpec("AAPL","us_equity",False,False)
    assert quantize_price(123.456, spec, side="buy") == 123.45
    assert quantize_price(123.456, spec, side="sell") == 123.46

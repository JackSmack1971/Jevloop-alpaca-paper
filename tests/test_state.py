import pytest
from jevloop.state import RuntimeState, TradeTick, build_snapshot, observe_trades, parse_rfc3339


def test_parse_rfc3339_zulu():
    assert parse_rfc3339("2026-09-22T12:00:00Z") == 1790078400.0


def make_snapshot(**overrides):
    runtime = overrides.pop("runtime", RuntimeState(account_equity_usd=1000,last_equity_usd=1000,high_water_mark_usd=1000))
    trades = overrides.pop("trades", [TradeTick(90,99,1,"sell","a"),TradeTick(95,100,1,"buy","b"),TradeTick(100,101,1,"buy","c")])
    params = dict(as_of=100,mid=100,best_bid=99,best_ask=101,bid_depth=[(99,3)],ask_depth=[(101,1)],
                  trades=trades,runtime=runtime,market_data_ts=99,max_position_usd=50,has_depth=True)
    params.update(overrides)
    return build_snapshot(**params)


def test_snapshot_uses_real_timestamps_and_flow():
    s = make_snapshot()
    assert s["data_age_s"] == 1
    assert s["imbalance"] == 0.5
    assert s["aggressive_buy_ratio"] == pytest.approx(2/3, abs=1e-4)


def test_future_market_timestamp_rejected():
    with pytest.raises(ValueError):
        make_snapshot(market_data_ts=102)


def test_future_trade_rejected():
    with pytest.raises(ValueError):
        make_snapshot(trades=[TradeTick(101,100,1)])


def test_observed_vwap_deduplicates_trade_ids():
    r = RuntimeState()
    t = TradeTick(1,100,2,trade_id="x")
    observe_trades(r,[t,t])
    assert r.observed_vwap_vol == 2
    assert r.observed_vwap_pv == 200


def test_observed_vwap_deduplicates_value_key_without_id():
    r = RuntimeState()
    t = TradeTick(1,100,2)
    observe_trades(r,[t,t])
    assert r.observed_vwap_vol == 2


def test_no_aggressor_side_is_reported_as_missing_not_neutral():
    s = make_snapshot(trades=[TradeTick(99,100,1,None,"x")])
    assert s["aggressive_buy_ratio"] is None


def test_return_window_is_time_sorted():
    s = make_snapshot(trades=[TradeTick(100,102,1),TradeTick(90,100,1),TradeTick(95,101,1)])
    assert s["return_1m"] == pytest.approx(0.02)


def test_drawdown_and_daily_loss_come_from_broker_equity_state():
    r = RuntimeState(account_equity_usd=900,last_equity_usd=1000,high_water_mark_usd=1100)
    s = make_snapshot(runtime=r)
    assert s["daily_loss_usd"] == 100
    assert s["drawdown_pct"] == pytest.approx((1100-900)/1100, abs=1e-6)


def test_inventory_is_normalized_to_usd():
    r = RuntimeState(inventory_qty=0.5,account_equity_usd=1000,last_equity_usd=1000,high_water_mark_usd=1000)
    s = make_snapshot(runtime=r,mid=100,best_bid=99,best_ask=101)
    assert s["inventory_usd"] == 50
    assert s["inventory_utilization"] == 1


def test_pricing_freshness_is_independent_of_fresh_trade_tape():
    s = make_snapshot(market_data_ts=80, trade_data_ts=99)
    assert s["data_age_s"] == 20
    assert s["trade_data_age_s"] == 1

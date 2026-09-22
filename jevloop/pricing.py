"""Unit-transparent default quote construction plus an optional A-S helper."""
from __future__ import annotations

import math
from .assets import AssetSpec, quantize_price
from .limits import Limits


def bounded_quote_prices(
    *, mid: float, best_bid: float, best_ask: float, inventory_utilization: float,
    limits: Limits, spec: AssetSpec, wide: bool = False,
) -> tuple[float, float]:
    if not (0 < best_bid <= mid <= best_ask):
        raise ValueError("invalid top of book")
    observed_half_bps = (best_ask - best_bid) / mid * 5_000
    half_bps = max(limits.min_half_spread_bps, observed_half_bps + limits.quote_buffer_bps)
    if wide:
        half_bps *= limits.wide_multiplier
    util = max(0.0, min(1.0, inventory_utilization))
    # Long inventory shifts both quotes lower to favor inventory reduction.
    skew_bps = limits.max_inventory_skew_bps * util
    bid = mid * (1 - (half_bps + skew_bps) / 10_000)
    ask = mid * (1 + max(0.1, half_bps - skew_bps) / 10_000)
    # Passive intent: never knowingly cross the observed top of book.
    bid = min(bid, best_bid)
    ask = max(ask, best_ask)
    return quantize_price(bid, spec, side="buy"), quantize_price(ask, spec, side="sell")


def avellaneda_stoikov_quotes(mid: float, inventory: float, gamma: float, sigma_price_per_sqrt_s: float,
                               horizon_s: float, kappa_per_dollar: float) -> tuple[float, float]:
    """Research helper only; callers must provide consistently estimated units.

    Avellaneda & Stoikov (2008), "High-frequency trading in a limit order book",
    Quantitative Finance 8(3), 217-224: reservation price r = s - q*gamma*sigma^2*(T-t);
    the *total* optimal bid-ask spread is
        delta = gamma*sigma^2*(T-t) + (2/gamma)*ln(1+gamma/kappa).
    `delta` is the full ask-minus-bid width, so each side sits at reservation +/- delta/2.
    Earlier code applied the full `delta` on each side, doubling the intended spread.
    """
    if gamma <= 0 or sigma_price_per_sqrt_s < 0 or horizon_s <= 0 or kappa_per_dollar <= 0:
        raise ValueError("A-S parameters must have valid positive units")
    variance = sigma_price_per_sqrt_s ** 2
    reservation = mid - inventory * gamma * variance * horizon_s
    full_spread = gamma * variance * horizon_s + (2 / gamma) * math.log1p(gamma / kappa_per_dollar)
    half_spread = full_spread / 2.0
    return reservation - half_spread, reservation + half_spread

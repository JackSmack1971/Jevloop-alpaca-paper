"""Canonical, read-only readiness check for Alpaca paper execution."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .assets import AssetNotTradableError, AssetSpec
from .client import BaseDecisionClient, DecisionClientError, resolve_decision_client
from .execution.alpaca import (
    PAPER_TRADING_BASE_URL,
    AlpacaAPIError,
    AlpacaConfigError,
    client_from_env,
)

NON_PAPER_ENDPOINT = "NON_PAPER_ALPACA_ENDPOINT"
ACCOUNT_READ_FAILURE = "ACCOUNT_READ_FAILURE"
ACCOUNT_NOT_TRADABLE = "ACCOUNT_INACTIVE_OR_TRADING_BLOCKED"
ASSET_NOT_TRADABLE = "ASSET_INACTIVE_OR_NOT_TRADABLE"
INVALID_PROVIDER_CONFIGURATION = "INVALID_PROVIDER_CONFIGURATION"
FOREIGN_SESSION_ORDERS = "FOREIGN_SESSION_ORDERS"


@dataclass(frozen=True)
class PaperPreflightReason:
    code: str
    message: str
    details: dict[str, Any] | None = None


@dataclass(frozen=True)
class PaperPreflightResult:
    """Result and already-resolved dependencies from the read-only preflight."""

    reasons: tuple[PaperPreflightReason, ...]
    alpaca: Any | None = None
    asset: AssetSpec | None = None
    account: dict[str, Any] | None = None
    decision_client: BaseDecisionClient | None = None

    @property
    def ready(self) -> bool:
        return not self.reasons

    def reasons_with_code(self, code: str) -> tuple[PaperPreflightReason, ...]:
        return tuple(reason for reason in self.reasons if reason.code == code)


def paper_preflight(
    *,
    symbol: str,
    mock: bool = False,
    alpaca: Any | None = None,
    decision_client: BaseDecisionClient | None = None,
) -> PaperPreflightResult:
    """Inspect every prerequisite for paper authority without writing broker state.

    Optional dependency arguments are test/integration seams.  The function never
    submits or cancels orders, including orders owned by another process session.
    """
    reasons: list[PaperPreflightReason] = []
    spec = None
    account = None

    if alpaca is None:
        try:
            alpaca = client_from_env(symbol=symbol)
        except AlpacaConfigError as exc:
            reasons.append(PaperPreflightReason(ACCOUNT_READ_FAILURE, str(exc)))

    if alpaca is not None:
        base_url = str(getattr(alpaca, "base_url", "")).rstrip("/")
        if base_url != PAPER_TRADING_BASE_URL:
            reasons.append(
                PaperPreflightReason(
                    NON_PAPER_ENDPOINT,
                    "Alpaca client is not configured for the canonical paper endpoint",
                    {"base_url": base_url},
                )
            )
        try:
            account = alpaca.get_account()
        except (AlpacaAPIError, ValueError, TypeError) as exc:
            reasons.append(PaperPreflightReason(ACCOUNT_READ_FAILURE, str(exc)))
        else:
            status = str(account.get("status") or "").upper()
            blocked = bool(account.get("trading_blocked"))
            if status != "ACTIVE" or blocked:
                reasons.append(
                    PaperPreflightReason(
                        ACCOUNT_NOT_TRADABLE,
                        "Alpaca account must be active and not trading-blocked",
                        {"status": account.get("status"), "trading_blocked": blocked},
                    )
                )
        try:
            spec = alpaca.load_asset_spec()
        except (AlpacaAPIError, AssetNotTradableError, ValueError, TypeError) as exc:
            reasons.append(PaperPreflightReason(ASSET_NOT_TRADABLE, str(exc)))

        try:
            foreign = alpaca.get_foreign_session_open_orders()
        except (AlpacaAPIError, ValueError, TypeError) as exc:
            # An unresolved ownership read cannot safely authorize paper writes.
            reasons.append(PaperPreflightReason(ACCOUNT_READ_FAILURE, f"open-order read failed: {exc}"))
        else:
            if foreign:
                reasons.append(
                    PaperPreflightReason(
                        FOREIGN_SESSION_ORDERS,
                        "jevloop-prefixed open orders belong to another session; reconcile manually",
                        {"order_ids": [str(order.get("id")) for order in foreign]},
                    )
                )

    if decision_client is None:
        try:
            decision_client = resolve_decision_client(mock=mock)
        except (DecisionClientError, ValueError, TypeError) as exc:
            reasons.append(PaperPreflightReason(INVALID_PROVIDER_CONFIGURATION, str(exc)))
    if decision_client is not None and decision_client.name == "MOCK":
        reasons.append(
            PaperPreflightReason(
                INVALID_PROVIDER_CONFIGURATION,
                "mock judgments are not a valid provider configuration for paper execution",
            )
        )

    return PaperPreflightResult(tuple(reasons), alpaca, spec, account, decision_client)

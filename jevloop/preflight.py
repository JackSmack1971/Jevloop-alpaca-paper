"""Canonical, read-only readiness check for Alpaca paper execution."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from .assets import AssetNotTradableError, AssetSpec
from .client import BaseDecisionClient, DecisionClientError, resolve_decision_client
from .execution.alpaca import (
    PAPER_TRADING_BASE_URL,
    AlpacaAPIError,
    AlpacaConfigError,
    IncompleteOrderEnumeration,
    client_from_env,
)

NON_PAPER_ENDPOINT = "NON_PAPER_ALPACA_ENDPOINT"
ACCOUNT_READ_FAILURE = "ACCOUNT_READ_FAILURE"
ACCOUNT_NOT_TRADABLE = "ACCOUNT_INACTIVE_OR_TRADING_BLOCKED"
ASSET_NOT_TRADABLE = "ASSET_INACTIVE_OR_NOT_TRADABLE"
INVALID_PROVIDER_CONFIGURATION = "INVALID_PROVIDER_CONFIGURATION"
FOREIGN_SESSION_ORDERS = "FOREIGN_SESSION_ORDERS"
OPEN_ORDER_ENUMERATION_INCOMPLETE = "OPEN_ORDER_ENUMERATION_INCOMPLETE"


class AccountRestrictionScope(str, Enum):
    ACCOUNT = "account"
    EQUITY = "equity"
    CRYPTO = "crypto"


@dataclass(frozen=True)
class AccountCapabilityReason:
    scope: AccountRestrictionScope
    field: str
    expected: object
    actual: object
    missing: bool = False


@dataclass(frozen=True)
class AccountCapabilityResult:
    """Typed, fail-closed capability decision for one selected asset class."""

    asset_class: str
    reasons: tuple[AccountCapabilityReason, ...]

    @property
    def capable(self) -> bool:
        return not self.reasons


def account_capability(account: dict[str, Any], asset_class: str) -> AccountCapabilityResult:
    if asset_class not in {"us_equity", "crypto"}:
        raise ValueError(f"unsupported asset class: {asset_class!r}")
    reasons: list[AccountCapabilityReason] = []
    required = (
        (AccountRestrictionScope.EQUITY, "status", "ACTIVE"),
        (AccountRestrictionScope.ACCOUNT, "trading_blocked", False),
        (AccountRestrictionScope.ACCOUNT, "account_blocked", False),
        (AccountRestrictionScope.ACCOUNT, "trade_suspended_by_user", False),
    )
    for scope, field, expected in required:
        missing = field not in account or account[field] is None
        actual = account.get(field)
        matches = (
            str(actual).upper() == expected if isinstance(expected, str) else actual is expected
        )
        if missing or not matches:
            reasons.append(AccountCapabilityReason(scope, field, expected, actual, missing))
    if asset_class == "crypto":
        actual = account.get("crypto_status")
        missing = "crypto_status" not in account or actual is None
        if missing or str(actual).upper() != "ACTIVE":
            reasons.append(
                AccountCapabilityReason(
                    AccountRestrictionScope.CRYPTO, "crypto_status", "ACTIVE", actual, missing
                )
            )
    return AccountCapabilityResult(asset_class, tuple(reasons))


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
            asset_class = getattr(getattr(alpaca, "asset_hint", None), "asset_class", None)
            if asset_class is None:
                asset_class = "crypto" if "/" in symbol else "us_equity"
            capability = account_capability(account, asset_class)
            if not capability.capable:
                reasons.append(
                    PaperPreflightReason(
                        ACCOUNT_NOT_TRADABLE,
                        "Alpaca account lacks a required capability for the selected asset class",
                        {
                            "asset_class": asset_class,
                            "restrictions": [
                                {
                                    "scope": reason.scope.value,
                                    "field": reason.field,
                                    "expected": reason.expected,
                                    "actual": reason.actual,
                                    "missing": reason.missing,
                                }
                                for reason in capability.reasons
                            ],
                        },
                    )
                )
        try:
            spec = alpaca.load_asset_spec()
        except (AlpacaAPIError, AssetNotTradableError, ValueError, TypeError) as exc:
            reasons.append(PaperPreflightReason(ASSET_NOT_TRADABLE, str(exc)))

        try:
            foreign = alpaca.get_foreign_session_open_orders()
        except IncompleteOrderEnumeration as exc:
            reasons.append(
                PaperPreflightReason(
                    OPEN_ORDER_ENUMERATION_INCOMPLETE,
                    f"open-order enumeration incomplete: {exc}",
                    {"reason": exc.reason, "orders_received": exc.orders_received},
                )
            )
        except (AlpacaAPIError, ValueError, TypeError) as exc:
            # An unresolved ownership read cannot safely authorize paper writes.
            reasons.append(
                PaperPreflightReason(ACCOUNT_READ_FAILURE, f"open-order read failed: {exc}")
            )
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

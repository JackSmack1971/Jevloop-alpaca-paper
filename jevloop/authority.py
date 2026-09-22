"""Explicit, fail-closed authority for mutating an Alpaca paper account."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Callable, TypeVar


class AuthorityState(str, Enum):
    NO_AUTHORITY = "NO_AUTHORITY"
    PAPER_READY = "PAPER_READY"
    PAPER_ACTIVE = "PAPER_ACTIVE"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    HALTED = "HALTED"


@dataclass(frozen=True)
class TransitionReason:
    code: str
    message: str
    details: dict[str, object] | None = None


class InvalidAuthorityTransition(RuntimeError):
    pass


_T = TypeVar("_T")


class PaperAuthority:
    """Validated state machine; only ``PAPER_ACTIVE`` may submit an order."""

    _ALLOWED = {
        AuthorityState.NO_AUTHORITY: {AuthorityState.PAPER_READY, AuthorityState.HALTED},
        AuthorityState.PAPER_READY: {
            AuthorityState.PAPER_ACTIVE,
            AuthorityState.RECONCILIATION_REQUIRED,
            AuthorityState.HALTED,
        },
        AuthorityState.PAPER_ACTIVE: {AuthorityState.RECONCILIATION_REQUIRED, AuthorityState.HALTED},
        AuthorityState.RECONCILIATION_REQUIRED: {AuthorityState.PAPER_ACTIVE, AuthorityState.HALTED},
        AuthorityState.HALTED: set(),
    }

    def __init__(self) -> None:
        self.state = AuthorityState.NO_AUTHORITY
        self.reason = TransitionReason("INITIAL", "paper authority has not been established")

    def transition(self, target: AuthorityState, reason: TransitionReason) -> None:
        if target not in self._ALLOWED[self.state]:
            raise InvalidAuthorityTransition(f"invalid authority transition {self.state.value} -> {target.value}")
        self.state = target
        self.reason = reason

    def require_submission(self) -> None:
        if self.state is not AuthorityState.PAPER_ACTIVE:
            raise PermissionError(f"paper submission blocked while authority={self.state.value}")

    def submit(self, operation: Callable[[], _T]) -> _T:
        """The single guard through which every paper order submission passes."""
        self.require_submission()
        return operation()

    def require_reconciliation(self, code: str, message: str, **details: object) -> None:
        if self.state is AuthorityState.HALTED:
            return
        if self.state is not AuthorityState.RECONCILIATION_REQUIRED:
            self.transition(
                AuthorityState.RECONCILIATION_REQUIRED,
                TransitionReason(code, message, details or None),
            )
        else:
            self.reason = TransitionReason(code, message, details or None)

    def broker_reconcile(
        self,
        read_owned_orders: Callable[[], list[dict]],
        read_position: Callable[[], object],
        *,
        require_no_owned: bool = True,
    ) -> tuple[list[dict], object]:
        """Recover authority only after fresh, successful broker reads of both resources."""
        if self.state not in {AuthorityState.PAPER_READY, AuthorityState.RECONCILIATION_REQUIRED}:
            raise InvalidAuthorityTransition(f"broker reconciliation not allowed from {self.state.value}")
        try:
            owned_orders = read_owned_orders()
            position = read_position()
        except BaseException as exc:
            self.require_reconciliation(
                "BROKER_STATE_UNREADABLE", "fresh broker-authoritative reconciliation read failed",
                error=f"{type(exc).__name__}: {exc}",
            )
            raise
        if require_no_owned and owned_orders:
            self.require_reconciliation(
                "OWNED_ORDERS_UNRESOLVED", "session-owned open orders remain after reconciliation",
                order_count=len(owned_orders),
            )
            return owned_orders, position
        self.transition(
            AuthorityState.PAPER_ACTIVE,
            TransitionReason("BROKER_STATE_RECONCILED", "fresh owned-order and position reads completed"),
        )
        return owned_orders, position

    def evidence(self) -> dict[str, object]:
        return {"authority_state": self.state.value, "authority_transition_reason": asdict(self.reason)}

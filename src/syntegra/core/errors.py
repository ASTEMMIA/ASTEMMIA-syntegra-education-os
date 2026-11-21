"""Domain errors that map onto RFC 9457 Problem Details (CEDA §10.4).

Every error carries a human message that says what to do next.
"""

from __future__ import annotations

from typing import Any, ClassVar


class DomainError(Exception):
    status: ClassVar[int] = 400
    type_slug: ClassVar[str] = "domain-error"
    title: ClassVar[str] = "The request could not be completed"

    def __init__(self, detail: str, *, next_action: str | None = None, **extra: Any) -> None:
        super().__init__(detail)
        self.detail = detail
        self.next_action = next_action
        self.extra = extra


class ValidationFailed(DomainError):
    status = 422
    type_slug = "validation-failed"
    title = "Some details need correcting"


class NotFound(DomainError):
    status = 404
    type_slug = "not-found"
    title = "Record not found"


class Forbidden(DomainError):
    status = 403
    type_slug = "forbidden"
    title = "You do not have access to this"


class Unauthenticated(DomainError):
    status = 401
    type_slug = "unauthenticated"
    title = "Sign in required"


class Conflict(DomainError):
    status = 409
    type_slug = "conflict"
    title = "This record changed or already exists"


class PreconditionFailed(DomainError):
    status = 412
    type_slug = "precondition-failed"
    title = "Someone else updated this record"


class PreconditionRequired(DomainError):
    status = 428
    type_slug = "precondition-required"
    title = "A required header is missing"


class InvariantViolation(DomainError):
    """A CEDA rule (e.g. P7 never overwrite) would be broken."""

    status = 409
    type_slug = "invariant-violation"
    title = "This change is not allowed"


class TenantIsolationError(RuntimeError):
    """Raised when code tries to touch tenant-scoped data without a tenant context.

    This is a programming error, not a user error; it must never be swallowed.
    """

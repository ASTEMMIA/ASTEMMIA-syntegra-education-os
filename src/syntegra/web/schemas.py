"""HTTP contracts (Pydantic v2 → OpenAPI 3.1). snake_case JSON (§10.4)."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from syntegra.governance.domain import GovernanceLevel, Ownership


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


Code = Field(pattern=r"^[A-Z0-9][A-Z0-9_-]{1,31}$", description="Upper-case code, e.g. ABIA-SUBEB")


class TenantCreate(Model):
    parent_scope_id: uuid.UUID
    code: str = Code
    name: str = Field(min_length=2, max_length=255)
    level: GovernanceLevel
    country: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    ownership: Ownership = Ownership.PUBLIC
    time_zone: str = "Africa/Lagos"
    local_label: str | None = Field(default=None, max_length=64)


class TenantOut(Model):
    id: uuid.UUID
    code: str
    name: str
    kind: str
    root_scope_node_id: uuid.UUID
    residency_region: str


class ScopeNodeCreate(Model):
    parent_id: uuid.UUID
    level: GovernanceLevel
    code: str = Code
    name: str = Field(min_length=1, max_length=255)
    local_label: str | None = Field(default=None, max_length=64)


class ScopeNodeOut(Model):
    id: uuid.UUID
    tenant_id: uuid.UUID
    parent_id: uuid.UUID | None
    level: int
    code: str
    name: str
    path: str


class Page[T](Model):
    items: list[T]
    next_cursor: str | None


class RuleTest(Model):
    name: str
    facts: dict[str, Any]
    expect: bool


class PolicyRuleCreate(Model):
    owner_scope_id: uuid.UUID
    rule_key: str = Field(pattern=r"^[A-Z]{2,6}-[A-Z]{2,6}-\d{2,3}$", examples=["ATT-INST-02"])
    domain: str = Field(max_length=32)
    title: str = Field(max_length=255)
    expression: str = Field(max_length=2000)
    parameters: dict[str, Any] = {}
    strictness: dict[str, Literal["min", "max"]] = {}
    test_cases: list[RuleTest] = Field(min_length=1)
    valid_from: date
    valid_to: date | None = None
    mandatory_below: bool = True


class PolicyRuleTestRequest(Model):
    expression: str = Field(max_length=2000)
    parameters: dict[str, Any] = {}
    test_cases: list[RuleTest] = Field(min_length=1)


class CreatedOut(Model):
    id: uuid.UUID


class GrantOut(Model):
    grant_id: uuid.UUID
    role: str
    scope_node_id: uuid.UUID
    valid_from: date
    valid_to: date | None
    clearance_cap: int
    role_name: str | None = None
    scope_name: str | None = None


class MeOut(Model):
    user_id: uuid.UUID
    display_name: str | None = None
    tenant_code: str | None = None
    tenant_id: uuid.UUID
    active_scope_id: uuid.UUID
    grants: list[GrantOut]
    permissions_here: dict[str, str]


class ChainBreakOut(Model):
    seq: int
    reason: str


class AuditVerifyOut(Model):
    tenant_id: uuid.UUID
    date: date
    events: int
    intact: bool
    breaks: list[ChainBreakOut]


class AuditEventOut(Model):
    seq: int
    chain_date: date
    occurred_at: str
    actor_id: uuid.UUID | None
    action: str
    changed_fields: list[str] | None

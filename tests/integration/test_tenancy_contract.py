"""Layer 4 of §10.3: contract tests that cross-tenant and out-of-scope access fail."""

from datetime import date

import pytest
from sqlalchemy import select, update

from syntegra.core.context import use_context
from syntegra.core.errors import InvariantViolation, TenantIsolationError, ValidationFailed
from syntegra.governance import api as governance
from syntegra.governance.models import ConfigSetting
from syntegra.platform.models import AuditEvent


async def _seed_config(sm, w):
    async with sm() as s, s.begin():
        with use_context(w.ctx(w.school_a_tenant, w.school_a_root, w.head_a)):
            await governance.set_config(
                s, scope_node_id=w.school_a_campus_1, key="att.am_cutoff", value="08:15"
            )
            await governance.set_config(
                s, scope_node_id=w.school_a_campus_2, key="att.am_cutoff", value="08:30"
            )
        with use_context(w.ctx(w.school_b_tenant, w.school_b_root, w.head_b)):
            await governance.set_config(
                s, scope_node_id=w.school_b_root, key="att.am_cutoff", value="07:45"
            )


async def test_reads_are_filtered_to_tenant(sm, world):
    await _seed_config(sm, world)
    async with sm() as s:
        with use_context(world.ctx(world.school_b_tenant, world.school_b_root)):
            rows = (await s.execute(select(ConfigSetting))).scalars().all()
    assert [r.value for r in rows] == ["07:45"]


async def test_reads_are_filtered_to_active_scope_subtree(sm, world):
    await _seed_config(sm, world)
    async with sm() as s:
        with use_context(world.ctx(world.school_a_tenant, world.school_a_campus_1)):
            rows = (await s.execute(select(ConfigSetting))).scalars().all()
    assert [r.value for r in rows] == ["08:15"]  # sibling campus invisible


async def test_bulk_update_is_filtered_too(sm, world):
    await _seed_config(sm, world)
    async with sm() as s, s.begin():
        with use_context(world.ctx(world.school_b_tenant, world.school_b_root)):
            await s.execute(update(ConfigSetting).values(locked_below=True))
    async with sm() as s:
        with use_context(world.ctx(world.school_a_tenant, world.school_a_root)):
            rows = (await s.execute(select(ConfigSetting))).scalars().all()
    assert rows and not any(r.locked_below for r in rows)


async def test_query_without_context_is_refused(sm, world):
    async with sm() as s:
        with pytest.raises(TenantIsolationError):
            await s.execute(select(ConfigSetting))


async def test_cross_tenant_write_is_refused(sm, world):
    async with sm() as s:
        with use_context(world.ctx(world.school_a_tenant, world.school_a_root)):
            s.add(
                ConfigSetting(
                    tenant_id=world.school_b_tenant,
                    scope_node_id=world.school_b_root,
                    setting_key="x",
                    value=1,
                    valid_from=date(2026, 1, 1),
                )
            )
            with pytest.raises(TenantIsolationError, match="cross-tenant"):
                await s.flush()


async def test_write_outside_active_scope_is_refused(sm, world):
    async with sm() as s:
        with use_context(world.ctx(world.school_a_tenant, world.school_a_campus_1)):
            s.add(
                ConfigSetting(
                    scope_node_id=world.school_a_campus_2,
                    setting_key="x",
                    value=1,
                    valid_from=date(2026, 1, 1),
                )
            )
            with pytest.raises(TenantIsolationError, match="scope"):
                await s.flush()


async def test_config_inherits_nearest_and_locked_wins(sm, world):
    async with sm() as s, s.begin():
        with use_context(world.ctx(world.abia_tenant, world.abia_root, world.state_officer)):
            await governance.set_config(
                s, scope_node_id=world.abia_root, key="att.min_rate", value=0.9, locked_below=True
            )
            await governance.set_config(
                s, scope_node_id=world.abia_root, key="term.weeks", value=13
            )
        with use_context(world.ctx(world.school_a_tenant, world.school_a_root, world.head_a)):
            await governance.set_config(
                s, scope_node_id=world.school_a_root, key="term.weeks", value=12
            )
            assert await governance.resolve_config(s, world.school_a_campus_1, "term.weeks") == 12
            assert await governance.resolve_config(s, world.school_b_root, "term.weeks") == 13
            with pytest.raises(ValidationFailed, match="fixed by a higher level"):
                await governance.set_config(
                    s, scope_node_id=world.school_a_root, key="att.min_rate", value=0.5
                )


async def test_new_config_version_end_dates_previous(sm, world):
    async with sm() as s, s.begin():
        with use_context(world.ctx(world.school_b_tenant, world.school_b_root, world.head_b)):
            await governance.set_config(
                s, scope_node_id=world.school_b_root, key="k", value=1, valid_from=date(2026, 1, 1)
            )
            await governance.set_config(
                s, scope_node_id=world.school_b_root, key="k", value=2, valid_from=date(2026, 9, 1)
            )
            assert (
                await governance.resolve_config(s, world.school_b_root, "k", date(2026, 5, 1)) == 1
            )
            assert (
                await governance.resolve_config(s, world.school_b_root, "k", date(2026, 10, 1)) == 2
            )


async def test_audit_events_are_immutable(sm, world):
    async with sm() as s:
        ev = (await s.execute(select(AuditEvent).limit(1))).scalar_one()
        ev.action = "tampered"
        with pytest.raises(InvariantViolation, match="evidential"):
            await s.flush()

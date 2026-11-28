from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

import syntegra.governance.models
import syntegra.iam.models  # noqa: F401
from syntegra.core.context import RequestContext, use_context
from syntegra.governance import api as governance
from syntegra.governance.domain import GovernanceLevel, Ownership
from syntegra.iam import api as iam
from syntegra.platform.db import Base, make_engine, make_sessionmaker
from syntegra.platform.settings import Settings
from syntegra.web.app import create_app

# Set SYNTEGRA_TEST_DATABASE_URL=mysql+asyncmy://user:pass@host:3306/ops to run the whole
# suite against MySQL (schema created by `alembic upgrade head`). Default: SQLite per test.
MYSQL_URL = os.environ.get("SYNTEGRA_TEST_DATABASE_URL")


@pytest.fixture
async def engine(tmp_path) -> AsyncIterator[AsyncEngine]:
    if MYSQL_URL:
        eng = make_engine(MYSQL_URL)
        async with eng.begin() as conn:
            await conn.exec_driver_sql("SET FOREIGN_KEY_CHECKS=0")
            for table in Base.metadata.sorted_tables:
                await conn.exec_driver_sql(f"TRUNCATE TABLE {table.schema}.{table.name}")
            await conn.exec_driver_sql("SET FOREIGN_KEY_CHECKS=1")
    else:
        eng = make_engine(f"sqlite+aiosqlite:///{tmp_path}/test.db")
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
def sm(engine) -> async_sessionmaker[AsyncSession]:
    return make_sessionmaker(engine)


@dataclass
class World:
    """Global → Nigeria → Abia State → (Umuahia LGEA) → school A, school B (+ campuses)."""

    global_tenant: uuid.UUID
    global_root: uuid.UUID
    ng_tenant: uuid.UUID
    ng_root: uuid.UUID
    abia_tenant: uuid.UUID
    abia_root: uuid.UUID
    lgea: uuid.UUID
    school_a_tenant: uuid.UUID
    school_a_root: uuid.UUID
    school_a_campus_1: uuid.UUID
    school_a_campus_2: uuid.UUID
    school_b_tenant: uuid.UUID
    school_b_root: uuid.UUID
    padm: uuid.UUID
    state_officer: uuid.UUID
    head_a: uuid.UUID
    teacher_a: uuid.UUID
    head_b: uuid.UUID

    def ctx(
        self, tenant: uuid.UUID, scope: uuid.UUID, user: uuid.UUID | None = None
    ) -> RequestContext:
        return RequestContext(tenant_id=tenant, scope_node_id=scope, user_id=user)


@pytest.fixture
async def world(sm) -> World:
    async with sm() as s, s.begin():
        await iam.seed_builtin_roles(s)
        g = await governance.create_root_tenant(
            s, code="SYNTEGRA", name="Syntegra programme", residency_region="af-south-1"
        )
        ng = await governance.create_child_tenant(
            s,
            parent_scope_id=g.root_scope_node_id,
            code="NG-FME",
            name="Federal Ministry of Education",
            level=GovernanceLevel.COUNTRY,
            country="NG",
            residency_region="af-south-1",
        )
        abia = await governance.create_child_tenant(
            s,
            parent_scope_id=ng.root_scope_node_id,
            code="NG-AB-MOE",
            name="Abia State MoE",
            level=GovernanceLevel.REGION,
            residency_region="af-south-1",
            local_label="State MoE",
        )
        sys_ctx = RequestContext(abia.id, abia.root_scope_node_id, None, system=True)
        with use_context(sys_ctx):
            lgea = await governance.create_scope_node(
                s,
                parent_id=abia.root_scope_node_id,
                level=GovernanceLevel.LOCAL_AUTHORITY,
                code="UMUAHIA-N",
                name="Umuahia North LGEA",
                local_label="LGEA",
            )
        a = await governance.create_child_tenant(
            s,
            parent_scope_id=lgea.id,
            code="INS-NG-000101",
            name="Government College Umuahia",
            level=GovernanceLevel.INSTITUTION,
            residency_region="af-south-1",
            ownership=Ownership.PUBLIC,
        )
        b = await governance.create_child_tenant(
            s,
            parent_scope_id=lgea.id,
            code="INS-NG-000102",
            name="Private Academy",
            level=GovernanceLevel.INSTITUTION,
            residency_region="af-south-1",
            ownership=Ownership.PRIVATE,
        )
        with use_context(RequestContext(a.id, a.root_scope_node_id, None, system=True)):
            c1 = await governance.create_scope_node(
                s,
                parent_id=a.root_scope_node_id,
                level=GovernanceLevel.CAMPUS,
                code="MAIN",
                name="Main campus",
            )
            c2 = await governance.create_scope_node(
                s,
                parent_id=a.root_scope_node_id,
                level=GovernanceLevel.CAMPUS,
                code="ANNEX",
                name="Annex",
            )

        users = {}
        for key, tenant, login, role, scope in [
            ("padm", g, "admin@syntegra.test", "PADM", g.root_scope_node_id),
            ("state_officer", abia, "officer@abia.test", "STA", abia.root_scope_node_id),
            ("head_a", a, "head@gcu.test", "HEAD", a.root_scope_node_id),
            ("teacher_a", a, "teacher@gcu.test", "TCH", c1.id),
            ("head_b", b, "head@academy.test", "HEAD", b.root_scope_node_id),
        ]:
            with use_context(
                RequestContext(tenant.id, tenant.root_scope_node_id, None, system=True)
            ):
                u = await iam.create_user(s, tenant_id=tenant.id, login=login, display_name=key)
                await iam.grant_role(s, user_id=u.id, role_code=role, scope_node_id=scope)
            users[key] = u.id

    return World(
        g.id,
        g.root_scope_node_id,
        ng.id,
        ng.root_scope_node_id,
        abia.id,
        abia.root_scope_node_id,
        lgea.id,
        a.id,
        a.root_scope_node_id,
        c1.id,
        c2.id,
        b.id,
        b.root_scope_node_id,
        **users,
    )


@pytest.fixture
async def client(engine, sm, world):
    app = create_app(Settings(env="test", database_url="sqlite+aiosqlite://"))
    app.state.engine, app.state.sessionmaker = engine, sm
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c

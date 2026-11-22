"""P4: each state or country describes its own structure in a file; the code is the same."""

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from syntegra.core.context import RequestContext, use_context
from syntegra.governance import api as governance
from syntegra.governance.domain import GovernanceLevel
from syntegra.governance.models import ScopeNode
from syntegra.governance.structure import Structure, load_structure
from syntegra.institutions import api as inst

OTHER_STATE = {
    "version": 1,
    "country": {"code": "NG", "name": "Nigeria", "time_zone": "Africa/Lagos"},
    "jurisdiction": {"code": "NG-XX", "name": "Example State"},
    "bodies": [
        {
            "code": "SUBEB",
            "name": "Example State Universal Basic Education Board",
            "label": "Board",
            "registers": ["primary", "jss"],
            "units": {
                "label": "LGEA",
                "code_prefix": "LGEA",
                "name_suffix": " LGEA",
                "names": ["North", "South"],
            },
        },
        {
            "code": "SEMB",
            "name": "Example Senior Secondary Education Board",
            "label": "Board",
            "registers": ["sss"],
            "units": {
                "label": "Zone",
                "code_prefix": "ZONE",
                "name_suffix": " Zone",
                "names": ["North", "South"],
            },
        },
    ],
}
NO_STATES = {
    "version": 1,
    "country": {"code": "GB", "name": "United Kingdom", "time_zone": "Europe/London"},
    "bodies": [
        {
            "code": "LA-EXAMPLE",
            "name": "Example Local Authority",
            "label": "Local authority",
            "registers": ["primary", "jss", "sss"],
        }
    ],
}


async def _apply(sm, world, data):
    st = data if isinstance(data, Structure) else Structure.model_validate(data)
    async with sm() as s, s.begin():
        return await governance.apply_structure(
            s, st, platform_scope_id=world.global_root, residency_region="af-south-1"
        )


async def test_abia_file_builds_boards_lgeas_and_settings_idempotently(sm, world):
    tenant, bodies, created = await _apply(sm, world, load_structure("ng-abia"))
    assert tenant.code == "NG-AB"
    assert set(bodies) == {"ASUBEB", "MIN-TERTIARY-EDUCATION", "ABIA-SAME"}  # ADR-010, ADR-020
    assert "LGEA-UMU-NNEOCHI" in created and "ASUBEB register levels" in created
    assert "location areas" in created and "MIN-TERTIARY-EDUCATION register levels" in created
    async with sm() as s:
        with use_context(RequestContext(tenant.id, tenant.root_scope_node_id, None)):
            lgeas = await governance.list_children(s, bodies["ASUBEB"], after=None, limit=50)
            assert len(lgeas) == 17 and all(n.level == GovernanceLevel.NETWORK for n in lgeas)
            ministry_levels = await governance.resolve_config(
                s, bodies["MIN-TERTIARY-EDUCATION"], "register.levels"
            )
            assert ministry_levels == ["tvet", "nce", "nd", "hnd", "degree", "postgraduate"]
            same = await governance.resolve_config(s, bodies["ABIA-SAME"], "register.levels")
            assert same == ["adult"]
            areas = await governance.resolve_config(s, bodies["ASUBEB"], "location.areas")
            assert len(areas) == 17 and "Umu Nneochi" in areas
    _, _, again = await _apply(sm, world, load_structure("ng-abia"))
    assert again == []


async def test_a_different_state_needs_no_code(sm, world):
    tenant, bodies, _ = await _apply(sm, world, OTHER_STATE)
    async with sm() as s:
        with use_context(RequestContext(tenant.id, bodies["SEMB"], None)):
            zones = await governance.list_children(s, bodies["SEMB"], after=None, limit=10)
            assert sorted(z.name for z in zones) == ["North Zone", "South Zone"]
    async with sm() as s, s.begin():
        with use_context(RequestContext(tenant.id, bodies["SEMB"], None, system=True)):
            job = await inst.preview_import(
                s,
                "name,lga,levels\nTEST College,North,SSS\nTEST Pry,South Zone,Primary\n",
                source_name=None,
            )
            _, rows = await inst.get_job(s, job)
            assert [r.status for r in rows] == ["valid", "error"]  # this board registers SSS only


async def test_a_country_without_states(sm, world):
    tenant, bodies, _ = await _apply(sm, world, NO_STATES)
    assert tenant.code == "GB" and tenant.kind == "country"
    async with sm() as s:
        body = (
            await s.execute(select(ScopeNode).where(ScopeNode.id == bodies["LA-EXAMPLE"]))
        ).scalar_one()
        assert body.level == GovernanceLevel.REGION and body.local_label == "Local authority"


async def test_a_body_that_registers_nothing_yet_rejects_every_row(sm, world):
    """registers: [] means none yet. It must not fall back to the basic-education default."""
    pending = OTHER_STATE | {"bodies": [OTHER_STATE["bodies"][0] | {"registers": []}]}
    tenant, bodies, _ = await _apply(sm, world, pending)
    async with sm() as s, s.begin():
        with use_context(RequestContext(tenant.id, bodies["SUBEB"], None, system=True)):
            _job, rows = await inst.get_job(
                s,
                await inst.preview_import(
                    s, "name,lga,levels\nTEST Pry,North,Primary\n", source_name=None
                ),
            )
            assert rows[0].status == "error"
            assert rows[0].messages == ["this body does not register Primary institutions"]


def test_bad_structure_files_are_refused():
    with pytest.raises(ValidationError):
        Structure.model_validate(
            OTHER_STATE | {"bodies": [OTHER_STATE["bodies"][0]] * 2}
        )  # duplicate body
    with pytest.raises(ValidationError):
        Structure.model_validate(
            NO_STATES | {"country": {"code": "Nigeria", "name": "x", "time_zone": "UTC"}}
        )
    with pytest.raises(FileNotFoundError, match="bundled: ng-abia"):
        load_structure("no-such-state")

"""Gazetteer: one address register for people and institutions (ADR-022)."""

from datetime import date, timedelta

import pytest
from sqlalchemy import func, select

from syntegra.core.clock import today
from syntegra.core.context import RequestContext, use_context
from syntegra.core.errors import Conflict, ValidationFailed
from syntegra.institutions import api as inst
from syntegra.mdm import api as mdm
from syntegra.mdm import gazetteer as gz
from syntegra.mdm.models import Address
from syntegra.platform.crypto import build_field_crypto
from tests.audit_helpers import verify_all_days
from tests.integration.test_tertiary_register import _abia

CRYPTO = build_field_crypto(
    env="test", kek_b64=None, previous_keks_b64=[], blind_index_key_b64=None
)
HOME = gz.AddressIn(
    address_line1="14 Faulks Road",
    address_line2="Off Asa Road",
    address_line3="Aba",
    local_government_area="Aba North LGA",
    postcode="450 001",
    address_description="Blue gate, opposite the church",
)


def at(tenant, scope):
    return use_context(RequestContext(tenant.id, scope, None, system=True))


async def _child(s, given, family, scope, born=date(2015, 3, 1)):
    r = await mdm.create_person(
        s,
        CRYPTO,
        mdm.PersonIn(
            given_name=given,
            family_name=family,
            other_names="Chidi",
            date_of_birth=born,
            scope_node_id=scope,
        ),
    )
    return r.person_id


async def test_registered_institutions_get_their_premises_address(sm, world):
    tenant, bodies = await _abia(sm, world)
    csv = (
        "name,lga,levels,address_line1,town,postcode\n"
        "TEST CPS Ndume,Umuahia North,Primary,2 Market Rd,Umuahia,440001\n"
    )
    async with sm() as s, s.begin():
        with at(tenant, bodies["ASUBEB"]):
            await inst.commit_import(
                s, await inst.preview_import(s, csv, source_name=None), managing_body="ASUBEB"
            )
            (school,) = await inst.list_institutions(s)
            (premises,) = await gz.addresses_of(s, institution_id=school.id)
    assert premises.address_type == "PREMISES" and premises.current
    a = premises.address
    assert a.lines == ["2 Market Rd", "Umuahia"] and a.local_government_area == "Umuahia North"
    assert a.postcode == "440001" and a.state == "Abia State" and a.address_no >= 1
    assert a.one_line == "2 Market Rd, Umuahia, Umuahia North, Abia State 440001, Nigeria"


async def test_a_family_shares_one_address_and_moves_keep_history(sm, world):
    tenant, bodies = await _abia(sm, world)
    scope = bodies["ASUBEB"]
    async with sm() as s, s.begin():
        with at(tenant, scope):
            ada = await _child(s, "Ada", "Okeke", scope)
            obi = await _child(s, "Obinna", "Okeke", scope, born=date(2013, 7, 9))
            start = today() - timedelta(days=30)
            a1 = await gz.set_address(
                s, HOME, address_type="home", person_id=ada, effective_from=start
            )
            a2 = await gz.set_address(
                s,
                gz.AddressIn(
                    address_line1="14 FAULKS ROAD.",
                    address_line2="off asa road",
                    address_line3="ABA",
                    local_government_area="aba north",
                    postcode="450001",
                ),
                address_type="HOME",
                person_id=obi,
                effective_from=start,
            )
            assert a1.address.id == a2.address.id  # one physical place, shared
            assert (
                a1.address.postcode == "450001" and a1.address.local_government_area == "Aba North"
            )
            # The same address again changes nothing.
            again = await gz.set_address(s, HOME, address_type="HOME", person_id=ada)
            assert again.id == a1.id
            # Ada moves: the old link ends the day before, the new one starts.
            moved = await gz.set_address(
                s,
                gz.AddressIn(
                    address_line1="3 Ikot Ekpene Road",
                    address_line3="Umuahia",
                    local_government_area="Umuahia North",
                    postcode="440001",
                ),
                address_type="HOME",
                person_id=ada,
            )
            history = await gz.addresses_of(s, person_id=ada, include_history=True)
            assert [(h.address.lines[0], h.effective_to) for h in history] == [
                ("3 Ikot Ekpene Road", None),
                ("14 Faulks Road", today() - timedelta(days=1)),
            ]
            assert [v.id for v in await gz.addresses_of(s, person_id=ada)] == [moved.id]
            # A postal address is a separate type.
            await gz.set_address(s, HOME, address_type="POSTAL", person_id=ada)
            assert len(await gz.addresses_of(s, person_id=ada)) == 2
            assert (await s.execute(select(func.count()).select_from(Address))).scalar() == 2
        assert await verify_all_days(s, tenant.id)


async def test_addresses_are_checked_against_the_state(sm, world):
    tenant, bodies = await _abia(sm, world)
    scope = bodies["ASUBEB"]
    async with sm() as s, s.begin():
        with at(tenant, scope):
            ada = await _child(s, "Ada", "Okeke", scope)
            bad = gz.AddressIn(
                address_line1="", local_government_area="Ikeja", postcode="12", paf_key="<x>"
            )
            with pytest.raises(ValidationFailed) as exc:
                await gz.set_address(s, bad, address_type="HOME", person_id=ada)
            detail = exc.value.detail
            assert "address line 1 is required" in detail
            assert "'Ikeja' is not a local government area of this state" in detail
            assert "not in the expected format, e.g. 440001" in detail and "PAF key" in detail
            with pytest.raises(ValidationFailed, match="not known"):
                await gz.set_address(s, HOME, address_type="HOLIDAY", person_id=ada)
            with pytest.raises(ValidationFailed, match="PREMISES address is not for a person"):
                await gz.set_address(s, HOME, address_type="PREMISES", person_id=ada)
            await gz.set_address(s, HOME, address_type="HOME", person_id=ada)
            with pytest.raises(Conflict, match="already starts on or after"):
                await gz.set_address(
                    s,
                    gz.AddressIn(
                        address_line1="9 Other Road",
                        local_government_area="Aba South",
                        postcode="450001",
                    ),
                    address_type="HOME",
                    person_id=ada,
                    effective_from=today() - timedelta(days=5),
                )


async def test_person_address_view_and_scope(sm, world):
    tenant, bodies = await _abia(sm, world)
    async with sm() as s, s.begin():
        with at(tenant, bodies["ASUBEB"]):
            ada = await _child(s, "Ada", "Okeke", bodies["ASUBEB"])
            await gz.set_address(s, HOME, address_type="HOME", person_id=ada)
    async with sm() as s:
        with at(tenant, bodies["ASUBEB"]):
            (row,) = await gz.person_addresses(s, local_government_area="Aba North")
            assert (row.first_name, row.middle_name, row.surname) == ("Ada", "Chidi", "Okeke")
            assert row.date_of_birth == date(2015, 3, 1) and row.address_type == "HOME"
            assert row.address.address_description == "Blue gate, opposite the church"
            assert await gz.person_addresses(s, postcode="440001") == []
        # The Ministry's scope does not see a learner registered under ASUBEB.
        with at(tenant, bodies["MIN-TERTIARY-EDUCATION"]):
            assert await gz.person_addresses(s) == []


async def test_api(client, sm, world):
    tenant, bodies = await _abia(sm, world)
    async with sm() as s, s.begin():
        with at(tenant, bodies["ASUBEB"]):
            ada = await _child(s, "Ada", "Okeke", bodies["ASUBEB"])
    from syntegra.iam import api as iam

    async with sm() as s, s.begin():
        with at(tenant, tenant.root_scope_node_id):
            u = await iam.create_user(
                s, tenant_id=tenant.id, login="registrar@asubeb.example", display_name="Registrar"
            )
            # The registrar is Person 360's data steward (§4, §16.1).
            await iam.grant_role(s, user_id=u.id, role_code="REG", scope_node_id=bodies["ASUBEB"])
    login = await client.post(
        "/v1/auth/dev-login", json={"tenant_code": "NG-AB", "login": "registrar@asubeb.example"}
    )
    h = {
        "Authorization": f"Bearer {login.json()['access_token']}",
        "X-Scope-Node-Id": str(bodies["ASUBEB"]),
    }
    types = (await client.get("/v1/address-types", headers=h)).json()["items"]
    assert {t["code"] for t in types} >= {"HOME", "POSTAL", "PREMISES"}
    body = {
        "address_type": "HOME",
        "address_line1": "14 Faulks Road",
        "address_line3": "Aba",
        "local_government_area": "Aba North",
        "zipcode": "450001",
        "paf_key": "ng-450001-0014",
    }
    r = await client.post(
        f"/v1/persons/{ada}/addresses", json=body, headers=h | {"Idempotency-Key": "a1"}
    )
    assert r.status_code == 201, r.text
    link = r.json()
    assert (
        link["address"]["postcode"] == "450001" and link["address"]["paf_key"] == "NG-450001-0014"
    )
    assert link["address"]["address_line3"] == "Aba" and link["address"]["address_line5"] is None
    view = (await client.get("/v1/person-addresses?lga=Aba%20North", headers=h)).json()["items"]
    assert view[0]["surname"] == "Okeke" and view[0]["address_type"] == "HOME"
    end = await client.post(
        f"/v1/party-addresses/{link['id']}/end", json={}, headers=h | {"Idempotency-Key": "a2"}
    )
    assert end.status_code == 200 and end.json()["effective_to"] == today().isoformat()
    hist = (await client.get(f"/v1/persons/{ada}/addresses?history=true", headers=h)).json()
    assert len(hist["items"]) == 1

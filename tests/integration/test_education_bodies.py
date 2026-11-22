"""Education bodies (WAEC, NECO, NABTEB, JAMB, NUC…) and institutions' links to them (ADR-019)."""

import uuid
from datetime import date

import pytest
from sqlalchemy import func, select

from syntegra.core.context import RequestContext, use_context
from syntegra.core.errors import Conflict, ValidationFailed
from syntegra.institutions import api as inst
from syntegra.institutions import bodies as eb
from syntegra.institutions.body_rules import load_catalogue
from syntegra.institutions.models import EducationBody, Institution
from tests.audit_helpers import verify_all_days
from tests.integration.test_private_registration import _officer
from tests.integration.test_tertiary_register import TERTIARY_CSV, _abia

SCHOOLS_CSV = """name,lga,levels,government_code,address_line1,town,postcode
TEST Government Secondary School Owerrinta,Isiala Ngwa South,JSS; SSS,ASB/ISS/001,1 Rd,Aba,441001
TEST Community Primary School Ndume,Umuahia North,Primary,ASB/UN/002,2 Market Road,Umuahia,440001
TEST Boys Secondary School Aba,Aba South,JSS; SSS,ASB/AS/003,3 Asa Road,Aba,450001
"""


async def _register(sm, tenant, scope, text):
    async with sm() as s, s.begin():
        with use_context(RequestContext(tenant.id, scope, None, system=True)):
            job_id = await inst.preview_import(s, text, source_name="t.csv")
            await inst.commit_import(s, job_id, managing_body="TEST")


async def _setup(sm, world):
    tenant, bodies = await _abia(sm, world)
    async with sm() as s, s.begin():
        for name in ("ng", "international"):
            await eb.seed_catalogue(s, load_catalogue(name))
    await _register(sm, tenant, bodies["ASUBEB"], SCHOOLS_CSV)
    await _register(sm, tenant, bodies["MIN-TERTIARY-EDUCATION"], TERTIARY_CSV)
    async with sm() as s:
        with use_context(RequestContext(tenant.id, tenant.root_scope_node_id, None, system=True)):
            found = (await s.execute(select(Institution))).scalars().all()
    by_name = {i.name.removeprefix("TEST "): i.id for i in found}
    return tenant, bodies, by_name


def at(tenant, scope):
    return use_context(RequestContext(tenant.id, scope, None, system=True))


# --------------------------------------------------------------------------- the register


async def test_catalogues_seed_once_and_update_in_place(sm, world):
    async with sm() as s, s.begin():
        first = await eb.seed_catalogue(s, load_catalogue("ng"))
        again = await eb.seed_catalogue(s, load_catalogue("ng"))
    assert {"WAEC", "NECO", "NABTEB", "JAMB", "NUC", "NBTE", "NCCE", "TRCN"} <= set(
        first["created"]
    )
    assert again == {"created": [], "updated": []}
    async with sm() as s:
        waec = (
            await s.execute(select(EducationBody).where(EducationBody.code == "WAEC"))
        ).scalar_one()
        assert waec.countries == ["NG", "GH", "SL", "GM", "LR"]
        assert [e["code"] for e in waec.examinations] == ["WASSCE-SC", "WASSCE-PC"]
        assert await verify_all_days(s, uuid.UUID(int=0))  # audited on the platform chain


async def test_listing_shows_the_states_own_bodies_and_external_ones(sm, world):
    tenant, _bodies, _ = await _setup(sm, world)
    async with sm() as s:
        with at(tenant, tenant.root_scope_node_id):
            internal = await eb.jurisdiction_bodies(s)
            exams = await eb.list_bodies(s, role="examination")
            ng_only = await eb.list_bodies(s, country="GH")
    assert {b["code"] for b in internal} == {"ASUBEB", "MIN-TERTIARY-EDUCATION"}
    assert {b.code for b in exams} == {
        "WAEC",
        "NECO",
        "NABTEB",
        "NBAIS",
        "CAMBRIDGE",
        "PEARSON-EDEXCEL",
        "IB",
        "COLLEGE-BOARD",
    }
    assert "WAEC" in {b.code for b in ng_only} and "NECO" not in {b.code for b in ng_only}


# --------------------------------------------------------------------------- the rules


async def test_exam_centres_fit_the_examination_and_the_school(sm, world):
    tenant, bodies, ids = await _setup(sm, world)
    sec, pri = ids["Government Secondary School Owerrinta"], ids["Community Primary School Ndume"]
    async with sm() as s, s.begin():
        with at(tenant, bodies["ASUBEB"]):
            v = await eb.link_institution(
                s,
                sec,
                body_code="waec",
                link_type="exam_centre",
                exam_code="wassce-sc",
                reference="1234567",
            )
            assert (v.body_code, v.exam_code, v.exam_name) == (
                "WAEC",
                "WASSCE-SC",
                "WASSCE for School Candidates",
            )
            await eb.link_institution(
                s,
                sec,
                body_code="NECO",
                link_type="exam_centre",
                exam_code="BECE",
                reference="N-55",
            )
            await eb.link_institution(
                s,
                pri,
                body_code="NECO",
                link_type="exam_centre",
                exam_code="NCEE",
                reference="N-77",
            )
            cases = [
                (
                    pri,
                    "WAEC",
                    "exam_centre",
                    "WASSCE-SC",
                    "999",
                    "WASSCE-SC is for SSS",
                ),
                (pri, "NECO", "exam_centre", "BECE", "N-78", "BECE is for JSS"),
                (sec, "WAEC", "exam_centre", None, "1", "say which WAEC examination"),
                (sec, "WAEC", "exam_centre", "GCE", "1", "has no examination 'GCE'"),
                (sec, "NABTEB", "exam_centre", "NBC-NTC", "", "needs NABTEB's number"),
                (sec, "WAEC", "accreditation", None, None, "does not record accreditation links"),
                (sec, "TRCN", "approval", None, None, "(it records: none)"),
            ]
            for target, body, kind, exam, ref, message in cases:
                with pytest.raises(
                    ValidationFailed, match=message.replace("(", r"\(").replace(")", r"\)")
                ):
                    await eb.link_institution(
                        s, target, body_code=body, link_type=kind, exam_code=exam, reference=ref
                    )


async def test_regulators_and_jamb_fit_tertiary_types(sm, world):
    tenant, bodies, ids = await _setup(sm, world)
    uni, poly = ids["State University"], ids["State Polytechnic"]
    async with sm() as s, s.begin():
        with at(tenant, bodies["MIN-TERTIARY-EDUCATION"]):
            nuc = await eb.link_institution(
                s,
                uni,
                body_code="NUC",
                link_type="accreditation",
                reference="NUC/ACC/2026/01",
                valid_from=date(2026, 1, 1),
                valid_to=date(2030, 12, 31),
            )
            assert nuc.current
            await eb.link_institution(
                s, uni, body_code="JAMB", link_type="admissions_code", reference="ABSU"
            )
            await eb.link_institution(
                s, poly, body_code="NBTE", link_type="accreditation", status="provisional"
            )
            with pytest.raises(ValidationFailed, match="does not cover"):
                await eb.link_institution(s, poly, body_code="NUC", link_type="accreditation")
            with pytest.raises(ValidationFailed, match="valid_to is before valid_from"):
                await eb.link_institution(
                    s,
                    poly,
                    body_code="TETFUND",
                    link_type="beneficiary",
                    valid_from=date(2026, 6, 1),
                    valid_to=date(2026, 1, 1),
                )
    async with sm() as s, s.begin():  # JAMB codes are not for schools
        with at(tenant, bodies["ASUBEB"]), pytest.raises(ValidationFailed, match="does not cover"):
            await eb.link_institution(
                s,
                ids["Government Secondary School Owerrinta"],
                body_code="JAMB",
                link_type="admissions_code",
                reference="X1",
            )


async def test_one_number_one_institution_and_one_current_link(sm, world):
    tenant, bodies, ids = await _setup(sm, world)
    sec = ids["Government Secondary School Owerrinta"]
    async with sm() as s, s.begin():
        with at(tenant, bodies["ASUBEB"]):
            first = await eb.link_institution(
                s,
                sec,
                body_code="WAEC",
                link_type="exam_centre",
                exam_code="WASSCE-SC",
                reference="1234567",
            )
            with pytest.raises(Conflict, match="already has a current WAEC WASSCE-SC exam centre"):
                await eb.link_institution(
                    s,
                    sec,
                    body_code="WAEC",
                    link_type="exam_centre",
                    exam_code="WASSCE-SC",
                    reference="7654321",
                )
    async with sm() as s, s.begin():
        with at(tenant, bodies["ASUBEB"]):
            ended = await eb.end_link(s, first.id, status="withdrawn", note="Centre moved")
            assert ended.status == "withdrawn" and not ended.current
            with pytest.raises(Conflict, match="already withdrawn"):
                await eb.end_link(s, first.id)
            await eb.link_institution(  # a new number once the old one has ended
                s,
                sec,
                body_code="WAEC",
                link_type="exam_centre",
                exam_code="WASSCE-SC",
                reference="7654321",
            )
            history = await eb.links(s, institution_id=sec)
            assert [(v.reference, v.status) for v in history] == [
                ("1234567", "withdrawn"),
                ("7654321", "active"),
            ]


async def test_a_body_number_belongs_to_one_institution(sm, world):
    tenant, bodies, ids = await _setup(sm, world)
    one, two = ids["Government Secondary School Owerrinta"], ids["Boys Secondary School Aba"]
    waec = {"body_code": "WAEC", "link_type": "exam_centre", "reference": "1234567"}
    async with sm() as s, s.begin():
        with at(tenant, bodies["ASUBEB"]):
            await eb.link_institution(s, one, exam_code="WASSCE-SC", **waec)
    async with sm() as s, s.begin():
        with at(tenant, bodies["ASUBEB"]):
            with pytest.raises(Conflict, match="already recorded for another institution"):
                await eb.link_institution(s, two, exam_code="WASSCE-SC", **waec)
            # The same digits for another examination are a different number: allowed.
            await eb.link_institution(s, two, exam_code="WASSCE-PC", **waec)


async def test_links_follow_scope(sm, world):
    tenant, bodies, ids = await _setup(sm, world)
    async with sm() as s, s.begin():
        with at(tenant, bodies["ASUBEB"]):
            await eb.link_institution(
                s,
                ids["Government Secondary School Owerrinta"],
                body_code="WAEC",
                link_type="exam_centre",
                exam_code="WASSCE-SC",
                reference="1234567",
            )
    async with sm() as s:
        with at(tenant, bodies["ASUBEB"]):
            assert [v.reference for v in await eb.links(s, body_code="WAEC")] == ["1234567"]
        with at(
            tenant, bodies["MIN-TERTIARY-EDUCATION"]
        ):  # the Ministry does not see ASUBEB's schools
            assert await eb.links(s, body_code="WAEC") == []
        with at(tenant, tenant.root_scope_node_id):  # the state sees everything
            assert len(await eb.links(s, body_code="WAEC")) == 1


# --------------------------------------------------------------------------- bulk


async def test_a_bodys_centre_list_is_checked_then_recorded(sm, world):
    tenant, bodies, _ = await _setup(sm, world)
    csv_text = """institution,body,link_type,exam,reference,status,valid_from,valid_to,note
ASB/ISS/001,WAEC,exam_centre,WASSCE-SC,1234567,,,,
ASB/ISS/001,NECO,exam_centre,SSCE-INT,N-901,active,,,
ASB/UN/002,NECO,exam_centre,NCEE,N-902,,,,
ASB/UN/002,WAEC,exam_centre,WASSCE-SC,1234568,,,,
ASB/NOPE/9,WAEC,exam_centre,WASSCE-SC,1234569,,,,
ASB/ISS/001,NABTEB,exam_centre,NBC-NTC,N-901,,2026-13-01,,
ASB/UN/002,NECO,exam_centre,NCEE,N-902,,,,
"""
    async with sm() as s, s.begin():
        with at(tenant, bodies["ASUBEB"]):
            summary, problems = await eb.import_links(s, csv_text, commit=False)
            assert summary["valid"] == 3 and summary["recorded"] == 0
            assert summary["by_body"] == {"WAEC": 1, "NECO": 2}
            msgs = dict(problems)
            assert "WASSCE-SC is for SSS" in msgs[5]
            assert "not found in your scope" in msgs[6]
            assert "must be a date" in msgs[7]
            assert "also appears on row 4" in msgs[8]
            assert await eb.links(s) == []  # nothing recorded on a check
            summary, _ = await eb.import_links(s, csv_text, commit=True)
            assert summary["recorded"] == 3 and len(await eb.links(s)) == 3


async def test_bulk_file_needs_the_columns(sm, world):
    tenant, bodies, _ = await _setup(sm, world)
    async with sm() as s:
        with at(tenant, bodies["ASUBEB"]), pytest.raises(ValidationFailed, match="link_type"):
            await eb.import_links(s, "institution,body\nX,WAEC\n", commit=False)


# --------------------------------------------------------------------------- API


async def test_api(client, sm, world):
    tenant, bodies, ids = await _setup(sm, world)
    await _officer(sm, tenant, bodies["ASUBEB"])
    login = await client.post(
        "/v1/auth/dev-login", json={"tenant_code": "NG-AB", "login": "officer@abia.example"}
    )
    h = {
        "Authorization": f"Bearer {login.json()['access_token']}",
        "X-Scope-Node-Id": str(bodies["ASUBEB"]),
    }
    listing = (await client.get("/v1/education-bodies?role=admissions", headers=h)).json()
    assert [b["code"] for b in listing["external"]] == ["JAMB"]
    assert listing["external"][0]["links_recorded"] == ["admissions_code"]
    assert {b["code"] for b in listing["internal"]} == {"ASUBEB", "MIN-TERTIARY-EDUCATION"}
    assert (await client.get("/v1/education-bodies/neco", headers=h)).json()["examinations"][3][
        "code"
    ] == "NCEE"
    assert (await client.get("/v1/education-bodies/NOPE", headers=h)).status_code == 404

    sec = ids["Government Secondary School Owerrinta"]
    body = {"body": "WAEC", "link_type": "exam_centre", "exam": "WASSCE-SC", "reference": "1234567"}
    r = await client.post(
        f"/v1/institutions/{sec}/bodies", json=body, headers=h | {"Idempotency-Key": "k-1"}
    )
    assert r.status_code == 201, r.text
    assert r.json()["exam"]["name"] == "WASSCE for School Candidates" and r.json()["current"]
    again = await client.post(
        f"/v1/institutions/{sec}/bodies", json=body, headers=h | {"Idempotency-Key": "k-1"}
    )
    assert (
        again.status_code == 201 and again.json()["id"] == r.json()["id"]
    )  # replayed, not duplicated
    bad = await client.post(
        f"/v1/institutions/{sec}/bodies",
        json=body | {"reference": "999"},
        headers=h | {"Idempotency-Key": "k-2"},
    )
    assert bad.status_code == 409
    centres = (await client.get("/v1/education-bodies/WAEC/institutions", headers=h)).json()[
        "items"
    ]
    assert [c["institution"]["name"] for c in centres] == [
        "TEST Government Secondary School Owerrinta"
    ]
    end = await client.post(
        f"/v1/institution-body-links/{r.json()['id']}/end",
        json={"status": "suspended"},
        headers=h | {"Idempotency-Key": "k-3"},
    )
    assert end.status_code == 200 and end.json()["status"] == "suspended"
    assert (await client.get("/v1/education-bodies/WAEC/institutions", headers=h)).json()[
        "items"
    ] == []
    mine = (await client.get(f"/v1/institutions/{sec}/bodies", headers=h)).json()["items"]
    assert [m["status"] for m in mine] == ["suspended"]
    bulk = await client.post(
        "/v1/bulk/institution-body-links",
        content="institution,body,link_type,exam,reference\nASB/UN/002,NECO,exam_centre,NCEE,N-1\n",
        headers=h | {"Idempotency-Key": "k-4"},
    )
    assert bulk.status_code == 200 and bulk.json()["summary"]["valid"] == 1
    async with sm() as s:
        assert await verify_all_days(s, tenant.id)
        assert (await s.execute(select(func.count()).select_from(EducationBody))).scalar() == 18

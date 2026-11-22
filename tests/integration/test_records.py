"""Document and records management (ADR-027)."""

import hashlib
from datetime import date, timedelta

import orjson
import pytest
from sqlalchemy import select

from syntegra.core.clock import today
from syntegra.core.errors import Conflict, Forbidden, InvariantViolation, NotFound, ValidationFailed
from syntegra.iam import api as iam
from syntegra.platform.blobstore import BlobExists, LocalBlobStore
from syntegra.records import api as rec
from syntegra.records.domain import (
    Numbering,
    check_content,
    file_number,
    model_plan,
    paper_due,
    plan_for_levels,
    search_terms,
)
from syntegra.records.models import Document, DocumentVersion
from tests.audit_helpers import verify_all_days
from tests.integration.test_staff import _person, _world, as_

PDF = b"%PDF-1.7\n1 0 obj << /Type /Catalog >> endobj\n%%EOF\n"
PDF2 = b"%PDF-1.7\n% rescanned at 300 dpi\n%%EOF\n"


def pdf(content=PDF, **kw):
    return rec.ContentIn(content=content, mime="application/pdf", **kw)


# --------------------------------------------------------------------------- pure rules


def test_rules():
    for plan, n in (("primary", 35), ("secondary", 39), ("tertiary", 32)):
        title, series = model_plan(plan)
        assert len(series) == n and title.startswith("Model file plan")
    _, sec = model_plan("secondary")
    assert {s.code for s in sec} >= {"LRN/1", "EXM/2", "STF/1"}
    assert plan_for_levels({"primary"}) == "primary"
    assert plan_for_levels({"primary", "jss"}) == "secondary"
    assert plan_for_levels({"degree"}) == "tertiary"
    assert file_number("FIN/1", Numbering.ANNUAL, seq=3, year=2026) == "FIN/1/2026/3"
    assert file_number("STF/1", Numbering.SELF, seq=42) == "STF/1/0042"
    assert file_number("GOV/4", Numbering.ALPHABETICAL, seq=2, letter="a") == "GOV/4/A/2"
    assert file_number("GOV/6", Numbering.SINGLE, seq=1) == "GOV/6"
    assert paper_due(date(2024, 2, 29), 6) == date(2030, 2, 28)
    assert paper_due(date(2020, 1, 1), None) is None
    assert check_content("application/pdf", PDF[:8]) is None
    assert "not a valid" in check_content("application/pdf", b"GIF89a")
    assert "file type" in check_content("text/html", b"<html>")
    assert search_terms("Report-Card: Ada  OKEKE!", None) == "report card ada okeke"


async def test_local_store_encrypts_and_never_overwrites(tmp_path):
    store = LocalBlobStore(b"k" * 32, tmp_path)
    sha = hashlib.sha256(PDF).hexdigest()
    await store.put("t/d/v1", PDF, content_type="application/pdf", sha256=sha)
    assert await store.get("t/d/v1") == PDF
    assert PDF not in next(tmp_path.iterdir()).read_bytes()  # AES-256-GCM at rest
    with pytest.raises(BlobExists):
        await store.put("t/d/v1", PDF, content_type="application/pdf", sha256=sha)
    with pytest.raises(ValueError, match="checksum"):
        await store.put("t/d/v2", PDF, content_type="application/pdf", sha256="0" * 64)


# --------------------------------------------------------------------------- set-up


async def _user(s, a, login, role, scope):
    u = await iam.create_user(s, tenant_id=a.tenant.id, login=login, display_name=login)
    await iam.grant_role(s, user_id=u.id, role_code=role, scope_node_id=scope)
    return u.id


async def _setup(sm, world):
    a = await _world(sm, world)
    a.school = a.aba.scope_node_id
    a.store = LocalBlobStore(b"s" * 32)
    async with sm() as s, s.begin(), as_(a, scope=a.tenant.root_scope_node_id):
        a.rec = await _user(s, a, "records@central.example", "REC", a.school)
        a.rec2 = await _user(s, a, "records2@central.example", "REC", a.school)
        a.head = await _user(s, a, "head@central.example", "HEAD", a.school)
        a.iadm = await _user(s, a, "admin@central.example", "IADM", a.school)
        a.tch = await _user(s, a, "teacher@central.example", "TCH", a.school)
        a.capt = await _user(s, a, "scanner@contractor.example", "CAPT", a.school)
        a.other_capt = await _user(s, a, "other@contractor.example", "CAPT", a.school)
        a.arch = await _user(s, a, "archives@abia.example", "ARCH", a.tenant.root_scope_node_id)
    async with sm() as s, s.begin(), as_(a, a.rec, a.school):
        plan = await rec.adopt_plan(s, a.aba.id)
        a.series = {x.code: x for x in plan}
        a.pupil = await _person(s, "Chidi", "Eze", born=date(2016, 5, 1), sex="M")
    return a


# --------------------------------------------------------------------------- file plan and files


async def test_file_plan_numbering_and_tracking(sm, world):
    a = await _setup(sm, world)
    assert len(a.series) == 35 and a.series["LRN/2"].numbering == "self"
    async with sm() as s, s.begin(), as_(a, a.rec, a.school):
        assert len(await rec.adopt_plan(s, a.aba.id)) == 35  # adopting again adds nothing
        with pytest.raises(NotFound):  # another school is outside this officer's scope
            await rec.suggested_plan(s, a.umu.id)
        with as_(a, scope=a.asubeb):
            assert await rec.suggested_plan(s, a.umu.id) == "secondary"
        local = await rec.add_series(
            s,
            a.aba.id,
            rec.SeriesIn(
                function_code="pri",
                function_name="Primary education",
                code="pri/9",
                title="Community volunteers",
                numbering="annual",
                classification="internal",
                paper_rule="destroy",
                paper_retain_years=3,
            ),
        )
        assert (local.code, local.plan) == ("PRI/9", "local")
        with pytest.raises(Conflict, match="already exists"):
            await rec.add_series(
                s,
                a.aba.id,
                rec.SeriesIn(
                    "PRI", "Primary", "PRI/9", "Again", "annual", "internal", "destroy", 3
                ),
            )
        # Personal files: one per person, numbered within the series.
        with pytest.raises(ValidationFailed, match="say whose file"):
            await rec.open_file(s, a.series["LRN/2"].id, rec.FileIn("Eze, Chidi", "Registry C1"))
        f = await rec.open_file(
            s,
            a.series["LRN/2"].id,
            rec.FileIn("Eze, Chidi", "Registry C1", person_id=a.pupil, keywords=["Admission"]),
        )
        assert f.file_number == "LRN/2/0001" and f.reference.endswith("LRN/2/0001")
        assert f.classification == "confidential" and f.keywords == ["admission"]
        with pytest.raises(Conflict, match="already has an open file"):
            await rec.open_file(
                s, a.series["LRN/2"].id, rec.FileIn("Eze", "Registry", person_id=a.pupil)
            )
        with pytest.raises(ValidationFailed, match="raised, not lowered"):
            await rec.open_file(
                s,
                a.series["LRN/2"].id,
                rec.FileIn("X", "Registry", person_id=a.tenant.id, classification="internal"),
            )
        # Annual files restart each year; single files exist once and continue in volumes.
        y = today().year
        fin = [
            (await rec.open_file(s, a.series["FIN/2"].id, rec.FileIn(t, "Bursary"))).file_number
            for t in ("Fees first term", "Fees second term")
        ]
        assert fin == [f"FIN/2/{y}/1", f"FIN/2/{y}/2"]
        log = await rec.open_file(s, a.series["GOV/6"].id, rec.FileIn("Log book", "Head's office"))
        with pytest.raises(Conflict, match="single running file"):
            await rec.open_file(s, a.series["GOV/6"].id, rec.FileIn("Log book 2", "Office"))
        v2 = await rec.new_volume(s, log.id)
        assert (v2.file_number, v2.volume, v2.reference.endswith("GOV/6 Vol 2")) == (
            "GOV/6",
            2,
            True,
        )
        assert (await rec.get_file(s, log.id)).status == "closed"
        # Tracking: issue, pass, return; overdue files are listed.
        moved = await rec.move_file(s, f.id, "issue", to_user_id=a.head)
        assert moved.out and moved.due_back == today() + timedelta(days=7)
        with pytest.raises(Conflict, match="already out"):
            await rec.move_file(s, f.id, "issue", to_user_id=a.tch)
        with pytest.raises(Conflict, match="must be back"):
            await rec.close_file(s, f.id)
        await rec.move_file(s, f.id, "pass", to_user_id=a.tch, note="for the transfer letter")
        fees = (await rec.file_list(s, a.aba.id, series_id=a.series["FIN/2"].id))[0]
        sent = await rec.move_file(s, fees.id, "external_out", to_name="LGEA Aba North")
        assert sent.holder_name == "LGEA Aba North"
        back = await rec.move_file(s, f.id, "return")
        assert not back.out and back.holder_user_id is None
        assert [m.kind for m in await rec.file_history(s, f.id)] == ["issue", "pass", "return"]
        listed = await rec.file_list(s, a.aba.id)
        assert [x.file_number for x in listed][:2] == [f"FIN/2/{y}/1", f"FIN/2/{y}/2"]
    async with sm() as s, s.begin(), as_(a, a.tch, a.school):
        # A teacher reads Internal records only: confidential files are not listed.
        assert {x.classification for x in await rec.file_list(s, a.aba.id)} <= {"internal"}
        with pytest.raises(NotFound):
            await rec.get_file(s, f.id)
        assert await verify_all_days(s, a.tenant.id)


# --------------------------------------------------------------------------- capture to verify


async def _captured(sm, a):
    """A contractor batch with two scans from a learner's file, checked, indexed, verified."""
    async with sm() as s, s.begin(), as_(a, a.rec, a.school):
        a.file = await rec.open_file(
            s,
            a.series["LRN/2"].id,
            rec.FileIn("Eze, Chidi", "Registry C1", person_id=a.pupil, opened_on=date(2010, 1, 4)),
        )
        b = await rec.open_batch(
            s,
            a.aba.id,
            rec.BatchIn(
                description="Learner files A-E, box 1",
                operator_kind="contractor",
                operator_user_id=a.capt,
                contractor_name="Imaging Services Ltd",
                contract_reference="ASUBEB/DIG/2026/01",
            ),
        )
        assert b.batch_number.startswith(f"BAT-{today().year}-")
        with pytest.raises(ValidationFailed, match="name the contractor"):
            await rec.open_batch(s, a.aba.id, rec.BatchIn("Box 2", "contractor", a.capt))
    async with sm() as s, s.begin(), as_(a, a.other_capt, a.school):
        with pytest.raises(Forbidden, match="only the operator"):
            await rec.capture(s, a.store, b.id, rec.CaptureIn(title="Admission form", file=pdf()))
        assert await rec.my_batches(s) == []
    async with sm() as s, s.begin(), as_(a, a.capt, a.school):
        d1 = await rec.capture(
            s,
            a.store,
            b.id,
            rec.CaptureIn(
                title="Scan 1",
                file=pdf(ocr_text="ADMISSION FORM Chidi Eze Primary 1", page_count=2),
                file_id=a.file.id,
                folio=1,
                proposed={
                    "title": "Admission form, Chidi Eze",
                    "doc_type": "admission_form",
                    "document_date": "2016-09-12",
                    "keywords": ["admission", "Primary 1"],
                },
            ),
        )
        assert d1.document_number.startswith(f"DOC-NG-AB-{today().year}-")
        assert (d1.status, d1.classification, d1.page_count) == ("captured", "confidential", 2)
        with pytest.raises(ValidationFailed, match="not a valid"):
            await rec.capture(
                s,
                a.store,
                b.id,
                rec.CaptureIn("Bad", rec.ContentIn(b"<html>", "application/pdf")),
            )
        d2 = await rec.capture(
            s,
            a.store,
            b.id,
            rec.CaptureIn(
                "Medical note",
                pdf(ocr_text="sickle cell clinic letter"),
                file_id=a.file.id,
                folio=2,
                doc_type="letter",
                classification="restricted",
            ),
        )
        [mine] = await rec.my_batches(s)
        assert (mine.documents, mine.pages) == (2, 2)
        await rec.finish_batch(s, b.id)
        with pytest.raises(Conflict, match="cannot check their own"):
            await rec.check_batch(s, b.id, sampled=2, defects=0, accept=True)
    async with sm() as s, s.begin(), as_(a, a.rec, a.school):
        back = await rec.check_batch(
            s, b.id, sampled=2, defects=1, accept=False, note="Scan 1 page 2 skewed"
        )
        assert back.status == "rejected"
    async with sm() as s, s.begin(), as_(a, a.capt, a.school):
        again = await rec.add_version(
            s, a.store, d1.id, pdf(PDF2, page_count=2), note="Rescanned: page 2 skewed"
        )
        assert [v.version for v in again.versions] == [1, 2]
        await rec.finish_batch(s, b.id)
    async with sm() as s, s.begin(), as_(a, a.rec, a.school):
        assert (await rec.check_batch(s, b.id, sampled=1, defects=0, accept=True)).status == (
            "accepted"
        )
        idx = await rec.index(
            s,
            d1.id,
            rec.IndexIn(accept_proposed=True, links=[("person", a.pupil), ("learner", a.pupil)]),
        )
        assert idx.document.title == "Admission form, Chidi Eze"
        assert (idx.document.doc_type, idx.document.document_date) == (
            "admission_form",
            date(2016, 9, 12),
        )
        assert idx.file_reference.endswith("LRN/2/0001")
        await rec.index(s, d2.id, rec.IndexIn(keywords=["health"]))
        with pytest.raises(Conflict, match="cannot verify"):
            await rec.verify(s, d1.id)
    async with sm() as s, s.begin(), as_(a, a.iadm, a.school):
        with pytest.raises(Conflict, match="not verified yet"):
            await rec.mark_digitised(s, a.file.id)
        assert (await rec.verify(s, d1.id)).status == "verified"
        with pytest.raises(NotFound):  # Restricted: not for the institution administrator
            await rec.verify(s, d2.id)
    async with sm() as s, s.begin(), as_(a, a.rec2, a.school):
        await rec.verify(s, d2.id)
        assert (await rec.mark_digitised(s, a.file.id)).digitised
    a.d1, a.d2, a.batch = d1, d2, b
    return a


async def test_capture_qc_index_verify(sm, world):
    a = await _captured(sm, await _setup(sm, world))
    async with sm() as s, s.begin(), as_(a, a.rec, a.school):
        got = await rec.download(s, a.store, a.d1.id)
        assert got.content == PDF2 and got.filename.endswith("-v2.pdf")
        assert (await rec.download(s, a.store, a.d1.id, version=1)).content == PDF
        hits = await rec.search(s, rec.SearchIn(text="admission eze"))
        assert [h.id for h in hits] == [a.d1.id]
        assert [h.id for h in await rec.search(s, rec.SearchIn(text="sickle"))] == [a.d2.id]
    # The medical note's OCR text is searchable only by those who may read Restricted records.
    async with sm() as s, s.begin(), as_(a, a.tch, a.school):
        assert await rec.search(s, rec.SearchIn(text="sickle")) == []
    async with sm() as s, s.begin(), as_(a, a.arch, a.tenant.root_scope_node_id):
        assert [h.id for h in await rec.search(s, rec.SearchIn(text="sickle"))] == [a.d2.id]
        assert [h.id for h in await rec.documents_about(s, "learner", a.pupil)] == [a.d1.id]


async def test_access_is_trimmed_purposed_and_logged(sm, world):
    a = await _captured(sm, await _setup(sm, world))
    async with sm() as s, s.begin(), as_(a, a.tch, a.school):
        assert await rec.search(s, rec.SearchIn()) == []  # confidential and restricted only
        with pytest.raises(NotFound):
            await rec.get_document(s, a.d1.id)
    async with sm() as s, s.begin(), as_(a, a.head, a.school):
        assert {d.id for d in await rec.search(s, rec.SearchIn())} == {a.d1.id, a.d2.id}
        with pytest.raises(ValidationFailed, match="stated purpose"):
            await rec.get_document(s, a.d2.id)
        seen = await rec.get_document(s, a.d2.id, purpose="Referral to the LGEA health desk")
        assert seen.document.classification == "restricted"
        await rec.get_document(s, a.d1.id)
    # The State Archives see every school's records from the state root, and the log.
    async with sm() as s, s.begin(), as_(a, a.arch, a.tenant.root_scope_node_id):
        log = await rec.access_log(s, a.d2.id)
        assert [(x.user_id, x.action, x.purpose) for x in log] == [
            (a.head, "view", "Referral to the LGEA health desk")
        ]
        [row] = [r for r in await rec.repository_summary(s) if r.institution_id == a.aba.id]
        assert (row.series, row.documents, row.documents_verified, row.files_digitised) == (
            35,
            2,
            2,
            1,
        )
        assert row.by_classification == {"confidential": 1, "restricted": 1}
        # Only State oversight may lower a classification.
        low = await rec.reclassify(s, a.d2.id, "confidential", reason="Redacted copy")
        assert low.classification == "confidential"
    async with sm() as s, s.begin(), as_(a, a.rec, a.school):
        with pytest.raises(Forbidden, match="records:oversight"):
            await rec.reclassify(s, a.d1.id, "internal", reason="Not sensitive")
        up = await rec.reclassify(s, a.d1.id, "restricted", reason="Contains medical history")
        assert up.classification == "restricted"


async def test_records_are_permanent(sm, world):
    a = await _captured(sm, await _setup(sm, world))
    async with sm() as s:
        with as_(a, a.rec, a.school):
            doc = (await s.execute(select(Document).where(Document.id == a.d1.id))).scalar_one()
            await s.delete(doc)
            with pytest.raises(InvariantViolation, match="kept permanently"):
                await s.flush()
            await s.rollback()
    async with sm() as s:
        with as_(a, a.rec, a.school):
            v = (
                (
                    await s.execute(
                        select(DocumentVersion).where(DocumentVersion.document_id == a.d1.id)
                    )
                )
                .scalars()
                .first()
            )
            v.note = "edited"
            with pytest.raises(InvariantViolation, match="evidential"):
                await s.flush()
            await s.rollback()


async def test_paper_disposition_after_digitisation(sm, world):
    a = await _captured(sm, await _setup(sm, world))
    async with sm() as s, s.begin(), as_(a, a.rec, a.school):
        with pytest.raises(Conflict, match="only a closed file"):
            await rec.request_disposition(s, a.file.id, "destroy", reason="LRN/2: 6 years")
        await rec.close_file(s, a.file.id, closed_on=date(2012, 7, 20))
        assert [f.id for f in await rec.due_for_disposition(s, a.aba.id)] == [a.file.id]
        await rec.set_legal_hold(s, a.file.id, True)
        with pytest.raises(Conflict, match="legal hold"):
            await rec.request_disposition(s, a.file.id, "destroy", reason="LRN/2: 6 years")
        await rec.set_legal_hold(s, a.file.id, False)
        p = await rec.request_disposition(s, a.file.id, "destroy", reason="LRN/2: 6 years")
        with pytest.raises(Conflict, match="four-eyes"):
            await rec.decide_disposition(s, p.id, approve=True, certificate_reference="DC-1")
    async with sm() as s, s.begin(), as_(a, a.head, a.school):
        with pytest.raises(ValidationFailed, match="destruction certificate"):
            await rec.decide_disposition(s, p.id, approve=True)
        done = await rec.decide_disposition(
            s, p.id, approve=True, certificate_reference="ASUBEB/DC/2026/014"
        )
        assert done.status == "approved"
        assert (await rec.get_file(s, a.file.id)).status == "disposed"
        # The paper is gone; the digital record stays, permanently.
        assert (await rec.download(s, a.store, a.d1.id)).content == PDF2


async def test_destruction_needs_digitisation(sm, world):
    a = await _setup(sm, world)
    async with sm() as s, s.begin(), as_(a, a.rec, a.school):
        f = await rec.open_file(
            s,
            a.series["LRN/3"].id,
            rec.FileIn("Attendance 2011", "Store", year=2011, opened_on=date(2011, 1, 10)),
        )
        await rec.close_file(s, f.id, closed_on=date(2011, 12, 15))
        with pytest.raises(Conflict, match="only after it has been digitised"):
            await rec.request_disposition(s, f.id, "destroy", reason="LRN/3: 6 years")
        kept = await rec.request_disposition(s, f.id, "retain", reason="Historic value")
        assert kept.action == "retain"
        reg = await rec.open_file(
            s, a.series["LRN/1"].id, rec.FileIn("Admission register", "Strongroom")
        )
        await rec.close_file(s, reg.id)
        with pytest.raises(Conflict, match="keeps this paper permanently"):
            await rec.request_disposition(s, reg.id, "destroy", reason="space")


# --------------------------------------------------------------------------- HTTP


async def test_api(client, sm, world):
    a = await _setup(sm, world)

    async def login(name, scope):
        r = await client.post("/v1/auth/dev-login", json={"tenant_code": "NG-AB", "login": name})
        return {
            "Authorization": f"Bearer {r.json()['access_token']}",
            "X-Scope-Node-Id": str(scope),
        }

    recs = await login("records@central.example", a.school)
    tch = await login("teacher@central.example", a.school)
    arch = await login("archives@abia.example", a.tenant.root_scope_node_id)
    r = await client.get("/v1/model-file-plans/tertiary", headers=tch)
    assert r.status_code == 200 and len(r.json()["series"]) == 32
    r = await client.get(f"/v1/institutions/{a.aba.id}/file-plan", headers=recs)
    assert r.json()["suggested_plan"] == "primary" and len(r.json()["items"]) == 35
    r = await client.post(
        f"/v1/records-series/{a.series['GOV/2'].id}/files",
        json={"title": "SBMC and PTA minutes", "home_location": "Head's office"},
        headers=recs | {"Idempotency-Key": "f1"},
    )
    assert r.status_code == 201, r.text
    fid = r.json()["id"]
    meta = {
        "title": "PTA minutes, March",
        "file_id": fid,
        "doc_type": "minutes",
        "ocr_text": "parents teachers association levy",
        "source": "upload",
    }
    r = await client.post(
        f"/v1/institutions/{a.aba.id}/documents",
        files={"file": ("minutes.pdf", PDF, "application/pdf")},
        data={"metadata": orjson.dumps(meta).decode()},
        headers=recs | {"Idempotency-Key": "u1"},
    )
    assert r.status_code == 201, r.text
    doc = r.json()
    assert doc["classification"] == "internal" and doc["status"] == "captured"
    r = await client.post(
        f"/v1/institutions/{a.aba.id}/documents",
        files={"file": ("x.pdf", b"not a pdf", "application/pdf")},
        data={"metadata": orjson.dumps(meta | {"title": "Bad"}).decode()},
        headers=recs | {"Idempotency-Key": "u2"},
    )
    assert r.status_code == 422 and "not a valid" in r.text
    # A teacher finds Internal minutes and downloads them, checked against SHA-256.
    r = await client.get("/v1/documents", params={"q": "levy"}, headers=tch)
    assert [d["id"] for d in r.json()["items"]] == [doc["id"]]
    r = await client.get(f"/v1/documents/{doc['id']}/content", headers=tch)
    assert r.content == PDF and r.headers["x-content-sha256"] == hashlib.sha256(PDF).hexdigest()
    r = await client.post(
        f"/v1/documents/{doc['id']}/index",
        json={"keywords": ["pta", "levy"]},
        headers=tch | {"Idempotency-Key": "i1"},
    )
    assert r.status_code == 403  # teachers do not index
    r = await client.get(f"/v1/institutions/{a.aba.id}/files.csv", headers=recs)
    assert r.headers["content-type"].startswith("text/csv")
    lines = r.text.strip().splitlines()
    assert lines[0].startswith("reference,title") and "SBMC and PTA minutes" in lines[1]
    r = await client.get("/v1/records/repository", headers=arch)
    assert r.status_code == 200
    assert any(x["institution_id"] == str(a.aba.id) for x in r.json()["items"])
    r = await client.get("/v1/records/repository", headers=tch)
    assert r.status_code == 403
    r = await client.get(f"/v1/documents/{doc['id']}/access-log", headers=arch)
    assert [x["action"] for x in r.json()["items"]] == ["download"]


async def test_first_filing_sets_a_loose_scans_classification(sm, world):
    a = await _setup(sm, world)
    async with sm() as s, s.begin(), as_(a, a.rec, a.school):
        minutes = await rec.open_file(
            s, a.series["GOV/2"].id, rec.FileIn("PTA minutes 2025", "Office", year=2025)
        )
        b = await rec.open_batch(s, a.aba.id, rec.BatchIn("Box 1", "in_house", a.capt))
    async with sm() as s, s.begin(), as_(a, a.capt, a.school):
        loose = await rec.capture(s, a.store, b.id, rec.CaptureIn("Scan 0001", pdf()))
        held = await rec.capture(
            s, a.store, b.id, rec.CaptureIn("Scan 0002", pdf(), classification="restricted")
        )
        assert loose.classification == "confidential"  # the safe default until it is filed
        # The operator sees what is in their batch (to rescan), never the content.
        assert [x.document_number for x in await rec.batch_documents(s, b.id)] == [
            loose.document_number,
            held.document_number,
        ]
    async with sm() as s, s.begin(), as_(a, a.other_capt, a.school):
        with pytest.raises(Forbidden):
            await rec.batch_documents(s, b.id)
    async with sm() as s, s.begin(), as_(a, a.rec, a.school):
        filed = await rec.index(s, loose.id, rec.IndexIn(file_id=minutes.id, folio=1))
        assert filed.document.classification == "internal"  # the file decides
        kept = await rec.index(s, held.id, rec.IndexIn(file_id=minutes.id, folio=2))
        assert kept.document.classification == "restricted"  # an explicit level is never lowered
        moved = await rec.index(s, loose.id, rec.IndexIn(file_id=minutes.id))
        assert moved.document.classification == "internal"
        found = await rec.search(s, rec.SearchIn(batch_id=b.id, status="indexed"))
        assert {d.id for d in found} == {loose.id, held.id}
    async with sm() as s, s.begin(), as_(a, a.tch, a.school):
        assert [d.id for d in await rec.search(s, rec.SearchIn(batch_id=b.id))] == [loose.id]

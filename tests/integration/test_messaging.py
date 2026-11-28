"""Secure messaging between authorities, schools and parents (CEDA §26, ADR-030)."""

import re

import pytest
from sqlalchemy import select, update

from syntegra.academic import api as acad
from syntegra.core.errors import Forbidden, NotFound, ValidationFailed
from syntegra.iam.models import AppUser
from syntegra.learners import api as lrn
from syntegra.messaging import api as msg
from syntegra.messaging.models import (
    ConversationAccess,
    Message,
    MessageAttachment,
    MessageEdit,
)
from syntegra.platform.blobstore import LocalBlobStore
from syntegra.platform.crypto import DecryptionError, build_protected_cipher
from syntegra.platform.models import OutboundEmail
from tests.audit_helpers import verify_all_days
from tests.integration.test_learners import _setup, _user, pupil
from tests.integration.test_staff import CRYPTO, as_

KEYS = msg.Keys(CRYPTO.cipher, build_protected_cipher(env="test", kek_b64=None))
PDF = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\ntrailer\n%%EOF\n"


async def _people(sm, world):
    """Ada teaches Primary 4A at Central School Aba; Emeka teaches at Township, Umuahia.
    Chidi is in 4A; his mother Ngozi gets a parent account. Femi's father is at Township."""
    a = await _setup(sm, world)
    root = a.tenant.root_scope_node_id
    a.store = LocalBlobStore(b"m" * 32)
    async with sm() as s, s.begin(), as_(a, scope=root):
        a.ada = await _user(s, a, "ada@central.example", "TCH", a.school)
        a.emeka = await _user(s, a, "emeka@township.example", "TCH", a.umu.scope_node_id)
        await s.execute(update(AppUser).where(AppUser.id == a.ada).values(person_id=a.t1.person_id))
        await s.execute(
            update(AppUser).where(AppUser.id == a.emeka).values(person_id=a.t_umu.person_id)
        )
    async with sm() as s, s.begin(), as_(a, a.reg, a.school):
        e = await lrn.register_direct(
            s, CRYPTO, a.aba.id, pupil(), lrn.EnrolIn(a.s1.id, "P4", class_id=a.p4a.id)
        )
        a.chidi = e.learner_id
        a.ngozi_person = (await lrn.get_learner(s, a.chidi)).guardians[0].person_id
        acct = await lrn.give_guardian_account(
            s, a.chidi, a.ngozi_person, "Ngozi.Eze@Example.com", sign_in_url="http://t/app/"
        )
        assert acct.created and acct.login == "ngozi.eze@example.com"
        a.ngozi = acct.user_id
        again = await lrn.give_guardian_account(
            s, a.chidi, a.ngozi_person, "ngozi.eze@example.com", sign_in_url="http://t/app/"
        )
        assert not again.created and again.user_id == a.ngozi
        assert (await lrn.get_learner(s, a.chidi)).guardians[0].has_account
    async with sm() as s, s.begin(), as_(a, a.reg_umu, a.umu.scope_node_id):
        sessions = await acad.sessions(s)
        e = await lrn.register_direct(
            s,
            CRYPTO,
            a.umu.id,
            pupil("Femi", "Ade", guardian_name="Tunde Ade", guardian_phone="08035550000"),
            lrn.EnrolIn(sessions[0].id, "P4"),
        )
        tunde = (await lrn.get_learner(s, e.learner_id)).guardians[0].person_id
        a.tunde = (
            await lrn.give_guardian_account(
                s, e.learner_id, tunde, "tunde@example.com", sign_in_url="http://t/app/"
            )
        ).user_id
    return a


async def test_who_can_message_whom(sm, world):
    a = await _people(sm, world)
    async with sm() as s, s.begin(), as_(a, a.ngozi, a.school):
        me = await msg._person(s, a.ngozi)
        assert me.is_parent
        assert await msg.can_contact(s, me, await msg._person(s, a.ada))  # her child's teacher
        assert await msg.can_contact(s, me, await msg._person(s, a.head))
        assert not await msg.can_contact(s, me, await msg._person(s, a.emeka))  # another school
        assert not await msg.can_contact(s, me, await msg._person(s, a.tunde))  # never parents
        assert not await msg.can_contact(s, me, await msg._person(s, a.sta))  # not the ministry
        with pytest.raises(Forbidden, match="cannot message"):
            await msg.open_direct(s, KEYS, a.emeka)
        with pytest.raises(Forbidden, match="parents cannot create groups"):
            await msg.create_group(s, KEYS, title="Parents", member_ids=[a.ada])
        with pytest.raises(Forbidden, match="only staff"):
            await msg.open_class_conversation(s, KEYS, a.p4a.id)
        found = await msg.contacts(s, "ada")
        assert [c.display_name for c in found] == ["ada@central.example"]
        assert found[0].role == "Teacher"
        assert await msg.contacts(s, "tunde") == []
    async with sm() as s, s.begin(), as_(a, a.ada, a.school):
        me = await msg._person(s, a.ada)
        assert await msg.can_contact(s, me, await msg._person(s, a.ngozi))
        assert await msg.can_contact(s, me, await msg._person(s, a.head))
        assert await msg.can_contact(s, me, await msg._person(s, a.sta))  # up her own line
        assert await msg.can_contact(s, me, await msg._person(s, a.hro))
        assert not await msg.can_contact(s, me, await msg._person(s, a.emeka))  # another school
        assert not await msg.can_contact(s, me, await msg._person(s, a.tunde))
    async with sm() as s, s.begin(), as_(a, a.sta, a.tenant.root_scope_node_id):
        me = await msg._person(s, a.sta)  # the ministry reaches everyone below it
        assert await msg.can_contact(s, me, await msg._person(s, a.emeka))
        assert await msg.can_contact(s, me, await msg._person(s, a.tunde))


async def test_a_conversation_between_a_parent_and_a_teacher(sm, world):
    a = await _people(sm, world)
    async with sm() as s, s.begin(), as_(a, a.ngozi, a.school):
        c = await msg.open_direct(s, KEYS, a.ada)
        assert c.kind == "direct" and c.title == "ada@central.example" and c.can_post
        assert (await msg.open_direct(s, KEYS, a.ada)).id == c.id  # the same conversation
        hello = await msg.send(
            s, KEYS, a.store, c.id, text="Good morning. Chidi was ill yesterday."
        )
        note = await msg.send(
            s,
            KEYS,
            a.store,
            c.id,
            text="Here is the doctor's note.",
            files=[msg.FileIn(PDF, "application/pdf", "Doctor's note.pdf")],
        )
        assert note.kind == "file" and note.attachments[0].name == "Doctor's note.pdf"
        assert (hello.seq, note.seq) == (1, 2) and note.read_by == 0 and note.others == 1
        with pytest.raises(ValidationFailed, match="not really a pdf"):
            await msg.send(
                s, KEYS, a.store, c.id, text=None,
                files=[msg.FileIn(b"MZ\x90\x00", "application/pdf", "note.pdf")],
            )  # fmt: skip
        with pytest.raises(ValidationFailed, match="send PDF, Word"):
            await msg.send(
                s, KEYS, a.store, c.id, text=None,
                files=[msg.FileIn(b"#!/bin/sh", "application/x-sh", "run.sh")],
            )  # fmt: skip
        with pytest.raises(ValidationFailed, match="write a message"):
            await msg.send(s, KEYS, a.store, c.id, text="   ")
        # Stored encrypted: the text is not in the row, and the AAD binds it to the row.
        row = (await s.execute(select(Message).where(Message.id == hello.id))).scalar_one()
        assert row.body_enc and b"Chidi" not in row.body_enc
        with pytest.raises(DecryptionError):
            CRYPTO.cipher.decrypt(
                row.body_enc, tenant_id=row.tenant_id, table="message", column="body",
                row_id=note.id,
            )  # fmt: skip
        att = (await s.execute(select(MessageAttachment))).scalar_one()
        assert b"Doctor" not in att.name_enc
    async with sm() as s, s.begin(), as_(a, a.ada, a.school):
        [mine] = await msg.my_conversations(s, KEYS)
        assert (mine.title, mine.unread, mine.last_preview) == (
            "Ngozi Eze",
            2,
            "Here is the doctor's note.",
        )
        got = await msg.messages(s, KEYS, c.id)
        assert [m.text for m in got] == [
            "Good morning. Chidi was ill yesterday.",
            "Here is the doctor's note.",
        ]
        f = await msg.attachment(s, KEYS, a.store, got[1].attachments[0].id)
        assert (f.content, f.name, f.mime) == (PDF, "Doctor's note.pdf", "application/pdf")
        reply = await msg.send(
            s, KEYS, a.store, c.id, text="Thank you, get well soon.", reply_to_id=hello.id
        )
        assert reply.reply_to["text"] == "Good morning. Chidi was ill yesterday."
        assert (await msg.react(s, KEYS, note.id, "🙏", on=True)).reactions == {"🙏": [a.ada]}
        assert (await msg.react(s, KEYS, note.id, "🙏", on=True)).reactions == {"🙏": [a.ada]}
        with pytest.raises(ValidationFailed, match="react with one of"):
            await msg.react(s, KEYS, note.id, "💩", on=True)
        with pytest.raises(Forbidden, match="your own messages"):
            await msg.edit(s, KEYS, hello.id, "changed")
        assert (await msg.my_conversations(s, KEYS))[0].unread == 0
    async with sm() as s, s.begin(), as_(a, a.ngozi, a.school):
        got = await msg.messages(s, KEYS, c.id, after_seq=2)
        assert [m.text for m in got] == ["Thank you, get well soon."]
        older = await msg.messages(s, KEYS, c.id, before_seq=2, mark_read=False)
        assert [m.seq for m in older] == [1] and older[0].read_by == 1  # Ada has read it
        fixed = await msg.edit(s, KEYS, hello.id, "Good morning. Chidi was ill on Monday.")
        assert fixed.edited and fixed.text.endswith("Monday.")
        gone = await msg.delete(s, KEYS, note.id)
        assert gone.deleted and gone.text is None and gone.attachments == []
        with pytest.raises(NotFound):
            await msg.attachment(s, KEYS, a.store, note.attachments[0].id)
        assert (await s.execute(select(MessageEdit))).scalar_one()  # the earlier text is kept
        report_id = await msg.report(s, reply.id, "Testing the report button")
    # A teacher in another school cannot see the conversation at all.
    async with sm() as s, s.begin(), as_(a, a.emeka, a.umu.scope_node_id):
        with pytest.raises(NotFound):
            await msg.messages(s, KEYS, c.id)
        with pytest.raises(NotFound):
            await msg.send(s, KEYS, a.store, c.id, text="hello")
    # The head moderates, and may review the conversation for a stated reason.
    async with sm() as s, s.begin(), as_(a, a.head, a.school):
        [r] = await msg.reports(s)
        assert (r.id, r.reason) == (report_id, "Testing the report button")
        await msg.handle_report(s, report_id, action="dismiss", note="A test")
        assert await msg.reports(s) == []
        with pytest.raises(ValidationFailed, match="say why"):
            await msg.review(s, KEYS, c.id, purpose="x")
        rv = await msg.review(s, KEYS, c.id, purpose="Parent complaint 2026/14")
        assert rv.messages[1].text == "Here is the doctor's note."  # deleted, still kept
        assert rv.edits[str(hello.id)] == ["Good morning. Chidi was ill yesterday."]
        f = await msg.attachment(s, KEYS, a.store, note.attachments[0].id, purpose="Complaint")
        assert f.content == PDF
        acc = await msg.accesses(s, c.id)  # reading it, and opening the document, are logged
        assert [x["purpose"] for x in acc] == ["Parent complaint 2026/14", "Complaint"]
        assert len((await s.execute(select(ConversationAccess))).scalars().all()) == 2
    async with sm() as s, s.begin(), as_(a, a.ada, a.school):
        with pytest.raises(Forbidden):
            await msg.review(s, KEYS, c.id, purpose="Just curious")  # teachers cannot
    async with sm() as s:
        assert await verify_all_days(s, a.tenant.id)


async def test_class_staff_room_authority_and_announcements(sm, world):
    a = await _people(sm, world)
    async with sm() as s, s.begin(), as_(a, a.ada, a.school):
        cls = await msg.open_class_conversation(s, KEYS, a.p4a.id)
        roles = {m.user_id: m.role for m in cls.members}
        assert roles == {a.ada: "owner", a.head: "admin", a.ngozi: "member"}
        assert cls.title == "Primary 4A parents"
        await msg.send(s, KEYS, a.store, cls.id, text="Mid-term break starts on Friday.")
        room = await msg.open_staff_room(s, KEYS, a.aba.id)
        assert {m.user_id for m in room.members} == {a.ada, a.head, a.reg}  # no parents
        with pytest.raises(NotFound):
            await msg.open_staff_room(s, KEYS, a.umu.id)  # not her school
        with pytest.raises(ValidationFailed, match="staff only"):
            await msg.create_group(
                s, KEYS, title="Welfare", member_ids=[a.head, a.ngozi], protected=True
            )
        welfare = await msg.create_group(
            s, KEYS, title="Welfare: Chidi", member_ids=[a.head], protected=True
        )
        await msg.send(s, KEYS, a.store, welfare.id, text="Concern about attendance.")
        row = (
            await s.execute(
                select(Message).where(Message.conversation_id == welfare.id, Message.seq == 2)
            )
        ).scalar_one()
        with pytest.raises(DecryptionError):  # a separate key from the institutional one
            CRYPTO.cipher.decrypt(
                row.body_enc, tenant_id=row.tenant_id, table="message", column="body",
                row_id=row.id,
            )  # fmt: skip
    async with sm() as s, s.begin(), as_(a, a.ngozi, a.school):
        convs = {c.kind: c for c in await msg.my_conversations(s, KEYS)}
        assert set(convs) == {"class"} and convs["class"].can_post
        assert convs["class"].unread == 1
        with pytest.raises(NotFound):
            await msg.get_conversation(s, KEYS, room.id)
    async with sm() as s, s.begin(), as_(a, a.head, a.school):
        with pytest.raises(Forbidden):  # oversight of protected needs DSL or DPO
            await msg.review(s, KEYS, welfare.id, purpose="Checking the welfare note")
    # ASUBEB's channel with its heads of schools.
    async with sm() as s, s.begin(), as_(a, a.hro, a.asubeb):
        ch = await msg.open_authority_channel(s, KEYS)
        members = {m.user_id: m.role for m in ch.members}
        assert members[a.hro] == "admin" and members[a.head] == "member"
        assert a.ada not in members and a.ngozi not in members
        await msg.send(s, KEYS, a.store, ch.id, text="Census returns are due on 30 November.")
    async with sm() as s, s.begin(), as_(a, a.head, a.school):
        up = await msg.updates(s, KEYS, None)
        assert up.unread_total >= 1
        assert "authority" in {c.kind for c in await msg.my_conversations(s, KEYS)}
        ann = await msg.announce(s, KEYS, audience="parents", title="Central School notices")
        assert ann.posting == "admins" and ann.can_post
        await msg.send(s, KEYS, a.store, ann.id, text="School resumes on 5 January.")
    async with sm() as s, s.begin(), as_(a, a.ngozi, a.school):
        c = next(c for c in await msg.my_conversations(s, KEYS) if c.kind == "announcement")
        assert not c.can_post and c.unread == 1
        with pytest.raises(Forbidden, match="administrators post"):
            await msg.send(s, KEYS, a.store, c.id, text="Thanks!")
    async with sm() as s, s.begin(), as_(a, a.tunde, a.umu.scope_node_id):
        assert await msg.my_conversations(s, KEYS) == []  # Township's parents are not included
    async with sm() as s:
        assert await verify_all_days(s, a.tenant.id)


async def test_parents_sign_in_by_email_code_and_message_the_school(client, sm, world):
    a = await _people(sm, world)

    async def login(name, scope):
        r = await client.post("/v1/auth/dev-login", json={"tenant_code": "NG-AB", "login": name})
        return {
            "Authorization": f"Bearer {r.json()['access_token']}",
            "X-Scope-Node-Id": str(scope),
        }

    reg = await login("registrar@central.example", a.school)
    ada = await login("ada@central.example", a.school)
    n = iter(range(100))

    def k(h):
        return h | {"Idempotency-Key": f"m{next(n)}"}

    # Giving an account again finds the one she already has.
    r = await client.post(
        f"/v1/learners/{a.chidi}/guardians/{a.ngozi_person}/account",
        json={"email": "ngozi.eze@example.com"},
        headers=k(reg),
    )
    assert r.status_code == 201 and r.json()["created"] is False, r.text
    r = await client.post(
        f"/v1/learners/{a.chidi}/guardians/{a.ngozi_person}/account",
        json={"email": "ngozi.eze@example.com"},
        headers=k(ada),
    )
    assert r.status_code == 403  # teachers do not hand out accounts

    # Sign-in by code: the same answer whether or not the address has an account.
    for email in ("ngozi.eze@example.com", "nobody@example.com"):
        r = await client.post("/v1/auth/email-code", json={"tenant_code": "NG-AB", "email": email})
        assert r.status_code == 202
    async with sm() as s:
        mails = (
            (await s.execute(select(OutboundEmail).where(OutboundEmail.purpose == "sign-in-code")))
            .scalars()
            .all()
        )
    assert [m.to_address for m in mails] == ["ngozi.eze@example.com"]
    code = re.search(r"\b(\d{6})\b", mails[0].body).group(1)
    assert code not in mails[0].subject
    bad = "000000" if code != "000000" else "111111"
    r = await client.post(
        "/v1/auth/email-code/verify",
        json={"tenant_code": "NG-AB", "email": "ngozi.eze@example.com", "code": bad},
    )
    assert r.status_code == 401
    r = await client.post(
        "/v1/auth/email-code/verify",
        json={"tenant_code": "NG-AB", "email": "ngozi.eze@example.com", "code": code},
    )
    assert r.status_code == 200, r.text
    parent = {
        "Authorization": f"Bearer {r.json()['access_token']}",
        "X-Scope-Node-Id": str(a.school),
    }
    r = await client.post(
        "/v1/auth/email-code/verify",
        json={"tenant_code": "NG-AB", "email": "ngozi.eze@example.com", "code": code},
    )
    assert r.status_code == 401  # a code works once
    # Staff cannot sign in by email code.
    r = await client.post(
        "/v1/auth/email-code", json={"tenant_code": "NG-AB", "email": "ada@central.example"}
    )
    assert r.status_code == 202
    async with sm() as s:
        assert (
            len(
                (
                    await s.execute(
                        select(OutboundEmail).where(OutboundEmail.purpose == "sign-in-code")
                    )
                )
                .scalars()
                .all()
            )
            == 1
        )

    # The parent finds the teacher and sends a document.
    people = (await client.get("/v1/messaging/contacts?q=ada", headers=parent)).json()["items"]
    assert [p["role"] for p in people] == ["Teacher"]
    r = await client.post(
        "/v1/messaging/direct", json={"user_id": people[0]["user_id"]}, headers=k(parent)
    )
    assert r.status_code == 200, r.text
    cid = r.json()["id"]
    h = k(parent)
    r = await client.post(
        f"/v1/messaging/conversations/{cid}/messages",
        data={"text": "The doctor's note, as promised."},
        files=[("files", ("note.pdf", PDF, "application/pdf"))],
        headers=h,
    )
    assert r.status_code == 201, r.text
    assert set(r.json()) == {"id", "seq"}  # the text is not kept in the idempotency store
    again = await client.post(
        f"/v1/messaging/conversations/{cid}/messages",
        data={"text": "The doctor's note, as promised."},
        files=[("files", ("note.pdf", PDF, "application/pdf"))],
        headers=h,
    )
    assert again.json() == r.json()  # a retry does not send it twice
    r = await client.post(
        f"/v1/messaging/conversations/{cid}/messages", data={"text": "hi"}, headers=parent
    )
    assert r.status_code == 428

    up = (await client.get("/v1/messaging/updates", headers=ada)).json()
    assert up["unread_total"] == 1
    items = (await client.get(f"/v1/messaging/conversations/{cid}/messages", headers=ada)).json()[
        "items"
    ]
    assert items[0]["text"] == "The doctor's note, as promised."
    att = items[0]["attachments"][0]
    r = await client.get(f"/v1/messaging/attachments/{att['id']}", headers=ada)
    assert r.status_code == 200 and r.content == PDF
    assert r.headers["content-disposition"] == 'attachment; filename="note.pdf"'
    assert r.headers["cache-control"] == "private, no-store"
    r = await client.put(f"/v1/messaging/messages/{items[0]['id']}/reactions/👍", headers=ada)
    assert r.json()["reactions"] == {"👍": [str(a.ada)]}
    r = await client.patch(
        f"/v1/messaging/messages/{items[0]['id']}", json={"text": "changed"}, headers=ada
    )
    assert r.status_code == 403
    assert (await client.get("/v1/messaging/reports", headers=ada)).status_code == 403
    assert (await client.get("/v1/messaging/updates", headers=ada)).json()["unread_total"] == 0
    # The idempotency store holds no message text.
    from syntegra.platform.models import IdempotencyRecord

    async with sm() as s:
        bodies = b"".join(
            (await s.execute(select(IdempotencyRecord.response_body))).scalars().all()
        )
    assert b"doctor" not in bodies

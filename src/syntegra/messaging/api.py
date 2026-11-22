"""Secure messaging between education authorities, schools and parents (CEDA §26, ADR-030).

**Encryption.** Every message body and attachment name is encrypted with AES-256-GCM under the
state's key (envelope keys, bound to the row), files are stored encrypted (SSE-KMS in
production), and everything travels over TLS. Conversations are "institutional records" (CEDA
§26.2): kept, and reviewable by an authorised officer who states why. "Protected" conversations
(pastoral, HR; staff only) use a separate key and stricter review rights.

**Who can talk to whom** comes from the platform's own data, never from a free-for-all directory:

* staff with staff up and down their own line (a school with its LGEA and ASUBEB, and back);
* parents with the staff of their children's schools, and those staff with them;
* never parent to parent.

**Conversation kinds:** direct (two people), group (made by staff), class (a class teacher with
the parents of the class), staff (a school's staff room), authority (a board with the heads of
its schools), announcement (one-way: only owners post).

**Messages:** text, documents and images, replies, reactions, edits (earlier text kept), delete
for everyone (hidden, still kept), read receipts, reports to the moderation queue.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from syntegra.academic.models import ClassGroup
from syntegra.core.clock import today, utcnow
from syntegra.core.context import current_context
from syntegra.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from syntegra.core.ids import uuid7
from syntegra.governance import api as governance
from syntegra.hr.models import Staff
from syntegra.iam import api as iam
from syntegra.iam.baseline import ROLES
from syntegra.iam.domain import GrantFacts
from syntegra.iam.models import AccessGrant, AppUser, Role
from syntegra.institutions.models import Institution
from syntegra.learners.models import ClassMembership, Learner, LearnerGuardian
from syntegra.messaging.models import (
    Conversation,
    ConversationAccess,
    ConversationMember,
    Message,
    MessageAttachment,
    MessageEdit,
    MessageReaction,
    MessageReport,
)
from syntegra.platform.blobstore import BlobStore
from syntegra.platform.crypto import FieldCipher
from syntegra.platform.session import PLATFORM_BYPASS

BYPASS: dict[str, Any] = {PLATFORM_BYPASS: True}

PARENT_ROLES = frozenset({"PAR"})
NOT_STAFF = frozenset({"PAR", "STU"})
EDIT_WINDOW = timedelta(hours=24)
MAX_TEXT = 4000
MAX_FILES = 5
MAX_FILE_BYTES = 20 * 1024 * 1024
REACTIONS = ("👍", "❤️", "😂", "😮", "🙏", "✅")
# Documents and pictures only; each type is checked by its first bytes, not its name.
FILE_TYPES: dict[str, tuple[bytes, ...]] = {
    "application/pdf": (b"%PDF",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/png": (b"\x89PNG",),
    "image/webp": (b"RIFF",),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": (b"PK\x03\x04",),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": (b"PK\x03\x04",),
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": (b"PK\x03\x04",),
    "text/plain": (b"",),
    "text/csv": (b"",),
}


@dataclass(frozen=True, slots=True)
class Keys:
    institutional: FieldCipher
    protected: FieldCipher

    def for_class(self, comm_class: str) -> FieldCipher:
        return self.protected if comm_class == "protected" else self.institutional


def _seal(
    keys: Keys, c: Conversation, table: str, column: str, row_id: uuid.UUID, text: str
) -> bytes:
    return keys.for_class(c.comm_class).encrypt(
        text, tenant_id=c.tenant_id, table=table, column=column, row_id=row_id
    )


def _open(
    keys: Keys, c: Conversation, table: str, column: str, row_id: uuid.UUID, blob: bytes
) -> str:
    return keys.for_class(c.comm_class).decrypt(
        blob, tenant_id=c.tenant_id, table=table, column=column, row_id=row_id
    )


# --------------------------------------------------------------------------- who is who


@dataclass(frozen=True, slots=True)
class Person:
    user_id: uuid.UUID
    display_name: str
    grants: tuple[GrantFacts, ...]

    @property
    def roles(self) -> set[str]:
        return {g.role_code for g in self.grants}

    @property
    def is_parent(self) -> bool:
        return bool(self.roles) and self.roles <= PARENT_ROLES

    @property
    def staff_scopes(self) -> list[uuid.UUID]:
        return [g.scope_node_id for g in self.grants if g.role_code not in NOT_STAFF]

    @property
    def parent_scopes(self) -> list[uuid.UUID]:
        return [g.scope_node_id for g in self.grants if g.role_code in PARENT_ROLES]


async def _person(session: AsyncSession, user_id: uuid.UUID) -> Person:
    ctx = current_context()
    u = (
        await session.execute(
            select(AppUser).where(AppUser.id == user_id, AppUser.tenant_id == ctx.tenant_id)
        )
    ).scalar_one_or_none()
    if u is None or u.status != "active":
        raise NotFound("that person is not on the system")
    grants = tuple(g for g in await iam.grants_for(session, u.id) if g.active_on(today()))
    return Person(u.id, u.display_name, grants)


async def _related(session: AsyncSession, a: uuid.UUID, b: uuid.UUID) -> bool:
    """One scope sits within the other (the same school, or a school and an office above it)."""
    return (
        a == b
        or await governance.is_within(session, a, b)
        or await governance.is_within(session, b, a)
    )


async def _any_within(
    session: AsyncSession, outer: list[uuid.UUID], inner: list[uuid.UUID]
) -> bool:
    for o in outer:
        for i in inner:
            if await governance.is_within(session, o, i):
                return True
    return False


async def can_contact(session: AsyncSession, me: Person, other: Person) -> bool:
    if me.user_id == other.user_id:
        return False
    if me.is_parent and other.is_parent:
        return False
    if me.is_parent:  # a parent reaches the staff of their children's schools
        return await _any_within(session, me.parent_scopes, other.staff_scopes)
    if other.is_parent:  # school staff, and the offices above them, reach parents
        return await _any_within(session, me.staff_scopes, other.parent_scopes)
    for s in me.staff_scopes:  # staff reach staff up and down their own line
        for t in other.staff_scopes:
            if await _related(session, s, t):
                return True
    return False


@dataclass(frozen=True, slots=True)
class Contact:
    user_id: uuid.UUID
    display_name: str
    role: str  # what the person is to the caller: "Teacher", "Parent", …


async def contacts(session: AsyncSession, text: str, limit: int = 20) -> list[Contact]:
    """People the caller may start a conversation with, by name or email."""
    ctx = current_context()
    assert ctx.user_id is not None
    me = await _person(session, ctx.user_id)
    out: list[Contact] = []
    for u in await iam.search_users(session, ctx.tenant_id, text, limit=50):
        if len(out) >= limit:
            break
        other = await _person(session, u.id)
        if await can_contact(session, me, other):
            names = sorted({ROLES.get(r, r) for r in other.roles})
            out.append(Contact(u.id, u.display_name, ", ".join(names)))
    return out


# --------------------------------------------------------------------------- views


@dataclass(frozen=True, slots=True)
class MemberView:
    user_id: uuid.UUID
    display_name: str
    role: str
    last_read_seq: int


@dataclass(frozen=True, slots=True)
class ConversationView:
    id: uuid.UUID
    kind: str
    title: str
    comm_class: str
    posting: str
    my_role: str | None
    can_post: bool
    unread: int
    last_seq: int
    last_message_at: datetime | None
    last_preview: str | None
    members: list[MemberView] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class AttachmentView:
    id: uuid.UUID
    name: str
    mime: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class MessageView:
    id: uuid.UUID
    seq: int
    sender_user_id: uuid.UUID | None
    sender_name: str | None
    sent_at: datetime
    kind: str
    text: str | None
    deleted: bool
    edited: bool
    reply_to: dict[str, Any] | None
    attachments: list[AttachmentView]
    reactions: dict[str, list[uuid.UUID]]
    read_by: int  # how many other members have read it
    others: int  # how many other members there are


async def _names(session: AsyncSession, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    ctx = current_context()
    return await iam.user_names(session, ctx.tenant_id, list(ids))


async def _members(session: AsyncSession, conversation_id: uuid.UUID) -> list[ConversationMember]:
    return list(
        (
            await session.execute(
                select(ConversationMember).where(
                    ConversationMember.conversation_id == conversation_id,
                    ConversationMember.left_at.is_(None),
                )
            )
        ).scalars()
    )


def _can_post(c: Conversation, m: ConversationMember | None) -> bool:
    if m is None or m.left_at is not None or m.role == "read_only":
        return False
    return c.posting == "all" or m.role in ("owner", "admin")


async def _title(
    session: AsyncSession, c: Conversation, me: uuid.UUID, members: list[ConversationMember]
) -> str:
    if c.kind == "direct":
        other = next((m.user_id for m in members if m.user_id != me), me)
        return (await _names(session, {other})).get(other, "Conversation")
    return c.title or "Conversation"


async def _preview(session: AsyncSession, keys: Keys, c: Conversation) -> str | None:
    last = (
        await session.execute(
            select(Message)
            .where(Message.conversation_id == c.id)
            .order_by(Message.seq.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if last is None:
        return None
    if last.deleted_at:
        return "This message was deleted"
    if last.kind == "file" and not last.body_enc:
        return "📎 Document"
    text = _open(keys, c, "message", "body", last.id, last.body_enc) if last.body_enc else ""
    return text[:80]


async def _cview(
    session: AsyncSession, keys: Keys, c: Conversation, me: uuid.UUID, *, with_members: bool = False
) -> ConversationView:
    members = await _members(session, c.id)
    mine = next((m for m in members if m.user_id == me), None)
    last_seq = c.next_seq - 1
    names = await _names(session, {m.user_id for m in members}) if with_members else {}
    return ConversationView(
        c.id,
        c.kind,
        await _title(session, c, me, members),
        c.comm_class,
        c.posting,
        mine.role if mine else None,
        _can_post(c, mine),
        max(0, last_seq - mine.last_read_seq) if mine and not mine.muted else 0,
        last_seq,
        c.last_message_at,
        await _preview(session, keys, c),
        [MemberView(m.user_id, names.get(m.user_id, "…"), m.role, m.last_read_seq) for m in members]
        if with_members
        else [],
    )


# --------------------------------------------------------------------------- conversations


async def _conversation(session: AsyncSession, conversation_id: uuid.UUID) -> Conversation:
    ctx = current_context()
    c = (
        await session.execute(
            select(Conversation).where(
                Conversation.id == conversation_id, Conversation.tenant_id == ctx.tenant_id
            )
        )
    ).scalar_one_or_none()
    if c is None:
        raise NotFound("conversation not found")
    return c


async def _membership(session: AsyncSession, c: Conversation) -> ConversationMember:
    ctx = current_context()
    m = (
        await session.execute(
            select(ConversationMember).where(
                ConversationMember.conversation_id == c.id,
                ConversationMember.user_id == ctx.user_id,
                ConversationMember.left_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if m is None:
        raise NotFound("conversation not found")  # do not reveal it exists
    return m


def _new_conversation(kind: str, scope_id: uuid.UUID, **kw: Any) -> Conversation:
    ctx = current_context()
    return Conversation(
        tenant_id=ctx.tenant_id,
        scope_node_id=scope_id,
        kind=kind,
        created_by=ctx.user_id,
        **kw,
    )


def _add_member(
    session: AsyncSession, c: Conversation, user_id: uuid.UUID, role: str, *, derived: bool
) -> None:
    session.add(
        ConversationMember(
            tenant_id=c.tenant_id, conversation_id=c.id, user_id=user_id, role=role, derived=derived
        )
    )


async def _home_scope(session: AsyncSession, people: list[Person]) -> uuid.UUID:
    """Where a conversation's oversight sits: the office above everyone in it."""
    scopes = [s for p in people for s in (p.staff_scopes or p.parent_scopes)]
    if not scopes:
        return current_context().scope_node_id
    home = scopes[0]
    for s in scopes[1:]:
        home = await governance.lowest_common_ancestor(session, home, s)
    return home


async def open_direct(
    session: AsyncSession, keys: Keys, other_user_id: uuid.UUID
) -> ConversationView:
    """Start (or reopen) a one-to-one conversation."""
    ctx = current_context()
    assert ctx.user_id is not None
    me = await _person(session, ctx.user_id)
    other = await _person(session, other_user_id)
    if not await can_contact(session, me, other):
        raise Forbidden(
            "you cannot message this person",
            next_action="Parents message the staff of their children's schools; staff message "
            "colleagues in their own line and the parents of their learners.",
        )
    key = ":".join(sorted([str(me.user_id), str(other.user_id)]))
    c = (
        await session.execute(
            select(Conversation).where(
                Conversation.tenant_id == ctx.tenant_id, Conversation.direct_key == key
            )
        )
    ).scalar_one_or_none()
    if c is None:
        c = _new_conversation("direct", await _home_scope(session, [me, other]), direct_key=key)
        session.add(c)
        await session.flush()
        _add_member(session, c, me.user_id, "member", derived=False)
        _add_member(session, c, other.user_id, "member", derived=False)
        await session.flush()
    return await _cview(session, keys, c, me.user_id, with_members=True)


async def create_group(
    session: AsyncSession,
    keys: Keys,
    *,
    title: str,
    member_ids: list[uuid.UUID],
    protected: bool = False,
    announcements: bool = False,
) -> ConversationView:
    """A group made by a member of staff, with people they may contact."""
    ctx = current_context()
    assert ctx.user_id is not None
    me = await _person(session, ctx.user_id)
    if me.is_parent:
        raise Forbidden("parents cannot create groups", next_action="Ask the school to add you.")
    name = " ".join(title.split())
    if not 2 <= len(name) <= 120:
        raise ValidationFailed("give the group a name (2-120 characters)")
    people = [me]
    for uid in dict.fromkeys(member_ids):
        if uid == me.user_id:
            continue
        p = await _person(session, uid)
        if not await can_contact(session, me, p):
            raise Forbidden(f"you cannot add {p.display_name} to a conversation")
        if protected and p.is_parent:
            raise ValidationFailed("a protected conversation is for staff only")
        people.append(p)
    if len(people) < 2:
        raise ValidationFailed("add at least one other person")
    if len(people) > 1024:
        raise ValidationFailed("a group has at most 1,024 people")
    c = _new_conversation(
        "group",
        await _home_scope(session, people),
        title=name,
        comm_class="protected" if protected else "institutional",
        posting="admins" if announcements else "all",
    )
    session.add(c)
    await session.flush()
    for p in people:
        _add_member(session, c, p.user_id, "owner" if p is me else "member", derived=False)
    await session.flush()
    await _system(session, keys, c, f"{me.display_name} created the group")
    return await _cview(session, keys, c, me.user_id, with_members=True)


async def _users_of_people(
    session: AsyncSession, person_ids: set[uuid.UUID]
) -> dict[uuid.UUID, uuid.UUID]:
    ctx = current_context()
    if not person_ids:
        return {}
    rows = await session.execute(
        select(AppUser.person_id, AppUser.id).where(
            AppUser.tenant_id == ctx.tenant_id,
            AppUser.person_id.in_(person_ids),
            AppUser.status == "active",
        )
    )
    return {p: u for p, u in rows.all() if p is not None}


async def _users_with_grants_at(
    session: AsyncSession, scope_id: uuid.UUID, *, staff: bool
) -> set[uuid.UUID]:
    ctx = current_context()
    rows = await session.execute(
        select(AccessGrant.user_id, Role.code)
        .join(Role, Role.id == AccessGrant.role_id)
        .where(
            AccessGrant.tenant_id == ctx.tenant_id,
            AccessGrant.scope_node_id == scope_id,
            AccessGrant.revoked_at.is_(None),
        )
    )
    return {u for u, code in rows.all() if (code not in NOT_STAFF) == staff}


async def _derive(session: AsyncSession, c: Conversation) -> dict[uuid.UUID, str]:
    """Who belongs in a class, staff or authority conversation now, and in what role."""
    want: dict[uuid.UUID, str] = {}
    if c.kind == "class" and c.class_group_id:
        cls = (
            await session.execute(
                select(ClassGroup)
                .where(ClassGroup.id == c.class_group_id)
                .execution_options(**BYPASS)
            )
        ).scalar_one()
        teachers = {s for s in (cls.class_teacher_staff_id, cls.assistant_staff_id) if s}
        persons = set(
            (
                await session.execute(
                    select(Staff.person_id)
                    .where(Staff.id.in_(teachers or {uuid.UUID(int=0)}))
                    .execution_options(**BYPASS)
                )
            ).scalars()
        )
        for uid in (await _users_of_people(session, persons)).values():
            want[uid] = "owner"
        guardians = set(
            (
                await session.execute(
                    select(LearnerGuardian.guardian_person_id)
                    .join(Learner, Learner.id == LearnerGuardian.learner_id)
                    .join(ClassMembership, ClassMembership.learner_id == Learner.id)
                    .where(
                        ClassMembership.class_group_id == cls.id,
                        ClassMembership.valid_to.is_(None),
                        LearnerGuardian.parental_responsibility.is_(True),
                        LearnerGuardian.court_order_flag.is_(False),
                        LearnerGuardian.valid_to.is_(None),
                    )
                    .execution_options(**BYPASS)
                )
            ).scalars()
        )
        for uid in (await _users_of_people(session, guardians)).values():
            want.setdefault(uid, "member")
        inst = (
            await session.execute(
                select(Institution)
                .where(Institution.id == cls.institution_id)
                .execution_options(**BYPASS)
            )
        ).scalar_one()
        heads = (
            await session.execute(
                select(AccessGrant.user_id)
                .join(Role, Role.id == AccessGrant.role_id)
                .where(
                    AccessGrant.scope_node_id == inst.scope_node_id,
                    Role.code == "HEAD",
                    AccessGrant.revoked_at.is_(None),
                )
            )
        ).scalars()
        for uid in heads:
            want[uid] = "admin" if want.get(uid) != "owner" else "owner"
    elif c.kind == "staff" and c.institution_id:
        inst = (
            await session.execute(
                select(Institution)
                .where(Institution.id == c.institution_id)
                .execution_options(**BYPASS)
            )
        ).scalar_one()
        for uid in await _users_with_grants_at(session, inst.scope_node_id, staff=True):
            want[uid] = "member"
    elif c.kind == "authority":
        # The board's officers, and the heads and administrators of the institutions beneath it.
        for uid in await _users_with_grants_at(session, c.scope_node_id, staff=True):
            want[uid] = "admin"
        rows = await session.execute(
            select(AccessGrant.user_id, AccessGrant.scope_node_id)
            .join(Role, Role.id == AccessGrant.role_id)
            .where(
                AccessGrant.tenant_id == c.tenant_id,
                Role.code.in_(("HEAD", "IADM")),
                AccessGrant.revoked_at.is_(None),
            )
        )
        for uid, scope in rows.all():
            if await governance.is_within(session, c.scope_node_id, scope):
                want.setdefault(uid, "member")
    return want


async def sync_members(session: AsyncSession, c: Conversation) -> int:
    """Bring a derived conversation's members in line with the class, school or board. People
    added by hand stay; derived members who no longer qualify leave. Returns the member count."""
    want = await _derive(session, c)
    owner = c.created_by
    have = {
        m.user_id: m
        for m in (
            await session.execute(
                select(ConversationMember).where(ConversationMember.conversation_id == c.id)
            )
        ).scalars()
    }
    for uid, role in want.items():
        m = have.get(uid)
        if m is None:
            _add_member(session, c, uid, role, derived=True)
        elif m.left_at is not None and m.derived:
            m.left_at, m.role = None, role
    for uid, m in have.items():
        if m.derived and m.left_at is None and uid not in want and uid != owner:
            m.left_at = utcnow()
    await session.flush()
    return len(await _members(session, c.id))


async def _staff_only(session: AsyncSession) -> Person:
    ctx = current_context()
    assert ctx.user_id is not None
    me = await _person(session, ctx.user_id)
    if not me.staff_scopes:
        raise Forbidden("only staff can do this")
    return me


async def open_class_conversation(
    session: AsyncSession, keys: Keys, class_id: uuid.UUID
) -> ConversationView:
    """The class's conversation: its class teacher, the head, and the parents of its learners."""
    ctx = current_context()
    assert ctx.user_id is not None
    await _staff_only(session)
    cls = (
        await session.execute(select(ClassGroup).where(ClassGroup.id == class_id))
    ).scalar_one_or_none()
    if cls is None:
        raise NotFound("class not found")
    c = (
        await session.execute(
            select(Conversation).where(
                Conversation.tenant_id == ctx.tenant_id,
                Conversation.kind == "class",
                Conversation.class_group_id == cls.id,
            )
        )
    ).scalar_one_or_none()
    if c is None:
        c = _new_conversation(
            "class",
            cls.scope_node_id,
            title=f"{cls.name} parents",
            class_group_id=cls.id,
            institution_id=cls.institution_id,
        )
        session.add(c)
        await session.flush()
    await sync_members(session, c)
    if ctx.user_id not in {m.user_id for m in await _members(session, c.id)}:
        _add_member(session, c, ctx.user_id, "admin", derived=False)  # the person who opened it
        await session.flush()
    return await _cview(session, keys, c, ctx.user_id, with_members=True)


async def open_staff_room(
    session: AsyncSession, keys: Keys, institution_id: uuid.UUID
) -> ConversationView:
    ctx = current_context()
    assert ctx.user_id is not None
    inst = (
        await session.execute(select(Institution).where(Institution.id == institution_id))
    ).scalar_one_or_none()
    if inst is None:
        raise NotFound("institution not found")
    c = (
        await session.execute(
            select(Conversation).where(
                Conversation.tenant_id == ctx.tenant_id,
                Conversation.kind == "staff",
                Conversation.institution_id == inst.id,
            )
        )
    ).scalar_one_or_none()
    if c is None:
        c = _new_conversation(
            "staff", inst.scope_node_id, title=f"{inst.name} staff room", institution_id=inst.id
        )
        session.add(c)
        await session.flush()
    await sync_members(session, c)
    await _membership(session, c)  # only the school's staff are in it
    return await _cview(session, keys, c, ctx.user_id, with_members=True)


async def open_authority_channel(session: AsyncSession, keys: Keys) -> ConversationView:
    """The active office's channel with the heads of every institution beneath it."""
    ctx = current_context()
    assert ctx.user_id is not None
    await _staff_only(session)
    node = await governance.get_scope_node(session, ctx.scope_node_id)
    c = (
        await session.execute(
            select(Conversation).where(
                Conversation.tenant_id == ctx.tenant_id,
                Conversation.kind == "authority",
                Conversation.scope_node_id == ctx.scope_node_id,
            )
        )
    ).scalar_one_or_none()
    if c is None:
        c = _new_conversation(
            "authority", ctx.scope_node_id, title=f"{node.name} and heads of institutions"
        )
        session.add(c)
        await session.flush()
    await sync_members(session, c)
    await _membership(session, c)
    return await _cview(session, keys, c, ctx.user_id, with_members=True)


async def announce(
    session: AsyncSession,
    keys: Keys,
    *,
    audience: str,
    title: str,
    institution_id: uuid.UUID | None = None,
) -> ConversationView:
    """A one-way announcement channel: to all staff, or all parents, of one institution or of
    every institution under the caller's office. Only its owners post (message:broadcast)."""
    ctx = current_context()
    assert ctx.user_id is not None
    if audience not in ("staff", "parents"):
        raise ValidationFailed("the audience is staff or parents")
    name = " ".join(title.split())
    if not 2 <= len(name) <= 120:
        raise ValidationFailed("give the channel a name")
    scope = ctx.scope_node_id
    if institution_id:
        inst = (
            await session.execute(select(Institution).where(Institution.id == institution_id))
        ).scalar_one_or_none()
        if inst is None:
            raise NotFound("institution not found")
        scope = inst.scope_node_id
    c = _new_conversation(
        "announcement", scope, title=name, posting="admins", institution_id=institution_id
    )
    session.add(c)
    await session.flush()
    _add_member(session, c, ctx.user_id, "owner", derived=False)
    rows = await session.execute(
        select(AccessGrant.user_id, AccessGrant.scope_node_id, Role.code)
        .join(Role, Role.id == AccessGrant.role_id)
        .where(AccessGrant.tenant_id == ctx.tenant_id, AccessGrant.revoked_at.is_(None))
    )
    added = {ctx.user_id}
    for uid, s, code in rows.all():
        if uid in added:
            continue
        is_parent = code in PARENT_ROLES
        if (audience == "parents") != is_parent or code == "STU":
            continue
        if s == scope or await governance.is_within(session, scope, s):
            _add_member(session, c, uid, "read_only", derived=True)
            added.add(uid)
    await session.flush()
    return await _cview(session, keys, c, ctx.user_id, with_members=False)


async def my_conversations(session: AsyncSession, keys: Keys) -> list[ConversationView]:
    ctx = current_context()
    assert ctx.user_id is not None
    rows = (
        await session.execute(
            select(Conversation)
            .join(ConversationMember, ConversationMember.conversation_id == Conversation.id)
            .where(
                Conversation.tenant_id == ctx.tenant_id,
                ConversationMember.user_id == ctx.user_id,
                ConversationMember.left_at.is_(None),
            )
            .order_by(Conversation.last_message_at.desc(), Conversation.created_at.desc())
            .limit(300)
        )
    ).scalars()
    return [await _cview(session, keys, c, ctx.user_id) for c in rows]


async def get_conversation(
    session: AsyncSession, keys: Keys, conversation_id: uuid.UUID
) -> ConversationView:
    ctx = current_context()
    assert ctx.user_id is not None
    c = await _conversation(session, conversation_id)
    await _membership(session, c)
    return await _cview(session, keys, c, ctx.user_id, with_members=True)


async def leave(session: AsyncSession, conversation_id: uuid.UUID) -> None:
    c = await _conversation(session, conversation_id)
    if c.kind == "direct":
        raise ValidationFailed("a direct conversation cannot be left; mute it instead")
    m = await _membership(session, c)
    m.left_at = utcnow()
    await session.flush()


async def mute(session: AsyncSession, conversation_id: uuid.UUID, muted: bool) -> None:
    c = await _conversation(session, conversation_id)
    (await _membership(session, c)).muted = muted
    await session.flush()


# --------------------------------------------------------------------------- messages


@dataclass(frozen=True, slots=True)
class FileIn:
    content: bytes
    mime: str
    name: str


def _check_file(f: FileIn) -> None:
    if f.mime not in FILE_TYPES:
        raise ValidationFailed(f"{f.name}: send PDF, Word, Excel, PowerPoint, text or a picture")
    if not f.content:
        raise ValidationFailed(f"{f.name} is empty")
    if len(f.content) > MAX_FILE_BYTES:
        raise ValidationFailed(f"{f.name} is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB")
    if not any(f.content.startswith(m) for m in FILE_TYPES[f.mime]):
        raise ValidationFailed(f"{f.name} is not really a {f.mime.split('/')[-1]} file")
    if f.mime.startswith("text/"):
        try:
            f.content[:4096].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValidationFailed(f"{f.name} is not plain text") from exc


async def _system(session: AsyncSession, keys: Keys, c: Conversation, text: str) -> None:
    await _post(
        session, keys, c, sender=None, text=text, kind="system", files=[], reply_to=None, store=None
    )


async def _post(
    session: AsyncSession,
    keys: Keys,
    c: Conversation,
    *,
    sender: uuid.UUID | None,
    text: str | None,
    kind: str,
    files: list[FileIn],
    reply_to: uuid.UUID | None,
    store: BlobStore | None,
) -> Message:
    locked = (
        await session.execute(select(Conversation).where(Conversation.id == c.id).with_for_update())
    ).scalar_one()
    seq = locked.next_seq
    locked.next_seq = seq + 1
    now = utcnow()
    locked.last_message_at = now

    msg_id = uuid7()
    m = Message(
        id=msg_id,
        tenant_id=c.tenant_id,
        conversation_id=c.id,
        seq=seq,
        sender_user_id=sender,
        sent_at=now,
        kind=kind,
        body_enc=_seal(keys, c, "message", "body", msg_id, text) if text else None,
        reply_to_id=reply_to,
    )
    session.add(m)
    await session.flush()
    for f in files:
        assert store is not None
        sha = hashlib.sha256(f.content).hexdigest()
        att_id = uuid7()
        key = f"{c.tenant_id}/messages/{c.id}/{att_id}"
        await store.put(key, f.content, content_type=f.mime, sha256=sha)
        session.add(
            MessageAttachment(
                id=att_id,
                tenant_id=c.tenant_id,
                message_id=m.id,
                blob_key=key,
                sha256=sha,
                size_bytes=len(f.content),
                mime=f.mime,
                name_enc=_seal(keys, c, "message_attachment", "name", att_id, f.name[:200]),
            )
        )
    if sender is not None:
        mine = (
            await session.execute(
                select(ConversationMember).where(
                    ConversationMember.conversation_id == c.id, ConversationMember.user_id == sender
                )
            )
        ).scalar_one_or_none()
        if mine is not None:
            mine.last_read_seq = seq  # you have read what you sent
    await session.flush()
    return m


async def send(
    session: AsyncSession,
    keys: Keys,
    store: BlobStore,
    conversation_id: uuid.UUID,
    *,
    text: str | None,
    files: list[FileIn] | None = None,
    reply_to_id: uuid.UUID | None = None,
) -> MessageView:
    ctx = current_context()
    assert ctx.user_id is not None
    c = await _conversation(session, conversation_id)
    m = await _membership(session, c)
    if not _can_post(c, m):
        raise Forbidden("only the group's administrators post here")
    body = (text or "").strip()
    files = files or []
    if not body and not files:
        raise ValidationFailed("write a message or attach a document")
    if len(body) > MAX_TEXT:
        raise ValidationFailed(f"a message is at most {MAX_TEXT} characters")
    if len(files) > MAX_FILES:
        raise ValidationFailed(f"attach at most {MAX_FILES} documents at a time")
    for f in files:
        _check_file(f)
    if reply_to_id:
        parent = (
            await session.execute(select(Message).where(Message.id == reply_to_id))
        ).scalar_one_or_none()
        if parent is None or parent.conversation_id != c.id:
            raise ValidationFailed("you can only reply to a message in this conversation")
    msg = await _post(
        session, keys, c, sender=ctx.user_id, text=body or None, kind="file" if files else "text",
        files=files, reply_to=reply_to_id, store=store,
    )  # fmt: skip
    return (await _views(session, keys, c, [msg], ctx.user_id))[0]


async def _views(
    session: AsyncSession, keys: Keys, c: Conversation, msgs: list[Message], me: uuid.UUID
) -> list[MessageView]:
    ids = [m.id for m in msgs]
    atts: dict[uuid.UUID, list[AttachmentView]] = {}
    for a in (
        await session.execute(
            select(MessageAttachment).where(
                MessageAttachment.message_id.in_(ids or [uuid.UUID(int=0)])
            )
        )
    ).scalars():
        atts.setdefault(a.message_id, []).append(
            AttachmentView(
                a.id,
                _open(keys, c, "message_attachment", "name", a.id, a.name_enc),
                a.mime,
                a.size_bytes,
            )
        )
    reacts: dict[uuid.UUID, dict[str, list[uuid.UUID]]] = {}
    for r in (
        await session.execute(
            select(MessageReaction).where(MessageReaction.message_id.in_(ids or [uuid.UUID(int=0)]))
        )
    ).scalars():
        reacts.setdefault(r.message_id, {}).setdefault(r.emoji, []).append(r.user_id)
    members = await _members(session, c.id)
    senders = {m.sender_user_id for m in msgs if m.sender_user_id}
    names = await _names(session, senders)
    replies = {m.reply_to_id: m.reply_to_id for m in msgs if m.reply_to_id}
    parents: dict[uuid.UUID, Message] = {}
    if replies:
        for p in (
            await session.execute(select(Message).where(Message.id.in_(list(replies))))
        ).scalars():
            parents[p.id] = p
    pnames = await _names(session, {p.sender_user_id for p in parents.values() if p.sender_user_id})
    out = []
    for m in msgs:
        others = [x for x in members if x.user_id != m.sender_user_id]
        reply = None
        if m.reply_to_id and m.reply_to_id in parents:
            p = parents[m.reply_to_id]
            reply = {
                "id": str(p.id),
                "sender_name": pnames.get(p.sender_user_id) if p.sender_user_id else None,
                "text": None
                if p.deleted_at
                else (
                    _open(keys, c, "message", "body", p.id, p.body_enc)[:120]
                    if p.body_enc
                    else "📎 Document"
                ),
            }
        out.append(
            MessageView(
                m.id,
                m.seq,
                m.sender_user_id,
                names.get(m.sender_user_id) if m.sender_user_id else None,
                m.sent_at,
                m.kind,
                None
                if m.deleted_at or not m.body_enc
                else _open(keys, c, "message", "body", m.id, m.body_enc),
                m.deleted_at is not None,
                m.edited_at is not None,
                reply,
                [] if m.deleted_at else atts.get(m.id, []),
                reacts.get(m.id, {}),
                sum(1 for x in others if x.last_read_seq >= m.seq),
                len(others),
            )
        )
    return out


async def messages(
    session: AsyncSession,
    keys: Keys,
    conversation_id: uuid.UUID,
    *,
    after_seq: int = 0,
    before_seq: int | None = None,
    limit: int = 50,
    mark_read: bool = True,
) -> list[MessageView]:
    """Messages in order: newer than ``after_seq`` (polling), or a page older than
    ``before_seq`` (scrolling back). Reading marks them read."""
    ctx = current_context()
    assert ctx.user_id is not None
    c = await _conversation(session, conversation_id)
    me = await _membership(session, c)
    q = select(Message).where(Message.conversation_id == c.id)
    limit = max(1, min(limit, 200))
    if before_seq is not None:
        rows = list(
            (
                await session.execute(
                    q.where(Message.seq < before_seq).order_by(Message.seq.desc()).limit(limit)
                )
            ).scalars()
        )[::-1]
    elif after_seq:
        rows = list(
            (
                await session.execute(
                    q.where(Message.seq > after_seq).order_by(Message.seq).limit(limit)
                )
            ).scalars()
        )
    else:
        rows = list((await session.execute(q.order_by(Message.seq.desc()).limit(limit))).scalars())[
            ::-1
        ]
    if mark_read and rows and rows[-1].seq > me.last_read_seq:
        me.last_read_seq = rows[-1].seq
        await session.flush()
    return await _views(session, keys, c, rows, ctx.user_id)


async def _message(session: AsyncSession, message_id: uuid.UUID) -> tuple[Message, Conversation]:
    ctx = current_context()
    m = (
        await session.execute(
            select(Message).where(Message.id == message_id, Message.tenant_id == ctx.tenant_id)
        )
    ).scalar_one_or_none()
    if m is None:
        raise NotFound("message not found")
    c = await _conversation(session, m.conversation_id)
    await _membership(session, c)
    return m, c


async def edit(session: AsyncSession, keys: Keys, message_id: uuid.UUID, text: str) -> MessageView:
    ctx = current_context()
    assert ctx.user_id is not None
    m, c = await _message(session, message_id)
    if m.sender_user_id != ctx.user_id or m.deleted_at:
        raise Forbidden("you can only edit your own messages")
    if utcnow() - m.sent_at > EDIT_WINDOW:
        raise Conflict("messages can be edited for 24 hours")
    body = text.strip()
    if not body or len(body) > MAX_TEXT:
        raise ValidationFailed(f"a message is 1-{MAX_TEXT} characters")
    if m.body_enc:
        old = _open(keys, c, "message", "body", m.id, m.body_enc)

        edit_id = uuid7()
        session.add(
            MessageEdit(
                id=edit_id,
                tenant_id=m.tenant_id,
                message_id=m.id,
                body_enc=_seal(keys, c, "message_edit", "body", edit_id, old),
            )
        )
    m.body_enc = _seal(keys, c, "message", "body", m.id, body)
    m.edited_at = utcnow()
    await session.flush()
    return (await _views(session, keys, c, [m], ctx.user_id))[0]


async def delete(session: AsyncSession, keys: Keys, message_id: uuid.UUID) -> MessageView:
    """Delete for everyone: hidden from the conversation. The record is kept (CEDA §26.2)."""
    ctx = current_context()
    assert ctx.user_id is not None
    m, c = await _message(session, message_id)
    mine = await _membership(session, c)
    own = m.sender_user_id == ctx.user_id and utcnow() - m.sent_at <= EDIT_WINDOW
    if not own and mine.role not in ("owner", "admin"):
        raise Forbidden("you can delete your own messages for 24 hours; administrators any")
    if m.deleted_at is None:
        m.deleted_at, m.deleted_by = utcnow(), ctx.user_id
        await session.flush()
    return (await _views(session, keys, c, [m], ctx.user_id))[0]


async def react(
    session: AsyncSession, keys: Keys, message_id: uuid.UUID, emoji: str, *, on: bool
) -> MessageView:
    """Add your reaction, or take it away. Doing either twice changes nothing."""
    ctx = current_context()
    assert ctx.user_id is not None
    if emoji not in REACTIONS:
        raise ValidationFailed(f"react with one of {' '.join(REACTIONS)}")
    m, c = await _message(session, message_id)
    existing = (
        await session.execute(
            select(MessageReaction).where(
                MessageReaction.message_id == m.id,
                MessageReaction.user_id == ctx.user_id,
                MessageReaction.emoji == emoji,
            )
        )
    ).scalar_one_or_none()
    if existing and not on:
        await session.delete(existing)
    elif on and not existing:
        session.add(
            MessageReaction(
                tenant_id=m.tenant_id, message_id=m.id, user_id=ctx.user_id, emoji=emoji
            )
        )
    await session.flush()
    return (await _views(session, keys, c, [m], ctx.user_id))[0]


@dataclass(frozen=True, slots=True)
class FileOut:
    content: bytes
    mime: str
    name: str


async def attachment(
    session: AsyncSession,
    keys: Keys,
    store: BlobStore,
    attachment_id: uuid.UUID,
    *,
    purpose: str | None = None,
) -> FileOut:
    ctx = current_context()
    a = (
        await session.execute(
            select(MessageAttachment).where(
                MessageAttachment.id == attachment_id, MessageAttachment.tenant_id == ctx.tenant_id
            )
        )
    ).scalar_one_or_none()
    if a is None:
        raise NotFound("document not found")
    m = (await session.execute(select(Message).where(Message.id == a.message_id))).scalar_one()
    c = await _conversation(session, m.conversation_id)
    try:
        await _membership(session, c)
    except NotFound:
        if purpose is None:
            raise
        await _review_access(session, c, purpose)
    if m.deleted_at and purpose is None:
        raise NotFound("document not found")
    data = await store.get(a.blob_key)
    if hashlib.sha256(data).hexdigest() != a.sha256:
        raise Conflict("the stored document does not match its checksum")
    return FileOut(data, a.mime, _open(keys, c, "message_attachment", "name", a.id, a.name_enc))


# --------------------------------------------------------------------------- reports, review


async def report(session: AsyncSession, message_id: uuid.UUID, reason: str) -> uuid.UUID:
    ctx = current_context()
    assert ctx.user_id is not None
    m, c = await _message(session, message_id)
    why = " ".join(reason.split())
    if len(why) < 3:
        raise ValidationFailed("say what is wrong with the message")
    r = MessageReport(
        tenant_id=m.tenant_id,
        scope_node_id=c.scope_node_id,
        message_id=m.id,
        reporter_user_id=ctx.user_id,
        reason=why[:500],
    )
    session.add(r)
    await session.flush()
    return r.id


@dataclass(frozen=True, slots=True)
class ReportView:
    id: uuid.UUID
    conversation_id: uuid.UUID
    conversation_title: str
    message_id: uuid.UUID
    reporter_name: str
    reason: str
    status: str
    created_at: datetime
    note: str | None


async def reports(session: AsyncSession, *, status: str | None = "open") -> list[ReportView]:
    """The moderation queue for the caller's scope and everything beneath it."""
    ctx = current_context()
    q = select(MessageReport).where(MessageReport.tenant_id == ctx.tenant_id)
    if status:
        q = q.where(MessageReport.status == status)
    out = []
    for r in (await session.execute(q.order_by(MessageReport.created_at))).scalars():
        if not await governance.is_within(session, ctx.scope_node_id, r.scope_node_id):
            continue
        m = (await session.execute(select(Message).where(Message.id == r.message_id))).scalar_one()
        c = await _conversation(session, m.conversation_id)
        names = await _names(session, {r.reporter_user_id})
        out.append(
            ReportView(
                r.id,
                c.id,
                c.title or c.kind,
                m.id,
                names.get(r.reporter_user_id, "…"),
                r.reason,
                r.status,
                r.created_at,
                r.note,
            )
        )
    return out


async def handle_report(
    session: AsyncSession, report_id: uuid.UUID, *, action: str, note: str | None
) -> None:
    ctx = current_context()
    r = (
        await session.execute(
            select(MessageReport).where(
                MessageReport.id == report_id, MessageReport.tenant_id == ctx.tenant_id
            )
        )
    ).scalar_one_or_none()
    if r is None or not await governance.is_within(session, ctx.scope_node_id, r.scope_node_id):
        raise NotFound("report not found")
    if action not in ("dismiss", "remove"):
        raise ValidationFailed("action is dismiss or remove")
    if r.status != "open":
        raise Conflict(f"this report is already {r.status}")
    if action == "remove":
        m = (await session.execute(select(Message).where(Message.id == r.message_id))).scalar_one()
        m.deleted_at, m.deleted_by = m.deleted_at or utcnow(), m.deleted_by or ctx.user_id
    r.status = "actioned" if action == "remove" else "dismissed"
    r.handled_by, r.handled_at, r.note = ctx.user_id, utcnow(), (note or "").strip()[:500] or None
    await session.flush()


async def _review_access(session: AsyncSession, c: Conversation, purpose: str) -> None:
    ctx = current_context()
    assert ctx.user_id is not None
    why = " ".join(purpose.split())
    if len(why) < 5:
        raise ValidationFailed("say why you need to read this conversation")
    if not await governance.is_within(session, ctx.scope_node_id, c.scope_node_id):
        raise NotFound("conversation not found")
    perm = "message:oversee_protected" if c.comm_class == "protected" else "message:oversee"
    d = await iam.check(
        session, user_id=ctx.user_id, permission=perm, target_scope_id=c.scope_node_id
    )
    d.require()
    session.add(
        ConversationAccess(
            tenant_id=c.tenant_id, conversation_id=c.id, user_id=ctx.user_id, purpose=why[:255]
        )
    )
    await session.flush()


@dataclass(frozen=True, slots=True)
class Review:
    conversation: ConversationView
    messages: list[MessageView]
    edits: dict[str, list[str]]  # message id → earlier texts


async def review(
    session: AsyncSession, keys: Keys, conversation_id: uuid.UUID, *, purpose: str
) -> Review:
    """An authorised officer reads a conversation they are not in, deleted messages and earlier
    edits included, for a stated reason that is logged (CEDA §26.2)."""
    ctx = current_context()
    assert ctx.user_id is not None
    c = await _conversation(session, conversation_id)
    await _review_access(session, c, purpose)
    rows = list(
        (
            await session.execute(
                select(Message).where(Message.conversation_id == c.id).order_by(Message.seq)
            )
        ).scalars()
    )
    views = await _views(session, keys, c, rows, ctx.user_id)
    full = []
    for v, m in zip(views, rows, strict=True):
        if m.deleted_at and m.body_enc:  # officers see what was deleted
            v = MessageView(
                **{
                    **{k: getattr(v, k) for k in v.__dataclass_fields__},
                    "text": _open(keys, c, "message", "body", m.id, m.body_enc),
                }
            )
        full.append(v)
    edits: dict[str, list[str]] = {}
    for e in (
        await session.execute(
            select(MessageEdit)
            .where(MessageEdit.message_id.in_([m.id for m in rows] or [uuid.UUID(int=0)]))
            .order_by(MessageEdit.edited_at)
        )
    ).scalars():
        edits.setdefault(str(e.message_id), []).append(
            _open(keys, c, "message_edit", "body", e.id, e.body_enc)
        )
    return Review(await _cview(session, keys, c, ctx.user_id, with_members=True), full, edits)


async def accesses(session: AsyncSession, conversation_id: uuid.UUID) -> list[dict[str, Any]]:
    ctx = current_context()
    c = await _conversation(session, conversation_id)
    if not await governance.is_within(session, ctx.scope_node_id, c.scope_node_id):
        raise NotFound("conversation not found")
    rows = (
        (
            await session.execute(
                select(ConversationAccess)
                .where(ConversationAccess.conversation_id == c.id)
                .order_by(ConversationAccess.at)
            )
        )
        .scalars()
        .all()
    )
    names = await _names(session, {a.user_id for a in rows})
    return [
        {"user": names.get(a.user_id, "…"), "purpose": a.purpose, "at": a.at.isoformat()}
        for a in rows
    ]


# --------------------------------------------------------------------------- polling


@dataclass(frozen=True, slots=True)
class Updates:
    now: datetime
    unread_total: int
    changed: list[ConversationView]


async def updates(session: AsyncSession, keys: Keys, since: datetime | None) -> Updates:
    """What changed since the last poll: conversations with new messages, and unread counts."""
    ctx = current_context()
    assert ctx.user_id is not None
    now = utcnow()
    base = (
        select(Conversation)
        .join(ConversationMember, ConversationMember.conversation_id == Conversation.id)
        .where(
            Conversation.tenant_id == ctx.tenant_id,
            ConversationMember.user_id == ctx.user_id,
            ConversationMember.left_at.is_(None),
        )
    )
    unread = (
        await session.execute(
            select(
                func.coalesce(
                    func.sum(Conversation.next_seq - 1 - ConversationMember.last_read_seq), 0
                )
            )
            .select_from(Conversation)
            .join(ConversationMember, ConversationMember.conversation_id == Conversation.id)
            .where(
                Conversation.tenant_id == ctx.tenant_id,
                ConversationMember.user_id == ctx.user_id,
                ConversationMember.left_at.is_(None),
                ConversationMember.muted.is_(False),
            )
        )
    ).scalar_one()
    changed: list[ConversationView] = []
    if since is not None:
        rows = (
            await session.execute(base.where(Conversation.last_message_at > since).limit(100))
        ).scalars()
        changed = [await _cview(session, keys, c, ctx.user_id) for c in rows]
    return Updates(now, int(unread or 0), changed)

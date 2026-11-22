/**
 * Messages (ADR-030): conversations between education offices, schools and parents.
 *
 * A list of conversations on the left, the open conversation on the right (one at a time on a
 * phone). New messages arrive by polling: the open conversation every few seconds, the list and
 * the unread count a little less often, and nothing while the tab is hidden.
 */
import { useCallback, useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { api, download, saveBlob } from "../api";
import { useLoad, useSession } from "../session";
import { Button, Empty, ErrorNote, Field, Loading, PageHead, useAction } from "../components/ui";
import { bytes, dateTime } from "../format";

export interface MemberRef {
  user_id: string;
  display_name: string;
  role: string;
  last_read_seq: number;
}

export interface ConversationRef {
  id: string;
  kind: "direct" | "group" | "class" | "staff" | "authority" | "announcement";
  title: string;
  comm_class: "institutional" | "protected";
  posting: "all" | "admins";
  my_role: string | null;
  can_post: boolean;
  unread: number;
  last_seq: number;
  last_message_at: string | null;
  last_preview: string | null;
  members: MemberRef[];
}

export interface AttachmentRef {
  id: string;
  name: string;
  mime: string;
  size_bytes: number;
}

export interface MessageRef {
  id: string;
  seq: number;
  sender_user_id: string | null;
  sender_name: string | null;
  sent_at: string;
  kind: "text" | "file" | "system";
  text: string | null;
  deleted: boolean;
  edited: boolean;
  reply_to: { id: string; sender_name: string | null; text: string | null } | null;
  attachments: AttachmentRef[];
  reactions: Record<string, string[]>;
  read_by: number;
  others: number;
}

interface Contact {
  user_id: string;
  display_name: string;
  role: string;
}

export const REACTIONS = ["👍", "❤️", "😂", "😮", "🙏", "✅"];
const ACCEPT = ".pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.csv,.jpg,.jpeg,.png,.webp";
const MAX_FILES = 5;
const MAX_MB = 20;
const KIND: Record<ConversationRef["kind"], string> = {
  direct: "",
  group: "Group",
  class: "Class",
  staff: "Staff room",
  authority: "Heads of schools",
  announcement: "Announcements",
};

/** Poll while the tab is visible. */
function usePoll(fn: () => void, ms: number, deps: unknown[]) {
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => {
    const t = setInterval(() => {
      if (document.visibilityState === "visible") ref.current();
    }, ms);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ms, ...deps]);
}

const UNREAD_EVENT = "syn:messages-changed";

/** The unread total for the navigation bar; re-checked whenever the conversation list changes. */
export function useUnread(): number {
  const [n, setN] = useState(0);
  const check = useCallback(() => {
    api<{ unread_total: number }>("/v1/messaging/updates").then((r) => setN(r.unread_total), () => {});
  }, []);
  useEffect(() => {
    check();
    window.addEventListener(UNREAD_EVENT, check);
    return () => window.removeEventListener(UNREAD_EVENT, check);
  }, [check]);
  usePoll(check, 20_000, []);
  return n;
}

function time(s: string | null): string {
  if (!s) return "";
  const d = new Date(s);
  const now = new Date();
  return d.toDateString() === now.toDateString()
    ? d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })
    : d.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]!.toUpperCase())
    .join("");
}

// --------------------------------------------------------------------------- the page

export function MessagesPage() {
  const { id } = useParams();
  const list = useLoad(() => api<{ items: ConversationRef[] }>("/v1/messaging/conversations"), []);
  const since = useRef<string | null>(null);
  usePoll(
    () => {
      api<{ now: string; changed: ConversationRef[] }>("/v1/messaging/updates", { query: { since: since.current } }).then((u) => {
        if (since.current && u.changed.length) list.reload();
        since.current = u.now;
      }, () => {});
    },
    5_000,
    [],
  );
  const items = list.data?.items ?? [];
  useEffect(() => {
    if (list.data) window.dispatchEvent(new Event(UNREAD_EVENT));
  }, [list.data]);
  return (
    <div className={`chat ${id ? "chat-open" : ""}`}>
      <aside className="chat-list" aria-label="Conversations">
        <div className="chat-list-head">
          <h1>Messages</h1>
          <NewConversation onOpened={list.reload} />
        </div>
        {list.loading && !list.data && <Loading />}
        <ErrorNote error={list.error} />
        {list.data && items.length === 0 && (
          <Empty title="No conversations yet">Start one with “New”. Messages are encrypted and kept by the state as records.</Empty>
        )}
        <ul className="convs">
          {items.map((c) => (
            <li key={c.id}>
              <Link to={`/messages/${c.id}`} className={`conv ${c.id === id ? "active" : ""}`} aria-current={c.id === id ? "page" : undefined}>
                <span className="avatar" aria-hidden="true">
                  {c.kind === "direct" ? initials(c.title) : c.kind === "announcement" ? "📣" : "👥"}
                </span>
                <span className="conv-main">
                  <span className="conv-top">
                    <strong className="conv-title">{c.title}</strong>
                    <span className="conv-time">{time(c.last_message_at)}</span>
                  </span>
                  <span className="conv-bottom">
                    <span className="conv-preview">
                      {c.comm_class === "protected" && <span title="Protected conversation">🔒 </span>}
                      {KIND[c.kind] && <span className="muted">{KIND[c.kind]} · </span>}
                      {c.last_preview ?? "No messages yet"}
                    </span>
                    {c.unread > 0 && (
                      <span className="unread" aria-label={`${c.unread} unread`}>
                        {c.unread}
                      </span>
                    )}
                  </span>
                </span>
              </Link>
            </li>
          ))}
        </ul>
      </aside>
      <section className="chat-thread" aria-label="Conversation">
        {id ? (
          <Thread key={id} id={id} onChange={list.reload} />
        ) : (
          <div className="chat-blank">
            <span aria-hidden="true">🔐</span>
            <p>Choose a conversation, or start a new one.</p>
            <p className="small muted">
              Messages and documents are encrypted in transit and when stored. They are institutional records: kept, and readable by an authorised
              officer only for a stated reason that is logged.
            </p>
          </div>
        )}
      </section>
    </div>
  );
}

// --------------------------------------------------------------------------- starting one

function NewConversation({ onOpened }: { onOpened: () => void }) {
  const s = useSession();
  const nav = useNavigate();
  const [mode, setMode] = useState<null | "chat" | "group" | "announce">(null);
  const parent = s.me!.grants.every((g) => g.role === "PAR");
  const open = useAction(
    async (path: string, json?: unknown) => (await api<{ id: string }>(path, { method: "POST", json: json ?? {} })).id,
    (cid) => {
      setMode(null);
      onOpened();
      nav(`/messages/${cid}`);
    },
  );
  return (
    <div className="new-conv">
      <div className="row wrap">
        <Button kind="primary" onClick={() => setMode(mode === "chat" ? null : "chat")}>
          New
        </Button>
        {!parent && (
          <Button kind="secondary" onClick={() => setMode(mode === "group" ? null : "group")}>
            Group
          </Button>
        )}
        {s.can("message:broadcast") && (
          <Button kind="secondary" onClick={() => setMode(mode === "announce" ? null : "announce")}>
            Announce
          </Button>
        )}
      </div>
      {!parent && mode === null && (
        <div className="row wrap small">
          {s.institution && (
            <button className="link" onClick={() => void open.run(`/v1/messaging/institutions/${s.institution!.id}/staff-room`)}>
              {s.institution.name} staff room
            </button>
          )}
          {!s.institution && (
            <button className="link" onClick={() => void open.run("/v1/messaging/authority-channel")}>
              Channel with heads of schools
            </button>
          )}
        </div>
      )}
      <ErrorNote error={open.error} />
      {mode === "chat" && <ContactSearch label="Who do you want to message?" onPick={(c) => void open.run("/v1/messaging/direct", { user_id: c.user_id })} />}
      {mode === "group" && <NewGroup onCreate={(body) => open.run("/v1/messaging/groups", body)} busy={open.busy} />}
      {mode === "announce" && <NewAnnouncement onCreate={(body) => open.run("/v1/messaging/announcements", body)} busy={open.busy} />}
    </div>
  );
}

function ContactSearch({ label, onPick, exclude = [] }: { label: string; onPick: (c: Contact) => void; exclude?: string[] }) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<Contact[]>([]);
  useEffect(() => {
    if (q.trim().length < 2) {
      setHits([]);
      return;
    }
    const t = setTimeout(() => {
      api<{ items: Contact[] }>("/v1/messaging/contacts", { query: { q } }).then((r) => setHits(r.items), () => setHits([]));
    }, 250);
    return () => clearTimeout(t);
  }, [q]);
  const shown = hits.filter((h) => !exclude.includes(h.user_id));
  return (
    <div className="stack">
      <Field label={label} hint="Type at least two letters of a name or email. Only people you may message are listed.">
        {(fid) => <input id={fid} value={q} onChange={(e) => setQ(e.target.value)} autoComplete="off" />}
      </Field>
      {q.trim().length >= 2 && (
        <ul className="plain contact-hits">
          {shown.length === 0 && <li className="muted small">Nobody you may message matches.</li>}
          {shown.map((h) => (
            <li key={h.user_id}>
              <button className="contact" onClick={() => onPick(h)}>
                <span className="avatar sm" aria-hidden="true">
                  {initials(h.display_name)}
                </span>
                <span>
                  <strong>{h.display_name}</strong>
                  <span className="small muted"> · {h.role}</span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function NewGroup({ onCreate, busy }: { onCreate: (body: unknown) => Promise<unknown>; busy: boolean }) {
  const [title, setTitle] = useState("");
  const [people, setPeople] = useState<Contact[]>([]);
  const [prot, setProt] = useState(false);
  const [ann, setAnn] = useState(false);
  return (
    <form
      className="stack panel"
      onSubmit={(e) => {
        e.preventDefault();
        void onCreate({ title, member_ids: people.map((p) => p.user_id), protected: prot, announcements: ann });
      }}
    >
      <Field label="Group name">{(id) => <input id={id} value={title} onChange={(e) => setTitle(e.target.value)} required minLength={2} maxLength={120} />}</Field>
      <ContactSearch label="Add people" exclude={people.map((p) => p.user_id)} onPick={(c) => setPeople([...people, c])} />
      {people.length > 0 && (
        <ul className="tags" aria-label="People in the group">
          {people.map((p) => (
            <li key={p.user_id} className="status">
              {p.display_name}{" "}
              <button type="button" className="link" aria-label={`Remove ${p.display_name}`} onClick={() => setPeople(people.filter((x) => x !== p))}>
                ×
              </button>
            </li>
          ))}
        </ul>
      )}
      <label className="check">
        <input type="checkbox" checked={prot} onChange={(e) => setProt(e.target.checked)} /> Protected (pastoral or HR; staff only, a separate key)
      </label>
      <label className="check">
        <input type="checkbox" checked={ann} onChange={(e) => setAnn(e.target.checked)} /> Only administrators post
      </label>
      <Button busy={busy} type="submit" disabled={people.length === 0}>
        Create group
      </Button>
    </form>
  );
}

function NewAnnouncement({ onCreate, busy }: { onCreate: (body: unknown) => Promise<unknown>; busy: boolean }) {
  const s = useSession();
  const [title, setTitle] = useState("");
  const [audience, setAudience] = useState<"parents" | "staff">("parents");
  return (
    <form
      className="stack panel"
      onSubmit={(e) => {
        e.preventDefault();
        void onCreate({ title, audience, institution_id: s.institution?.id ?? null });
      }}
    >
      <p className="small muted">
        A one-way channel: everyone in it reads, only you post. It goes to {s.institution ? s.institution.name : "every institution in your scope"}.
      </p>
      <Field label="Channel name">{(id) => <input id={id} value={title} onChange={(e) => setTitle(e.target.value)} required minLength={2} maxLength={120} />}</Field>
      <Field label="To">
        {(id) => (
          <select id={id} value={audience} onChange={(e) => setAudience(e.target.value as "parents" | "staff")}>
            <option value="parents">All parents and guardians</option>
            <option value="staff">All staff</option>
          </select>
        )}
      </Field>
      <Button busy={busy} type="submit">
        Create channel
      </Button>
    </form>
  );
}

// --------------------------------------------------------------------------- one conversation

function Thread({ id, onChange }: { id: string; onChange: () => void }) {
  const s = useSession();
  const me = s.me!.user_id;
  const conv = useLoad(() => api<ConversationRef>(`/v1/messaging/conversations/${id}`), [id]);
  const [msgs, setMsgs] = useState<MessageRef[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [more, setMore] = useState(true);
  const [reply, setReply] = useState<MessageRef | null>(null);
  const [editing, setEditing] = useState<MessageRef | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const polls = useRef(0);
  const last = msgs.length ? msgs[msgs.length - 1]!.seq : 0;

  const merge = (incoming: MessageRef[]) =>
    setMsgs((cur) => {
      const by = new Map(cur.map((m) => [m.id, m]));
      for (const m of incoming) by.set(m.id, m);
      return [...by.values()].sort((a, b) => a.seq - b.seq);
    });

  const latest = useCallback(async () => {
    const r = await api<{ items: MessageRef[] }>(`/v1/messaging/conversations/${id}/messages`, { query: { limit: 50 } });
    merge(r.items);
    setMore(r.items.length === 50);
  }, [id]);

  useEffect(() => {
    setMsgs([]);
    latest().then(onChange, setError);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  usePoll(
    () => {
      polls.current += 1;
      // New messages every poll; now and then the whole window, for read receipts and edits.
      const p =
        polls.current % 5 === 0
          ? latest()
          : api<{ items: MessageRef[] }>(`/v1/messaging/conversations/${id}/messages`, { query: { after: last } }).then((r) => {
              if (r.items.length) {
                merge(r.items);
                onChange();
              }
            });
      p.then(() => setError(null), setError);
    },
    3_000,
    [id, last],
  );

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end" });
  }, [last]);

  const older = useAction(async () => {
    const first = msgs[0]?.seq;
    if (!first) return;
    const r = await api<{ items: MessageRef[] }>(`/v1/messaging/conversations/${id}/messages`, { query: { before: first, limit: 50 } });
    merge(r.items);
    setMore(r.items.length === 50);
  });

  const act = useAction(async (fn: () => Promise<MessageRef>) => merge([await fn()]));

  if (conv.loading && !conv.data) return <Loading />;
  if (conv.error) return <ErrorNote error={conv.error} />;
  const c = conv.data!;
  const admin = c.my_role === "owner" || c.my_role === "admin";
  const sub =
    c.kind === "direct"
      ? "Encrypted · kept as a school record"
      : `${c.members.length} people${c.posting === "admins" ? " · only administrators post" : ""}`;

  return (
    <div className="thread">
      <header className="thread-head">
        <Link to="/messages" className="back" aria-label="Back to conversations">
          ←
        </Link>
        <div className="thread-title">
          <strong>{c.title}</strong>
          <span className="small muted">
            {c.comm_class === "protected" && "🔒 Protected · "}
            {sub}
          </span>
        </div>
        <ThreadMenu c={c} onChange={() => {
          conv.reload();
          onChange();
        }} />
      </header>
      <div className="msgs" role="log" aria-live="polite" aria-relevant="additions">
        {more && msgs.length > 0 && (
          <div className="center">
            <Button kind="quiet" busy={older.busy} onClick={() => void older.run()}>
              Earlier messages
            </Button>
          </div>
        )}
        {msgs.map((m, i) => (
          <Bubble
            key={m.id}
            m={m}
            mine={m.sender_user_id === me}
            showName={c.kind !== "direct" && m.sender_user_id !== me && msgs[i - 1]?.sender_user_id !== m.sender_user_id}
            canAdmin={admin}
            me={me}
            onReply={() => setReply(m)}
            onEdit={() => setEditing(m)}
            onAct={(fn) => void act.run(fn)}
          />
        ))}
        <div ref={bottom} />
      </div>
      <ErrorNote error={error ?? act.error ?? older.error} />
      {c.can_post ? (
        <Composer
          id={id}
          reply={reply}
          editing={editing}
          clear={() => {
            setReply(null);
            setEditing(null);
          }}
          sent={(m) => {
            if (m) merge([m]);
            else void latest();
            onChange();
          }}
        />
      ) : (
        <p className="composer-off small muted">Only the administrators of this conversation post here.</p>
      )}
    </div>
  );
}

function ThreadMenu({ c, onChange }: { c: ConversationRef; onChange: () => void }) {
  const nav = useNavigate();
  const [open, setOpen] = useState(false);
  const muted = useAction(async (on: boolean) => api(`/v1/messaging/conversations/${c.id}/mute`, { method: "PUT", json: { muted: on } }), onChange);
  const leave = useAction(async () => api(`/v1/messaging/conversations/${c.id}/membership`, { method: "DELETE" }), () => {
    onChange();
    nav("/messages");
  });
  return (
    <div className="thread-menu">
      <button className="btn btn-quiet" aria-expanded={open} onClick={() => setOpen(!open)}>
        Details
      </button>
      {open && (
        <div className="menu-pop panel">
          <h2>{c.title}</h2>
          <p className="small muted">
            {c.comm_class === "protected"
              ? "Protected: staff only, encrypted under a separate key; only safeguarding and data protection officers may review it."
              : "Institutional record: encrypted, kept, and reviewable by an authorised officer for a logged reason."}
          </p>
          <ul className="plain small">
            {c.members.map((m) => (
              <li key={m.user_id}>
                {m.display_name} {m.role !== "member" && <span className="badge">{m.role.replace("_", " ")}</span>}
              </li>
            ))}
          </ul>
          <div className="row wrap">
            <Button kind="secondary" busy={muted.busy} onClick={() => void muted.run(true)}>
              Mute
            </Button>
            <Button kind="secondary" busy={muted.busy} onClick={() => void muted.run(false)}>
              Unmute
            </Button>
            {c.kind !== "direct" && (
              <Button kind="danger" busy={leave.busy} onClick={() => void leave.run()}>
                Leave
              </Button>
            )}
          </div>
          <ErrorNote error={muted.error ?? leave.error} />
        </div>
      )}
    </div>
  );
}

function Ticks({ m }: { m: MessageRef }) {
  if (m.others === 0) return null;
  const all = m.read_by >= m.others;
  const label = all ? "Read" : m.read_by > 0 ? `Read by ${m.read_by} of ${m.others}` : "Sent";
  return (
    <span className={`ticks ${all ? "read" : ""}`} title={label} aria-label={label}>
      {m.read_by > 0 ? "✓✓" : "✓"}
    </span>
  );
}

function Bubble({
  m, mine, showName, canAdmin, me, onReply, onEdit, onAct,
}: {
  m: MessageRef;
  mine: boolean;
  showName: boolean;
  canAdmin: boolean;
  me: string;
  onReply: () => void;
  onEdit: () => void;
  onAct: (fn: () => Promise<MessageRef>) => void;
}) {
  const [menu, setMenu] = useState(false);
  const [reporting, setReporting] = useState(false);
  const fresh = Date.now() - new Date(m.sent_at).getTime() < 24 * 3600 * 1000;
  if (m.kind === "system")
    return (
      <div className="sys small">
        {m.text} · {time(m.sent_at)}
      </div>
    );
  const get = async (a: AttachmentRef) => {
    const f = await download(`/v1/messaging/attachments/${a.id}`);
    saveBlob(f.blob, a.name);
  };
  const react = (e: string) =>
    onAct(() =>
      api<MessageRef>(`/v1/messaging/messages/${m.id}/reactions/${encodeURIComponent(e)}`, {
        method: (m.reactions[e] ?? []).includes(me) ? "DELETE" : "PUT",
      }),
    );
  return (
    <div className={`bubble-row ${mine ? "mine" : ""}`}>
      <div className={`bubble ${m.deleted ? "deleted" : ""}`}>
        {showName && <div className="sender">{m.sender_name}</div>}
        {m.reply_to && (
          <div className="quoted">
            <strong>{m.reply_to.sender_name ?? ""}</strong>
            <span>{m.reply_to.text ?? "This message was deleted"}</span>
          </div>
        )}
        {m.deleted ? (
          <em className="muted">This message was deleted</em>
        ) : (
          <>
            {m.attachments.map((a) => (
              <button key={a.id} className="attachment" onClick={() => void get(a)}>
                <span aria-hidden="true">{a.mime.startsWith("image/") ? "🖼️" : "📄"}</span>
                <span className="att-name">{a.name}</span>
                <span className="small muted">{bytes(a.size_bytes)} · Download</span>
              </button>
            ))}
            {m.text && <div className="text">{m.text}</div>}
          </>
        )}
        <div className="meta">
          {m.edited && !m.deleted && <span>edited · </span>}
          <time dateTime={m.sent_at} title={dateTime(m.sent_at)}>
            {time(m.sent_at)}
          </time>
          {mine && !m.deleted && <Ticks m={m} />}
        </div>
        {Object.keys(m.reactions).length > 0 && (
          <div className="reactions">
            {Object.entries(m.reactions).map(([e, who]) => (
              <button key={e} className={`reaction ${who.includes(me) ? "own" : ""}`} onClick={() => react(e)} aria-label={`${e} ${who.length}`}>
                {e} {who.length > 1 ? who.length : ""}
              </button>
            ))}
          </div>
        )}
      </div>
      {!m.deleted && (
        <button className="msg-more" aria-label="Message options" aria-expanded={menu} onClick={() => setMenu(!menu)}>
          ⋯
        </button>
      )}
      {menu && (
        <div className="msg-menu panel" onMouseLeave={() => setMenu(false)}>
          <div className="react-row">
            {REACTIONS.map((e) => (
              <button key={e} onClick={() => (react(e), setMenu(false))} aria-label={`React ${e}`}>
                {e}
              </button>
            ))}
          </div>
          <button className="link" onClick={() => (onReply(), setMenu(false))}>
            Reply
          </button>
          {mine && fresh && m.text && (
            <button className="link" onClick={() => (onEdit(), setMenu(false))}>
              Edit
            </button>
          )}
          {((mine && fresh) || canAdmin) && (
            <button
              className="link bad"
              onClick={() => {
                if (confirm("Delete this message for everyone? It is hidden from the conversation and kept as a record.")) {
                  onAct(() => api<MessageRef>(`/v1/messaging/messages/${m.id}`, { method: "DELETE" }));
                }
                setMenu(false);
              }}
            >
              Delete for everyone
            </button>
          )}
          {!mine && (
            <button className="link" onClick={() => setReporting(true)}>
              Report
            </button>
          )}
          {reporting && <ReportForm id={m.id} done={() => (setReporting(false), setMenu(false))} />}
        </div>
      )}
    </div>
  );
}

function ReportForm({ id, done }: { id: string; done: () => void }) {
  const [reason, setReason] = useState("");
  const [ok, setOk] = useState(false);
  const send = useAction(async () => api(`/v1/messaging/messages/${id}/report`, { json: { reason } }), () => setOk(true));
  if (ok)
    return (
      <p className="small" role="status">
        Reported. The school's moderators will look at it.{" "}
        <button className="link" onClick={done}>
          Close
        </button>
      </p>
    );
  return (
    <form
      className="stack"
      onSubmit={(e) => {
        e.preventDefault();
        void send.run();
      }}
    >
      <Field label="What is wrong with it?">{(fid) => <textarea id={fid} rows={2} value={reason} onChange={(e) => setReason(e.target.value)} required minLength={3} maxLength={500} />}</Field>
      <ErrorNote error={send.error} />
      <Button busy={send.busy} type="submit">
        Report
      </Button>
    </form>
  );
}

function Composer({
  id, reply, editing, clear, sent,
}: {
  id: string;
  reply: MessageRef | null;
  editing: MessageRef | null;
  clear: () => void;
  sent: (m: MessageRef | null) => void;
}) {
  const [text, setText] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [problem, setProblem] = useState<string | null>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const picker = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editing) setText(editing.text ?? "");
    if (editing || reply) input.current?.focus();
  }, [editing, reply]);

  const send = useAction(async () => {
    if (editing) {
      const m = await api<MessageRef>(`/v1/messaging/messages/${editing.id}`, { method: "PATCH", json: { text } });
      return m;
    }
    const form = new FormData();
    form.set("text", text);
    if (reply) form.set("reply_to", reply.id);
    for (const f of files) form.append("files", f, f.name);
    await api(`/v1/messaging/conversations/${id}/messages`, { form });
    return null;
  }, (m) => {
    setText("");
    setFiles([]);
    clear();
    sent(m);
  });

  const add = (list: FileList | null) => {
    setProblem(null);
    const next = [...files, ...Array.from(list ?? [])];
    if (next.length > MAX_FILES) return setProblem(`Attach at most ${MAX_FILES} documents at a time.`);
    const big = next.find((f) => f.size > MAX_MB * 1024 * 1024);
    if (big) return setProblem(`${big.name} is larger than ${MAX_MB} MB.`);
    setFiles(next);
  };

  const submit = (e?: FormEvent) => {
    e?.preventDefault();
    if (!text.trim() && files.length === 0) return;
    void send.run();
  };
  const key = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) submit(e);
    if (e.key === "Escape") clear();
  };

  return (
    <form className="composer" onSubmit={submit}>
      {(reply || editing) && (
        <div className="replying small">
          <span>
            {editing ? "Editing your message" : <>Replying to <strong>{reply!.sender_name ?? "message"}</strong>: {reply!.text ?? "Document"}</>}
          </span>
          <button
            type="button"
            className="link"
            onClick={() => {
              clear();
              if (editing) setText("");
            }}
          >
            Cancel
          </button>
        </div>
      )}
      {files.length > 0 && (
        <ul className="tags" aria-label="Documents to send">
          {files.map((f, i) => (
            <li key={`${f.name}${i}`} className="status">
              📄 {f.name} <span className="muted">{bytes(f.size)}</span>{" "}
              <button type="button" className="link" aria-label={`Remove ${f.name}`} onClick={() => setFiles(files.filter((_, j) => j !== i))}>
                ×
              </button>
            </li>
          ))}
        </ul>
      )}
      {problem && <p className="small bad">{problem}</p>}
      <ErrorNote error={send.error} />
      <div className="composer-row">
        {!editing && (
          <>
            <input ref={picker} type="file" multiple accept={ACCEPT} className="sr" tabIndex={-1} aria-hidden="true" onChange={(e) => (add(e.target.files), (e.target.value = ""))} />
            <button type="button" className="btn btn-quiet attach-btn" aria-label="Attach documents" title="PDF, Word, Excel, PowerPoint, text or pictures, up to 20 MB" onClick={() => picker.current?.click()}>
              📎
            </button>
          </>
        )}
        <label className="sr" htmlFor="compose">
          Message
        </label>
        <textarea id="compose" ref={input} rows={1} value={text} maxLength={4000} placeholder="Type a message" onChange={(e) => setText(e.target.value)} onKeyDown={key} />
        <Button busy={send.busy} type="submit" aria-label={editing ? "Save" : "Send"}>
          {editing ? "Save" : "Send"}
        </Button>
      </div>
    </form>
  );
}

// --------------------------------------------------------------------------- moderation

interface ReportRef {
  id: string;
  conversation_id: string;
  conversation_title: string;
  message_id: string;
  reporter_name: string;
  reason: string;
  status: string;
  created_at: string;
  note: string | null;
}

interface Review {
  conversation: ConversationRef;
  messages: MessageRef[];
  edits: Record<string, string[]>;
}

/** Reported messages, and an officer's review of a conversation for a logged reason. */
export function ModerationPage() {
  const s = useSession();
  const [status, setStatus] = useState("open");
  const list = useLoad(() => api<{ items: ReportRef[] }>("/v1/messaging/reports", { query: { status } }), [status]);
  const handle = useAction(async (id: string, action: "dismiss" | "remove") => api(`/v1/messaging/reports/${id}`, { json: { action } }), list.reload);
  const [reviewing, setReviewing] = useState<string | null>(null);
  return (
    <div className="page">
      <PageHead eyebrow="Messages" title="Reported messages" />
      <div className="filters">
        <Field label="Show">
          {(id) => (
            <select id={id} value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="open">Waiting</option>
              <option value="actioned">Removed</option>
              <option value="dismissed">Dismissed</option>
              <option value="all">All</option>
            </select>
          )}
        </Field>
      </div>
      <ErrorNote error={list.error ?? handle.error} />
      {list.data?.items.length === 0 && <Empty title="Nothing to look at" />}
      <ul className="plain">
        {list.data?.items.map((r) => (
          <li key={r.id} className="panel stack">
            <div>
              <strong>{r.conversation_title}</strong> <span className="small muted">· reported by {r.reporter_name}, {dateTime(r.created_at)}</span>
            </div>
            <p>“{r.reason}”</p>
            {r.note && <p className="small muted">Note: {r.note}</p>}
            <div className="row wrap">
              {r.status === "open" && (
                <>
                  <Button kind="danger" busy={handle.busy} onClick={() => void handle.run(r.id, "remove")}>
                    Remove the message
                  </Button>
                  <Button kind="secondary" busy={handle.busy} onClick={() => void handle.run(r.id, "dismiss")}>
                    Dismiss
                  </Button>
                </>
              )}
              {s.can("message:oversee", "message:oversee_protected") && (
                <Button kind="quiet" onClick={() => setReviewing(reviewing === r.conversation_id ? null : r.conversation_id)}>
                  Review the conversation
                </Button>
              )}
            </div>
            {reviewing === r.conversation_id && <ReviewPanel conversationId={r.conversation_id} highlight={r.message_id} />}
          </li>
        ))}
      </ul>
    </div>
  );
}

function ReviewPanel({ conversationId, highlight }: { conversationId: string; highlight: string }) {
  const [purpose, setPurpose] = useState("");
  const [r, setR] = useState<Review | null>(null);
  const open = useAction(async () => setR(await api<Review>(`/v1/messaging/conversations/${conversationId}/review`, { json: { purpose } })));
  if (!r)
    return (
      <form
        className="stack"
        onSubmit={(e) => {
          e.preventDefault();
          void open.run();
        }}
      >
        <Field label="Why do you need to read it?" hint="Recorded with your name and the time, and shown to auditors.">
          {(id) => <input id={id} value={purpose} onChange={(e) => setPurpose(e.target.value)} required minLength={5} maxLength={255} />}
        </Field>
        <ErrorNote error={open.error} />
        <Button busy={open.busy} type="submit">
          Read the conversation
        </Button>
      </form>
    );
  return (
    <div className="review">
      <p className="small muted">
        {r.conversation.members.map((m) => m.display_name).join(", ")} · your reading is logged
      </p>
      {r.messages.map((m) => (
        <div key={m.id} className={`review-msg ${m.id === highlight ? "hl" : ""}`}>
          <span className="small muted">
            {m.sender_name ?? "System"} · {dateTime(m.sent_at)}
            {m.deleted && " · deleted"}
            {m.edited && " · edited"}
          </span>
          <div>{m.text}</div>
          {m.attachments.map((a) => (
            <div key={a.id} className="small">
              📄 {a.name}
            </div>
          ))}
          {(r.edits[m.id] ?? []).map((t, i) => (
            <div key={i} className="small muted">
              Earlier: {t}
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

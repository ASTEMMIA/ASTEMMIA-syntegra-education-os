import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { api } from "../api";
import { LEVEL_NAME, useAcademicSession, useClasses, useTeachers, useYears } from "../academic";
import { useLoad, useSession } from "../session";
import { Button, Dl, Empty, ErrorNote, Field, Loading, PageHead, Status, useAction } from "../components/ui";
import { NeedInstitution } from "../App";
import type { ClassRef, Items, Member, OfferingRef, Room, StaffRef, SubjectRef } from "../types";
import { date, words } from "../format";

function SessionPicker({ s }: { s: ReturnType<typeof useAcademicSession> }) {
  if (!s.sessions.length) return null;
  return (
    <Field label="Session">
      {(id) => (
        <select id={id} value={s.session?.id ?? ""} onChange={(e) => s.choose(e.target.value)}>
          {s.sessions.map((x) => (
            <option key={x.id} value={x.id}>
              {x.name}
              {x.status === "current" ? " (current)" : ""}
            </option>
          ))}
        </select>
      )}
    </Field>
  );
}

export { SessionPicker };

/** Every class (arm) of the institution in a session, with size against capacity. */
export function ClassesPage() {
  const s = useSession();
  const inst = s.institution;
  const sess = useAcademicSession();
  const classes = useClasses(inst?.id, sess.session?.id);
  if (!inst) return <NeedInstitution />;
  const groups = new Map<string, ClassRef[]>();
  for (const c of classes.data?.items ?? []) groups.set(c.level, [...(groups.get(c.level) ?? []), c]);
  return (
    <div className="page">
      <PageHead eyebrow={inst.name} title="Classes">
        <Link className="btn btn-secondary" to="/calendar">
          Calendar and curriculum
        </Link>
      </PageHead>
      <div className="filters">
        <SessionPicker s={sess} />
      </div>
      <ErrorNote error={sess.error ?? classes.error} />
      {!sess.loading && !sess.session && (
        <Empty title="No session set yet">The board (for schools) or the institution (tertiary) sets the session and its terms first.</Empty>
      )}
      {sess.session && s.can("class:manage") && <NewClass institutionId={inst.id} sessionId={sess.session.id} done={classes.reload} />}
      {classes.loading && <Loading />}
      {classes.data && classes.data.items.length === 0 && sess.session && <Empty title={`No classes for ${sess.session.name} yet`} />}
      {[...groups.entries()].map(([level, list]) => (
        <section key={level} className="panel">
          <h2>{LEVEL_NAME[level] ?? level}</h2>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Class</th>
                  <th>Class teacher</th>
                  <th>Room</th>
                  <th>Learners</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {list.map((c) => (
                  <tr key={c.id}>
                    <td>
                      <Link to={`/classes/${c.id}`} className="strong">
                        {c.name}
                      </Link>
                    </td>
                    <td>{c.class_teacher_name ?? <span className="warn">Not assigned</span>}</td>
                    <td>{c.room ? `${c.room.code} · ${c.room.name}` : "—"}</td>
                    <td>
                      <Fill size={c.size} capacity={c.capacity} />
                    </td>
                    <td>{c.status === "closed" && <Status value="closed" />}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ))}
    </div>
  );
}

function Fill({ size, capacity }: { size: number; capacity: number }) {
  const pct = Math.min(100, Math.round((100 * size) / Math.max(1, capacity)));
  return (
    <span className="fill" title={`${size} of ${capacity}`}>
      <span className="fill-bar" aria-hidden="true">
        <span style={{ width: `${pct}%` }} className={pct >= 100 ? "full" : ""} />
      </span>
      <span className="mono">
        {size} / {capacity}
      </span>
    </span>
  );
}

function NewClass({ institutionId, sessionId, done }: { institutionId: string; sessionId: string; done: () => void }) {
  const s = useSession();
  const years = useYears();
  const rooms = useLoad(() => api<Items<Room>>(`/v1/institutions/${institutionId}/rooms`), [institutionId]);
  const teachers = useTeachers(institutionId, s.can("staff:view"));
  const levels = new Set(s.institution?.level_codes ?? []);
  const [f, setF] = useState({ year_code: "", arm: "A", capacity: "40", room_id: "", class_teacher_staff_id: "" });
  const [room, setRoom] = useState({ code: "", name: "", capacity: "45" });
  const open = useAction(
    () =>
      api(`/v1/institutions/${institutionId}/classes`, {
        json: {
          session_id: sessionId,
          year_code: f.year_code,
          arm: f.arm.trim(),
          capacity: Number(f.capacity),
          room_id: f.room_id || null,
          class_teacher_staff_id: f.class_teacher_staff_id || null,
        },
      }),
    () => {
      setF({ ...f, arm: nextArm(f.arm) });
      done();
    },
  );
  const addRoom = useAction(
    () => api<Room>(`/v1/institutions/${institutionId}/rooms`, { json: { code: room.code, name: room.name, capacity: Number(room.capacity) } }),
    (r) => {
      setRoom({ code: "", name: "", capacity: "45" });
      rooms.reload();
      setF({ ...f, room_id: r.id });
    },
  );
  const yearOptions = (years.data?.items ?? []).filter((y) => levels.has(y.level));
  return (
    <details className="panel">
      <summary>
        <h2 className="inline">Open a class</h2>
      </summary>
      <form
        className="form-grid"
        onSubmit={(e) => {
          e.preventDefault();
          void open.run();
        }}
      >
        <Field label="Year">
          {(id) => (
            <select id={id} value={f.year_code} onChange={(e) => setF({ ...f, year_code: e.target.value })} required>
              <option value="">Choose…</option>
              {yearOptions.map((y) => (
                <option key={y.code} value={y.code}>
                  {y.name}
                </option>
              ))}
            </select>
          )}
        </Field>
        <Field label="Arm" hint="A letter, number or short word: A, B, Gold.">
          {(id) => <input id={id} value={f.arm} onChange={(e) => setF({ ...f, arm: e.target.value })} required maxLength={20} />}
        </Field>
        <Field label="Capacity">
          {(id) => <input id={id} type="number" min={1} max={120} value={f.capacity} onChange={(e) => setF({ ...f, capacity: e.target.value })} required />}
        </Field>
        <Field label="Room">
          {(id) => (
            <select id={id} value={f.room_id} onChange={(e) => setF({ ...f, room_id: e.target.value })}>
              <option value="">None yet</option>
              {rooms.data?.items.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.code} · {r.name} (seats {r.capacity})
                </option>
              ))}
            </select>
          )}
        </Field>
        <Field label="Class teacher">
          {(id) => (
            <select id={id} value={f.class_teacher_staff_id} onChange={(e) => setF({ ...f, class_teacher_staff_id: e.target.value })}>
              <option value="">Assign later</option>
              {teachers.data?.items.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.first_name} {t.surname}
                </option>
              ))}
            </select>
          )}
        </Field>
        <div className="form-actions">
          <p className="muted small">The class gets its subjects from the curriculum the state has adopted for this year.</p>
          <ErrorNote error={open.error} />
          <Button busy={open.busy} type="submit">
            Open the class
          </Button>
        </div>
      </form>
      <form
        className="row wrap"
        onSubmit={(e) => {
          e.preventDefault();
          void addRoom.run();
        }}
      >
        <Field label="New room code">{(id) => <input id={id} value={room.code} onChange={(e) => setRoom({ ...room, code: e.target.value })} placeholder="B4" required />}</Field>
        <Field label="Room name">{(id) => <input id={id} value={room.name} onChange={(e) => setRoom({ ...room, name: e.target.value })} placeholder="Block B, room 4" required />}</Field>
        <Field label="Seats">{(id) => <input id={id} type="number" min={1} value={room.capacity} onChange={(e) => setRoom({ ...room, capacity: e.target.value })} />}</Field>
        <Button kind="secondary" busy={addRoom.busy} type="submit">
          Add the room
        </Button>
      </form>
      <ErrorNote error={addRoom.error} />
    </details>
  );
}

function nextArm(arm: string): string {
  return /^[A-Y]$/.test(arm) ? String.fromCharCode(arm.charCodeAt(0) + 1) : arm;
}

/** One class: its teacher, room, learners, subjects and who teaches them. */
export function ClassPage() {
  const { id = "" } = useParams();
  const s = useSession();
  const c = useLoad(() => api<ClassRef>(`/v1/classes/${id}`), [id]);
  const members = useLoad(() => (s.can("learner:view") ? api<Items<Member>>(`/v1/classes/${id}/learners`) : Promise.resolve({ items: [] as Member[] })), [id]);
  const teachers = useTeachers(c.data?.institution_id, s.can("staff:view") && s.can("class:manage", "teaching:assign"));
  if (c.loading && !c.data) return <Loading />;
  if (c.error) return <ErrorNote error={c.error} />;
  const k = c.data!;
  return (
    <div className="page">
      <PageHead eyebrow={`${k.session_name} · ${k.year_name}`} title={k.name} />
      <div className="two">
        <section className="panel">
          <Dl
            rows={[
              ["Class teacher", k.class_teacher_name ?? <span className="warn">Not assigned</span>],
              ["Room", k.room ? `${k.room.code} · ${k.room.name} (seats ${k.room.capacity})` : "—"],
              ["Learners", <Fill key="f" size={k.size} capacity={k.capacity} />],
              ["Status", <Status key="s" value={k.status} />],
            ]}
          />
          {s.can("class:manage") && k.status === "active" && <ClassSettings k={k} teachers={teachers.data?.items ?? []} done={c.reload} />}
        </section>
        <section className="panel">
          <h2>Class list</h2>
          <ErrorNote error={members.error} />
          {members.data && members.data.items.length === 0 && <p className="muted">No learners in this class yet.</p>}
          {members.data && members.data.items.length > 0 && (
            <ol className="class-list">
              {members.data.items.map((m) => (
                <li key={m.enrolment_id}>
                  <Link to={`/learners/${m.learner_id}`}>
                    {m.surname}, {m.first_name} {m.other_names ?? ""}
                  </Link>{" "}
                  <span className="muted small">
                    {m.sex} · {date(m.date_of_birth)} · <span className="mono">{m.admission_number ?? m.learner_number}</span>
                  </span>
                </li>
              ))}
            </ol>
          )}
        </section>
      </div>
      <Subjects k={k} teachers={teachers.data?.items ?? []} done={c.reload} />
      {s.can("learner:register") && k.status === "active" && <Promotion k={k} members={members.data?.items ?? []} />}
    </div>
  );
}

function ClassSettings({ k, teachers, done }: { k: ClassRef; teachers: StaffRef[]; done: () => void }) {
  const rooms = useLoad(() => api<Items<Room>>(`/v1/institutions/${k.institution_id}/rooms`), [k.institution_id]);
  const [teacher, setTeacher] = useState(k.class_teacher_staff_id ?? "");
  const [room, setRoom] = useState(k.room?.id ?? "");
  const [capacity, setCapacity] = useState(String(k.capacity));
  const [warnings, setWarnings] = useState<string[]>([]);
  const save = useAction(
    () =>
      api<ClassRef>(`/v1/classes/${k.id}/settings`, {
        json: {
          capacity: Number(capacity) !== k.capacity ? Number(capacity) : null,
          class_teacher_staff_id: teacher && teacher !== k.class_teacher_staff_id ? teacher : null,
          room_id: room && room !== k.room?.id ? room : null,
          clear_room: !room && !!k.room,
        },
      }),
    (r) => {
      setWarnings(r.warnings ?? []);
      done();
    },
  );
  return (
    <form
      className="stack sec"
      onSubmit={(e) => {
        e.preventDefault();
        void save.run();
      }}
    >
      <div className="row wrap">
        <Field label="Class teacher">
          {(id) => (
            <select id={id} value={teacher} onChange={(e) => setTeacher(e.target.value)}>
              <option value="">Not assigned</option>
              {teachers.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.first_name} {t.surname}
                </option>
              ))}
            </select>
          )}
        </Field>
        <Field label="Room">
          {(id) => (
            <select id={id} value={room} onChange={(e) => setRoom(e.target.value)}>
              <option value="">None</option>
              {rooms.data?.items.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.code} (seats {r.capacity})
                </option>
              ))}
            </select>
          )}
        </Field>
        <Field label="Capacity">{(id) => <input id={id} type="number" min={1} max={120} value={capacity} onChange={(e) => setCapacity(e.target.value)} />}</Field>
      </div>
      <ErrorNote error={save.error} />
      {warnings.length > 0 && <p className="note-info">Saved. Note: {warnings.join("; ")}.</p>}
      <Button kind="secondary" busy={save.busy} type="submit">
        Save the class
      </Button>
    </form>
  );
}

function Subjects({ k, teachers, done }: { k: ClassRef; teachers: StaffRef[]; done: () => void }) {
  const s = useSession();
  const [warn, setWarn] = useState<Record<string, string[]>>({});
  const assign = useAction(
    (o: OfferingRef, staffId: string) => api<{ offering: OfferingRef; warnings: string[] }>(`/v1/offerings/${o.id}/teachers`, { json: { staff_id: staffId } }),
    (r) => {
      setWarn((w) => ({ ...w, [r.offering.id]: r.warnings }));
      done();
    },
  );
  const subjects = k.subjects ?? [];
  const canAssign = s.can("teaching:assign") && k.status === "active";
  return (
    <section className="panel">
      <h2>Subjects and teachers</h2>
      <ErrorNote error={assign.error} />
      {subjects.length === 0 && <p className="muted">No subjects yet. The state's curriculum for {k.year_name} has not been adopted, or add subjects below.</p>}
      {subjects.length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Subject</th>
                <th>Kind</th>
                <th>Periods a week</th>
                <th>Teacher</th>
              </tr>
            </thead>
            <tbody>
              {subjects.map((o) => {
                const lead = o.teachers.find((t) => t.role === "lead");
                return (
                  <tr key={o.id}>
                    <td>
                      {o.subject.name} <span className="mono muted small">{o.subject.code}</span>
                    </td>
                    <td>{words(o.kind)}</td>
                    <td className="mono">{o.periods_per_week ?? "—"}</td>
                    <td>
                      {canAssign && teachers.length > 0 ? (
                        <select
                          aria-label={`Teacher for ${o.subject.name}`}
                          value={lead?.staff_id ?? ""}
                          disabled={assign.busy}
                          onChange={(e) => e.target.value && void assign.run(o, e.target.value)}
                        >
                          <option value="">Not assigned</option>
                          {teachers.map((t) => (
                            <option key={t.id} value={t.id}>
                              {t.first_name} {t.surname}
                            </option>
                          ))}
                        </select>
                      ) : (
                        lead?.staff_name ?? <span className="warn">Not assigned</span>
                      )}
                      {o.teachers.filter((t) => t.role === "assistant").map((t) => (
                        <div key={t.id} className="small muted">
                          with {t.staff_name}
                        </div>
                      ))}
                      {warn[o.id]?.length ? <div className="small warn">{warn[o.id]!.join("; ")}</div> : null}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {s.can("class:manage") && k.status === "active" && <AddSubject k={k} done={done} />}
    </section>
  );
}

function AddSubject({ k, done }: { k: ClassRef; done: () => void }) {
  const all = useLoad(() => api<Items<SubjectRef>>(`/v1/institutions/${k.institution_id}/subjects`), [k.institution_id]);
  const [subject, setSubject] = useState("");
  const [periods, setPeriods] = useState("2");
  const [kind, setKind] = useState("core");
  const add = useAction(() => api(`/v1/classes/${k.id}/subjects`, { json: { subject_id: subject, kind, periods_per_week: Number(periods) } }), () => {
    setSubject("");
    done();
  });
  const taught = new Set((k.subjects ?? []).map((o) => o.subject.id));
  return (
    <form
      className="row wrap"
      onSubmit={(e) => {
        e.preventDefault();
        void add.run();
      }}
    >
      <Field label="Add a subject">
        {(id) => (
          <select id={id} value={subject} onChange={(e) => setSubject(e.target.value)} required>
            <option value="">Choose…</option>
            {all.data?.items.filter((x) => !taught.has(x.id) && !x.units).map((x) => (
              <option key={x.id} value={x.id}>
                {x.name} ({x.code})
              </option>
            ))}
          </select>
        )}
      </Field>
      <Field label="Kind">
        {(id) => (
          <select id={id} value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="core">Core</option>
            <option value="elective">Elective</option>
            <option value="optional">Optional</option>
          </select>
        )}
      </Field>
      <Field label="Periods a week">{(id) => <input id={id} type="number" min={0} max={20} value={periods} onChange={(e) => setPeriods(e.target.value)} />}</Field>
      <Button kind="secondary" busy={add.busy} type="submit">
        Add
      </Button>
      <ErrorNote error={add.error} />
    </form>
  );
}

function Promotion({ k, members }: { k: ClassRef; members: Member[] }) {
  const nav = useNavigate();
  const sess = useAcademicSession();
  const later = sess.sessions.filter((x) => x.starts_on > (sess.sessions.find((y) => y.id === k.session_id)?.starts_on ?? ""));
  const [to, setTo] = useState("");
  const [toSession, setToSession] = useState(later[0]?.id ?? "");
  const targets = useClasses(k.institution_id, toSession || later[0]?.id);
  const [repeatClass, setRepeatClass] = useState("");
  const [repeaters, setRepeaters] = useState<string[]>([]);
  const go = useAction(
    () => api<{ promoted: number; repeated: number; to_class: string }>(`/v1/classes/${k.id}/promotion`, { json: { to_class_id: to, repeat_class_id: repeatClass || null, repeaters } }),
    () => nav(`/classes/${to}`),
  );
  if (!later.length || !members.length) return null;
  const opts = targets.data?.items ?? [];
  return (
    <details className="panel">
      <summary>
        <h2 className="inline">End of session: promote this class</h2>
      </summary>
      <div className="form-grid">
        <Field label="Into session">
          {(id) => (
            <select id={id} value={toSession || later[0]!.id} onChange={(e) => setToSession(e.target.value)}>
              {later.map((x) => (
                <option key={x.id} value={x.id}>
                  {x.name}
                </option>
              ))}
            </select>
          )}
        </Field>
        <Field label="Promote into">
          {(id) => (
            <select id={id} value={to} onChange={(e) => setTo(e.target.value)}>
              <option value="">Choose the next year's class…</option>
              {opts.filter((c) => c.year_code !== k.year_code).map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name} ({c.places_left} places)
                </option>
              ))}
            </select>
          )}
        </Field>
        <Field label="Repeaters go into">
          {(id) => (
            <select id={id} value={repeatClass} onChange={(e) => setRepeatClass(e.target.value)}>
              <option value="">Nobody repeats</option>
              {opts.filter((c) => c.year_code === k.year_code).map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          )}
        </Field>
      </div>
      {repeatClass && (
        <fieldset className="seg">
          <legend>Who repeats {k.year_name}</legend>
          {members.map((m) => (
            <label key={m.learner_id}>
              <input
                type="checkbox"
                checked={repeaters.includes(m.learner_id)}
                onChange={(e) => setRepeaters((r) => (e.target.checked ? [...r, m.learner_id] : r.filter((x) => x !== m.learner_id)))}
              />{" "}
              {m.surname}, {m.first_name}
            </label>
          ))}
        </fieldset>
      )}
      <ErrorNote error={go.error} />
      <Button busy={go.busy} disabled={!to} onClick={() => void go.run()}>
        Promote {members.length - repeaters.length} learner{members.length - repeaters.length === 1 ? "" : "s"}
      </Button>
    </details>
  );
}

/** For teachers: the classes they are class teacher of or teach a subject in. */
export function MyClassesPage() {
  const mine = useLoad(() => api<Items<ClassRef>>("/v1/my/classes"), []);
  return (
    <div className="page">
      <PageHead eyebrow="Teaching" title="My classes" />
      <ErrorNote error={mine.error} />
      {mine.loading && <Loading />}
      {mine.data && mine.data.items.length === 0 && (
        <Empty title="No classes yet">Your head teacher assigns class teachers and subject teachers. Your staff record must be linked to your sign-in.</Empty>
      )}
      <div className="panels">
        {mine.data?.items.map((c) => (
          <Link key={c.id} to={`/classes/${c.id}`} className="panel tile">
            <span className="eyebrow">{c.session_name}</span>
            <strong className="tile-title">{c.name}</strong>
            <Fill size={c.size} capacity={c.capacity} />
            <span className="small muted">{c.class_teacher_name ? `Class teacher: ${c.class_teacher_name}` : "No class teacher yet"}</span>
          </Link>
        ))}
      </div>
    </div>
  );
}

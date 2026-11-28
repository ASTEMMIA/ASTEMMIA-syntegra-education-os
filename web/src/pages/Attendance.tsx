/**
 * Attendance (ADR-032): the class register (morning and afternoon), the day across the school,
 * a class's term attendance, and staff attendance (sign in and out, or recorded by the head).
 */
import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { useAcademicSession, useClasses } from "../academic";
import { useLoad, useSession } from "../session";
import { Button, DemoBadge, Empty, ErrorNote, Field, Loading, PageHead, useAction } from "../components/ui";
import { NeedInstitution } from "../App";
import type { ClassRef, Items, Period } from "../types";

type Status = "present" | "late" | "absent" | "excused";
const STATUS: { value: Status; label: string; short: string }[] = [
  { value: "present", label: "Present", short: "P" },
  { value: "late", label: "Late", short: "L" },
  { value: "absent", label: "Absent", short: "A" },
  { value: "excused", label: "Excused absence", short: "E" },
];

interface Mark {
  learner_id: string;
  name: string;
  sex: string;
  status: Status | null;
  reason: string | null;
  demo: boolean;
}

interface Register {
  class_id: string;
  class_name: string;
  on_date: string;
  session: "am" | "pm";
  taken: boolean;
  taken_at: string | null;
  closed_reason: string | null;
  marks: Mark[];
  counts: Record<Status, number>;
}

interface MyDay {
  staff_id: string | null;
  institution_name: string | null;
  status: string | null;
  time_in: string | null;
  time_out: string | null;
  late_after: string;
}

export function localToday(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export function termOn(session: Period | null, on: string): Period | null {
  return session?.parts.find((p) => p.starts_on <= on && on <= p.ends_on) ?? null;
}

const pct = (r: number | null | undefined) => (r === null || r === undefined ? "—" : `${Math.round(r * 1000) / 10}%`);
const hm = (t: string | null) => (t ? t.slice(0, 5) : "—");

/** Staff: sign in when you arrive and out when you leave. */
function MyDayCard() {
  const d = useLoad(() => api<MyDay>("/v1/me/attendance"), []);
  const sign = useAction((action: "in" | "out") => api<MyDay>(`/v1/me/attendance/${action}`, { method: "POST" }), d.reload);
  if (!d.data?.staff_id) return null;
  const x = d.data;
  return (
    <section className="panel row wrap my-day" aria-label="My attendance today">
      <div>
        <strong>My attendance today</strong>
        <div className="small muted">
          {x.institution_name} · in {hm(x.time_in)} · out {hm(x.time_out)}
          {x.status && ` · ${x.status === "late" ? `late (after ${hm(x.late_after)})` : x.status.replace("_", " ")}`}
        </div>
      </div>
      {!x.time_in && (
        <Button busy={sign.busy} onClick={() => void sign.run("in")}>
          Sign in
        </Button>
      )}
      {x.time_in && !x.time_out && (
        <Button kind="secondary" busy={sign.busy} onClick={() => void sign.run("out")}>
          Sign out
        </Button>
      )}
      <ErrorNote error={sign.error} />
    </section>
  );
}

export function AttendancePage() {
  const s = useSession();
  const amend = s.can("attendance:amend");
  const acad = useAcademicSession();
  const all = useClasses(amend ? s.institution?.id : undefined, acad.session?.id);
  const mine = useLoad(() => (amend ? Promise.resolve({ items: [] as ClassRef[] }) : api<Items<ClassRef>>("/v1/my/classes")), [amend]);
  const classes = (amend ? all.data?.items : mine.data?.items) ?? [];
  const [classId, setClassId] = useState("");
  const [on, setOn] = useState(localToday());
  const [sess, setSess] = useState<"am" | "pm">(new Date().getHours() < 12 ? "am" : "pm");
  const [version, setVersion] = useState(0); // bumped on save, so the summaries refresh
  useEffect(() => {
    if (!classId && classes.length) setClassId(classes[0]!.id);
  }, [classes, classId]);
  if (amend && !s.institution) return <NeedInstitution />;
  const cls = classes.find((c) => c.id === classId);
  const term = termOn(acad.session, on);
  return (
    <div className="page">
      <PageHead eyebrow="Attendance" title="Class register" />
      <MyDayCard />
      {classes.length === 0 && !(amend ? all.loading : mine.loading) && (
        <Empty title="No classes">{amend ? "Open classes for this session first." : "You are not a class teacher of any class this session."}</Empty>
      )}
      {classes.length > 0 && (
        <div className="filters">
          <Field label="Class">
            {(id) => (
              <select id={id} value={classId} onChange={(e) => setClassId(e.target.value)}>
                {classes.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
            )}
          </Field>
          <Field label="Date">{(id) => <input id={id} type="date" value={on} max={localToday()} onChange={(e) => setOn(e.target.value)} />}</Field>
          <Field label="Register">
            {(id) => (
              <select id={id} value={sess} onChange={(e) => setSess(e.target.value as "am" | "pm")}>
                <option value="am">Morning</option>
                <option value="pm">Afternoon</option>
              </select>
            )}
          </Field>
        </div>
      )}
      {cls && (
        <RegisterSheet key={`${cls.id}${on}${sess}`} classId={cls.id} on={on} sess={sess} amend={amend} onSaved={() => setVersion((v) => v + 1)} />
      )}
      {cls && term && <TermAttendance classId={cls.id} term={term} version={version} />}
      {amend && s.institution && <SchoolDayPanel institutionId={s.institution.id} on={on} version={version} />}
    </div>
  );
}

function RegisterSheet({ classId, on, sess, amend, onSaved }: { classId: string; on: string; sess: "am" | "pm"; amend: boolean; onSaved: () => void }) {
  const r = useLoad(() => api<Register>(`/v1/classes/${classId}/register`, { query: { on, session: sess } }), [classId, on, sess]);
  const [marks, setMarks] = useState<Record<string, { status: Status; reason: string }>>({});
  const [why, setWhy] = useState("");
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    if (!r.data) return;
    setMarks(Object.fromEntries(r.data.marks.map((m) => [m.learner_id, { status: m.status ?? "present", reason: m.reason ?? "" }])));
  }, [r.data]);
  const past = on < localToday();
  const save = useAction(
    () =>
      api<Register>(`/v1/classes/${classId}/register`, {
        method: "PUT",
        json: {
          on,
          session: sess,
          marks: Object.entries(marks).map(([learner_id, m]) => ({ learner_id, status: m.status, reason: m.reason || null })),
          amend_reason: why || null,
        },
      }),
    () => {
      setSaved(true);
      r.reload();
      onSaved();
    },
  );
  const counts = useMemo(() => {
    const c: Record<Status, number> = { present: 0, late: 0, absent: 0, excused: 0 };
    Object.values(marks).forEach((m) => (c[m.status] += 1));
    return c;
  }, [marks]);
  if (r.loading && !r.data) return <Loading />;
  if (r.error) return <ErrorNote error={r.error} />;
  const x = r.data!;
  if (x.closed_reason) return <p className="note-info">No register on this day: {x.closed_reason}.</p>;
  if (!x.marks.length) return <Empty title="No learners in this class on this day" />;
  return (
    <section className="panel stack" aria-label={`${x.class_name} register`}>
      <div className="row wrap between">
        <h2>
          {x.class_name} · {sess === "am" ? "Morning" : "Afternoon"} · {new Date(`${on}T12:00`).toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" })}
        </h2>
        <span className={x.taken ? "status tone-good" : "status tone-warn"}>{x.taken ? "Taken" : "Not taken yet"}</span>
      </div>
      <p className="small muted" role="status">
        {counts.present} present · {counts.late} late · {counts.absent} absent · {counts.excused} excused, of {x.marks.length}
      </p>
      <ol className="register">
        {x.marks.map((m) => {
          const cur = marks[m.learner_id] ?? { status: "present" as Status, reason: "" };
          return (
            <li key={m.learner_id}>
              <span className="reg-name">
                {m.name} <DemoBadge demo={m.demo} />
              </span>
              <span className="seg-buttons" role="radiogroup" aria-label={`${m.name}: attendance`}>
                {STATUS.map((st) => (
                  <button
                    key={st.value}
                    type="button"
                    role="radio"
                    aria-checked={cur.status === st.value}
                    aria-label={st.label}
                    title={st.label}
                    className={`seg seg-${st.value} ${cur.status === st.value ? "on" : ""}`}
                    onClick={() => {
                      setSaved(false);
                      setMarks({ ...marks, [m.learner_id]: { ...cur, status: st.value } });
                    }}
                  >
                    {st.short}
                  </button>
                ))}
              </span>
              {cur.status === "excused" && (
                <input
                  aria-label={`${m.name}: reason for absence`}
                  placeholder="Reason (sick, bereavement…)"
                  value={cur.reason}
                  maxLength={200}
                  onChange={(e) => setMarks({ ...marks, [m.learner_id]: { ...cur, reason: e.target.value } })}
                />
              )}
            </li>
          );
        })}
      </ol>
      {past && x.taken && amend && (
        <Field label="Why are you correcting this register?" hint="Kept with the correction.">
          {(id) => <input id={id} value={why} onChange={(e) => setWhy(e.target.value)} maxLength={200} />}
        </Field>
      )}
      <ErrorNote error={save.error} />
      {saved && !save.error && (
        <p className="note-info" role="status">
          Register saved.
        </p>
      )}
      <div className="row wrap">
        <Button busy={save.busy} onClick={() => void save.run()}>
          {x.taken ? "Save changes" : "Save register"}
        </Button>
        <Button
          kind="quiet"
          onClick={() => setMarks(Object.fromEntries(Object.keys(marks).map((k) => [k, { status: "present" as Status, reason: "" }])))}
        >
          Mark everyone present
        </Button>
      </div>
    </section>
  );
}

interface TermLine {
  learner_id: string;
  name: string;
  summary: { opened: number; present: number; late: number; absent: number; excused: number; rate: number | null };
  persistent: boolean;
  demo: boolean;
}

function TermAttendance({ classId, term, version }: { classId: string; term: Period; version: number }) {
  const t = useLoad(
    () => api<{ term_name: string; registers: number; rate: number | null; learners: TermLine[] }>(`/v1/classes/${classId}/attendance`, { query: { term_id: term.id } }),
    [classId, term.id, version],
  );
  if (!t.data) return t.error ? <ErrorNote error={t.error} /> : null;
  const d = t.data;
  return (
    <section className="panel stack">
      <h2>
        {d.term_name} so far · {pct(d.rate)} attendance over {d.registers} registers
      </h2>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>Learner</th>
              <th className="num">Opened</th>
              <th className="num">Present</th>
              <th className="num">Late</th>
              <th className="num">Absent</th>
              <th className="num">Excused</th>
              <th className="num">Rate</th>
            </tr>
          </thead>
          <tbody>
            {d.learners.map((l) => (
              <tr key={l.learner_id}>
                <td>
                  {l.name} <DemoBadge demo={l.demo} />
                  {l.persistent && <span className="badge bad-badge">Persistent absence</span>}
                </td>
                <td className="num">{l.summary.opened}</td>
                <td className="num">{l.summary.present}</td>
                <td className="num">{l.summary.late}</td>
                <td className="num">{l.summary.absent}</td>
                <td className="num">{l.summary.excused}</td>
                <td className="num">{pct(l.summary.rate)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function SchoolDayPanel({ institutionId, on, version }: { institutionId: string; on: string; version: number }) {
  const d = useLoad(
    () =>
      api<{ closed_reason: string | null; classes: { class_id: string; class_name: string; am: boolean; pm: boolean; present: number; marked: number }[] }>(
        `/v1/institutions/${institutionId}/attendance/day`,
        { query: { on } },
      ),
    [institutionId, on, version],
  );
  if (!d.data) return null;
  const taken = d.data.classes.filter((c) => c.am).length;
  return (
    <section className="panel stack">
      <h2>
        Registers across the school · {taken} of {d.data.classes.length} morning registers taken
      </h2>
      {d.data.closed_reason && <p className="note-info">No school: {d.data.closed_reason}.</p>}
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>Class</th>
              <th>Morning</th>
              <th>Afternoon</th>
              <th className="num">Present (morning)</th>
            </tr>
          </thead>
          <tbody>
            {d.data.classes.map((c) => (
              <tr key={c.class_id}>
                <td>{c.class_name}</td>
                <td>{c.am ? "✓ Taken" : <span className="warn">Not taken</span>}</td>
                <td>{c.pm ? "✓ Taken" : <span className="muted">—</span>}</td>
                <td className="num">{c.am ? `${c.present} of ${c.marked}` : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

// --------------------------------------------------------------------------- staff

interface StaffLine {
  staff_id: string;
  name: string;
  position: string;
  status: string | null;
  time_in: string | null;
  time_out: string | null;
  note: string | null;
  self_recorded: boolean;
  demo: boolean;
}

const STAFF_STATUS = ["present", "late", "absent", "leave", "sick", "official_duty"];

export function StaffAttendancePage() {
  const s = useSession();
  const [on, setOn] = useState(localToday());
  const inst = s.institution?.id;
  const d = useLoad(() => (inst ? api<Items<StaffLine>>(`/v1/institutions/${inst}/staff-attendance`, { query: { on } }) : Promise.resolve({ items: [] })), [inst, on]);
  const [edits, setEdits] = useState<Record<string, { status: string; note: string }>>({});
  const save = useAction(
    () =>
      api<Items<StaffLine>>(`/v1/institutions/${inst}/staff-attendance`, {
        method: "PUT",
        json: { on, entries: Object.entries(edits).map(([staff_id, e]) => ({ staff_id, status: e.status, note: e.note || null })) },
      }),
    () => {
      setEdits({});
      d.reload();
    },
  );
  if (!inst) return <NeedInstitution />;
  const rows = d.data?.items ?? [];
  const recorded = rows.filter((r) => r.status).length;
  return (
    <div className="page">
      <PageHead eyebrow="Attendance" title="Staff attendance" />
      <div className="filters">
        <Field label="Date">{(id) => <input id={id} type="date" value={on} max={localToday()} onChange={(e) => setOn(e.target.value)} />}</Field>
      </div>
      {d.loading && !d.data && <Loading />}
      <ErrorNote error={d.error} />
      <p className="small muted" role="status">
        {recorded} of {rows.length} staff recorded. Staff who sign in themselves appear here; you can record anyone else.
      </p>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>Staff</th>
              <th>In</th>
              <th>Out</th>
              <th>Status</th>
              <th>Note</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const e = edits[r.staff_id];
              return (
                <tr key={r.staff_id}>
                  <td>
                    {r.name} <DemoBadge demo={r.demo} />
                    <div className="small muted">
                      {r.position}
                      {r.self_recorded && " · signed in themselves"}
                    </div>
                  </td>
                  <td>{hm(r.time_in)}</td>
                  <td>{hm(r.time_out)}</td>
                  <td>
                    <select
                      aria-label={`${r.name}: status`}
                      value={e?.status ?? r.status ?? ""}
                      onChange={(ev) => setEdits({ ...edits, [r.staff_id]: { status: ev.target.value, note: e?.note ?? r.note ?? "" } })}
                    >
                      <option value="" disabled>
                        Not recorded
                      </option>
                      {STAFF_STATUS.map((x) => (
                        <option key={x} value={x}>
                          {x.replace("_", " ")}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td>
                    <input
                      aria-label={`${r.name}: note`}
                      value={e?.note ?? r.note ?? ""}
                      maxLength={200}
                      onChange={(ev) => setEdits({ ...edits, [r.staff_id]: { status: e?.status ?? r.status ?? "present", note: ev.target.value } })}
                    />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <ErrorNote error={save.error} />
      <div>
        <Button busy={save.busy} disabled={!Object.keys(edits).length} onClick={() => void save.run()}>
          Save {Object.keys(edits).length || ""} change{Object.keys(edits).length === 1 ? "" : "s"}
        </Button>
      </div>
    </div>
  );
}

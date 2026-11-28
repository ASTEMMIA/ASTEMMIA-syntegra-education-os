/**
 * Continuous assessment and examinations (ADR-032): the markbook (subject teachers), results
 * approval and report cards (the head), the examination timetable, a printable report card and
 * transcript, and what parents see.
 */
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { api } from "../api";
import { useAcademicSession, useClasses } from "../academic";
import { useLoad, useSession } from "../session";
import { Button, DemoBadge, Empty, ErrorNote, Field, Loading, PageHead, useAction } from "../components/ui";
import { NeedInstitution } from "../App";
import { localToday, termOn } from "./Attendance";
import type { Items, Period } from "../types";

interface Part {
  code: string;
  name: string;
  max_score: number;
}

interface Rules {
  group: string;
  name: string;
  components: Part[];
  bands: { min_score: number; grade: string; remark: string; points: string | null }[];
  pass_mark: number;
}

interface Line {
  learner_id: string;
  name: string;
  demo: boolean;
  scores: Record<string, { score: string | null; absent: boolean }>;
  total: string | null;
  grade: string | null;
  remark: string | null;
  position: number | null;
  complete: boolean;
}

interface Sheet {
  id: string | null;
  offering_id: string;
  subject: string;
  class_name: string | null;
  year_code: string;
  term_id: string;
  term_name: string;
  status: "open" | "submitted" | "approved";
  rules: Rules;
  lines: Line[];
  count: number;
  missing: number;
  average: string | null;
  highest: string | null;
  lowest: string | null;
  passed: number;
  return_note: string | null;
}

const STATUS_TONE: Record<string, string> = { open: "tone-muted", submitted: "tone-warn", approved: "tone-good" };
const STATUS_NAME: Record<string, string> = { open: "Being marked", submitted: "Waiting for approval", approved: "Approved" };

function SheetStatus({ status }: { status: string }) {
  return <span className={`status ${STATUS_TONE[status] ?? ""}`}>{STATUS_NAME[status] ?? status}</span>;
}

// --------------------------------------------------------------------------- the markbook

interface Taught {
  offering_id: string;
  subject: string;
  class_name: string | null;
  year_code: string;
  terms: { id: string; name: string; starts_on: string; ends_on: string }[];
}

export function MarkbookPage() {
  const mine = useLoad(() => api<Items<Taught>>("/v1/my/subjects"), []);
  const items = mine.data?.items ?? [];
  const [pick, setPick] = useState("");
  const [term, setTerm] = useState("");
  useEffect(() => {
    if (!pick && items.length) setPick(items[0]!.offering_id);
  }, [items, pick]);
  const chosen = items.find((i) => i.offering_id === pick);
  useEffect(() => {
    if (!chosen) return;
    const today = localToday();
    const now = chosen.terms.find((t) => t.starts_on <= today && today <= t.ends_on) ?? chosen.terms[0];
    setTerm(now?.id ?? "");
  }, [chosen]);
  return (
    <div className="page">
      <PageHead eyebrow="Results" title="Markbook" />
      {mine.loading && !mine.data && <Loading />}
      {mine.data && !items.length && <Empty title="No subjects">You are not assigned to teach a subject this session.</Empty>}
      {items.length > 0 && (
        <div className="filters">
          <Field label="Subject">
            {(id) => (
              <select id={id} value={pick} onChange={(e) => setPick(e.target.value)}>
                {items.map((i) => (
                  <option key={i.offering_id} value={i.offering_id}>
                    {i.subject} · {i.class_name ?? i.year_code}
                  </option>
                ))}
              </select>
            )}
          </Field>
          {chosen && (
            <Field label="Term">
              {(id) => (
                <select id={id} value={term} onChange={(e) => setTerm(e.target.value)}>
                  {chosen.terms.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.name}
                    </option>
                  ))}
                </select>
              )}
            </Field>
          )}
        </div>
      )}
      {pick && term && <SheetEditor key={pick + term} offeringId={pick} termId={term} mode="teacher" />}
    </div>
  );
}

type Cell = { score: string; absent: boolean };

function SheetEditor({ offeringId, termId, mode, done }: { offeringId: string; termId: string; mode: "teacher" | "head"; done?: () => void }) {
  const sh = useLoad(() => api<Sheet>(`/v1/offerings/${offeringId}/results/${termId}`), [offeringId, termId]);
  const [cells, setCells] = useState<Record<string, Cell>>({});
  const [note, setNote] = useState("");
  useEffect(() => {
    if (!sh.data) return;
    const next: Record<string, Cell> = {};
    for (const l of sh.data.lines)
      for (const p of sh.data.rules.components) {
        const v = l.scores[p.code];
        next[`${l.learner_id}:${p.code}`] = { score: v?.score ?? "", absent: v?.absent ?? false };
      }
    setCells(next);
  }, [sh.data]);
  const dirty = useMemo(() => {
    if (!sh.data) return [];
    return Object.entries(cells).filter(([k, c]) => {
      const [lid, code] = k.split(":");
      const v = sh.data!.lines.find((l) => l.learner_id === lid)?.scores[code!];
      return (v?.score ?? "") !== c.score || (v?.absent ?? false) !== c.absent;
    });
  }, [cells, sh.data]);
  const save = useAction(
    () =>
      api<Sheet>(`/v1/offerings/${offeringId}/results/${termId}/scores`, {
        method: "PUT",
        json: {
          entries: dirty
            .filter(([, c]) => c.absent || c.score !== "")
            .map(([k, c]) => {
              const [learner_id, component] = k.split(":");
              return { learner_id, component, score: c.absent ? null : c.score, absent: c.absent };
            }),
        },
      }),
    sh.reload,
  );
  const step = useAction(
    (action: "submit" | "approve" | "return") =>
      api(`/v1/offerings/${offeringId}/results/${termId}/${action}`, { method: "POST", json: action === "return" ? { note } : undefined }),
    () => {
      sh.reload();
      done?.();
    },
  );
  if (sh.loading && !sh.data) return <Loading />;
  if (sh.error) return <ErrorNote error={sh.error} />;
  const x = sh.data!;
  const editable = mode === "teacher" && x.status === "open";
  const parts = x.rules.components;
  return (
    <section className="panel stack" aria-label={`${x.subject} results`}>
      <div className="row wrap between">
        <h2>
          {x.subject} · {x.class_name ?? x.year_code} · {x.term_name}
        </h2>
        <SheetStatus status={x.status} />
      </div>
      <p className="small muted">
        {x.rules.name}: {parts.map((p) => `${p.name} ${p.max_score}`).join(" + ")} = 100. Pass mark {x.rules.pass_mark}.
      </p>
      {x.return_note && x.status === "open" && <p className="note-error">Sent back: {x.return_note}</p>}
      {!x.lines.length && <Empty title="Nobody takes this subject in this term" />}
      {x.lines.length > 0 && (
        <div className="table-wrap">
          <table className="table markbook">
            <thead>
              <tr>
                <th>Learner</th>
                {parts.map((p) => (
                  <th key={p.code} className="num">
                    {p.name}
                    <div className="small">/ {p.max_score}</div>
                  </th>
                ))}
                <th className="num">Total</th>
                <th>Grade</th>
                <th className="num">Position</th>
              </tr>
            </thead>
            <tbody>
              {x.lines.map((l) => (
                <tr key={l.learner_id} className={l.complete ? "" : "incomplete"}>
                  <td>
                    {l.name} <DemoBadge demo={l.demo} />
                  </td>
                  {parts.map((p) => {
                    const key = `${l.learner_id}:${p.code}`;
                    const c = cells[key] ?? { score: "", absent: false };
                    return (
                      <td key={p.code} className="num">
                        {editable ? (
                          <span className="score-cell">
                            <input
                              aria-label={`${l.name}: ${p.name}`}
                              inputMode="decimal"
                              value={c.absent ? "" : c.score}
                              disabled={c.absent}
                              onChange={(e) => setCells({ ...cells, [key]: { score: e.target.value.trim(), absent: false } })}
                            />
                            <label className="small" title="Absent">
                              <input
                                type="checkbox"
                                checked={c.absent}
                                onChange={(e) => setCells({ ...cells, [key]: { score: "", absent: e.target.checked } })}
                              />{" "}
                              ABS
                            </label>
                          </span>
                        ) : c.absent ? (
                          "ABS"
                        ) : (
                          c.score || "—"
                        )}
                      </td>
                    );
                  })}
                  <td className="num strong">{l.total ?? "—"}</td>
                  <td>
                    {l.grade ?? "—"} {l.remark && <span className="small muted">{l.remark}</span>}
                  </td>
                  <td className="num">{l.position ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="small muted" role="status">
        {x.count - x.missing} of {x.count} complete · average {x.average ?? "—"} · highest {x.highest ?? "—"} · lowest {x.lowest ?? "—"} · {x.passed} passed
      </p>
      <ErrorNote error={save.error ?? step.error} />
      <div className="row wrap">
        {editable && (
          <Button busy={save.busy} disabled={!dirty.length} onClick={() => void save.run()}>
            Save {dirty.length ? `${dirty.length} change${dirty.length === 1 ? "" : "s"}` : "scores"}
          </Button>
        )}
        {editable && (
          <Button kind="secondary" busy={step.busy} disabled={dirty.length > 0 || x.missing > 0 || !x.count} onClick={() => void step.run("submit")}>
            Submit for approval
          </Button>
        )}
        {mode === "head" && x.status === "submitted" && (
          <Button busy={step.busy} onClick={() => void step.run("approve")}>
            Approve
          </Button>
        )}
      </div>
      {mode === "head" && x.status !== "open" && (
        <div className="row wrap">
          <Field label={x.status === "approved" ? "Reopen for correction, because…" : "Send back to the teacher, because…"}>
            {(id) => <input id={id} value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} />}
          </Field>
          <Button kind="secondary" busy={step.busy} disabled={note.trim().length < 5} onClick={() => void step.run("return")}>
            {x.status === "approved" ? "Reopen" : "Send back"}
          </Button>
        </div>
      )}
    </section>
  );
}

// --------------------------------------------------------------------------- the head

interface SheetSummary {
  offering_id: string;
  subject: string;
  class_name: string | null;
  year_code: string;
  status: string;
  entered: boolean;
  teachers: string[];
}

function useTerm() {
  const acad = useAcademicSession();
  const [term, setTerm] = useState("");
  const parts: Period[] = acad.session?.parts ?? [];
  useEffect(() => {
    if (!term && parts.length) setTerm((termOn(acad.session, localToday()) ?? parts[0]!).id);
  }, [parts, term, acad.session]);
  return { acad, parts, term, setTerm };
}

function TermPicker({ parts, term, setTerm }: { parts: Period[]; term: string; setTerm: (t: string) => void }) {
  return (
    <Field label="Term">
      {(id) => (
        <select id={id} value={term} onChange={(e) => setTerm(e.target.value)}>
          {parts.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      )}
    </Field>
  );
}

export function ResultsPage() {
  const s = useSession();
  const { acad, parts, term, setTerm } = useTerm();
  const inst = s.institution?.id;
  const list = useLoad(
    () => (inst && term ? api<Items<SheetSummary>>(`/v1/institutions/${inst}/results`, { query: { term_id: term } }) : Promise.resolve({ items: [] as SheetSummary[] })),
    [inst, term],
  );
  const classes = useClasses(inst, acad.session?.id);
  const [open, setOpen] = useState<string | null>(null);
  const [filter, setFilter] = useState("submitted");
  const [version, setVersion] = useState(0); // bumped when a sheet changes, to refresh the classes
  if (!inst) return <NeedInstitution />;
  const items = (list.data?.items ?? []).filter((i) => filter === "all" || i.status === filter);
  const count = (st: string) => (list.data?.items ?? []).filter((i) => i.status === st).length;
  return (
    <div className="page">
      <PageHead eyebrow="Results" title="Approve results and publish report cards" />
      <div className="filters">
        <TermPicker parts={parts} term={term} setTerm={setTerm} />
        <Field label="Show">
          {(id) => (
            <select id={id} value={filter} onChange={(e) => setFilter(e.target.value)}>
              <option value="submitted">Waiting for approval ({count("submitted")})</option>
              <option value="open">Being marked ({count("open")})</option>
              <option value="approved">Approved ({count("approved")})</option>
              <option value="all">All subjects</option>
            </select>
          )}
        </Field>
      </div>
      <ErrorNote error={list.error} />
      {list.data && !items.length && <Empty title="Nothing here" />}
      {items.length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Class</th>
                <th>Subject</th>
                <th>Teacher</th>
                <th>Status</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {items.map((i) => (
                <tr key={i.offering_id}>
                  <td>{i.class_name ?? i.year_code}</td>
                  <td>{i.subject}</td>
                  <td className="small">{i.teachers.join(", ") || <span className="muted">No teacher assigned</span>}</td>
                  <td>
                    <SheetStatus status={i.status} />
                  </td>
                  <td>
                    <button className="link" onClick={() => setOpen(open === i.offering_id ? null : i.offering_id)}>
                      {open === i.offering_id ? "Close" : "Open"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {open && term && (
        <SheetEditor
          key={open + term}
          offeringId={open}
          termId={term}
          mode="head"
          done={() => {
            list.reload();
            setVersion((v) => v + 1);
          }}
        />
      )}
      <section className="panel stack">
        <h2>Report cards</h2>
        <p className="small muted">A class's report cards are published when every subject it takes is approved.</p>
        <ul className="plain">
          {(classes.data?.items ?? []).map((c) => (
            <li key={c.id}>
              <ClassPublish classId={c.id} name={c.name} termId={term} version={version} />
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

interface ClassResults {
  ready: boolean;
  waiting: { subject: string; status: string }[];
  published: number;
  cards: unknown[];
}

function ClassPublish({ classId, name, termId, version }: { classId: string; name: string; termId: string; version: number }) {
  const r = useLoad(
    () => (termId ? api<ClassResults>(`/v1/classes/${classId}/results`, { query: { term_id: termId } }) : Promise.resolve(null)),
    [classId, termId, version],
  );
  const pub = useAction(() => api(`/v1/classes/${classId}/results/${termId}/publish`, { method: "POST" }), r.reload);
  if (!r.data) return <span className="muted">{name}…</span>;
  const d = r.data;
  return (
    <div className="row wrap between">
      <span>
        <strong>{name}</strong>{" "}
        <span className="small muted">
          {d.published ? `${d.published} published` : "not published"}
          {d.waiting.length > 0 && ` · ${d.waiting.length} subject(s) not approved yet`}
          {!d.cards.length && !d.waiting.length && " · no results yet"}
        </span>
      </span>
      <span className="row">
        <ErrorNote error={pub.error} />
        <Button kind={d.published ? "secondary" : "primary"} busy={pub.busy} disabled={!d.ready} onClick={() => void pub.run()}>
          {d.published ? "Publish changes" : d.ready ? `Publish ${d.cards.length} report cards` : "Publish report cards"}
        </Button>
      </span>
    </div>
  );
}

// --------------------------------------------------------------------------- timetable

interface Paper {
  id: string;
  offering_id: string;
  subject: string;
  class_name: string | null;
  year_code: string;
  starts_at: string;
  ends_at: string;
  room: string | null;
  invigilator: string | null;
}

export function ExamTimetablePage() {
  const s = useSession();
  const { parts, term, setTerm } = useTerm();
  const inst = s.institution?.id;
  const papers = useLoad(
    () => (inst && term ? api<Items<Paper>>(`/v1/institutions/${inst}/exam-timetable`, { query: { term_id: term } }) : Promise.resolve({ items: [] as Paper[] })),
    [inst, term],
  );
  const subjects = useLoad(
    () => (inst && term && s.can("exam:schedule") ? api<Items<SheetSummary>>(`/v1/institutions/${inst}/results`, { query: { term_id: term } }) : Promise.resolve({ items: [] as SheetSummary[] })),
    [inst, term],
  );
  const [form, setForm] = useState({ offering_id: "", date: "", time: "09:00", duration: 60 });
  const add = useAction(
    () =>
      api("/v1/exam-papers", {
        json: { offering_id: form.offering_id, term_id: term, starts_at: `${form.date}T${form.time}:00`, duration_minutes: Number(form.duration) },
      }),
    papers.reload,
  );
  const cancel = useAction((id: string) => api(`/v1/exam-papers/${id}`, { method: "DELETE" }), papers.reload);
  if (!inst) return <NeedInstitution />;
  const byDay = new Map<string, Paper[]>();
  for (const p of papers.data?.items ?? []) byDay.set(p.starts_at.slice(0, 10), [...(byDay.get(p.starts_at.slice(0, 10)) ?? []), p]);
  return (
    <div className="page">
      <PageHead eyebrow="Results" title="Examination timetable" />
      <div className="filters">
        <TermPicker parts={parts} term={term} setTerm={setTerm} />
      </div>
      {s.can("exam:schedule") && (
        <form
          className="panel form-grid"
          onSubmit={(e) => {
            e.preventDefault();
            void add.run();
          }}
        >
          <Field label="Subject">
            {(id) => (
              <select id={id} value={form.offering_id} onChange={(e) => setForm({ ...form, offering_id: e.target.value })} required>
                <option value="">Choose…</option>
                {(subjects.data?.items ?? []).map((o) => (
                  <option key={o.offering_id} value={o.offering_id}>
                    {o.class_name ?? o.year_code} · {o.subject}
                  </option>
                ))}
              </select>
            )}
          </Field>
          <Field label="Date">{(id) => <input id={id} type="date" value={form.date} onChange={(e) => setForm({ ...form, date: e.target.value })} required />}</Field>
          <Field label="Starts">{(id) => <input id={id} type="time" value={form.time} onChange={(e) => setForm({ ...form, time: e.target.value })} required />}</Field>
          <Field label="Minutes">
            {(id) => <input id={id} type="number" min={10} max={360} value={form.duration} onChange={(e) => setForm({ ...form, duration: Number(e.target.value) })} required />}
          </Field>
          <div className="form-actions">
            <ErrorNote error={add.error} />
            <Button busy={add.busy} type="submit">
              Add to the timetable
            </Button>
          </div>
        </form>
      )}
      <ErrorNote error={papers.error ?? cancel.error} />
      {papers.data && !papers.data.items.length && <Empty title="No papers yet" />}
      {[...byDay.entries()].map(([day, list]) => (
        <section key={day} className="panel stack">
          <h2>{new Date(`${day}T12:00`).toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long", year: "numeric" })}</h2>
          <ul className="plain">
            {list.map((p) => (
              <li key={p.id} className="row wrap between">
                <span>
                  <strong>
                    {p.starts_at.slice(11, 16)}–{p.ends_at.slice(11, 16)}
                  </strong>{" "}
                  {p.subject} · {p.class_name ?? p.year_code}
                  {p.room && <span className="small muted"> · {p.room}</span>}
                  {p.invigilator && <span className="small muted"> · {p.invigilator}</span>}
                </span>
                {s.can("exam:schedule") && (
                  <button className="link bad" onClick={() => void cancel.run(p.id)}>
                    Remove
                  </button>
                )}
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

// --------------------------------------------------------------------------- report card

interface CardContent {
  learner: { name: string; learner_number: string; admission_number: string | null; sex: string; date_of_birth: string | null; demo: boolean };
  school: { name: string; code: string; lga: string };
  class: { name: string };
  session: string;
  term: { name: string };
  subjects: {
    subject: string;
    parts: Record<string, string | null>;
    total: string;
    grade: string;
    remark: string;
    position: string;
    class_average: string;
    highest: string;
    lowest: string;
  }[];
  summary: { subjects: number; total: string; average: string; position: string; out_of: number; class_average: string | null };
  attendance: { opened: number; present: number; absent: number; rate: number | null };
  comments: { class_teacher: string | null; head: string | null };
  rules: Rules;
  next_term_begins: string | null;
}

export function ReportCardPage() {
  const { id = "" } = useParams();
  const s = useSession();
  const c = useLoad(() => api<{ version: number; published_at: string; content: CardContent; content_sha256: string }>(`/v1/report-cards/${id}`), [id]);
  if (c.loading && !c.data) return <Loading />;
  if (c.error) return <ErrorNote error={c.error} />;
  const { content: x, version, published_at, content_sha256 } = c.data!;
  const parts = x.rules.components ?? [];
  return (
    <article className="report-card">
      <div className="no-print row wrap">
        <Button kind="secondary" onClick={() => window.print()}>
          Print
        </Button>
      </div>
      {x.learner.demo && (
        <p className="demo-banner" role="note">
          <strong>Demonstration data.</strong> This learner is made up for the demonstration.
        </p>
      )}
      <header className="rc-head">
        <div>
          <span className="eyebrow">{s.me?.tenant_code === "NG-AB" ? "Abia State" : ""}</span>
          <h1>{x.school.name}</h1>
          <div className="small muted">
            {x.school.code} · {x.school.lga}
          </div>
        </div>
        <div className="rc-title">
          <strong>Report card</strong>
          <div>
            {x.term.name}, {x.session}
          </div>
        </div>
      </header>
      <dl className="rc-facts">
        <div>
          <dt>Name</dt>
          <dd>{x.learner.name}</dd>
        </div>
        <div>
          <dt>Class</dt>
          <dd>{x.class.name}</dd>
        </div>
        <div>
          <dt>Learner number</dt>
          <dd className="mono">{x.learner.learner_number}</dd>
        </div>
        <div>
          <dt>Admission no.</dt>
          <dd className="mono">{x.learner.admission_number ?? "—"}</dd>
        </div>
      </dl>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>Subject</th>
              {parts.map((p) => (
                <th key={p.code} className="num">
                  {p.name} ({p.max_score})
                </th>
              ))}
              <th className="num">Total</th>
              <th>Grade</th>
              <th className="num">Position</th>
              <th className="num">Class avg</th>
              <th className="num">High / low</th>
            </tr>
          </thead>
          <tbody>
            {x.subjects.map((r) => (
              <tr key={r.subject}>
                <td>{r.subject}</td>
                {parts.map((p) => (
                  <td key={p.code} className="num">
                    {r.parts[p.code] ?? "—"}
                  </td>
                ))}
                <td className="num strong">{r.total}</td>
                <td>
                  {r.grade} <span className="small muted">{r.remark}</span>
                </td>
                <td className="num">{r.position}</td>
                <td className="num">{r.class_average}</td>
                <td className="num">
                  {r.highest} / {r.lowest}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <dl className="rc-facts">
        <div>
          <dt>Total</dt>
          <dd>{x.summary.total}</dd>
        </div>
        <div>
          <dt>Average</dt>
          <dd>{x.summary.average}</dd>
        </div>
        <div>
          <dt>Position</dt>
          <dd>
            {x.summary.position} of {x.summary.out_of}
          </dd>
        </div>
        <div>
          <dt>Attendance</dt>
          <dd>
            {x.attendance.present} of {x.attendance.opened} times the school opened
          </dd>
        </div>
      </dl>
      <div className="rc-comments">
        <p>
          <strong>Class teacher:</strong> {x.comments.class_teacher ?? "—"}
        </p>
        <p>
          <strong>Head teacher:</strong> {x.comments.head ?? "—"}
        </p>
        {x.next_term_begins && (
          <p>
            <strong>Next term begins:</strong> {new Date(`${x.next_term_begins}T12:00`).toLocaleDateString(undefined, { day: "numeric", month: "long", year: "numeric" })}
          </p>
        )}
      </div>
      <p className="small muted">
        Grades: {x.rules.bands.map((b) => `${b.grade} ${b.min_score}+`).join(", ")}. Published {new Date(published_at + "Z").toLocaleDateString()}
        {version > 1 && ` (correction, version ${version})`} · checksum <span className="mono">{content_sha256.slice(0, 12)}</span>
      </p>
    </article>
  );
}

// --------------------------------------------------------------------------- learner 360 and transcripts

interface CardItem {
  id: string;
  version: number;
  published_at: string;
  content: { session: string; term: { name: string }; summary: { average: string; position: string; out_of: number } };
}

/** On Learner 360: attendance this term, report cards and transcripts. */
export function LearnerResults({ learnerId, sessionId }: { learnerId: string; sessionId: string | null }) {
  const s = useSession();
  const nav = useNavigate();
  const acad = useAcademicSession();
  const sess = acad.sessions.find((x) => x.id === sessionId) ?? null;
  const term = termOn(sess, localToday());
  const att = useLoad(
    () => (term ? api<{ opened: number; present: number; absent: number; rate: number | null }>(`/v1/learners/${learnerId}/attendance`, { query: { term_id: term.id } }) : Promise.resolve(null)),
    [learnerId, term?.id],
  );
  const cards = useLoad(() => api<Items<CardItem>>(`/v1/learners/${learnerId}/report-cards`), [learnerId]);
  const issued = useLoad(() => api<Items<{ id: string; reference: string; purpose: string; issued_on: string }>>(`/v1/learners/${learnerId}/transcripts`), [learnerId]);
  const [purpose, setPurpose] = useState("");
  const issue = useAction(
    () => api<{ id: string }>(`/v1/learners/${learnerId}/transcripts`, { json: { purpose } }),
    (t) => nav(`/transcripts/${t.id}`),
  );
  return (
    <section className="panel stack">
      <h2>Attendance and results</h2>
      {term && att.data && (
        <p>
          {term.name}: present {att.data.present} of {att.data.opened} times the school opened
          {att.data.rate !== null && ` (${Math.round(att.data.rate * 100)}%)`}.
        </p>
      )}
      {term && (s.can("results:approve") || s.me!.grants.some((g) => g.role === "TCH")) && <Remarks learnerId={learnerId} termId={term.id} head={s.can("results:approve")} />}
      {cards.data && !cards.data.items.length && <p className="muted">No report cards published yet.</p>}
      <ul className="plain">
        {(cards.data?.items ?? []).map((c) => (
          <li key={c.id}>
            <Link to={`/report-cards/${c.id}`}>
              {c.content.term.name}, {c.content.session}
            </Link>{" "}
            <span className="small muted">
              average {c.content.summary.average} · {c.content.summary.position} of {c.content.summary.out_of}
              {c.version > 1 && ` · version ${c.version}`}
            </span>
          </li>
        ))}
      </ul>
      {(issued.data?.items ?? []).length > 0 && (
        <ul className="plain small">
          {issued.data!.items.map((t) => (
            <li key={t.id}>
              Transcript <Link to={`/transcripts/${t.id}`}>{t.reference}</Link> · {t.purpose} · {t.issued_on}
            </li>
          ))}
        </ul>
      )}
      {s.can("transcript:issue") && (
        <form
          className="row wrap"
          onSubmit={(e) => {
            e.preventDefault();
            void issue.run();
          }}
        >
          <Field label="Issue a transcript, for…" hint="Admission, transfer, scholarship…">
            {(id) => <input id={id} value={purpose} minLength={3} maxLength={200} onChange={(e) => setPurpose(e.target.value)} required />}
          </Field>
          <Button busy={issue.busy} type="submit">
            Issue transcript
          </Button>
          <ErrorNote error={issue.error} />
        </form>
      )}
    </section>
  );
}

/** The class teacher's and head's remarks for the term's report card (published with it). */
function Remarks({ learnerId, termId, head }: { learnerId: string; termId: string; head: boolean }) {
  const [ct, setCt] = useState("");
  const [hd, setHd] = useState("");
  const [saved, setSaved] = useState(false);
  const save = useAction(
    () => api(`/v1/learners/${learnerId}/comments/${termId}`, { method: "PUT", json: { class_teacher: ct || null, ...(head ? { head: hd || null } : {}) } }),
    () => setSaved(true),
  );
  return (
    <form
      className="stack panel-soft"
      onSubmit={(e) => {
        e.preventDefault();
        void save.run();
      }}
    >
      <Field label="Class teacher's remark for this term">
        {(id) => <textarea id={id} rows={2} maxLength={500} value={ct} onChange={(e) => (setCt(e.target.value), setSaved(false))} />}
      </Field>
      {head && (
        <Field label="Head teacher's remark">{(id) => <textarea id={id} rows={2} maxLength={500} value={hd} onChange={(e) => (setHd(e.target.value), setSaved(false))} />}</Field>
      )}
      <ErrorNote error={save.error} />
      {saved && <p className="note-info" role="status">Remarks saved. They appear on the report card when it is next published.</p>}
      <div>
        <Button kind="secondary" busy={save.busy} type="submit">
          Save remarks
        </Button>
      </div>
    </form>
  );
}

interface TranscriptContent {
  learner: { name: string; learner_number: string; date_of_birth: string | null; demo: boolean };
  school_terms: { session: string; term: string; school: string; class: string; subjects: { subject: string; total: string; grade: string }[]; average: string; position: string }[];
  semesters: { session: string; semester: string; courses: { code: string; title: string; units: number; total: string; grade: string; points: string | null }[]; units: number; gpa: string | null; cgpa: string | null }[];
  cgpa: string | null;
  standing: string | null;
}

export function TranscriptPage() {
  const { id = "" } = useParams();
  const t = useLoad(() => api<{ reference: string; purpose: string; issued_on: string; content: TranscriptContent; content_sha256: string }>(`/v1/transcripts/${id}`), [id]);
  if (t.loading && !t.data) return <Loading />;
  if (t.error) return <ErrorNote error={t.error} />;
  const { reference, purpose, issued_on, content: x, content_sha256 } = t.data!;
  return (
    <article className="report-card">
      <div className="no-print">
        <Button kind="secondary" onClick={() => window.print()}>
          Print
        </Button>
      </div>
      {x.learner.demo && (
        <p className="demo-banner" role="note">
          <strong>Demonstration data.</strong> This learner is made up for the demonstration.
        </p>
      )}
      <header className="rc-head">
        <div>
          <h1>Transcript</h1>
          <div>
            {x.learner.name} · <span className="mono">{x.learner.learner_number}</span>
          </div>
        </div>
        <div className="rc-title">
          <strong className="mono">{reference}</strong>
          <div className="small">
            Issued {issued_on} · {purpose}
          </div>
        </div>
      </header>
      {x.school_terms.map((st, i) => (
        <section key={i} className="stack">
          <h2>
            {st.term}, {st.session} · {st.class}, {st.school}
          </h2>
          <p className="small">
            {st.subjects.map((s) => `${s.subject} ${s.total} (${s.grade})`).join(" · ")}
          </p>
          <p className="small muted">
            Average {st.average} · position {st.position}
          </p>
        </section>
      ))}
      {x.semesters.map((se, i) => (
        <section key={`s${i}`} className="stack">
          <h2>
            {se.semester}, {se.session}
          </h2>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Code</th>
                  <th>Course</th>
                  <th className="num">Units</th>
                  <th className="num">Score</th>
                  <th>Grade</th>
                  <th className="num">Points</th>
                </tr>
              </thead>
              <tbody>
                {se.courses.map((c) => (
                  <tr key={c.code}>
                    <td className="mono">{c.code}</td>
                    <td>{c.title}</td>
                    <td className="num">{c.units}</td>
                    <td className="num">{c.total}</td>
                    <td>{c.grade}</td>
                    <td className="num">{c.points}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="small">
            Units {se.units} · GPA {se.gpa ?? "—"} · CGPA {se.cgpa ?? "—"}
          </p>
        </section>
      ))}
      {x.cgpa && (
        <p>
          <strong>Cumulative GPA {x.cgpa}</strong>
          {x.standing && ` · ${x.standing}`}
        </p>
      )}
      <p className="small muted">
        Checksum <span className="mono">{content_sha256}</span>. The issuing office keeps this transcript exactly as issued.
      </p>
    </article>
  );
}

// --------------------------------------------------------------------------- parents

interface Child {
  learner_id: string;
  name: string;
  demo: boolean;
  school: string | null;
  class_name: string | null;
  term: string | null;
  attendance: { opened: number; present: number; absent: number; rate: number | null } | null;
  report_cards: { id: string; session: string; term: string; published_at: string }[];
}

export function MyChildrenPage() {
  const d = useLoad(() => api<Items<Child>>("/v1/my/children"), []);
  if (d.loading && !d.data) return <Loading />;
  if (d.error) return <ErrorNote error={d.error} />;
  return (
    <div className="page">
      <PageHead title="My children" />
      {!d.data!.items.length && <Empty title="No children linked to your account" />}
      <div className="panels">
        {d.data!.items.map((c) => (
          <section key={c.learner_id} className="panel stack">
            <h2>
              {c.name} <DemoBadge demo={c.demo} />
            </h2>
            <p className="small muted">
              {c.class_name} · {c.school}
            </p>
            {c.attendance && (
              <p>
                {c.term}: present {c.attendance.present} of {c.attendance.opened} times
                {c.attendance.rate !== null && ` (${Math.round(c.attendance.rate * 100)}%)`}; absent {c.attendance.absent}.
              </p>
            )}
            <h3 className="small">Report cards</h3>
            {!c.report_cards.length && <p className="small muted">None published yet.</p>}
            <ul className="plain">
              {c.report_cards.map((r) => (
                <li key={r.id}>
                  <Link to={`/report-cards/${r.id}`}>
                    {r.term}, {r.session}
                  </Link>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </div>
  );
}

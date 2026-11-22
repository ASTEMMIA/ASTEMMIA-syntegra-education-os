import { useState } from "react";
import { Link, useParams } from "react-router";
import { ApiError, api, download, saveBlob } from "../api";
import { useAcademicSession, useClasses, useYears } from "../academic";
import { useLoad, useSession } from "../session";
import { Button, DemoBadge, Dl, Empty, ErrorNote, Field, Loading, PageHead, Status, useAction } from "../components/ui";
import { NeedInstitution } from "../App";
import { SessionPicker } from "./Classes";
import type { EnrolmentRef, Items, LearnerRef } from "../types";
import { date, words } from "../format";

const RELATIONSHIPS = ["mother", "father", "guardian", "grandparent", "sibling", "aunt_uncle", "foster_carer", "sponsor", "other"];

export function LearnersPage() {
  const s = useSession();
  const inst = s.institution;
  const [q, setQ] = useState("");
  const [asked, setAsked] = useState("");
  const found = useLoad(
    () => (asked.length >= 2 ? api<Items<EnrolmentRef>>("/v1/learners", { query: { q: asked, institution_id: inst?.id } }) : Promise.resolve(null)),
    [asked, inst?.id],
  );
  return (
    <div className="page">
      <PageHead eyebrow={inst?.name ?? "All institutions in scope"} title="Learners">
        {s.can("learner:register") && inst && (
          <Link className="btn btn-secondary" to="/class-lists">
            Import a class list
          </Link>
        )}
      </PageHead>
      {s.cell("learner:view") !== "C" && (
        <form
          className="search"
          role="search"
          onSubmit={(e) => {
            e.preventDefault();
            setAsked(q.trim());
          }}
        >
          <label className="sr" htmlFor="lq">
            Find a learner
          </label>
          <input id="lq" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Name, LRN or admission number" />
          <button className="btn btn-primary" type="submit">
            Find
          </button>
        </form>
      )}
      <ErrorNote error={found.error} />
      {found.data && found.data.items.length === 0 && <Empty title={`No enrolled learner matches “${asked}”`} />}
      {found.data && found.data.items.length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Learner</th>
                <th>Class</th>
                <th>School</th>
              </tr>
            </thead>
            <tbody>
              {found.data.items.map((e) => (
                <tr key={e.id}>
                  <td>
                    <Link to={`/learners/${e.learner_id}`} className="strong">
                      {e.learner_name}
                    </Link>
                    <DemoBadge demo={e.demo} />
                    <div className="mono muted small">
                      {e.learner_number}
                      {e.admission_number ? ` · ${e.admission_number}` : ""}
                    </div>
                  </td>
                  <td>{e.class_name ?? `${e.year_name} (no class yet)`}</td>
                  <td>{e.institution_name}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {s.can("learner:register") && (inst ? <Register institutionId={inst.id} /> : <NeedInstitution />)}
    </div>
  );
}

function Register({ institutionId }: { institutionId: string }) {
  const s = useSession();
  const sess = useAcademicSession();
  const years = useYears();
  const classes = useClasses(institutionId, sess.session?.id);
  const levels = new Set(s.institution?.level_codes ?? []);
  const blank = {
    given_name: "", family_name: "", other_names: "", date_of_birth: "", sex: "F", guardian_name: "", guardian_phone: "",
    guardian_relationship: "mother", year_code: "", class_id: "", admission_number: "", age_override: "", override_reason: "",
  };
  const [f, setF] = useState(blank);
  const [sameAs, setSameAs] = useState<string[]>([]);
  const [done, setDone] = useState<EnrolmentRef | null>(null);
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value });
  const go = useAction(
    () =>
      api<EnrolmentRef>(`/v1/institutions/${institutionId}/learners`, {
        json: {
          pupil: {
            given_name: f.given_name, family_name: f.family_name, other_names: f.other_names || null, date_of_birth: f.date_of_birth,
            sex: f.sex, guardian_name: f.guardian_name, guardian_phone: f.guardian_phone, guardian_relationship: f.guardian_relationship,
            not_duplicate_of: sameAs, override_reason: f.override_reason || null,
          },
          session_id: sess.session?.id, year_code: f.year_code, class_id: f.class_id || null,
          admission_number: f.admission_number || null, age_override: f.age_override || null,
        },
      }),
    (e) => {
      setDone(e);
      setF(blank);
      setSameAs([]);
    },
  );
  const err = go.error instanceof ApiError ? go.error : null;
  type Candidate = { person_id: string; display_name: string; date_of_birth: string | null; visible: boolean };
  const dupes = err?.status === 409 ? ((err.extra.candidates as Candidate[] | undefined) ?? []).filter((c) => c.visible) : [];
  const ageAsked = err?.status === 422 && /usually/.test(err.detail);
  return (
    <details className="panel" open={!!go.error}>
      <summary>
        <h2 className="inline">Enrol a pupil directly</h2>
      </summary>
      <p className="muted small">For a pupil already in a class, without an application. New pupils normally come through Admissions.</p>
      <div className="filters">
        <SessionPicker s={sess} />
      </div>
      <form
        className="form-grid"
        onSubmit={(e) => {
          e.preventDefault();
          setDone(null);
          void go.run();
        }}
      >
        <Field label="First name">{(id) => <input id={id} value={f.given_name} onChange={set("given_name")} required />}</Field>
        <Field label="Surname">{(id) => <input id={id} value={f.family_name} onChange={set("family_name")} required />}</Field>
        <Field label="Other names">{(id) => <input id={id} value={f.other_names} onChange={set("other_names")} />}</Field>
        <Field label="Date of birth">{(id) => <input id={id} type="date" value={f.date_of_birth} onChange={set("date_of_birth")} required />}</Field>
        <Field label="Sex">
          {(id) => (
            <select id={id} value={f.sex} onChange={set("sex")}>
              <option value="F">Female</option>
              <option value="M">Male</option>
            </select>
          )}
        </Field>
        <Field label="Year">
          {(id) => (
            <select id={id} value={f.year_code} onChange={set("year_code")} required>
              <option value="">Choose…</option>
              {(years.data?.items ?? []).filter((y) => levels.has(y.level)).map((y) => (
                <option key={y.code} value={y.code}>
                  {y.name}
                </option>
              ))}
            </select>
          )}
        </Field>
        <Field label="Class">
          {(id) => (
            <select id={id} value={f.class_id} onChange={set("class_id")}>
              <option value="">Place later</option>
              {(classes.data?.items ?? []).filter((c) => c.year_code === f.year_code).map((c) => (
                <option key={c.id} value={c.id} disabled={c.places_left === 0}>
                  {c.name} ({c.places_left} places)
                </option>
              ))}
            </select>
          )}
        </Field>
        <Field label="Admission number">{(id) => <input id={id} value={f.admission_number} onChange={set("admission_number")} />}</Field>
        <Field label="Parent or guardian">{(id) => <input id={id} value={f.guardian_name} onChange={set("guardian_name")} required />}</Field>
        <Field label="Their phone">{(id) => <input id={id} value={f.guardian_phone} onChange={set("guardian_phone")} required inputMode="tel" />}</Field>
        <Field label="Relationship">
          {(id) => (
            <select id={id} value={f.guardian_relationship} onChange={set("guardian_relationship")}>
              {RELATIONSHIPS.map((r) => (
                <option key={r} value={r}>
                  {words(r)}
                </option>
              ))}
            </select>
          )}
        </Field>
        {ageAsked && <Field label="Reason for the age">{(id) => <input id={id} value={f.age_override} onChange={set("age_override")} required placeholder="Late start; repeating" />}</Field>}
        <div className="form-actions">
          {dupes.length > 0 && (
            <div className="note-info">
              <strong>This pupil may already be registered:</strong>
              <ul className="plain">
                {dupes.map((d) => (
                  <li key={d.person_id}>
                    {d.display_name}, born {date(d.date_of_birth)}{" "}
                    <label>
                      <input type="checkbox" checked={sameAs.includes(d.person_id)} onChange={(e) => setSameAs((x) => (e.target.checked ? [...x, d.person_id] : x.filter((y) => y !== d.person_id)))} /> a different child
                    </label>
                  </li>
                ))}
              </ul>
              {sameAs.length > 0 && <Field label="Why they are different">{(id) => <input id={id} value={f.override_reason} onChange={set("override_reason")} required />}</Field>}
            </div>
          )}
          <ErrorNote error={go.error} />
          {done && (
            <p className="good">
              Enrolled <Link to={`/learners/${done.learner_id}`}>{done.learner_name}</Link> ({done.learner_number}){done.class_name ? ` in ${done.class_name}` : ""}.
            </p>
          )}
          <Button busy={go.busy} type="submit" disabled={!sess.session}>
            Enrol
          </Button>
        </div>
      </form>
    </details>
  );
}

/** Give a parent or guardian an account: messaging with the school, by an emailed code. */
function GiveAccount({ learnerId, personId, name, done }: { learnerId: string; personId: string; name: string; done: () => void }) {
  const [open, setOpen] = useState(false);
  const [email, setEmail] = useState("");
  const give = useAction(
    async () => api<{ login: string; created: boolean }>(`/v1/learners/${learnerId}/guardians/${personId}/account`, { json: { email } }),
    done,
  );
  if (!open)
    return (
      <button className="link small" onClick={() => setOpen(true)}>
        Give {name} a parent account
      </button>
    );
  return (
    <form
      className="row wrap"
      onSubmit={(e) => {
        e.preventDefault();
        void give.run();
      }}
    >
      <Field label={`${name}'s email address`} hint="They sign in with a one-time code sent here, and can message the school.">
        {(id) => <input id={id} type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoComplete="off" />}
      </Field>
      <Button busy={give.busy} type="submit">
        Give account
      </Button>
      <ErrorNote error={give.error} />
    </form>
  );
}

/** Learner 360: identity, guardians, enrolment history, and what the school can do next. */
export function LearnerPage() {
  const { id = "" } = useParams();
  const s = useSession();
  const l = useLoad(() => api<LearnerRef>(`/v1/learners/${id}`), [id]);
  if (l.loading && !l.data) return <Loading />;
  if (l.error) return <ErrorNote error={l.error} />;
  const x = l.data!;
  const cur = x.current;
  return (
    <div className="page">
      <PageHead eyebrow={x.learner_number} title={`${x.first_name} ${x.other_names ? `${x.other_names} ` : ""}${x.surname}`} />
      {x.demo && (
        <p className="demo-banner" role="note">
          <strong>Demonstration data.</strong> This learner and their guardian are made up for the demonstration; they are not real people.
        </p>
      )}
      <div className="two">
        <section className="panel">
          <Dl
            rows={[
              ["Born", `${date(x.date_of_birth)} · ${x.sex === "F" ? "Female" : x.sex === "M" ? "Male" : x.sex}`],
              ["Now", cur ? `${cur.class_name ?? cur.year_name}, ${cur.institution_name} (${cur.session_name})` : words(x.status)],
              ["Admission no.", cur?.admission_number ?? "—"],
              ["First enrolled", date(x.first_enrolled_on)],
            ]}
          />
          {cur?.age_override && <p className="small muted">Age noted at enrolment: {cur.age_override}</p>}
        </section>
        <section className="panel">
          <h2>Parents and guardians</h2>
          <ul className="plain">
            {x.guardians.map((g) => (
              <li key={g.person_id}>
                <strong>{g.name}</strong> · {words(g.relationship)} {g.parental_responsibility && <span className="badge">Parental responsibility</span>}
                <div className="small">{g.court_order_flag ? "Contact details withheld (court order)" : g.phone ?? "No phone"}</div>
                {g.has_account ? (
                  <div className="small good">Has a parent account (messaging)</div>
                ) : (
                  s.can("learner:register") &&
                  g.parental_responsibility &&
                  !g.court_order_flag &&
                  cur && <GiveAccount learnerId={x.id} personId={g.person_id} name={g.name} done={l.reload} />
                )}
              </li>
            ))}
          </ul>
        </section>
      </div>
      <section className="panel">
        <h2>Enrolments</h2>
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Session</th>
                <th>Class</th>
                <th>School</th>
                <th>How</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {x.history.map((e) => (
                <tr key={e.id}>
                  <td>{e.session_name}</td>
                  <td>{e.class_name ?? e.year_name}</td>
                  <td>{e.institution_name}</td>
                  <td>{words(e.source)}</td>
                  <td>
                    <Status value={e.status === "active" ? "open" : e.status} />
                    {e.exit_reason && <div className="small muted">{e.exit_reason}</div>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      {cur && s.can("learner:register") && (
        <div className="panels">
          <MoveClass e={cur} done={l.reload} />
          <TransferOut e={cur} done={l.reload} />
          <Leave e={cur} done={l.reload} />
        </div>
      )}
    </div>
  );
}

function MoveClass({ e, done }: { e: EnrolmentRef; done: () => void }) {
  const classes = useClasses(e.institution_id, e.session_id);
  const [cls, setCls] = useState("");
  const go = useAction(() => api(`/v1/enrolments/${e.id}/class`, { json: { class_id: cls } }), done);
  return (
    <section className="panel">
      <h2>{e.class_id ? "Move to another arm" : "Place in a class"}</h2>
      <Field label="Class">
        {(id) => (
          <select id={id} value={cls} onChange={(x) => setCls(x.target.value)}>
            <option value="">Choose…</option>
            {(classes.data?.items ?? []).filter((c) => c.year_code === e.year_code && c.id !== e.class_id).map((c) => (
              <option key={c.id} value={c.id} disabled={c.places_left === 0}>
                {c.name} ({c.places_left} places)
              </option>
            ))}
          </select>
        )}
      </Field>
      <ErrorNote error={go.error} />
      <Button kind="secondary" busy={go.busy} disabled={!cls} onClick={() => void go.run()}>
        Move
      </Button>
    </section>
  );
}

function TransferOut({ e, done }: { e: EnrolmentRef; done: () => void }) {
  const [q, setQ] = useState("");
  const [to, setTo] = useState<{ id: string; name: string } | null>(null);
  const [reason, setReason] = useState("");
  const hits = useLoad(() => (q.length >= 2 && !to ? api<Items<{ id: string; name: string; lga: string }>>("/v1/schools", { query: { q } }) : Promise.resolve(null)), [q, to]);
  const go = useAction(() => api(`/v1/enrolments/${e.id}/transfer`, { json: { to_institution_id: to!.id, reason } }), done);
  return (
    <section className="panel">
      <h2>Transfer to another school</h2>
      <Field label="Receiving school">
        {(id) =>
          to ? (
            <div className="picked">
              {to.name}{" "}
              <button className="link" onClick={() => setTo(null)}>
                Change
              </button>
            </div>
          ) : (
            <div className="picker">
              <input id={id} value={q} onChange={(x) => setQ(x.target.value)} placeholder="School name or code" />
              {hits.data && hits.data.items.length > 0 && (
                <ul className="picker-list" role="listbox">
                  {hits.data.items.filter((h) => h.id !== e.institution_id).map((h) => (
                    <li key={h.id}>
                      <button type="button" role="option" aria-selected="false" onClick={() => setTo(h)}>
                        {h.name} <span className="muted">{h.lga}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )
        }
      </Field>
      <Field label="Reason">{(id) => <input id={id} value={reason} onChange={(x) => setReason(x.target.value)} placeholder="The family is moving" />}</Field>
      <ErrorNote error={go.error} />
      <Button kind="secondary" busy={go.busy} disabled={!to || reason.trim().length < 3} onClick={() => void go.run()}>
        Ask them to take the learner
      </Button>
    </section>
  );
}

function Leave({ e, done }: { e: EnrolmentRef; done: () => void }) {
  const [status, setStatus] = useState("withdrawn");
  const [reason, setReason] = useState("");
  const go = useAction(() => api(`/v1/enrolments/${e.id}/end`, { json: { status, reason } }), done);
  return (
    <section className="panel">
      <h2>Leaving</h2>
      <Field label="How">
        {(id) => (
          <select id={id} value={status} onChange={(x) => setStatus(x.target.value)}>
            <option value="withdrawn">Withdrawn</option>
            <option value="graduated">Completed the final year</option>
          </select>
        )}
      </Field>
      <Field label="Reason">{(id) => <input id={id} value={reason} onChange={(x) => setReason(x.target.value)} />}</Field>
      <ErrorNote error={go.error} />
      <Button kind="danger" busy={go.busy} disabled={reason.trim().length < 3} onClick={() => void go.run()}>
        Record it
      </Button>
    </section>
  );
}

const ROW: Record<string, [string, string]> = {
  valid: ["Ready", "info"],
  created: ["Enrolled", "good"],
  error: ["Needs correcting", "bad"],
  skipped: ["Skipped", "warn"],
};

interface Job {
  id: string;
  status: string;
  summary: { rows: number; valid: number; errors: number; created?: number; skipped?: number };
  rows: { row: number; status: string; messages: string[]; data: Record<string, string> }[];
}

/** Register a whole class at once from the school's existing register (CSV). */
export function ImportPage() {
  const s = useSession();
  const inst = s.institution;
  const sess = useAcademicSession();
  const [job, setJob] = useState<Job | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const upload = useAction(async () => {
    const text = await file!.text();
    setJob(
      await api<Job>(`/v1/institutions/${inst!.id}/class-lists`, {
        query: { session_id: sess.session!.id, source_name: file!.name },
        body: text,
        contentType: "text/csv",
      }),
    );
  });
  const commit = useAction(async () => setJob(await api<Job>(`/v1/class-lists/${job!.id}/commit`, { method: "POST" })));
  const template = useAction(async () => {
    const f = await download("/v1/class-lists/template.csv");
    saveBlob(f.blob, "class-list-template.csv");
  });
  if (!inst) return <NeedInstitution />;
  return (
    <div className="page">
      <PageHead eyebrow={inst.name} title="Import a class list">
        <Button kind="secondary" busy={template.busy} onClick={() => void template.run()}>
          Download the template
        </Button>
      </PageHead>
      <p className="lead narrow">
        One row per pupil, in the template's columns. Every row is checked first (names, sex, dates, the class, its capacity, and whether the
        pupil is already registered); nothing is registered until you confirm.
      </p>
      <div className="filters">
        <SessionPicker s={sess} />
        <Field label="Class list (CSV)">{(id) => <input id={id} type="file" accept=".csv,text/csv" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />}</Field>
        <Button busy={upload.busy} disabled={!file || !sess.session} onClick={() => void upload.run()}>
          Check it
        </Button>
      </div>
      <ErrorNote error={upload.error ?? commit.error ?? template.error} />
      {job && (
        <section className="panel">
          <h2>
            {job.status === "committed"
              ? `${job.summary.created ?? 0} pupils enrolled${job.summary.skipped ? `, ${job.summary.skipped} skipped` : ""}`
              : `${job.summary.valid} ready, ${job.summary.errors} to correct`}
          </h2>
          {job.status === "previewed" && job.summary.valid > 0 && (
            <Button busy={commit.busy} onClick={() => void commit.run()}>
              Enrol the {job.summary.valid} ready pupils
            </Button>
          )}
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Row</th>
                  <th>Pupil</th>
                  <th>Class</th>
                  <th>Result</th>
                </tr>
              </thead>
              <tbody>
                {job.rows.map((r) => (
                  <tr key={r.row}>
                    <td className="mono">{r.row}</td>
                    <td>
                      {r.data.surname}, {r.data.first_name}
                    </td>
                    <td>{r.data.class}</td>
                    <td>
                      <span className={`status tone-${ROW[r.status]?.[1] ?? "muted"}`}>{ROW[r.status]?.[0] ?? r.status}</span>
                      {r.messages.map((m) => (
                        <div key={m} className="small bad">
                          {m}
                        </div>
                      ))}
                      {r.data.age_note && <div className="small warn">{r.data.age_note}</div>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}


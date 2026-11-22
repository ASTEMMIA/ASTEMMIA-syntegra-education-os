import { useState } from "react";
import { Link } from "react-router";
import { api } from "../api";
import { useAcademicSession, useClasses, useYears } from "../academic";
import { useLoad, useSession } from "../session";
import { Button, Dl, Empty, ErrorNote, Field, Loading, PageHead, Status, useAction } from "../components/ui";
import { NeedInstitution } from "../App";
import type { Application, EnrolmentRef, Items, Transfer } from "../types";
import { date, dateTime, words } from "../format";

const RELATIONSHIPS = ["mother", "father", "guardian", "grandparent", "sibling", "aunt_uncle", "foster_carer", "sponsor", "other"];

/** The admission pipeline: application → review → assessment → offer → acceptance → enrolment. */
export function AdmissionsPage() {
  const s = useSession();
  const inst = s.institution;
  const [show, setShow] = useState("open");
  const [tab, setTab] = useState<"applications" | "transfers">("applications");
  const apps = useLoad(
    () => (inst ? api<Items<Application>>(`/v1/institutions/${inst.id}/applications`, { query: { status: show === "all" ? "" : show } }) : Promise.resolve(null)),
    [inst?.id, show],
  );
  const [open, setOpen] = useState<string | null>(null);
  const [enrolled, setEnrolled] = useState<EnrolmentRef | null>(null);
  if (!inst) return <NeedInstitution />;
  const chosen = apps.data?.items.find((a) => a.id === open) ?? null;
  return (
    <div className="page">
      <PageHead eyebrow={inst.name} title="Admissions" />
      {enrolled && (
        <p className="note-info" role="status">
          Enrolled <Link to={`/learners/${enrolled.learner_id}`}>{enrolled.learner_name}</Link> ({enrolled.learner_number})
          {enrolled.class_name ? ` in ${enrolled.class_name}` : ""}.
        </p>
      )}
      <div className="tabs" role="tablist">
        <button role="tab" aria-selected={tab === "applications"} onClick={() => setTab("applications")}>
          Applications
        </button>
        <button role="tab" aria-selected={tab === "transfers"} onClick={() => setTab("transfers")}>
          Transfers
        </button>
      </div>
      {tab === "transfers" ? (
        <Transfers institutionId={inst.id} />
      ) : (
        <>
          {s.can("learner:register") && <NewApplication institutionId={inst.id} done={apps.reload} />}
          <div className="filters">
            <Field label="Show">
              {(id) => (
                <select id={id} value={show} onChange={(e) => setShow(e.target.value)}>
                  <option value="open">In progress</option>
                  <option value="enrolled">Enrolled</option>
                  <option value="rejected">Rejected</option>
                  <option value="all">All</option>
                </select>
              )}
            </Field>
          </div>
          <ErrorNote error={apps.error} />
          {apps.loading && <Loading />}
          {apps.data && apps.data.items.length === 0 && <Empty title="No applications here" />}
          {apps.data && apps.data.items.length > 0 && (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>Reference</th>
                    <th>Applicant</th>
                    <th>For</th>
                    <th>Status</th>
                    <th>Decision due</th>
                  </tr>
                </thead>
                <tbody>
                  {apps.data.items.map((a) => (
                    <tr key={a.id} aria-current={a.id === open}>
                      <td className="mono nowrap">
                        <button className="link" onClick={() => setOpen(a.id === open ? null : a.id)}>
                          {a.reference}
                        </button>
                      </td>
                      <td>
                        {a.applicant_name}
                        <div className="muted small">
                          {a.sex} · born {date(a.date_of_birth)}
                          {a.channel === "online" && " · applied online"}
                        </div>
                      </td>
                      <td>{a.year_name}</td>
                      <td>
                        <Status value={a.status} />
                      </td>
                      <td className={a.overdue ? "overdue nowrap" : "nowrap"}>{["submitted", "under_review", "assessment"].includes(a.status) ? date(a.due_by) : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {chosen && (
            <ApplicationPanel
              a={chosen}
              done={apps.reload}
              onEnrolled={(e) => {
                setEnrolled(e);
                setOpen(null);
                apps.reload();
              }}
            />
          )}
        </>
      )}
    </div>
  );
}

function NewApplication({ institutionId, done }: { institutionId: string; done: () => void }) {
  const s = useSession();
  const sess = useAcademicSession();
  const years = useYears();
  const levels = new Set(s.institution?.level_codes ?? []);
  const blank = { year_code: "", given_name: "", family_name: "", other_names: "", date_of_birth: "", sex: "F", guardian_name: "", guardian_phone: "", guardian_relationship: "mother", previous_school: "" };
  const [f, setF] = useState(blank);
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value });
  const submit = useAction(
    () => api<Application>(`/v1/institutions/${institutionId}/applications`, { json: { ...f, session_id: sess.session?.id, other_names: f.other_names || null, previous_school: f.previous_school || null } }),
    () => {
      setF(blank);
      done();
    },
  );
  return (
    <details className="panel">
      <summary>
        <h2 className="inline">Record an application</h2>
      </summary>
      <form
        className="form-grid"
        onSubmit={(e) => {
          e.preventDefault();
          void submit.run();
        }}
      >
        <Field label={`Class wanted (${sess.session?.name ?? "no session"})`}>
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
        <Field label="Parent or guardian">{(id) => <input id={id} value={f.guardian_name} onChange={set("guardian_name")} required placeholder="First name and surname" />}</Field>
        <Field label="Their phone">{(id) => <input id={id} value={f.guardian_phone} onChange={set("guardian_phone")} required inputMode="tel" placeholder="0803 123 4567" />}</Field>
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
        <Field label="Previous school">{(id) => <input id={id} value={f.previous_school} onChange={set("previous_school")} />}</Field>
        <div className="form-actions">
          <ErrorNote error={submit.error} />
          <Button busy={submit.busy} type="submit" disabled={!sess.session}>
            Record the application
          </Button>
        </div>
      </form>
    </details>
  );
}

function ApplicationPanel({ a, done, onEnrolled }: { a: Application; done: () => void; onEnrolled: (e: EnrolmentRef) => void }) {
  const s = useSession();
  const [note, setNote] = useState("");
  const [score, setScore] = useState("");
  const act = useAction((action: string) => api(`/v1/applications/${a.id}/${action}`, { json: { note: note.trim() || null, score: score ? Number(score) : null } }), () => {
    setNote("");
    done();
  });
  const decide = s.can("admission:decide");
  const reg = s.can("learner:register");
  const btn = (action: string, label: string, kind: "primary" | "secondary" | "danger" = "secondary") => (
    <Button key={action} kind={kind} busy={act.busy} onClick={() => void act.run(action)}>
      {label}
    </Button>
  );
  const actions: React.ReactNode[] = [];
  if (reg && a.status === "submitted") actions.push(btn("review", "Start the review", "primary"));
  if (reg && ["under_review", "assessment"].includes(a.status)) actions.push(btn("assess", "Record the assessment"));
  if (decide && ["under_review", "assessment", "waitlisted"].includes(a.status)) actions.push(btn("offer", "Offer a place", "primary"), btn("reject", "Reject", "danger"));
  if (decide && ["under_review", "assessment"].includes(a.status)) actions.push(btn("waitlist", "Waiting list"));
  if (reg && a.status === "offered") actions.push(btn("accept", "Family accepted", "primary"), btn("decline", "Family declined"));
  return (
    <section className="panel focus" aria-label={`Application ${a.reference}`}>
      <h2>
        {a.applicant_name} <span className="mono muted small">{a.reference}</span>
      </h2>
      <div className="two">
        <Dl
          rows={[
            ["Class", a.year_name],
            ["Born", `${date(a.date_of_birth)} (${a.sex})`],
            ["Guardian", `${a.guardian_name}, ${words(a.guardian_relationship).toLowerCase()} · ${a.guardian_phone}`],
            ["Previous school", a.previous_school ?? "—"],
            ["Assessment", a.assessment_score ? `${a.assessment_score}${a.assessment_note ? `: ${a.assessment_note}` : ""}` : "—"],
            ["Offer open until", date(a.offer_expires_on)],
          ]}
        />
        <ol className="timeline">
          {a.history.map((h, i) => (
            <li key={i}>
              <strong>{words(h.status)}</strong> · {dateTime(h.at)}
              {h.note && <div className="small">{h.note}</div>}
            </li>
          ))}
        </ol>
      </div>
      {a.age_note && <p className="note-info">Age: {a.age_note}. A reason will be asked for at enrolment.</p>}
      {actions.length > 0 && (
        <div className="stack sec">
          <div className="row wrap">
            <Field label="Note" hint="Needed to reject; the family is told.">
              {(id) => <input id={id} value={note} onChange={(e) => setNote(e.target.value)} />}
            </Field>
            {["under_review", "assessment"].includes(a.status) && (
              <Field label="Assessment score (0-100)">{(id) => <input id={id} type="number" min={0} max={100} step="0.5" value={score} onChange={(e) => setScore(e.target.value)} />}</Field>
            )}
          </div>
          <ErrorNote error={act.error} />
          <div className="row wrap">{actions}</div>
        </div>
      )}
      {reg && a.status === "accepted" && <Enrol a={a} done={onEnrolled} />}
      {a.enrolment_id && <p className="good">Enrolled.</p>}
    </section>
  );
}

function Enrol({ a, done }: { a: Application; done: (e: EnrolmentRef) => void }) {
  const s = useSession();
  const classes = useClasses(s.institution?.id, a.session_id);
  const [cls, setCls] = useState("");
  const [number, setNumber] = useState("");
  const [why, setWhy] = useState("");
  const go = useAction(
    () => api<EnrolmentRef>(`/v1/applications/${a.id}/enrolment`, { json: { class_id: cls || null, admission_number: number || null, age_override: why || null } }),
    done,
  );
  return (
    <form
      className="form-grid sec"
      onSubmit={(e) => {
        e.preventDefault();
        void go.run();
      }}
    >
      <Field label="Class">
        {(id) => (
          <select id={id} value={cls} onChange={(e) => setCls(e.target.value)}>
            <option value="">Place later</option>
            {(classes.data?.items ?? []).filter((c) => c.year_code === a.year_code && c.status === "active").map((c) => (
              <option key={c.id} value={c.id} disabled={c.places_left === 0}>
                {c.name} ({c.places_left} places)
              </option>
            ))}
          </select>
        )}
      </Field>
      <Field label="Admission number" hint="The school's own register number.">
        {(id) => <input id={id} value={number} onChange={(e) => setNumber(e.target.value)} />}
      </Field>
      {a.age_note && <Field label="Reason for the age">{(id) => <input id={id} value={why} onChange={(e) => setWhy(e.target.value)} required />}</Field>}
      <div className="form-actions">
        <ErrorNote error={go.error} />
        <Button busy={go.busy} type="submit">
          Enrol {a.applicant_name.split(" ")[0]}
        </Button>
      </div>
    </form>
  );
}

function Transfers({ institutionId }: { institutionId: string }) {
  const [dir, setDir] = useState<"incoming" | "outgoing">("incoming");
  const list = useLoad(() => api<Items<Transfer>>(`/v1/institutions/${institutionId}/transfers`, { query: { direction: dir, status: "all" } }), [institutionId, dir]);
  return (
    <div className="stack">
      <div className="filters">
        <Field label="Show">
          {(id) => (
            <select id={id} value={dir} onChange={(e) => setDir(e.target.value as typeof dir)}>
              <option value="incoming">Into this school</option>
              <option value="outgoing">Out of this school</option>
            </select>
          )}
        </Field>
      </div>
      <ErrorNote error={list.error} />
      {list.data && list.data.items.length === 0 && <Empty title="No transfers">Transfers out start from a learner's page.</Empty>}
      <div className="panels">
        {list.data?.items.map((t) => (
          <section key={t.id} className="panel">
            <h2>{t.learner_name}</h2>
            <span className="mono muted small">{t.learner_number}</span>
            <p>
              {t.from_institution} → {t.to_institution} · {t.year_code} · from {date(t.effective_on)}
            </p>
            <p className="small">{t.reason}</p>
            <Status value={t.status} />
            {t.decision_note && <p className="small">{t.decision_note}</p>}
            {dir === "incoming" && t.status === "requested" && <Receive t={t} done={list.reload} />}
          </section>
        ))}
      </div>
    </div>
  );
}

function Receive({ t, done }: { t: Transfer; done: () => void }) {
  const s = useSession();
  const sess = useAcademicSession();
  const classes = useClasses(s.institution?.id, sess.session?.id);
  const [cls, setCls] = useState("");
  const [note, setNote] = useState("");
  const decide = useAction(
    (accept: boolean) => api(`/v1/transfers/${t.id}/decision`, { json: { accept, class_id: cls || null, session_id: sess.session?.id ?? null, note: note || null } }),
    done,
  );
  return (
    <div className="stack sec">
      <Field label="Into class">
        {(id) => (
          <select id={id} value={cls} onChange={(e) => setCls(e.target.value)}>
            <option value="">Place later</option>
            {(classes.data?.items ?? []).filter((c) => c.year_code === t.year_code).map((c) => (
              <option key={c.id} value={c.id} disabled={c.places_left === 0}>
                {c.name} ({c.places_left} places)
              </option>
            ))}
          </select>
        )}
      </Field>
      <Field label="Note" hint="Needed to decline.">
        {(id) => <input id={id} value={note} onChange={(e) => setNote(e.target.value)} />}
      </Field>
      <ErrorNote error={decide.error} />
      <div className="row wrap">
        <Button busy={decide.busy} onClick={() => void decide.run(true)}>
          Accept
        </Button>
        <Button kind="danger" busy={decide.busy} onClick={() => void decide.run(false)}>
          Decline
        </Button>
      </div>
    </div>
  );
}


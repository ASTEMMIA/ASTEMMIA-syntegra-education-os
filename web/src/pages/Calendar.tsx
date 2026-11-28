import { useState } from "react";
import { api } from "../api";
import { useAcademicSession, useYears } from "../academic";
import { useLoad, useSession } from "../session";
import { Button, Empty, ErrorNote, Field, PageHead, Status, useAction } from "../components/ui";
import type { Curriculum, Items, SubjectRef } from "../types";
import { date, words } from "../format";

/** Sessions with their terms or semesters, and the curriculum the state has adopted. */
export function CalendarPage() {
  const s = useSession();
  const sess = useAcademicSession();
  return (
    <div className="page">
      <PageHead eyebrow="Academic structure" title="Calendar and curriculum" />
      <section className="panel">
        <h2>Sessions</h2>
        <ErrorNote error={sess.error} />
        {sess.sessions.length === 0 && !sess.loading && <Empty title="No session set" />}
        <div className="panels">
          {sess.sessions.map((x) => (
            <SessionCard key={x.id} id={x.id} reload={sess.reload} />
          ))}
        </div>
        {s.can("calendar:manage") && <NewSession done={sess.reload} />}
      </section>
      <Curricula />
    </div>
  );
}

function SessionCard({ id, reload }: { id: string; reload: () => void }) {
  const s = useSession();
  const sess = useAcademicSession();
  const x = sess.sessions.find((y) => y.id === id)!;
  const current = useAction((pid: string) => api(`/v1/periods/${pid}/current`, { method: "POST" }), reload);
  const mine = x.set_at === s.me?.active_scope_id;
  return (
    <div className="panel">
      <h3>
        {x.name} <Status value={x.status === "current" ? "open" : x.status} />
      </h3>
      <ul className="plain">
        {x.parts.map((p) => (
          <li key={p.id}>
            <strong>{p.name}</strong> {date(p.starts_on)} – {date(p.ends_on)}{" "}
            {p.status === "current" ? (
              <span className="good small">current</span>
            ) : (
              mine &&
              s.can("calendar:manage") && (
                <button className="link small" onClick={() => void current.run(p.id)}>
                  Make current
                </button>
              )
            )}
          </li>
        ))}
      </ul>
      {!mine && <span className="muted small">Set by the board above this institution.</span>}
      <ErrorNote error={current.error} />
    </div>
  );
}

function NewSession({ done }: { done: () => void }) {
  const [tertiary, setTertiary] = useState(false);
  const [name, setName] = useState(() => {
    const y = new Date().getFullYear();
    return `${y}/${y + 1}`;
  });
  const n = tertiary ? 2 : 3;
  const [parts, setParts] = useState<{ starts_on: string; ends_on: string }[]>([{ starts_on: "", ends_on: "" }, { starts_on: "", ends_on: "" }, { starts_on: "", ends_on: "" }]);
  const create = useAction(() => api("/v1/sessions", { json: { name, tertiary, parts: parts.slice(0, n) } }), done);
  const label = tertiary ? ["First semester", "Second semester"] : ["First term", "Second term", "Third term"];
  return (
    <details className="panel">
      <summary>
        <h3 className="inline">Set a new session</h3>
      </summary>
      <form
        className="stack"
        onSubmit={(e) => {
          e.preventDefault();
          void create.run();
        }}
      >
        <div className="row wrap">
          <Field label="Session">{(id) => <input id={id} value={name} onChange={(e) => setName(e.target.value)} pattern="\d{4}/\d{4}" required />}</Field>
          <label className="check">
            <input type="checkbox" checked={tertiary} onChange={(e) => setTertiary(e.target.checked)} /> Two semesters (tertiary)
          </label>
        </div>
        {label.map((l, i) => (
          <div className="row wrap" key={l}>
            <Field label={`${l} starts`}>
              {(id) => <input id={id} type="date" value={parts[i]!.starts_on} onChange={(e) => setParts(parts.map((p, j) => (j === i ? { ...p, starts_on: e.target.value } : p)))} required />}
            </Field>
            <Field label={`${l} ends`}>
              {(id) => <input id={id} type="date" value={parts[i]!.ends_on} onChange={(e) => setParts(parts.map((p, j) => (j === i ? { ...p, ends_on: e.target.value } : p)))} required />}
            </Field>
          </div>
        ))}
        <ErrorNote error={create.error} />
        <Button busy={create.busy} type="submit">
          Set the session
        </Button>
      </form>
    </details>
  );
}

function Curricula() {
  const s = useSession();
  const list = useLoad(() => api<Items<Curriculum>>("/v1/curricula"), []);
  const years = useYears();
  const [year, setYear] = useState("P1");
  const [from, setFrom] = useState(() => {
    const y = new Date().getFullYear();
    return `${y}/${y + 1}`;
  });
  const first = list.data?.items[0];
  const subjects = useLoad(
    () => (first ? api<Items<{ subject: SubjectRef; kind: string; periods_per_week: number | null }>>(`/v1/curricula/${first.version_id}/years/${year}`) : Promise.resolve(null)),
    [first?.version_id, year],
  );
  const adopt = useAction(() => api(`/v1/curricula/${first!.version_id}/adopt`, { json: { years: null, from_session: from } }), list.reload);
  return (
    <section className="panel">
      <h2>Curriculum</h2>
      <ErrorNote error={list.error} />
      {list.data && list.data.items.length === 0 && <Empty title="No curriculum loaded">An administrator loads it with the seed-curriculum command.</Empty>}
      {list.data?.items.map((c) => (
        <div key={c.version_id} className="stack">
          <p>
            <strong>{c.name}</strong> · version {c.version} · <Status value={c.status === "published" ? "verified" : c.status} /> · {c.authority}
          </p>
          {c.note && <p className="muted small narrow">{c.note}</p>}
          <p className="small">
            {c.adoptions.length
              ? c.adoptions.map((a) => `Adopted by ${a.scope_name} for ${a.years === "*" ? "every year" : a.years} from ${a.from_session}`).join("; ")
              : "Not adopted yet: classes get no subjects until it is."}
          </p>
        </div>
      ))}
      {first && s.can("curriculum:publish") && (
        <form
          className="row wrap"
          onSubmit={(e) => {
            e.preventDefault();
            void adopt.run();
          }}
        >
          <Field label="Adopt for every year from session">{(id) => <input id={id} value={from} onChange={(e) => setFrom(e.target.value)} pattern="\d{4}/\d{4}" />}</Field>
          <Button busy={adopt.busy} type="submit">
            Adopt
          </Button>
          <ErrorNote error={adopt.error} />
        </form>
      )}
      {first && (
        <div className="stack sec">
          <Field label="Subjects for">
            {(id) => (
              <select id={id} value={year} onChange={(e) => setYear(e.target.value)}>
                {(years.data?.items ?? []).filter((y) => ["eccde", "primary", "jss", "sss", "adult"].includes(y.level)).map((y) => (
                  <option key={y.code} value={y.code}>
                    {y.name}
                  </option>
                ))}
              </select>
            )}
          </Field>
          <div className="tags">
            {subjects.data?.items.map((e) => (
              <span key={e.subject.id} title={`${words(e.kind)}, ${e.periods_per_week ?? "—"} periods a week`}>
                {e.subject.name}
                {e.kind !== "core" ? ` (${e.kind})` : ""}
              </span>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}


import { useState } from "react";
import { useNavigate } from "react-router";
import { api } from "../api";
import { useLoad, useSession } from "../session";
import { Button, Chip, Empty, ErrorNote, Field, Loading, PageHead, useAction } from "../components/ui";
import { NeedInstitution } from "../App";
import { CLASSIFICATIONS, type Items, type PaperFile, type Series } from "../types";
import { NUMBERING, PAPER_RULE, words } from "../format";

type Plan = { suggested_plan: string } & Items<Series>;

export function FilePlanPage() {
  const s = useSession();
  const inst = s.institution;
  const plan = useLoad(() => (inst ? api<Plan>(`/v1/institutions/${inst.id}/file-plan`) : Promise.resolve(null)), [inst?.id]);
  const [choice, setChoice] = useState("");
  const [openFor, setOpenFor] = useState<Series | null>(null);
  const adopt = useAction(() => api(`/v1/institutions/${inst!.id}/file-plan/adopt`, { json: { plan: choice || null } }), plan.reload);
  if (!inst) return <NeedInstitution />;
  if (plan.loading && !plan.data) return <Loading />;
  const items = plan.data?.items ?? [];
  const groups = new Map<string, Series[]>();
  for (const x of items) groups.set(`${x.function_code} · ${x.function_name}`, [...(groups.get(`${x.function_code} · ${x.function_name}`) ?? []), x]);

  return (
    <div className="page">
      <PageHead eyebrow={inst.name} title="File plan" />
      <ErrorNote error={plan.error} />
      {s.can("records:admin") && (
        <section className="panel">
          <h2>{items.length ? "Add series from a model plan" : "Adopt a model file plan"}</h2>
          <p className="muted small">
            The model plans for primary, secondary and tertiary institutions set each series' numbering, classification and what
            happens to the paper. Adopting again only adds series this institution does not have yet.
          </p>
          <form
            className="row wrap"
            onSubmit={(e) => {
              e.preventDefault();
              void adopt.run();
            }}
          >
            <Field label="Model plan">
              {(id) => (
                <select id={id} value={choice} onChange={(e) => setChoice(e.target.value)}>
                  <option value="">Suggested for this institution ({plan.data?.suggested_plan})</option>
                  <option value="primary">Primary (ECCDE, primary, adult centres)</option>
                  <option value="secondary">Secondary (JSS and SSS)</option>
                  <option value="tertiary">Tertiary</option>
                </select>
              )}
            </Field>
            <Button busy={adopt.busy} type="submit">
              Adopt
            </Button>
          </form>
          <ErrorNote error={adopt.error} />
        </section>
      )}
      {items.length === 0 && <Empty title="No file plan yet">A records officer adopts the model plan for this institution.</Empty>}
      {[...groups.entries()].map(([fn, series]) => (
        <section key={fn} className="panel">
          <h2>{fn}</h2>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Series</th>
                  <th>Numbering</th>
                  <th>Class</th>
                  <th>Paper, after the file closes</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {series.map((x) => (
                  <tr key={x.id}>
                    <td>
                      <span className="mono">{x.code}</span> {x.title}
                      {x.vital && <span className="badge">Vital record</span>}
                      {x.plan === "local" && <span className="badge">Local</span>}
                    </td>
                    <td>{NUMBERING[x.numbering]}</td>
                    <td>
                      <Chip level={x.classification} />
                    </td>
                    <td>
                      {x.paper_rule === "retain" ? "Keep permanently" : `${x.paper_retain_years} years, then ${x.paper_rule === "destroy" ? "destroy (once digitised)" : "to the State Archives"}`}
                    </td>
                    <td>
                      {s.can("file:manage") && x.active && (
                        <button className="link" onClick={() => setOpenFor(x)}>
                          Open a file
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ))}
      {openFor && <OpenFile series={openFor} close={() => setOpenFor(null)} />}
      {s.can("records:admin") && items.length > 0 && <LocalSeries institutionId={inst.id} done={plan.reload} />}
    </div>
  );
}

function OpenFile({ series, close }: { series: Series; close: () => void }) {
  const nav = useNavigate();
  const [title, setTitle] = useState("");
  const [home, setHome] = useState("");
  const [person, setPerson] = useState("");
  const [subject, setSubject] = useState("");
  const [year, setYear] = useState(String(new Date().getFullYear()));
  const [level, setLevel] = useState(series.classification);
  const open = useAction(
    () =>
      api<PaperFile>(`/v1/records-series/${series.id}/files`, {
        json: {
          title: title.trim(),
          home_location: home.trim(),
          person_id: series.numbering === "self" ? person.trim() || null : null,
          subject_name: series.numbering === "alphabetical" ? subject.trim() || null : null,
          year: series.numbering === "annual" ? Number(year) : null,
          classification: level,
        },
      }),
    (f) => nav(`/files/${f.id}`),
  );
  const allowed = CLASSIFICATIONS.slice(CLASSIFICATIONS.indexOf(series.classification));
  return (
    <section className="panel focus" aria-label={`Open a file in ${series.code}`}>
      <h2>
        Open a file in <span className="mono">{series.code}</span> {series.title}
      </h2>
      <p className="muted small">{NUMBERING[series.numbering]}. The number is given when the file is opened.</p>
      <form
        className="form-grid"
        onSubmit={(e) => {
          e.preventDefault();
          void open.run();
        }}
      >
        <Field label="File title">{(id) => <input id={id} value={title} onChange={(e) => setTitle(e.target.value)} required minLength={2} placeholder={series.numbering === "self" ? "Surname, other names" : "What the file holds"} />}</Field>
        <Field label="Home location" hint="Cabinet, drawer or shelf.">
          {(id) => <input id={id} value={home} onChange={(e) => setHome(e.target.value)} required minLength={2} placeholder="Registry, cabinet 2" />}
        </Field>
        {series.numbering === "self" && (
          <Field label="Whose file" hint="Their Person 360 id, from their learner or staff record.">
            {(id) => <input id={id} value={person} onChange={(e) => setPerson(e.target.value)} required className="mono" />}
          </Field>
        )}
        {series.numbering === "alphabetical" && (
          <Field label="Filed under the name of">{(id) => <input id={id} value={subject} onChange={(e) => setSubject(e.target.value)} placeholder="Okafor" />}</Field>
        )}
        {series.numbering === "annual" && (
          <Field label="Year">{(id) => <input id={id} type="number" min={1900} max={2100} value={year} onChange={(e) => setYear(e.target.value)} required />}</Field>
        )}
        <Field label="Classification" hint="May be raised above the series', never lowered.">
          {(id) => (
            <select id={id} value={level} onChange={(e) => setLevel(e.target.value as typeof level)}>
              {allowed.map((c) => (
                <option key={c} value={c}>
                  {words(c)}
                </option>
              ))}
            </select>
          )}
        </Field>
        <div className="form-actions">
          <ErrorNote error={open.error} />
          <div className="row">
            <Button busy={open.busy} type="submit">
              Open the file
            </Button>
            <Button kind="quiet" type="button" onClick={close}>
              Cancel
            </Button>
          </div>
        </div>
      </form>
    </section>
  );
}

function LocalSeries({ institutionId, done }: { institutionId: string; done: () => void }) {
  const [f, setF] = useState({ function_code: "", function_name: "", code: "", title: "", numbering: "annual", classification: "internal", paper_rule: "destroy", paper_retain_years: "6" });
  const add = useAction(
    () =>
      api(`/v1/institutions/${institutionId}/file-plan/series`, {
        json: { ...f, paper_retain_years: f.paper_rule === "retain" ? null : Number(f.paper_retain_years) },
      }),
    done,
  );
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value });
  return (
    <details className="panel">
      <summary>
        <h2 className="inline">Add a local series</h2>
      </summary>
      <form
        className="form-grid"
        onSubmit={(e) => {
          e.preventDefault();
          void add.run();
        }}
      >
        <Field label="Function code">{(id) => <input id={id} value={f.function_code} onChange={set("function_code")} required placeholder="PRI" />}</Field>
        <Field label="Function name">{(id) => <input id={id} value={f.function_name} onChange={set("function_name")} required placeholder="Primary education" />}</Field>
        <Field label="Series code">{(id) => <input id={id} value={f.code} onChange={set("code")} required placeholder="PRI/9" />}</Field>
        <Field label="Title">{(id) => <input id={id} value={f.title} onChange={set("title")} required />}</Field>
        <Field label="Numbering">
          {(id) => (
            <select id={id} value={f.numbering} onChange={set("numbering")}>
              {Object.entries(NUMBERING).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </select>
          )}
        </Field>
        <Field label="Classification">
          {(id) => (
            <select id={id} value={f.classification} onChange={set("classification")}>
              {CLASSIFICATIONS.map((c) => (
                <option key={c} value={c}>
                  {words(c)}
                </option>
              ))}
            </select>
          )}
        </Field>
        <Field label="Paper, after the file closes">
          {(id) => (
            <select id={id} value={f.paper_rule} onChange={set("paper_rule")}>
              {Object.entries(PAPER_RULE).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </select>
          )}
        </Field>
        {f.paper_rule !== "retain" && (
          <Field label="Years kept first">{(id) => <input id={id} type="number" min={0} max={100} value={f.paper_retain_years} onChange={set("paper_retain_years")} />}</Field>
        )}
        <div className="form-actions">
          <ErrorNote error={add.error} />
          <Button busy={add.busy} type="submit" kind="secondary">
            Add the series
          </Button>
        </div>
      </form>
    </details>
  );
}

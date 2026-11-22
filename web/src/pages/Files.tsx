import { useState } from "react";
import { Link, useParams } from "react-router";
import { api, download, saveBlob } from "../api";
import { useLoad, useNames, useSession } from "../session";
import { Button, Chip, Dl, Empty, ErrorNote, Field, Loading, PageHead, Status, UserPicker, useAction } from "../components/ui";
import { NeedInstitution } from "../App";
import type { Doc, Items, Movement, PaperFile } from "../types";
import { MOVE, PAPER_RULE, date, dateTime } from "../format";

function Where({ f }: { f: PaperFile }) {
  const name = useNames([f.holder_user_id]);
  if (!f.out) return <>{f.home_location}</>;
  return (
    <span className={f.overdue ? "overdue" : "out"}>
      Out with {f.holder_user_id ? name(f.holder_user_id) : f.holder_name}
      {f.due_back && <span className="small">, {f.overdue ? "overdue since" : "due back"} {date(f.due_back)}</span>}
    </span>
  );
}

/** The file register: every paper file of the institution, where it is and who has it. */
export function FilesPage() {
  const s = useSession();
  const inst = s.institution;
  const [status, setStatus] = useState("");
  const [overdue, setOverdue] = useState(false);
  const files = useLoad(
    () => (inst ? api<Items<PaperFile>>(`/v1/institutions/${inst.id}/files`, { query: { status, overdue: overdue || undefined } }) : Promise.resolve(null)),
    [inst?.id, status, overdue],
  );
  const csv = useAction(async () => {
    const f = await download(`/v1/institutions/${inst!.id}/files.csv`);
    saveBlob(f.blob, `${inst!.institution_code}-file-list.csv`);
  });
  if (!inst) return <NeedInstitution />;
  return (
    <div className="page">
      <PageHead eyebrow={inst.name} title="File register">
        <Button kind="secondary" busy={csv.busy} onClick={() => void csv.run()}>
          Download the file list (CSV)
        </Button>
        {s.can("file:manage") && (
          <Link className="btn btn-primary" to="/file-plan">
            Open a new file
          </Link>
        )}
      </PageHead>
      <ErrorNote error={csv.error} />
      <div className="filters">
        <Field label="Status">
          {(id) => (
            <select id={id} value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="">All files</option>
              <option value="open">Open</option>
              <option value="closed">Closed</option>
              <option value="disposed">Paper destroyed</option>
              <option value="transferred">Paper sent to the State Archives</option>
            </select>
          )}
        </Field>
        <label className="check">
          <input type="checkbox" checked={overdue} onChange={(e) => setOverdue(e.target.checked)} /> Only files overdue for return
        </label>
      </div>
      {files.loading && <Loading />}
      <ErrorNote error={files.error} />
      {files.data && files.data.items.length === 0 && (
        <Empty title="No files here yet">Open a file from a series in the file plan.</Empty>
      )}
      {files.data && files.data.items.length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Reference</th>
                <th>Title</th>
                <th>Class</th>
                <th>Where it is</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {files.data.items.map((f) => (
                <tr key={f.id}>
                  <td className="mono nowrap">
                    <Link to={`/files/${f.id}`}>
                      {f.file_number}
                      {f.volume > 1 ? ` Vol ${f.volume}` : ""}
                    </Link>
                  </td>
                  <td>
                    {f.title}
                    <div className="muted small">{f.series_title}</div>
                  </td>
                  <td>
                    <Chip level={f.classification} />
                  </td>
                  <td>
                    <Where f={f} />
                  </td>
                  <td>
                    <Status value={f.status} />
                    {f.digitised && <div className="small good">Digitised</div>}
                    {f.legal_hold && <div className="small bad">Legal hold</div>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export function FilePage() {
  const { id = "" } = useParams();
  const s = useSession();
  const file = useLoad(() => api<PaperFile>(`/v1/files/${id}`), [id]);
  const moves = useLoad(() => api<Items<Movement>>(`/v1/files/${id}/movements`), [id, file.data?.holder_user_id, file.data?.holder_name]);
  const docs = useLoad(() => api<Items<Doc>>("/v1/documents", { query: { file_id: id, limit: 200 } }), [id]);
  const name = useNames([...(moves.data?.items.flatMap((m) => [m.to_user_id, m.by]) ?? [])]);
  const reload = () => {
    file.reload();
    docs.reload();
  };
  const act = useAction((path: string, json?: unknown) => api<PaperFile>(`/v1/files/${id}/${path}`, { method: "POST", json }), reload);

  if (file.loading && !file.data) return <Loading />;
  if (file.error) return <ErrorNote error={file.error} />;
  const f = file.data!;
  const live = f.status === "open" || f.status === "closed";
  return (
    <div className="page">
      <PageHead eyebrow={`${f.series_code} · ${f.series_title}`} title={f.title}>
        {s.can("file:manage") && f.status === "open" && !f.out && (
          <Button kind="secondary" busy={act.busy} onClick={() => void act.run("close")}>
            Close the file
          </Button>
        )}
        {s.can("file:manage") && live && (
          <Button kind="secondary" busy={act.busy} onClick={() => void act.run("new-volume")}>
            Continue in a new volume
          </Button>
        )}
      </PageHead>
      <ErrorNote error={act.error} />
      <div className="two">
        <section className="cover" aria-label="File cover">
          <span className="eyebrow">{f.institution_name}</span>
          <div className="cover-ref mono">{f.reference}</div>
          <h2>{f.title}</h2>
          <Dl
            rows={[
              ["Classification", <Chip key="c" level={f.classification} />],
              ["Volume", f.volume],
              ["Opened", date(f.opened_on)],
              ["Closed", date(f.closed_on)],
              ["Status", <Status key="s" value={f.status} />],
              ["Paper rule", PAPER_RULE[f.paper_rule]],
              ["Paper may go", f.paper_due ? date(f.paper_due) : f.paper_rule === "retain" ? "Never" : "When the file closes and retention runs out"],
              ["Digitised", f.digitised ? "Yes, every document verified" : "Not yet"],
              ["Legal hold", f.legal_hold ? "Yes: nothing may be disposed of" : "No"],
            ]}
          />
          {f.previous_file_id && <Link to={`/files/${f.previous_file_id}`}>Previous volume</Link>}
        </section>
        <section className="panel">
          <h2>Where it is</h2>
          <p className="lead">
            <Where f={f} />
          </p>
          {s.can("file:manage") && live && <MoveForm key={`${f.out}-${f.holder_user_id}-${f.holder_name}`} f={f} onDone={reload} />}
        </section>
      </div>
      <div className="panels">
        <section className="panel">
          <h2>Documents in this file</h2>
          <ErrorNote error={docs.error} />
          {docs.data && docs.data.items.length === 0 && <p className="muted">No scanned documents are filed here yet (or none you may read).</p>}
          {docs.data && docs.data.items.length > 0 && (
            <ol className="folios">
              {[...docs.data.items]
                .sort((a, b) => (a.folio ?? 999) - (b.folio ?? 999))
                .map((d) => (
                  <li key={d.id}>
                    <span className="mono muted">fol. {d.folio ?? "—"}</span> <Link to={`/documents/${d.id}`}>{d.title}</Link> <Status value={d.status} />
                  </li>
                ))}
            </ol>
          )}
          {s.can("document:index") && !f.digitised && docs.data && docs.data.items.length > 0 && (
            <Button kind="secondary" busy={act.busy} onClick={() => void act.run("digitised")}>
              Mark the whole file digitised
            </Button>
          )}
        </section>
        <section className="panel">
          <h2>Movements</h2>
          {moves.data && moves.data.items.length === 0 && <p className="muted">It has not left its home location.</p>}
          {moves.data && moves.data.items.length > 0 && (
            <ul className="timeline">
              {moves.data.items.map((m) => (
                <li key={m.id}>
                  <strong>{MOVE[m.kind]}</strong>
                  {m.kind !== "return" && <> to {m.to_user_id ? name(m.to_user_id) : m.to_name}</>} · {dateTime(m.moved_at)}
                  {m.by && <span className="muted"> by {name(m.by)}</span>}
                  {m.note && <div className="small">{m.note}</div>}
                </li>
              ))}
            </ul>
          )}
        </section>
        {s.can("records:admin") && live && <AdminPanel f={f} onDone={reload} />}
      </div>
    </div>
  );
}

function MoveForm({ f, onDone }: { f: PaperFile; onDone: () => void }) {
  const [kind, setKind] = useState<"issue" | "pass" | "external_out">(f.out ? "pass" : "issue");
  const [user, setUser] = useState<{ id: string; display_name: string; login: string } | null>(null);
  const [office, setOffice] = useState("");
  const [due, setDue] = useState("");
  const [note, setNote] = useState("");
  const move = useAction(
    (k: string) =>
      api(`/v1/files/${f.id}/movements`, {
        json:
          k === "return"
            ? { kind: "return" }
            : {
                kind: k,
                to_user_id: k === "external_out" ? null : user?.id ?? null,
                to_name: k === "external_out" ? office.trim() : null,
                due_back: due || null,
                note: note.trim() || null,
              },
      }),
    () => {
      setUser(null);
      setOffice("");
      setNote("");
      onDone();
    },
  );
  return (
    <form
      className="stack"
      onSubmit={(e) => {
        e.preventDefault();
        void move.run(kind);
      }}
    >
      <fieldset className="seg">
        <legend className="sr">Movement</legend>
        {(f.out ? (["pass", "external_out"] as const) : (["issue", "external_out"] as const)).map((k) => (
          <label key={k}>
            <input type="radio" name="kind" checked={kind === k} onChange={() => setKind(k)} /> {k === "issue" ? "Issue to a person" : k === "pass" ? "Pass to another person" : "Send to another office"}
          </label>
        ))}
      </fieldset>
      {kind === "external_out" ? (
        <Field label="Office">{(id) => <input id={id} value={office} onChange={(e) => setOffice(e.target.value)} placeholder="LGEA Aba North" required />}</Field>
      ) : (
        <Field label="Person">{(id) => <UserPicker id={id} value={user} onChange={setUser} />}</Field>
      )}
      <div className="row wrap">
        <Field label="Due back" hint="Default: 7 days, or 14 for another office.">
          {(id) => <input id={id} type="date" value={due} onChange={(e) => setDue(e.target.value)} />}
        </Field>
        <Field label="Note">{(id) => <input id={id} value={note} onChange={(e) => setNote(e.target.value)} placeholder="For the transfer letter" />}</Field>
      </div>
      <ErrorNote error={move.error} />
      <div className="row wrap">
        <Button busy={move.busy} type="submit" disabled={kind !== "external_out" && !user}>
          Record the movement
        </Button>
        {f.out && (
          <Button kind="secondary" type="button" busy={move.busy} onClick={() => void move.run("return")}>
            It is back
          </Button>
        )}
      </div>
    </form>
  );
}

function AdminPanel({ f, onDone }: { f: PaperFile; onDone: () => void }) {
  const [action, setAction] = useState<"destroy" | "transfer_archives" | "retain">(f.paper_rule);
  const [reason, setReason] = useState(`${f.series_code}: ${PAPER_RULE[f.paper_rule]}`);
  const hold = useAction(() => api(`/v1/files/${f.id}/legal-hold`, { json: { hold: !f.legal_hold } }), onDone);
  const propose = useAction(() => api(`/v1/files/${f.id}/disposition`, { json: { action, reason: reason.trim() } }), onDone);
  return (
    <section className="panel">
      <h2>Holds and the paper original</h2>
      <Button kind={f.legal_hold ? "secondary" : "danger"} busy={hold.busy} onClick={() => void hold.run()}>
        {f.legal_hold ? "Lift the legal hold" : "Place a legal hold"}
      </Button>
      <ErrorNote error={hold.error} />
      {f.status === "closed" && (
        <form
          className="stack sec"
          onSubmit={(e) => {
            e.preventDefault();
            void propose.run();
          }}
        >
          <p className="muted small">
            Propose what happens to the paper. Someone else approves it. The scanned copies stay in the repository permanently whatever is decided.
          </p>
          <Field label="Proposal">
            {(id) => (
              <select id={id} value={action} onChange={(e) => setAction(e.target.value as typeof action)}>
                <option value="destroy">Destroy the paper (only once digitised)</option>
                <option value="transfer_archives">Transfer the paper to the State Archives</option>
                <option value="retain">Keep the paper</option>
              </select>
            )}
          </Field>
          <Field label="Reason">{(id) => <input id={id} value={reason} onChange={(e) => setReason(e.target.value)} required minLength={3} />}</Field>
          <ErrorNote error={propose.error} />
          <Button busy={propose.busy} type="submit" kind="secondary">
            Send for approval
          </Button>
        </form>
      )}
    </section>
  );
}

import { useState } from "react";
import { Link } from "react-router";
import { api } from "../api";
import { useLoad, useNames, useSession } from "../session";
import { Button, Empty, ErrorNote, Field, Loading, PageHead, Status, useAction } from "../components/ui";
import type { Disposition, Items, PaperFile } from "../types";
import { PAPER_RULE, date } from "../format";

const ACTION: Record<string, string> = {
  destroy: "Destroy the paper",
  transfer_archives: "Transfer the paper to the State Archives",
  retain: "Keep the paper",
};

/** Paper originals only: the digital record stays in the repository permanently. */
export function DispositionsPage() {
  const s = useSession();
  const inst = s.institution;
  const [status, setStatus] = useState<"pending" | "approved" | "rejected">("pending");
  const list = useLoad(() => api<Items<Disposition>>("/v1/dispositions", { query: { status } }), [status]);
  const due = useLoad(
    () => (inst && s.can("records:admin", "records:oversight", "disposal:approve") ? api<Items<PaperFile>>(`/v1/institutions/${inst.id}/disposition-due`) : Promise.resolve(null)),
    [inst?.id],
  );
  const name = useNames(list.data?.items.flatMap((p) => [p.requested_by, p.decided_by]) ?? []);
  return (
    <div className="page">
      <PageHead eyebrow="Paper originals" title="Paper disposal" />
      <p className="lead narrow">
        Scanned records are kept permanently. This page is only about the paper originals once their files have closed: destroy
        them (only after digitisation), send them to the State Archives, or keep them. Each proposal is approved by a second person.
      </p>
      {inst && due.data && (
        <section className="panel">
          <h2>Due for a decision at {inst.name}</h2>
          {due.data.items.length === 0 ? (
            <p className="muted">No closed files have reached the end of their paper retention.</p>
          ) : (
            <ul className="plain">
              {due.data.items.map((f) => (
                <li key={f.id}>
                  <Link className="mono" to={`/files/${f.id}`}>
                    {f.file_number}
                  </Link>{" "}
                  {f.title} · due {date(f.paper_due)} · {PAPER_RULE[f.paper_rule]}
                  {!f.digitised && f.paper_rule === "destroy" && <span className="warn small"> · scan it first</span>}
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
      <div className="filters">
        <Field label="Show">
          {(id) => (
            <select id={id} value={status} onChange={(e) => setStatus(e.target.value as typeof status)}>
              <option value="pending">Waiting for a decision</option>
              <option value="approved">Approved</option>
              <option value="rejected">Refused</option>
            </select>
          )}
        </Field>
      </div>
      {list.loading && <Loading />}
      <ErrorNote error={list.error} />
      {list.data && list.data.items.length === 0 && <Empty title="Nothing here" />}
      <div className="panels">
        {list.data?.items.map((p) => (
          <section key={p.id} className="panel">
            <h2>
              <Link className="mono" to={`/files/${p.file_id}`}>
                {p.file_reference}
              </Link>
            </h2>
            <p>
              <strong>{ACTION[p.action]}</strong> · <Status value={p.status} />
            </p>
            <p className="small">
              Proposed by {name(p.requested_by)}: {p.reason}
            </p>
            {p.decided_by && (
              <p className="small">
                Decided by {name(p.decided_by)}
                {p.certificate_reference && <>, certificate <span className="mono">{p.certificate_reference}</span></>}
                {p.decision_note && <>: {p.decision_note}</>}
              </p>
            )}
            {p.status === "pending" && s.can("disposal:approve") && <Decide p={p} done={list.reload} />}
          </section>
        ))}
      </div>
    </div>
  );
}

function Decide({ p, done }: { p: Disposition; done: () => void }) {
  const [cert, setCert] = useState("");
  const [note, setNote] = useState("");
  const decide = useAction(
    (approve: boolean) => api(`/v1/dispositions/${p.id}/decision`, { json: { approve, note: note.trim() || null, certificate_reference: cert.trim() || null } }),
    done,
  );
  return (
    <div className="stack sec">
      {p.action !== "retain" && (
        <Field label={p.action === "destroy" ? "Destruction certificate number" : "State Archives transfer reference"}>
          {(id) => <input id={id} value={cert} onChange={(e) => setCert(e.target.value)} className="mono" />}
        </Field>
      )}
      <Field label="Note" hint="Needed when refusing.">
        {(id) => <input id={id} value={note} onChange={(e) => setNote(e.target.value)} />}
      </Field>
      <ErrorNote error={decide.error} />
      <div className="row wrap">
        <Button busy={decide.busy} onClick={() => void decide.run(true)}>
          Approve
        </Button>
        <Button kind="danger" busy={decide.busy} onClick={() => void decide.run(false)}>
          Refuse
        </Button>
      </div>
    </div>
  );
}

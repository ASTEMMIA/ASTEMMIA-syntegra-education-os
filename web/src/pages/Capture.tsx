import { useRef, useState } from "react";
import { Link, useParams } from "react-router";
import { api, newKey } from "../api";
import { useLoad, useNames, useSession } from "../session";
import { Button, Dl, Empty, ErrorNote, Field, Loading, PageHead, Status, UserPicker, useAction } from "../components/ui";
import { NeedInstitution } from "../App";
import { DOC_TYPES, type Batch, type Items } from "../types";
import { bytes, words } from "../format";

interface BatchItem {
  id: string;
  document_number: string;
  title: string;
  status: string;
  current_version: number;
  page_count: number | null;
}

const ACCEPT = "application/pdf,image/jpeg,image/png,image/tiff";

/** For capture operators (in-house or a contractor): the batches assigned to me. */
export function CapturePage() {
  const mine = useLoad(() => api<Items<Batch>>("/v1/my-batches"), []);
  const [chosen, setChosen] = useState<string | null>(null);
  if (mine.loading && !mine.data) return <Loading />;
  const batches = mine.data?.items ?? [];
  const working = batches.filter((b) => b.status === "open" || b.status === "rejected");
  const current = batches.find((b) => b.id === chosen) ?? working[0] ?? null;
  return (
    <div className="page">
      <PageHead eyebrow="Scanning" title="My batches" />
      <ErrorNote error={mine.error} />
      {batches.length === 0 && <Empty title="No batches assigned to you">The records officer assigns each box of paper to an operator as a batch.</Empty>}
      {batches.length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Batch</th>
                <th>Box</th>
                <th>Scanned</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {batches.map((b) => (
                <tr key={b.id} aria-current={current?.id === b.id}>
                  <td className="mono nowrap">
                    <button className="link" onClick={() => setChosen(b.id)}>
                      {b.batch_number}
                    </button>
                  </td>
                  <td>{b.description}</td>
                  <td>
                    {b.documents} documents, {b.pages} pages
                  </td>
                  <td>
                    <Status value={b.status} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {current && <Scanner batch={current} done={mine.reload} />}
    </div>
  );
}

interface Queued {
  key: string; // idempotency key: a retry never registers the scan twice
  file: File;
  title: string;
  docType: string;
  state: "waiting" | "sending" | "done" | "failed";
  number?: string;
  error?: unknown;
}

function Scanner({ batch, done }: { batch: Batch; done: () => void }) {
  const [queue, setQueue] = useState<Queued[]>([]);
  const [drag, setDrag] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const items = useLoad(() => api<Items<BatchItem>>(`/v1/batches/${batch.id}/documents`), [batch.id, batch.documents]);
  const capturing = batch.status === "open" || batch.status === "rejected";
  const add = (files: FileList | null) => {
    if (!files) return;
    const start = batch.documents + queue.length;
    setQueue((q) => [
      ...q,
      ...[...files].map((file, i) => ({
        key: newKey(),
        file,
        title: `Scan ${String(start + i + 1).padStart(4, "0")}`,
        docType: "other",
        state: "waiting" as const,
      })),
    ]);
  };
  const send = useAction(async () => {
    for (const item of queue) {
      if (item.state === "done") continue;
      setQueue((q) => q.map((x) => (x.key === item.key ? { ...x, state: "sending", error: undefined } : x)));
      const form = new FormData();
      form.set("file", item.file);
      form.set("metadata", JSON.stringify({ title: item.title, doc_type: item.docType, source: "scanned" }));
      try {
        const r = await api<{ document_number: string }>(`/v1/batches/${batch.id}/documents`, { form, key: item.key });
        setQueue((q) => q.map((x) => (x.key === item.key ? { ...x, state: "done", number: r.document_number } : x)));
      } catch (error) {
        setQueue((q) => q.map((x) => (x.key === item.key ? { ...x, state: "failed", error } : x)));
      }
    }
    done();
  });
  const finish = useAction(() => api(`/v1/batches/${batch.id}/finish`, { method: "POST" }), () => {
    setQueue([]);
    done();
  });
  const pending = queue.filter((x) => x.state !== "done").length;

  return (
    <section className="panel">
      <h2>
        Batch <span className="mono">{batch.batch_number}</span>
      </h2>
      <Dl
        rows={[
          ["Box", batch.description],
          ["Operator", batch.operator_kind === "contractor" ? `${batch.contractor_name} (contractor)` : "In-house"],
          ["Status", <Status key="s" value={batch.status} />],
          ...(batch.status === "rejected" ? ([["What to rescan", batch.qc_note ?? "—"]] as [string, string][]) : []),
        ]}
      />
      {capturing && (
        <>
          <div
            className={`drop${drag ? " over" : ""}`}
            onDragOver={(e) => {
              e.preventDefault();
              setDrag(true);
            }}
            onDragLeave={() => setDrag(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDrag(false);
              add(e.dataTransfer.files);
            }}
          >
            <p>
              <strong>Drop scans here</strong>, in the order they come out of the box.
            </p>
            <p className="muted small">PDF/A at 300 dpi is best. JPEG, PNG and TIFF are accepted.</p>
            <Button kind="secondary" type="button" onClick={() => input.current?.click()}>
              Choose files
            </Button>
            <input ref={input} type="file" multiple accept={ACCEPT} hidden onChange={(e) => add(e.target.files)} />
          </div>
          {queue.length > 0 && (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>File</th>
                    <th>Title</th>
                    <th>Type, if known</th>
                    <th>Result</th>
                  </tr>
                </thead>
                <tbody>
                  {queue.map((x) => (
                    <tr key={x.key}>
                      <td>
                        {x.file.name}
                        <div className="muted small">{bytes(x.file.size)}</div>
                      </td>
                      <td>
                        <input
                          aria-label={`Title for ${x.file.name}`}
                          value={x.title}
                          disabled={x.state === "done"}
                          onChange={(e) => setQueue((q) => q.map((y) => (y.key === x.key ? { ...y, title: e.target.value } : y)))}
                        />
                      </td>
                      <td>
                        <select
                          aria-label={`Type for ${x.file.name}`}
                          value={x.docType}
                          disabled={x.state === "done"}
                          onChange={(e) => setQueue((q) => q.map((y) => (y.key === x.key ? { ...y, docType: e.target.value } : y)))}
                        >
                          {DOC_TYPES.map((t) => (
                            <option key={t} value={t}>
                              {words(t)}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td>
                        {x.state === "done" && <span className="good mono">{x.number}</span>}
                        {x.state === "sending" && "Sending…"}
                        {x.state === "waiting" && <span className="muted">Waiting</span>}
                        {x.state === "failed" && <ErrorNote error={x.error} />}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div className="row wrap">
            <Button busy={send.busy} disabled={!pending} onClick={() => void send.run()}>
              {pending ? `Send ${pending} scan${pending > 1 ? "s" : ""}` : "All sent"}
            </Button>
            <Button kind="secondary" busy={finish.busy} disabled={send.busy || pending > 0 || batch.documents + queue.length === 0} onClick={() => void finish.run()}>
              Capture finished: send for checking
            </Button>
          </div>
          <ErrorNote error={send.error ?? finish.error} />
        </>
      )}
      {(items.data?.items.length ?? 0) > 0 && (
        <section className="sec">
          <h3 className="h3">Already in this batch</h3>
          <ul className="plain">
            {items.data!.items.map((d) => (
              <li key={d.id}>
                <span className="mono">{d.document_number}</span> {d.title} · v{d.current_version}
                {batch.status === "rejected" && <Rescan id={d.id} reload={items.reload} />}
              </li>
            ))}
          </ul>
        </section>
      )}
    </section>
  );
}

function Rescan({ id, reload }: { id: string; reload: () => void }) {
  const input = useRef<HTMLInputElement>(null);
  const up = useAction(async (file: File) => {
    const form = new FormData();
    form.set("file", file);
    form.set("metadata", JSON.stringify({ note: "Rescanned after the batch check" }));
    await api(`/v1/documents/${id}/versions`, { form });
  }, reload);
  return (
    <>
      {" "}
      <button className="link" onClick={() => input.current?.click()} disabled={up.busy}>
        {up.busy ? "Sending…" : "Rescan"}
      </button>
      <input ref={input} type="file" accept={ACCEPT} hidden onChange={(e) => e.target.files?.[0] && void up.run(e.target.files[0])} />
      <ErrorNote error={up.error} />
    </>
  );
}

/** For the records officer: every batch of the institution, opening new ones. */
export function BatchesPage() {
  const s = useSession();
  const inst = s.institution;
  const [status, setStatus] = useState("");
  const list = useLoad(() => (inst ? api<Items<Batch>>(`/v1/institutions/${inst.id}/batches`, { query: { status } }) : Promise.resolve(null)), [inst?.id, status]);
  const name = useNames(list.data?.items.map((b) => b.operator_user_id) ?? []);
  if (!inst) return <NeedInstitution />;
  return (
    <div className="page">
      <PageHead eyebrow={inst.name} title="Scanning batches" />
      {s.can("records:admin") && <NewBatch institutionId={inst.id} done={list.reload} />}
      <div className="filters">
        <Field label="Status">
          {(id) => (
            <select id={id} value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="">All</option>
              <option value="open">Being scanned</option>
              <option value="captured">Waiting for its check</option>
              <option value="rejected">Sent back for rescanning</option>
              <option value="accepted">Accepted</option>
            </select>
          )}
        </Field>
      </div>
      <ErrorNote error={list.error} />
      {list.data && list.data.items.length === 0 && <Empty title="No batches" />}
      {list.data && list.data.items.length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Batch</th>
                <th>Box</th>
                <th>Operator</th>
                <th>Scanned</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {list.data.items.map((b) => (
                <tr key={b.id}>
                  <td className="mono nowrap">
                    <Link to={`/batches/${b.id}`}>{b.batch_number}</Link>
                  </td>
                  <td>{b.description}</td>
                  <td>
                    {name(b.operator_user_id)}
                    <div className="muted small">{b.operator_kind === "contractor" ? b.contractor_name : "In-house"}</div>
                  </td>
                  <td>
                    {b.documents} / {b.pages} pp.
                  </td>
                  <td>
                    <Status value={b.status} />
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

function NewBatch({ institutionId, done }: { institutionId: string; done: () => void }) {
  const [description, setDescription] = useState("");
  const [kind, setKind] = useState<"in_house" | "contractor">("in_house");
  const [contractor, setContractor] = useState("");
  const [contract, setContract] = useState("");
  const [operator, setOperator] = useState<{ id: string; display_name: string; login: string } | null>(null);
  const open = useAction(
    () =>
      api(`/v1/institutions/${institutionId}/batches`, {
        json: {
          description: description.trim(),
          operator_kind: kind,
          operator_user_id: operator!.id,
          contractor_name: kind === "contractor" ? contractor.trim() : null,
          contract_reference: kind === "contractor" ? contract.trim() || null : null,
        },
      }),
    () => {
      setDescription("");
      setOperator(null);
      done();
    },
  );
  return (
    <details className="panel">
      <summary>
        <h2 className="inline">Open a batch</h2>
      </summary>
      <form
        className="form-grid"
        onSubmit={(e) => {
          e.preventDefault();
          void open.run();
        }}
      >
        <Field label="What is in the box">{(id) => <input id={id} value={description} onChange={(e) => setDescription(e.target.value)} required minLength={3} placeholder="Pupils' files A–E, box 3" />}</Field>
        <fieldset className="seg">
          <legend>Who scans it</legend>
          <label>
            <input type="radio" name="kind" checked={kind === "in_house"} onChange={() => setKind("in_house")} /> Our own staff
          </label>
          <label>
            <input type="radio" name="kind" checked={kind === "contractor"} onChange={() => setKind("contractor")} /> A contractor
          </label>
        </fieldset>
        {kind === "contractor" && (
          <>
            <Field label="Contractor">{(id) => <input id={id} value={contractor} onChange={(e) => setContractor(e.target.value)} required />}</Field>
            <Field label="Contract reference">{(id) => <input id={id} value={contract} onChange={(e) => setContract(e.target.value)} />}</Field>
          </>
        )}
        <Field label="Operator" hint="Only this person can scan into the batch.">
          {(id) => <UserPicker id={id} value={operator} onChange={setOperator} />}
        </Field>
        <div className="form-actions">
          <ErrorNote error={open.error} />
          <Button busy={open.busy} type="submit" disabled={!operator}>
            Open the batch
          </Button>
        </div>
      </form>
    </details>
  );
}

export function BatchPage() {
  const { id = "" } = useParams();
  const s = useSession();
  const inst = s.institution;
  const list = useLoad(() => (inst ? api<Items<Batch>>(`/v1/institutions/${inst.id}/batches`) : Promise.resolve(null)), [inst?.id]);
  const items = useLoad(() => api<Items<BatchItem>>(`/v1/batches/${id}/documents`), [id]);
  const b = list.data?.items.find((x) => x.id === id);
  const name = useNames([b?.operator_user_id, b?.qc_by]);
  const [sampled, setSampled] = useState("");
  const [defects, setDefects] = useState("0");
  const [note, setNote] = useState("");
  const check = useAction(
    (accept: boolean) => api(`/v1/batches/${id}/check`, { json: { sampled: Number(sampled), defects: Number(defects), accept, note: note.trim() || null } }),
    () => {
      list.reload();
      items.reload();
    },
  );
  if (!inst) return <NeedInstitution />;
  if (list.loading && !list.data) return <Loading />;
  if (!b) return <ErrorNote error={list.error ?? new Error("Batch not found in this institution.")} />;
  const suggested = Math.max(1, Math.ceil(b.documents * 0.1));
  return (
    <div className="page">
      <PageHead eyebrow={inst.name} title={`Batch ${b.batch_number}`} />
      <div className="two">
        <section className="panel">
          <Dl
            rows={[
              ["Box", b.description],
              ["Scanned by", `${name(b.operator_user_id)}${b.operator_kind === "contractor" ? `, ${b.contractor_name}` : ""}`],
              ["Contract", b.contract_reference ?? "—"],
              ["Captured", `${b.documents} documents, ${b.pages} pages`],
              ["Status", <Status key="s" value={b.status} />],
              ["Checked by", b.qc_by ? `${name(b.qc_by)}: ${b.qc_sampled} sampled, ${b.qc_defects} defects` : "—"],
              ["Note", b.qc_note ?? "—"],
            ]}
          />
        </section>
        {s.can("document:index") && b.status === "captured" && (
          <section className="panel">
            <h2>Check the batch</h2>
            <p className="muted small">
              Compare a sample with the paper: every page there, the right way up, legible, in order. You cannot check a batch you
              scanned yourself. About one in ten ({suggested}) is a sensible sample.
            </p>
            <div className="row wrap">
              <Field label="Documents sampled">{(fid) => <input id={fid} type="number" min={1} max={b.documents} value={sampled} onChange={(e) => setSampled(e.target.value)} placeholder={String(suggested)} />}</Field>
              <Field label="With defects">{(fid) => <input id={fid} type="number" min={0} value={defects} onChange={(e) => setDefects(e.target.value)} />}</Field>
            </div>
            <Field label="What must be rescanned" hint="Needed when sending it back.">
              {(fid) => <textarea id={fid} rows={2} value={note} onChange={(e) => setNote(e.target.value)} />}
            </Field>
            <ErrorNote error={check.error} />
            <div className="row wrap">
              <Button busy={check.busy} disabled={!sampled} onClick={() => void check.run(true)}>
                Accept the batch
              </Button>
              <Button kind="danger" busy={check.busy} disabled={!sampled} onClick={() => void check.run(false)}>
                Send it back
              </Button>
            </div>
          </section>
        )}
      </div>
      <section className="panel">
        <h2>Documents in the batch</h2>
        <ErrorNote error={items.error} />
        <ul className="plain">
          {items.data?.items.map((d) => (
            <li key={d.id}>
              <span className="mono">{d.document_number}</span> <Link to={`/documents/${d.id}`}>{d.title}</Link> <Status value={d.status} /> <span className="muted small">v{d.current_version}</span>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

import { useEffect, useState } from "react";
import { Link, useParams } from "react-router";
import { ApiError, api, download, saveBlob } from "../api";
import { useLoad, useNames, useSession } from "../session";
import { Button, Chip, Dl, ErrorNote, Field, Loading, PageHead, Status, useAction } from "../components/ui";
import { CLASSIFICATIONS, DOC_TYPES, type Access, type DocDetail, type Items, type PaperFile } from "../types";
import { bytes, date, dateTime, words } from "../format";

function needsPurpose(e: unknown): boolean {
  return e instanceof ApiError && e.status === 422 && /purpose/i.test(e.detail);
}

export function DocumentPage() {
  const { id = "" } = useParams();
  const [purpose, setPurpose] = useState<string | null>(null);
  const doc = useLoad(() => api<DocDetail>(`/v1/documents/${id}`, { query: { purpose } }), [id, purpose]);

  if (doc.loading && !doc.data) return <Loading what="Opening the record" />;
  if (needsPurpose(doc.error)) return <PurposeGate onGive={setPurpose} />;
  if (doc.error)
    return (
      <div className="page">
        <PageHead title="Record not available" />
        <ErrorNote error={doc.error} />
        <Link to="/">Back to search</Link>
      </div>
    );
  return <DocumentView d={doc.data!} purpose={purpose} reload={doc.reload} />;
}

function PurposeGate({ onGive }: { onGive: (p: string) => void }) {
  const [text, setText] = useState("");
  return (
    <div className="page narrow">
      <PageHead eyebrow="Restricted record" title="Why do you need this record?" />
      <p>
        This record is restricted (for example medical, SEND, discipline or safeguarding). It opens only for a stated
        reason, which is kept in its access log with your name.
      </p>
      <form
        className="stack"
        onSubmit={(e) => {
          e.preventDefault();
          if (text.trim().length >= 5) onGive(text.trim());
        }}
      >
        <Field label="Reason" hint="For example: transfer request from the LGEA; parent's subject access request.">
          {(fid) => <textarea id={fid} value={text} onChange={(e) => setText(e.target.value)} rows={3} minLength={5} required maxLength={255} />}
        </Field>
        <div className="row">
          <Button type="submit">Open the record</Button>
          <Link to="/" className="btn btn-quiet">
            Cancel
          </Link>
        </div>
      </form>
    </div>
  );
}

function Viewer({ docId, version, purpose, mime }: { docId: string; version: number; purpose: string | null; mime: string }) {
  const [src, setSrc] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [zoom, setZoom] = useState(false);
  useEffect(() => {
    let url: string | null = null;
    let live = true;
    setSrc(null);
    setError(null);
    download(`/v1/documents/${docId}/content`, { version, purpose }).then(
      (f) => {
        url = URL.createObjectURL(f.blob);
        if (live) setSrc(url);
      },
      (e) => live && setError(e),
    );
    return () => {
      live = false;
      if (url) URL.revokeObjectURL(url);
    };
  }, [docId, version, purpose]);
  return (
    <section className="viewer" aria-label="The scanned document">
      <div className="viewer-bar">
        <span className="mono">Version {version}</span>
        {mime.startsWith("image/") && (
          <button type="button" className="btn btn-dark" onClick={() => setZoom((z) => !z)} aria-pressed={zoom}>
            {zoom ? "Fit to view" : "Zoom in"}
          </button>
        )}
      </div>
      <div className={`stage${zoom ? " zoom" : ""}`}>
        {error ? <ErrorNote error={error} /> : null}
        {!src && !error && <Loading what="Loading the scan" />}
        {src && mime === "application/pdf" && <iframe src={src} title="Scanned document (PDF)" />}
        {src && mime.startsWith("image/") && mime !== "image/tiff" && <img src={src} alt="Scanned page" />}
        {src && mime === "image/tiff" && <p className="muted">TIFF scans cannot be shown in the browser. Use Download.</p>}
      </div>
    </section>
  );
}

function DocumentView({ d, purpose, reload }: { d: DocDetail; purpose: string | null; reload: () => void }) {
  const s = useSession();
  const doc = d.document;
  const [version, setVersion] = useState(doc.current_version);
  useEffect(() => setVersion(doc.current_version), [doc.current_version]);
  const v = d.versions.find((x) => x.version === version) ?? d.versions[d.versions.length - 1]!;
  const name = useNames([d.captured_by, d.indexed_by, d.verified_by, ...d.versions.map((x) => x.created_by)]);
  const save = useAction(async () => {
    const f = await download(`/v1/documents/${doc.id}/content`, { version, purpose });
    saveBlob(f.blob, f.name);
  });
  const verify = useAction(() => api(`/v1/documents/${doc.id}/verify`, { method: "POST" }), reload);

  return (
    <div className="page doc-page">
      <PageHead eyebrow={`${words(doc.doc_type)} · ${d.institution_name}`} title={doc.title}>
        <Button kind="secondary" busy={save.busy} onClick={() => void save.run()}>
          Download
        </Button>
        {s.can("document:index") && doc.status === "indexed" && (
          <Button busy={verify.busy} onClick={() => void verify.run()}>
            Verify
          </Button>
        )}
      </PageHead>
      <ErrorNote error={save.error ?? verify.error} />
      <div className="doc-grid">
        <Viewer docId={doc.id} version={v.version} purpose={purpose} mime={v.mime} />
        <aside className="card">
          <div className="row wrap">
            <Chip level={doc.classification} />
            <Status value={doc.status} />
          </div>
          <span className="mono muted">{doc.document_number}</span>
          <Dl
            rows={[
              ["Dated", date(doc.document_date)],
              [
                "Paper file",
                doc.file_id ? (
                  <>
                    <Link to={`/files/${doc.file_id}`} className="mono">
                      {d.file_reference}
                    </Link>
                    {doc.folio ? `, folio ${doc.folio}` : ""}
                  </>
                ) : (
                  "Not filed yet"
                ),
              ],
              ["Digital copy", "Kept permanently"],
              ["Linked to", d.links.length ? d.links.map((l) => words(l.kind)).join(", ") : "—"],
              ["Pages", doc.page_count ?? "—"],
            ]}
          />
          {doc.keywords.length > 0 && (
            <div className="tags">
              {doc.keywords.map((k) => (
                <span key={k}>{k}</span>
              ))}
            </div>
          )}
          <section className="sec">
            <h2 className="h3">How it got here</h2>
            <ol className="steps">
              <li>Captured by {name(d.captured_by)}</li>
              <li className={d.indexed_by ? "" : "todo"}>{d.indexed_by ? `Details confirmed by ${name(d.indexed_by)}` : "Waiting for its details to be confirmed"}</li>
              <li className={d.verified_by ? "" : "todo"}>{d.verified_by ? `Verified by ${name(d.verified_by)}, ${date(d.verified_at)}` : "Waiting for a second person to verify it"}</li>
            </ol>
          </section>
          <section className="sec">
            <h2 className="h3">Versions</h2>
            <ul className="versions">
              {d.versions.map((x) => (
                <li key={x.version}>
                  <button type="button" className={`link${x.version === version ? " current" : ""}`} onClick={() => setVersion(x.version)} aria-pressed={x.version === version}>
                    Version {x.version}
                  </button>{" "}
                  · {bytes(x.size_bytes)} · {dateTime(x.created_at)} · {name(x.created_by)}
                  {x.note && <div className="small">{x.note}</div>}
                  <div className="sha mono">SHA-256 {x.sha256}</div>
                </li>
              ))}
            </ul>
          </section>
        </aside>
      </div>
      <div className="panels">
        {s.can("document:index") && <IndexPanel d={d} reload={reload} />}
        {s.can("document:capture") && <VersionPanel docId={doc.id} reload={reload} />}
        {s.can("records:admin", "records:oversight") && <ClassifyPanel d={d} reload={reload} />}
        {s.can("records:oversight", "audit:view") && <AccessPanel docId={doc.id} />}
      </div>
    </div>
  );
}

function IndexPanel({ d, reload }: { d: DocDetail; reload: () => void }) {
  const s = useSession();
  const doc = d.document;
  const [title, setTitle] = useState(doc.title);
  const [type, setType] = useState<string>(doc.doc_type);
  const [when, setWhen] = useState(doc.document_date ?? "");
  const [kw, setKw] = useState(doc.keywords.join(", "));
  const [fileId, setFileId] = useState(doc.file_id ?? "");
  const [folio, setFolio] = useState(doc.folio ? String(doc.folio) : "");
  const [person, setPerson] = useState("");
  const files = useLoad(
    () => (s.can("file:manage", "records:admin") ? api<Items<PaperFile>>(`/v1/institutions/${doc.institution_id}/files`, { query: { status: "open" } }) : Promise.resolve({ items: [] })),
    [doc.institution_id],
  );
  const save = useAction(
    (accept: boolean) =>
      api(`/v1/documents/${doc.id}/index`, {
        json: accept
          ? { accept_proposed: true }
          : {
              title: title.trim() || null,
              doc_type: type,
              document_date: when || null,
              keywords: kw.split(",").map((k) => k.trim()).filter(Boolean),
              file_id: fileId || null,
              folio: folio ? Number(folio) : null,
              links: person.trim() ? [{ kind: "person", target_id: person.trim() }] : null,
            },
      }),
    reload,
  );
  return (
    <section className="panel">
      <h2>Confirm the details</h2>
      <p className="muted small">
        {doc.status === "verified"
          ? "Changing the details sends the record back for a second person to verify."
          : "Check each detail against the paper. A second person then verifies it."}
      </p>
      {d.proposed && doc.status === "captured" && (
        <div className="note-info">
          Suggested from the scan: <strong>{String(d.proposed.title ?? "")}</strong>
          {d.proposed.document_date ? `, dated ${date(String(d.proposed.document_date))}` : ""}.{" "}
          <Button kind="secondary" busy={save.busy} onClick={() => void save.run(true)}>
            Accept the suggestion
          </Button>
        </div>
      )}
      <form
        className="form-grid"
        onSubmit={(e) => {
          e.preventDefault();
          void save.run(false);
        }}
      >
        <Field label="Title">{(id) => <input id={id} value={title} onChange={(e) => setTitle(e.target.value)} maxLength={255} />}</Field>
        <Field label="Type">
          {(id) => (
            <select id={id} value={type} onChange={(e) => setType(e.target.value)}>
              {DOC_TYPES.map((t) => (
                <option key={t} value={t}>
                  {words(t)}
                </option>
              ))}
            </select>
          )}
        </Field>
        <Field label="Date on the document">{(id) => <input id={id} type="date" value={when} onChange={(e) => setWhen(e.target.value)} />}</Field>
        <Field label="Keywords" hint="Separate with commas.">
          {(id) => <input id={id} value={kw} onChange={(e) => setKw(e.target.value)} />}
        </Field>
        <Field label="Paper file">
          {(id) => (
            <select id={id} value={fileId} onChange={(e) => setFileId(e.target.value)}>
              <option value="">Not filed</option>
              {files.data?.items.map((f) => (
                <option key={f.id} value={f.id}>
                  {f.file_number} · {f.title}
                </option>
              ))}
            </select>
          )}
        </Field>
        <Field label="Folio (position in the file)">
          {(id) => <input id={id} type="number" min={1} value={folio} onChange={(e) => setFolio(e.target.value)} />}
        </Field>
        <Field label="Link to a person" hint="Their Person 360 id, from their learner or staff record.">
          {(id) => <input id={id} value={person} onChange={(e) => setPerson(e.target.value)} className="mono" placeholder="optional" />}
        </Field>
        <div className="form-actions">
          <ErrorNote error={save.error} />
          <Button busy={save.busy} type="submit">
            Save the details
          </Button>
        </div>
      </form>
    </section>
  );
}

function VersionPanel({ docId, reload }: { docId: string; reload: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [note, setNote] = useState("");
  const add = useAction(async () => {
    const form = new FormData();
    form.set("file", file!);
    form.set("metadata", JSON.stringify({ note: note.trim() }));
    await api(`/v1/documents/${docId}/versions`, { form });
    setFile(null);
    setNote("");
  }, reload);
  return (
    <section className="panel">
      <h2>Add a new version</h2>
      <p className="muted small">For a rescan or a corrected copy. Earlier versions are kept as they are.</p>
      <form
        className="stack"
        onSubmit={(e) => {
          e.preventDefault();
          if (file) void add.run();
        }}
      >
        <Field label="File (PDF, JPEG, PNG or TIFF)">
          {(id) => <input id={id} type="file" accept="application/pdf,image/jpeg,image/png,image/tiff" onChange={(e) => setFile(e.target.files?.[0] ?? null)} required />}
        </Field>
        <Field label="Why a new version">{(id) => <input id={id} value={note} onChange={(e) => setNote(e.target.value)} minLength={3} required placeholder="Rescanned: page 2 was skewed" />}</Field>
        <ErrorNote error={add.error} />
        <Button busy={add.busy} type="submit" kind="secondary">
          Add the version
        </Button>
      </form>
    </section>
  );
}

function ClassifyPanel({ d, reload }: { d: DocDetail; reload: () => void }) {
  const s = useSession();
  const [to, setTo] = useState(d.document.classification);
  const [reason, setReason] = useState("");
  const change = useAction(() => api(`/v1/documents/${d.document.id}/classification`, { json: { classification: to, reason: reason.trim() } }), reload);
  const here = CLASSIFICATIONS.indexOf(d.document.classification);
  const options = s.can("records:oversight") ? CLASSIFICATIONS : CLASSIFICATIONS.slice(here);
  return (
    <section className="panel">
      <h2>Classification</h2>
      <p className="muted small">
        {s.can("records:oversight")
          ? "As State oversight you may raise or lower it."
          : "You may raise it. Only the State Government or the State Archives may lower it."}
      </p>
      <form
        className="stack"
        onSubmit={(e) => {
          e.preventDefault();
          void change.run();
        }}
      >
        <Field label="Classification">
          {(id) => (
            <select id={id} value={to} onChange={(e) => setTo(e.target.value as typeof to)}>
              {options.map((c) => (
                <option key={c} value={c}>
                  {words(c)}
                </option>
              ))}
            </select>
          )}
        </Field>
        <Field label="Reason">{(id) => <input id={id} value={reason} onChange={(e) => setReason(e.target.value)} minLength={3} required />}</Field>
        <ErrorNote error={change.error} />
        <Button busy={change.busy} type="submit" kind="secondary" disabled={to === d.document.classification}>
          Change the classification
        </Button>
      </form>
    </section>
  );
}

function AccessPanel({ docId }: { docId: string }) {
  const log = useLoad(() => api<Items<Access>>(`/v1/documents/${docId}/access-log`), [docId]);
  const name = useNames(log.data?.items.map((a) => a.user_id) ?? []);
  return (
    <section className="panel">
      <h2>Who opened it</h2>
      <ErrorNote error={log.error} />
      {log.data && (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Who</th>
                <th>What</th>
                <th>Why</th>
                <th>When</th>
              </tr>
            </thead>
            <tbody>
              {log.data.items.map((a, i) => (
                <tr key={i}>
                  <td>{name(a.user_id)}</td>
                  <td>{words(a.action)}{a.version ? ` v${a.version}` : ""}</td>
                  <td>{a.purpose ?? "—"}</td>
                  <td className="nowrap">{dateTime(a.at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

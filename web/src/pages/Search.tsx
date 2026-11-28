import { useState } from "react";
import { Link, useSearchParams } from "react-router";
import { api } from "../api";
import { useLoad, useSession } from "../session";
import { Chip, Empty, ErrorNote, Field, Loading, PageHead, Status } from "../components/ui";
import { DOC_TYPES, type Doc, type Items } from "../types";
import { date, words } from "../format";

/** Search the repository. Results only ever include what the signed-in person may read here. */
export function SearchPage() {
  const s = useSession();
  const [params, setParams] = useSearchParams();
  const q = params.get("q") ?? "";
  const docType = params.get("type") ?? "";
  const status = params.get("status") ?? "";
  const [text, setText] = useState(q);
  const inst = s.institution;

  const res = useLoad(
    () =>
      api<Items<Doc>>("/v1/documents", {
        query: { q, doc_type: docType, status, institution_id: inst?.id, limit: 100 },
      }),
    [q, docType, status, inst?.id],
  );

  const set = (k: string, v: string) => {
    const next = new URLSearchParams(params);
    if (v) next.set(k, v);
    else next.delete(k);
    setParams(next, { replace: true });
  };

  return (
    <div className="page">
      <PageHead eyebrow={inst ? inst.name : "All institutions in scope"} title="Find a record" />
      <form
        className="search"
        role="search"
        onSubmit={(e) => {
          e.preventDefault();
          set("q", text.trim());
        }}
      >
        <label className="sr" htmlFor="q">
          Search words
        </label>
        <input
          id="q"
          type="search"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="A pupil's name, a document number, words in the scan…"
        />
        <button className="btn btn-primary" type="submit">
          Search
        </button>
      </form>
      <div className="filters">
        <Field label="Type">
          {(id) => (
            <select id={id} value={docType} onChange={(e) => set("type", e.target.value)}>
              <option value="">Any type</option>
              {DOC_TYPES.map((t) => (
                <option key={t} value={t}>
                  {words(t)}
                </option>
              ))}
            </select>
          )}
        </Field>
        <Field label="Status">
          {(id) => (
            <select id={id} value={status} onChange={(e) => set("status", e.target.value)}>
              <option value="">Any status</option>
              <option value="captured">Captured (waiting to be indexed)</option>
              <option value="indexed">Indexed (waiting to be verified)</option>
              <option value="verified">Verified</option>
            </select>
          )}
        </Field>
      </div>
      <p className="muted small">
        You see only the records your role may open here. Restricted and safeguarding records ask why you need them before
        they open, and every opening is logged.
      </p>
      {res.loading && <Loading what="Searching" />}
      <ErrorNote error={res.error} />
      {res.data && res.data.items.length === 0 && (
        <Empty title={q ? `Nothing found for “${q}”` : "No records yet"}>
          {q ? "Try fewer words, or a document number such as DOC-NG-AB-2026-000012." : "Scanned and uploaded records appear here once captured."}
        </Empty>
      )}
      {res.data && res.data.items.length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <caption className="sr">Records found</caption>
            <thead>
              <tr>
                <th>Record</th>
                <th>Type</th>
                <th>Dated</th>
                <th>Classification</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {res.data.items.map((d) => (
                <tr key={d.id}>
                  <td>
                    <Link to={`/documents/${d.id}`} className="strong">
                      {d.title}
                    </Link>
                    <div className="mono muted small">{d.document_number}</div>
                  </td>
                  <td>{words(d.doc_type)}</td>
                  <td className="nowrap">{date(d.document_date)}</td>
                  <td>
                    <Chip level={d.classification} />
                  </td>
                  <td>
                    <Status value={d.status} />
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

import { api } from "../api";
import { useLoad } from "../session";
import { Empty, ErrorNote, Loading, PageHead } from "../components/ui";
import { CLASSIFICATIONS, type Items, type RepoRow } from "../types";

/** Counts only, never content: how the repository stands in every institution in scope. */
export function OversightPage() {
  const repo = useLoad(() => api<Items<RepoRow>>("/v1/records/repository"), []);
  const rows = repo.data?.items ?? [];
  const total = (k: keyof RepoRow) => rows.reduce((n, r) => n + (r[k] as number), 0);
  return (
    <div className="page">
      <PageHead eyebrow="State Government and State Archives" title="Repository overview" />
      {repo.loading && <Loading />}
      <ErrorNote error={repo.error} />
      {repo.data && rows.length === 0 && <Empty title="No institution has started its records yet" />}
      {rows.length > 0 && (
        <>
          <div className="stats">
            {(
              [
                ["Institutions started", rows.length],
                ["Documents", total("documents")],
                ["Verified", total("documents_verified")],
                ["Pages", total("pages")],
                ["Files out now", total("files_out")],
                ["Overdue", total("files_overdue")],
                ["Batches to check", total("batches_waiting_check")],
              ] as [string, number][]
            ).map(([l, n]) => (
              <div key={l} className="stat">
                <b>{n.toLocaleString("en-GB")}</b>
                <span>{l}</span>
              </div>
            ))}
          </div>
          <div className="table-wrap">
            <table className="table num">
              <thead>
                <tr>
                  <th>Institution</th>
                  <th>Series</th>
                  <th>Files open</th>
                  <th>Out / overdue</th>
                  <th>Digitised files</th>
                  <th>Documents</th>
                  <th>Verified</th>
                  {CLASSIFICATIONS.map((c) => (
                    <th key={c} className="cap">
                      {c}
                    </th>
                  ))}
                  <th>To check</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.institution_id}>
                    <td>{r.institution_name}</td>
                    <td>{r.series}</td>
                    <td>{r.files_open}</td>
                    <td className={r.files_overdue ? "overdue" : ""}>
                      {r.files_out} / {r.files_overdue}
                    </td>
                    <td>{r.files_digitised}</td>
                    <td>{r.documents}</td>
                    <td>
                      {r.documents_verified}
                      {r.documents > 0 && <span className="muted small"> ({Math.round((100 * r.documents_verified) / r.documents)}%)</span>}
                    </td>
                    {CLASSIFICATIONS.map((c) => (
                      <td key={c}>{r.by_classification[c] ?? 0}</td>
                    ))}
                    <td className={r.batches_waiting_check ? "warn" : ""}>{r.batches_waiting_check}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}

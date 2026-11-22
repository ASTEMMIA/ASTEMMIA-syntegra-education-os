import { useEffect, useId, useState, type ReactNode } from "react";
import { ApiError, api } from "../api";
import type { Classification } from "../types";
import { words } from "../format";

export function Chip({ level }: { level: Classification }) {
  return <span className={`chip cls-${level}`}>{level}</span>;
}

const STATUS_TONE: Record<string, string> = {
  verified: "good", accepted: "good", approved: "good", open: "info", indexed: "info",
  captured: "warn", pending: "warn", rejected: "bad", closed: "muted", disposed: "muted",
  transferred: "muted",
};

export function Status({ value }: { value: string }) {
  return <span className={`status tone-${STATUS_TONE[value] ?? "muted"}`}>{words(value)}</span>;
}

/** An error from the API, with the sentence that says what to do next. */
export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  const e = error instanceof ApiError ? error : null;
  return (
    <div className="note-error" role="alert">
      <strong>{e ? e.detail || e.title : String((error as Error).message ?? error)}</strong>
      {e?.nextAction && <span> {e.nextAction}</span>}
      {e?.fields.length ? (
        <ul>
          {e.fields.map((f) => (
            <li key={f.field}>
              {words(f.field.split(".").pop() ?? f.field)}: {f.message}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

export function Loading({ what = "Loading" }: { what?: string }) {
  return (
    <p className="loading" role="status">
      {what}…
    </p>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <strong>{title}</strong>
      {children && <div>{children}</div>}
    </div>
  );
}

export function PageHead({ eyebrow, title, children }: { eyebrow?: string; title: string; children?: ReactNode }) {
  return (
    <header className="page-head">
      <div>
        {eyebrow && <span className="eyebrow">{eyebrow}</span>}
        <h1>{title}</h1>
      </div>
      {children && <div className="page-actions">{children}</div>}
    </header>
  );
}

/** Run a change against the API: shows progress, keeps the error, calls `done` on success. */
export function useAction<A extends unknown[], R>(fn: (...a: A) => Promise<R>, done?: (r: R) => void) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const run = async (...a: A): Promise<R | undefined> => {
    setBusy(true);
    setError(null);
    try {
      const r = await fn(...a);
      done?.(r);
      return r;
    } catch (e) {
      setError(e);
      return undefined;
    } finally {
      setBusy(false);
    }
  };
  return { run, busy, error, clear: () => setError(null) };
}

export function Button({
  busy, children, kind = "primary", ...rest
}: { busy?: boolean; kind?: "primary" | "secondary" | "danger" | "quiet" } & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button className={`btn btn-${kind}`} disabled={busy || rest.disabled} aria-busy={busy} {...rest}>
      {busy ? "Working…" : children}
    </button>
  );
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: (id: string) => ReactNode }) {
  const id = useId();
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {children(id)}
      {hint && <span className="hint">{hint}</span>}
    </div>
  );
}

export function Dl({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="dl">
      {rows.map(([k, v]) => (
        <div key={k}>
          <dt>{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  );
}

interface UserHit {
  id: string;
  display_name: string;
  login: string;
}

/** Find a person by name or login (to assign a batch or hand over a file). */
export function UserPicker({ id, value, onChange }: { id: string; value: UserHit | null; onChange: (u: UserHit | null) => void }) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<UserHit[]>([]);
  useEffect(() => {
    if (value || q.trim().length < 2) {
      setHits([]);
      return;
    }
    const t = setTimeout(() => {
      api<{ items: UserHit[] }>("/v1/users", { query: { q } }).then((r) => setHits(r.items), () => setHits([]));
    }, 250);
    return () => clearTimeout(t);
  }, [q, value]);
  if (value) {
    return (
      <div className="picked">
        <span>
          {value.display_name} <span className="muted">({value.login})</span>
        </span>
        <button type="button" className="link" onClick={() => onChange(null)}>
          Change
        </button>
      </div>
    );
  }
  return (
    <div className="picker">
      <input id={id} value={q} onChange={(e) => setQ(e.target.value)} placeholder="Type a name or email" autoComplete="off" />
      {hits.length > 0 && (
        <ul className="picker-list" role="listbox">
          {hits.map((h) => (
            <li key={h.id}>
              <button type="button" role="option" aria-selected="false" onClick={() => onChange(h)}>
                {h.display_name} <span className="muted">{h.login}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Marks a made-up record (ADR-031) wherever a learner or teacher is shown. */
export function DemoBadge({ demo }: { demo?: boolean }) {
  return demo ? <span className="badge demo-badge">Demonstration data</span> : null;
}

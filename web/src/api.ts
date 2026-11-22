/**
 * The one way the app talks to the API (/v1, same origin).
 *
 * - The access token lives in memory only. The refresh token is kept in sessionStorage so a
 *   reload keeps you signed in for this tab; closing the tab forgets it. Each refresh rotates it,
 *   and the API ends the session if an old one is ever replayed.
 * - Every POST carries a fresh Idempotency-Key, so a retry after a dropped connection is safe.
 * - Errors arrive as RFC 9457 Problem Details and become ApiError, with the API's own "what to do
 *   next" sentence for the screen.
 */

export interface Tokens {
  access_token: string;
  refresh_token: string;
  expires_in: number;
  user_id: string;
  tenant_id: string;
}

export interface MfaChallenge {
  mfa_required: true;
  mfa_token: string;
  enrolment_required: boolean;
  next_action: string;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly title: string,
    readonly detail: string,
    readonly nextAction?: string,
    readonly fields: { field: string; message: string }[] = [],
    readonly extra: Record<string, unknown> = {},
  ) {
    super(detail || title);
  }
}

const REFRESH_KEY = "syn.refresh";
const SCOPE_KEY = "syn.scope";

let accessToken: string | null = null;
let refreshing: Promise<boolean> | null = null;
let onSignedOut: () => void = () => {};

function store(): Storage | null {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

export function setSession(t: Tokens): void {
  accessToken = t.access_token;
  try {
    store()?.setItem(REFRESH_KEY, t.refresh_token);
  } catch {
    /* private mode: stay signed in until reload */
  }
}

export function clearSession(): void {
  accessToken = null;
  try {
    store()?.removeItem(REFRESH_KEY);
    store()?.removeItem(SCOPE_KEY);
  } catch {
    /* ignore */
  }
}

export function hasSession(): boolean {
  return accessToken !== null || !!store()?.getItem(REFRESH_KEY);
}

export function whenSignedOut(fn: () => void): void {
  onSignedOut = fn;
}

export function getScope(): string | null {
  return store()?.getItem(SCOPE_KEY) ?? null;
}

export function setScope(id: string | null): void {
  try {
    if (id) store()?.setItem(SCOPE_KEY, id);
    else store()?.removeItem(SCOPE_KEY);
  } catch {
    /* ignore */
  }
}

export function newKey(): string {
  return crypto.randomUUID();
}

async function toError(r: Response): Promise<ApiError> {
  let body: Record<string, unknown> = {};
  try {
    body = await r.json();
  } catch {
    /* not JSON */
  }
  const s = (k: string) => (typeof body[k] === "string" ? (body[k] as string) : undefined);
  const fields = Array.isArray(body.errors)
    ? (body.errors as { field: string; message: string }[])
    : [];
  return new ApiError(
    r.status,
    s("title") ?? r.statusText ?? "Something went wrong",
    s("detail") ?? (r.status === 0 ? "The server could not be reached." : ""),
    s("next_action"),
    fields,
    body,
  );
}

async function refresh(): Promise<boolean> {
  const token = store()?.getItem(REFRESH_KEY);
  if (!token) return false;
  refreshing ??= (async () => {
    try {
      const r = await fetch("/v1/auth/refresh", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: token }),
      });
      if (!r.ok) return false;
      setSession((await r.json()) as Tokens);
      return true;
    } catch {
      return false;
    } finally {
      refreshing = null;
    }
  })();
  return refreshing;
}

export type Query = Record<string, string | number | boolean | null | undefined>;

interface Options {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  json?: unknown;
  form?: FormData;
  body?: string; // a raw body, e.g. a CSV file's text
  contentType?: string;
  query?: Query;
  key?: string; // reuse when retrying the same action
  raw?: boolean;
}

function url(path: string, query?: Query): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(query ?? {})) {
    if (v !== undefined && v !== null && v !== "") q.set(k, String(v));
  }
  const s = q.toString();
  return s ? `${path}?${s}` : path;
}

async function send(path: string, o: Options): Promise<Response> {
  const method = o.method ?? (o.json !== undefined || o.form || o.body !== undefined ? "POST" : "GET");
  const headers: Record<string, string> = { Accept: "application/json, */*" };
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
  const scope = getScope();
  if (scope) headers["X-Scope-Node-Id"] = scope;
  if (method === "POST") headers["Idempotency-Key"] = o.key ?? newKey();
  let body: BodyInit | undefined;
  if (o.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(o.json);
  } else if (o.form) {
    body = o.form;
  } else if (o.body !== undefined) {
    headers["Content-Type"] = o.contentType ?? "text/plain";
    body = o.body;
  }
  return fetch(url(path, o.query), { method, headers, body, credentials: "same-origin" });
}

/** Call the API. Signs you out (and says so) if the session has ended. */
export async function api<T = unknown>(path: string, o: Options = {}): Promise<T> {
  const key = o.key ?? newKey(); // the same key on the retry after a refresh
  let r: Response;
  try {
    if (!accessToken) await refresh();
    r = await send(path, { ...o, key });
    if (r.status === 401 && (await refresh())) r = await send(path, { ...o, key });
  } catch {
    throw new ApiError(0, "No connection", "The server could not be reached.", "Check your connection and try again.");
  }
  if (r.status === 401) {
    clearSession();
    onSignedOut();
  }
  if (!r.ok) throw await toError(r);
  if (o.raw) return r as unknown as T;
  if (r.status === 204) return undefined as T;
  return (await r.json()) as T;
}

/** A file from the API (a scan, a CSV) as a Blob, with its suggested name. */
export async function download(path: string, query?: Query): Promise<{ blob: Blob; name: string; sha?: string }> {
  const r = await api<Response>(path, { query, raw: true });
  const cd = r.headers.get("Content-Disposition") ?? "";
  const name = /filename="([^"]+)"/.exec(cd)?.[1] ?? "download";
  return { blob: await r.blob(), name, sha: r.headers.get("X-Content-SHA256") ?? undefined };
}

/** Save a Blob as a file (the browser's own download). */
export function saveBlob(blob: Blob, name: string): void {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 10_000);
}

// --------------------------------------------------------------------------- sign-in

export async function devLogin(tenant: string, login: string): Promise<Tokens> {
  const r = await fetch("/v1/auth/dev-login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tenant_code: tenant.trim().toUpperCase(), login: login.trim() }),
  });
  if (r.status === 404 || r.status === 405) {
    throw new ApiError(404, "Not available", "Development sign-in is switched off here.", "Use your organisation's sign-in.");
  }
  if (!r.ok) throw await toError(r);
  return (await r.json()) as Tokens;
}

export interface Provider {
  key: string;
  name: string;
}

export async function providers(tenant: string): Promise<Provider[]> {
  const r = await fetch(url("/v1/auth/providers", { tenant: tenant.trim().toUpperCase() }));
  if (!r.ok) return [];
  const body = (await r.json()) as { providers: Provider[] };
  return body.providers ?? [];
}

export async function finishSso(params: URLSearchParams): Promise<Tokens | MfaChallenge> {
  const r = await fetch(url("/v1/auth/oidc/callback", Object.fromEntries(params)));
  if (!r.ok) throw await toError(r);
  return (await r.json()) as Tokens | MfaChallenge;
}

export async function mfa(path: "verify" | "enrolment" | "enrolment/confirm", body: unknown): Promise<any> {
  const r = await fetch(`/v1/auth/mfa/${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw await toError(r);
  return r.json();
}

/** Parents: ask for a one-time code by email. The answer is the same whether or not the
 * address has an account, so it says nothing about who is registered. */
export async function emailCode(tenant: string, email: string): Promise<void> {
  const r = await fetch("/v1/auth/email-code", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tenant_code: tenant.trim().toUpperCase(), email: email.trim() }),
  });
  if (!r.ok) throw await toError(r);
}

export async function verifyEmailCode(tenant: string, email: string, code: string): Promise<Tokens> {
  const r = await fetch("/v1/auth/email-code/verify", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tenant_code: tenant.trim().toUpperCase(), email: email.trim(), code: code.trim() }),
  });
  if (!r.ok) throw await toError(r);
  return (await r.json()) as Tokens;
}

export async function signOut(): Promise<void> {
  try {
    if (accessToken) await send("/v1/auth/logout", { method: "POST" });
  } finally {
    clearSession();
  }
}

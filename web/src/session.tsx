import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api, clearSession, getScope, hasSession, setScope, signOut as apiSignOut, whenSignedOut, type Tokens, setSession } from "./api";
import type { Institution, Items, Me } from "./types";

const INST_KEY = "syn.institution";

interface Session {
  status: "loading" | "signedout" | "ready";
  me: Me | null;
  institutions: Institution[];
  institution: Institution | null;
  ended: boolean; // the session ended by itself (not by signing out)
  can: (...permissions: string[]) => boolean;
  cell: (permission: string) => string | undefined;
  signedIn: (t: Tokens) => Promise<void>;
  signOut: () => Promise<void>;
  switchScope: (scopeId: string) => Promise<void>;
  chooseInstitution: (id: string) => void;
}

const Ctx = createContext<Session | null>(null);

function remembered(): string | null {
  try {
    return sessionStorage.getItem(INST_KEY);
  } catch {
    return null;
  }
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<Session["status"]>(hasSession() ? "loading" : "signedout");
  const [me, setMe] = useState<Me | null>(null);
  const [institutions, setInstitutions] = useState<Institution[]>([]);
  const [instId, setInstId] = useState<string | null>(remembered());
  const [ended, setEnded] = useState(false);
  const loading = useRef(false);

  const load = useCallback(async () => {
    if (loading.current) return;
    loading.current = true;
    try {
      const m = await api<Me>("/v1/me");
      if (!getScope()) setScope(m.active_scope_id);
      const list = await api<Items<Institution>>("/v1/institutions", { query: { limit: 200 } });
      setMe(m);
      setInstitutions(list.items);
      setInstId((cur) => {
        const keep = list.items.find((i) => i.id === cur);
        const here = list.items.find((i) => i.scope_node_id === m.active_scope_id);
        return (keep ?? here ?? (list.items.length === 1 ? list.items[0] : null))?.id ?? null;
      });
      setStatus("ready");
    } catch {
      clearSession();
      setStatus("signedout");
    } finally {
      loading.current = false;
    }
  }, []);

  useEffect(() => {
    whenSignedOut(() => {
      setEnded(true);
      setStatus("signedout");
      setMe(null);
    });
    if (hasSession()) void load();
  }, [load]);

  useEffect(() => {
    try {
      if (instId) sessionStorage.setItem(INST_KEY, instId);
    } catch {
      /* ignore */
    }
  }, [instId]);

  const value = useMemo<Session>(() => {
    const perms = me?.permissions_here ?? {};
    return {
      status,
      me,
      institutions,
      institution: institutions.find((i) => i.id === instId) ?? null,
      ended,
      can: (...ps) => ps.some((p) => p in perms),
      cell: (p) => perms[p],
      signedIn: async (t) => {
        setSession(t);
        setScope(null);
        setEnded(false);
        setStatus("loading");
        await load();
      },
      signOut: async () => {
        await apiSignOut();
        setMe(null);
        setStatus("signedout");
      },
      switchScope: async (scopeId) => {
        setScope(scopeId);
        setInstId(null);
        setStatus("loading");
        await load();
      },
      chooseInstitution: (id) => setInstId(id),
    };
  }, [status, me, institutions, instId, ended, load]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useSession(): Session {
  const s = useContext(Ctx);
  if (!s) throw new Error("useSession outside SessionProvider");
  return s;
}

// --------------------------------------------------------------------------- names

const names = new Map<string, string>();
const pending = new Set<string>();
let flush: ReturnType<typeof setTimeout> | null = null;
const listeners = new Set<() => void>();

function request(ids: string[]): void {
  for (const id of ids) if (id && !names.has(id)) pending.add(id);
  if (!pending.size || flush) return;
  flush = setTimeout(async () => {
    const batch = [...pending].slice(0, 200);
    batch.forEach((id) => pending.delete(id));
    flush = null;
    try {
      const got = await api<Record<string, string>>("/v1/users/names", { query: { ids: batch.join(",") } });
      for (const id of batch) names.set(id, got[id] ?? "Unknown user");
    } catch {
      for (const id of batch) names.set(id, "Unknown user");
    }
    listeners.forEach((l) => l());
  }, 20);
}

/** Display names for user ids, fetched in one call per screen. */
export function useNames(ids: (string | null | undefined)[]): (id: string | null | undefined) => string {
  const [, tick] = useState(0);
  const key = ids.filter(Boolean).join(",");
  useEffect(() => {
    const l = () => tick((n) => n + 1);
    listeners.add(l);
    request(key ? key.split(",") : []);
    return () => {
      listeners.delete(l);
    };
  }, [key]);
  return (id) => (id ? names.get(id) ?? "…" : "—");
}

// --------------------------------------------------------------------------- loading data

export interface Loaded<T> {
  data: T | null;
  error: Error | null;
  loading: boolean;
  reload: () => void;
}

/** Load something for a screen; `reload()` after a change. Stale answers are ignored. */
export function useLoad<T>(fn: () => Promise<T>, deps: unknown[]): Loaded<T> {
  const [state, setState] = useState<{ data: T | null; error: Error | null; loading: boolean }>({
    data: null,
    error: null,
    loading: true,
  });
  const [n, setN] = useState(0);
  useEffect(() => {
    let live = true;
    setState((s) => ({ ...s, loading: true, error: null }));
    fn().then(
      (data) => live && setState({ data, error: null, loading: false }),
      (error: Error) => live && setState({ data: null, error, loading: false }),
    );
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, n]);
  return { ...state, reload: () => setN((x) => x + 1) };
}

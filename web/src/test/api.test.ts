import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, api, clearSession, setScope, setSession } from "../api";
import { date, words } from "../format";

const tokens = (n: number) => ({ access_token: `a${n}`, refresh_token: `r${n}`, expires_in: 600, user_id: "u", tenant_id: "t" });

function reply(status: number, body: unknown, headers: Record<string, string> = {}) {
  return new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...headers } });
}

describe("api client", () => {
  let calls: { url: string; init: RequestInit }[];
  beforeEach(() => {
    calls = [];
    clearSession();
  });
  afterEach(() => vi.unstubAllGlobals());

  it("sends the token, the scope and an idempotency key on POST", async () => {
    setSession(tokens(1));
    setScope("scope-1");
    vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit) => (calls.push({ url, init }), reply(200, { ok: true }))));
    await api("/v1/documents/x/verify", { method: "POST" });
    const h = calls[0]!.init.headers as Record<string, string>;
    expect(h.Authorization).toBe("Bearer a1");
    expect(h["X-Scope-Node-Id"]).toBe("scope-1");
    expect(h["Idempotency-Key"]).toMatch(/[0-9a-f-]{36}/);
  });

  it("refreshes once on 401 and retries with the same idempotency key", async () => {
    setSession(tokens(1));
    let first = true;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init: RequestInit) => {
        calls.push({ url, init });
        if (url === "/v1/auth/refresh") return reply(200, tokens(2));
        if (first) {
          first = false;
          return reply(401, { title: "Unauthenticated", detail: "expired" });
        }
        return reply(201, { id: "d1" });
      }),
    );
    expect(await api("/v1/batches/b/finish", { method: "POST" })).toEqual({ id: "d1" });
    const posts = calls.filter((c) => c.url.startsWith("/v1/batches"));
    const key = (c: (typeof posts)[0]) => (c.init.headers as Record<string, string>)["Idempotency-Key"];
    expect(posts).toHaveLength(2);
    expect(key(posts[0]!)).toBe(key(posts[1]!));
    expect((posts[1]!.init.headers as Record<string, string>).Authorization).toBe("Bearer a2");
    expect(JSON.parse(String(calls.find((c) => c.url === "/v1/auth/refresh")!.init.body))).toEqual({ refresh_token: "r1" });
  });

  it("turns Problem Details into an error with the next step", async () => {
    setSession(tokens(1));
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        reply(409, { title: "Conflict", detail: "the file is out", next_action: "Return it first.", status: 409 }, { "Content-Type": "application/problem+json" }),
      ),
    );
    const e = await api("/v1/files/f/close", { method: "POST" }).catch((x: ApiError) => x) as ApiError;
    expect(e).toBeInstanceOf(ApiError);
    expect([e.status, e.detail, e.nextAction]).toEqual([409, "the file is out", "Return it first."]);
  });

  it("says so when the server cannot be reached", async () => {
    setSession(tokens(1));
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("Failed to fetch"))));
    const e = await api("/v1/me").catch((x: ApiError) => x) as ApiError;
    expect([e.status, e.nextAction]).toEqual([0, "Check your connection and try again."]);
  });
});

describe("format", () => {
  it("shows dates the Nigerian way and codes as words", () => {
    expect(date("2022-09-12")).toBe("12 Sept 2022");
    expect(date(null)).toBe("—");
    expect(words("transfer_archives")).toBe("Transfer archives");
  });
});

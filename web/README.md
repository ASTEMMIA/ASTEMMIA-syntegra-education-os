# Records web app (ADR-028)

React 19 + TypeScript + Vite. Talks only to the `/v1` API on the same origin, served at `/app`.

```bash
npm ci
```

```bash
npm run dev        # http://localhost:5173/app/, proxies /v1 to an API on 127.0.0.1:8000
```

```bash
npx tsc -p . && npx vitest run && npx vite build   # what CI runs
```

```bash
web/e2e/run.sh     # from the repository root: seed, serve, walk through every role in Chromium
```

The API serves `web/dist` at `/app` when it exists (`SYNTEGRA_WEB_DIR`). For single sign-on, set
`SYNTEGRA_OIDC_REDIRECT_URI` to `https://<host>/app/sso/callback` and register that URL with the
identity provider.

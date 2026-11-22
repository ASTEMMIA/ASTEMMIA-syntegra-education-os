# ADR-028: The web front end, records first

**Status:** Accepted 2026-11-22. Built for document and records management (ADR-027); the other
modules add their screens to the same app.

## Context
The platform had an API and no screens. Schools, LGEAs, ASUBEB, the Ministry and the State
Archives need to use the records system from a browser, often on slow connections and phones.
The CEDA sets WCAG 2.2 AA, low-bandwidth operation and the §10.4 API contract (Problem Details,
Idempotency-Key, scope header). Passkeys (ADR-016) also wait for a browser front end.

## Decision
1. **A single-page app in `web/`: React 19, TypeScript (strict), Vite 7, React Router 7.** It talks
   only to the existing `/v1` API, so every rule (scope, classification, four-eyes, purpose,
   idempotency) stays in one place, the server. Every dependency is pinned to an exact version,
   has no known vulnerability, and was published at least two weeks before it was adopted.
2. **Same origin, served at `/app`.** The API serves the built files when `SYNTEGRA_WEB_DIR`
   (default `web/dist`) exists; production puts the same files behind CloudFront. No CORS, no
   third-party hosts. Every page carries a strict Content Security Policy (`default-src 'self'`,
   no inline script, `frame-ancestors 'none'`); scans open as `blob:` URLs only. Hashed assets are
   cached for a year; `index.html` is never cached.
3. **Tokens.** The access token is held in memory only. The refresh token is kept in
   `sessionStorage` so a reload keeps the tab signed in; closing the tab forgets it. It rotates on
   every use and the API ends the session if an old one is replayed (ADR-007). A 401 triggers one
   refresh and one retry with the **same** Idempotency-Key, so a retried change is never applied
   twice.
4. **Sign-in.** Single sign-on works by pointing the identity provider's redirect at
   `/app/sso/callback` (set `SYNTEGRA_OIDC_REDIRECT_URI` to it): the page hands `state` and `code`
   to the existing JSON callback and handles the MFA challenge, including first-time
   authenticator set-up and recovery codes. The development sign-in form works only where the API
   allows it (dev/test).
5. **Screens follow permissions.** The app reads `/v1/me` (now with display name, role names and
   scope names) and shows only what the person may use; the server still checks everything.
   Two small read endpoints were added for the screens: `GET /v1/users/names` (display names for
   ids) and `GET /v1/users?q=` (finding a person to assign a batch or hand a file to; records and
   user administrators only). `GET /v1/batches/{id}/documents` lists what was captured into a
   batch (numbers and scan titles, never content) for its operator and for records staff.
6. **Low bandwidth and accessibility.** System fonts, no images in lists, about 100 KB of
   compressed script, text labels on every classification (never colour alone), visible focus,
   keyboard-operable controls, and layouts that work at 390 px wide without sideways scrolling.
   Light and dark themes follow the device.

## The records screens
| Screen | Who | What |
|---|---|---|
| Search | everyone | Full-text and metadata search, only over what the person may read here |
| Document | readers | The scan, its details, how it was captured and verified, versions with SHA-256, download. Restricted and Special records ask for a reason first |
| | records officers | Confirm details (or accept the suggestion), file it and give the folio, verify (a second person), add a version, raise the classification |
| | State oversight | Lower a classification; who opened the record, when and why |
| Files | file managers | The file register, overdue files, the file list as CSV; a file's cover, where it is, issue, pass, send out, return, close, new volume, legal hold, mark digitised, propose the paper's disposal |
| File plan | records officers | Adopt the primary, secondary or tertiary model plan, add local series, open a file under a series |
| My scanning | capture operators | Batches assigned to them; drag and drop scans in order; finish; rescan what the check sent back |
| Batches | records officers | Open a batch for in-house staff or a contractor; check a sample and accept or send back |
| Paper disposal | approvers | Files due for a decision; approve or refuse with the certificate number |
| Overview | State Government, State Archives | Counts for every institution in scope, never content |

## Policy change found while building it
A scan captured loose (without a paper file) is held as Confidential, the safe default. Before,
filing it at indexing could only raise its classification, so minutes filed in an Internal file
stayed Confidential and only the State could lower them. Now **the first filing of a loose scan
takes the file's classification**; after that, classification is only raised by records staff,
and lowered only by State oversight. A level the operator chose explicitly above the default is
never lowered by filing.

## Testing
* `web/src/test`: the API client (token, scope and idempotency headers; one refresh and a retry
  with the same key; Problem Details; no connection) and the error display.
* `web/e2e/run.sh`: seeds the test school, starts the API serving the built app, and walks through
  it in Chromium as a records officer, a contractor's operator, a second records officer, a class
  teacher, the head teacher and the State Archives, on desktop and phone width. CI runs it and
  keeps the screenshots.
* `tests/integration/test_web_app.py`: `/v1/me` names, user lookup, `/app` serving and its headers.

## Consequences
* New CI jobs: `web` (types, unit tests, build, audit) and `web-e2e`. The container image builds
  the app with Node.js 22 from RHEL AppStream and serves it at `/app`.
* **Open:** passkeys (now possible), offline capture for field use (§10.5), translations, a QR
  code for authenticator set-up (needs a small library), OCR at upload (Textract), and screens for
  the other modules.

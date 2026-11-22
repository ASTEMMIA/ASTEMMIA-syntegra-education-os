#!/bin/bash
# Seed a database, start the API serving the built app, walk through it in Chromium, stop.
#   web/e2e/run.sh            (from the repository root; needs uv, node and Playwright)
set -euo pipefail
cd "$(dirname "$0")/../.."
WORK=$(mktemp -d)
PORT=${PORT:-8077}
SYNTEGRA_E2E_SEED_TO=$WORK/e2e.db uv run pytest -q tests/integration/test_e2e_seed.py >/dev/null
(cd web && npx vite build >/dev/null)
SYNTEGRA_ENV=dev SYNTEGRA_DATABASE_URL=sqlite+aiosqlite:///$WORK/e2e.db SYNTEGRA_DOCUMENT_DIR=$WORK/blobs \
  SYNTEGRA_WEB_DIR=web/dist uv run uvicorn syntegra.web.app:create_app --factory --port "$PORT" > "$WORK/server.log" 2>&1 &
SERVER=$!
trap 'kill $SERVER 2>/dev/null || true' EXIT
for _ in $(seq 60); do curl -sf "http://127.0.0.1:$PORT/app/" >/dev/null && break; sleep 0.5; done
BASE=http://127.0.0.1:$PORT SCANS=web/e2e/fixtures SHOTS=${SHOTS:-$WORK/shots}/records node web/e2e/records.e2e.mjs || { tail -40 "$WORK/server.log"; exit 1; }
BASE=http://127.0.0.1:$PORT FIXTURES=web/e2e/fixtures SHOTS=${SHOTS:-$WORK/shots}/school node web/e2e/school.e2e.mjs || { tail -40 "$WORK/server.log"; exit 1; }

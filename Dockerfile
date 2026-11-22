# syntax=docker/dockerfile:1.7
# Enterprise runtime: Red Hat Universal Base Image 10 (RHEL 10.x userspace) with Red Hat's
# supported Python 3.14 from RHEL AppStream. UBI images are freely redistributable and are
# covered by Red Hat support when run on RHEL or OpenShift (ADR-006).
ARG UBI=registry.access.redhat.com/ubi10/ubi-minimal:10.2
# The browser app (ADR-028), built with Node.js 22 from RHEL AppStream.
FROM ${UBI} AS web
RUN microdnf install -y --nodocs nodejs npm && microdnf clean all
WORKDIR /web
COPY web/package.json web/package-lock.json web/.npmrc ./
RUN npm ci --no-audit --no-fund
COPY web/index.html web/tsconfig.json web/vite.config.ts ./
COPY web/src ./src
RUN npx tsc -p . && npx vite build

FROM ${UBI} AS build
RUN microdnf install -y --nodocs python3.14 python3.14-devel gcc \
    && microdnf clean all
COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /usr/local/bin/uv
ENV UV_PYTHON=/usr/bin/python3.14 UV_PYTHON_DOWNLOADS=never UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
RUN uv sync --frozen --no-dev

FROM ${UBI}
RUN microdnf install -y --nodocs python3.14 shadow-utils \
    && microdnf clean all \
    && useradd --system --uid 10001 --no-create-home syntegra
WORKDIR /app
COPY --from=build --chown=10001:0 /app /app
COPY --from=web --chown=10001:0 /web/dist /app/web/dist
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 SYNTEGRA_ENV=prod SYNTEGRA_DEV_AUTH=false
USER 10001
EXPOSE 8000
HEALTHCHECK CMD python3.14 -c "import urllib.request,sys;urllib.request.urlopen('http://127.0.0.1:8000/healthz',timeout=2)" || exit 1
CMD ["uvicorn", "syntegra.web.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "4", "--loop", "uvloop"]

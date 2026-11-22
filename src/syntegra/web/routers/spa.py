"""The browser app (ADR-028): the built files of ``web/`` served at ``/app``.

Same origin as the API, so no CORS and no third-party hosts. In production CloudFront serves the
same files from S3 with the same headers; this router covers dev, test and single-host installs.
Any path that is not a file returns ``index.html`` so the app's own routes work on reload.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, RedirectResponse

# Scans open as blob: URLs (images, and PDFs in an iframe); everything else is this origin only.
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; "
    "frame-src blob:; object-src 'none'; connect-src 'self'; font-src 'self'; "
    "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)
HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


def build_router(root: Path) -> APIRouter:
    router = APIRouter(include_in_schema=False)
    root = root.resolve()

    @router.get("/app")
    async def app_root() -> RedirectResponse:
        return RedirectResponse("/app/", status_code=308)

    @router.get("/app/{path:path}")
    async def app_file(path: str) -> FileResponse:
        target = (root / path).resolve()
        if path and target.is_file() and target.is_relative_to(root):
            immutable = target.parent.name == "assets"  # hashed names from the build
            cache = "public, max-age=31536000, immutable" if immutable else "no-cache"
            return FileResponse(target, headers=HEADERS | {"Cache-Control": cache})
        if path.startswith("assets/"):
            raise HTTPException(status_code=404)
        return FileResponse(root / "index.html", headers=HEADERS | {"Cache-Control": "no-cache"})

    return router

"""The Government Monitoring Dashboard (ADR-031)."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from syntegra.monitoring import api as mon
from syntegra.web.deps import UowDep
from syntegra.web.routers.common import dump

router = APIRouter(prefix="/v1/monitoring", tags=["monitoring"])


@router.get("/overview", summary="Institutions, learners and teachers beneath the active scope")
async def overview(uow: UowDep) -> JSONResponse:
    await uow.require("monitoring:view")
    return JSONResponse(dump(await mon.overview(uow.session)))

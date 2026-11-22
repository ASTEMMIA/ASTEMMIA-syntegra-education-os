"""Seed a dev database for the browser walk-through (web/e2e). Runs only when asked:

    SYNTEGRA_E2E_SEED_TO=/tmp/e2e.db uv run pytest tests/integration/test_e2e_seed.py

The test school, its file plan and one user per records role (see test_records._setup), plus a
pupil's personal file and the 2025 PTA minutes file.
"""

import os
import shutil
from datetime import date

import pytest

from syntegra.records import api as rec
from tests.integration.test_records import _setup
from tests.integration.test_staff import as_

TARGET = os.environ.get("SYNTEGRA_E2E_SEED_TO")


@pytest.mark.skipif(not TARGET, reason="set SYNTEGRA_E2E_SEED_TO to seed a browser run")
async def test_seed_for_browser_run(sm, world, engine):
    a = await _setup(sm, world)
    async with sm() as s, s.begin(), as_(a, a.rec, a.school):
        await rec.open_file(
            s,
            a.series["LRN/2"].id,
            rec.FileIn(
                "Eze, Chidi Emmanuel",
                "Registry, cabinet 2",
                person_id=a.pupil,
                opened_on=date(2022, 9, 12),
            ),
        )
        await rec.open_file(
            s,
            a.series["GOV/2"].id,
            rec.FileIn("PTA and SBMC minutes 2025", "Head teacher's office", year=2025),
        )
    await engine.dispose()
    shutil.copy(str(engine.url.database), str(TARGET))

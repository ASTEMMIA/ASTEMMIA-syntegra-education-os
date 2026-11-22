"""Seed a dev database for the browser walk-throughs (web/e2e). Runs only when asked:

    SYNTEGRA_E2E_SEED_TO=/tmp/e2e.db uv run pytest tests/integration/test_e2e_seed.py

One Abia test world for both walk-throughs:

* classes (test_learners._setup): the national curriculum adopted, the 2026/2027 session, two
  teachers posted to TEST Central School Aba, Primary 4A and 4B, a head teacher and registrars;
* records (as test_records._setup): a records officer and a second one, a contractor's scanner,
  a class teacher (linked to Ada Okeke's staff record), the State Archives, the primary file plan,
  a pupil's personal file and the 2025 PTA minutes file.
"""

import os
import shutil
from datetime import date

import pytest
from sqlalchemy import update

from syntegra.iam.models import AppUser
from syntegra.records import api as rec
from tests.integration.test_learners import _setup, _user
from tests.integration.test_staff import _person, as_

TARGET = os.environ.get("SYNTEGRA_E2E_SEED_TO")


@pytest.mark.skipif(not TARGET, reason="set SYNTEGRA_E2E_SEED_TO to seed a browser run")
async def test_seed_for_browser_run(sm, world, engine):
    a = await _setup(sm, world)
    root = a.tenant.root_scope_node_id
    async with sm() as s, s.begin(), as_(a, scope=root):
        a.rec = await _user(s, a, "records@central.example", "REC", a.school)
        await _user(s, a, "records2@central.example", "REC", a.school)
        await _user(s, a, "admin@central.example", "IADM", a.school)
        teacher = await _user(s, a, "teacher@central.example", "TCH", a.school)
        await s.execute(
            update(AppUser).where(AppUser.id == teacher).values(person_id=a.t1.person_id)
        )
        await _user(s, a, "scanner@contractor.example", "CAPT", a.school)
        await _user(s, a, "other@contractor.example", "CAPT", a.school)
        await _user(s, a, "archives@abia.example", "ARCH", root)
    async with sm() as s, s.begin(), as_(a, a.rec, a.school):
        series = {x.code: x for x in await rec.adopt_plan(s, a.aba.id)}
        pupil = await _person(s, "Chidi", "Eze", born=date(2016, 5, 1), sex="M")
        await rec.open_file(
            s,
            series["LRN/2"].id,
            rec.FileIn(
                "Eze, Chidi Emmanuel",
                "Registry, cabinet 2",
                person_id=pupil,
                opened_on=date(2022, 9, 12),
            ),
        )
        await rec.open_file(
            s,
            series["GOV/2"].id,
            rec.FileIn("PTA and SBMC minutes 2025", "Head teacher's office", year=2025),
        )
    # Made-up pilot institutions with demonstration learners, for the monitoring walk-through.
    from syntegra.pilot import api as pilot
    from tests.integration.test_pilot import PILOT
    from tests.integration.test_staff import CRYPTO

    async with sm() as s, s.begin():
        pilots = await pilot.load_institutions(s, a.tenant.id, PILOT)
        await pilot.load_demo(s, CRYPTO, a.tenant.id, pilots, learners_total=60)
    await engine.dispose()
    shutil.copy(str(engine.url.database), str(TARGET))

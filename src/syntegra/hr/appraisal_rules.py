"""Staff appraisal rules (ADR-025). Framework-free.

Modelled on Nigerian public-service practice: the Annual Performance Evaluation Report (APER)
and the newer Performance Management System. A staff member is rated on weighted criteria, from
1 (Poor) to 5 (Outstanding), by an appraiser (for a teacher, normally the head teacher), and a
different officer countersigns. Lesson observations during the year are evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Any

RATING_LABEL = {1: "Poor", 2: "Fair", 3: "Good", 4: "Very good", 5: "Outstanding"}
# Overall bands on the weighted score (percent).
BANDS = ((Decimal(85), "Outstanding"), (Decimal(70), "Very good"), (Decimal(55), "Good"),
         (Decimal(40), "Fair"), (Decimal(0), "Poor"))  # fmt: skip


class Recommendation(StrEnum):
    NONE = "none"
    PROMOTION = "promotion"
    COMMENDATION = "commendation"
    TRAINING = "training"
    IMPROVEMENT_PLAN = "improvement_plan"


@dataclass(frozen=True, slots=True)
class Criterion:
    code: str
    name: str
    weight: int  # weights of a template add up to 100


TEACHING_CRITERIA = (
    Criterion("subject_knowledge", "Knowledge of subject", 15),
    Criterion("lesson_planning", "Lesson planning and preparation (lesson notes)", 15),
    Criterion("delivery", "Teaching methods and classroom delivery", 20),
    Criterion("classroom_management", "Classroom management and learner discipline", 10),
    Criterion("assessment", "Assessment, marking and record keeping", 10),
    Criterion("learner_progress", "Learner progress and outcomes", 10),
    Criterion("punctuality", "Punctuality and attendance", 10),
    Criterion("conduct", "Professional conduct and teamwork", 5),
    Criterion("development", "Professional development (CPD)", 5),
)
NON_TEACHING_CRITERIA = (
    Criterion("job_knowledge", "Knowledge of the job", 20),
    Criterion("quality", "Quality and accuracy of work", 20),
    Criterion("output", "Output and timeliness", 15),
    Criterion("initiative", "Initiative and problem solving", 10),
    Criterion("punctuality", "Punctuality and attendance", 10),
    Criterion("relationships", "Working relationships and service to others", 10),
    Criterion("conduct", "Professional conduct and integrity", 10),
    Criterion("development", "Self-development and training", 5),
)
# A lesson observation rates the classroom part of the teaching template.
OBSERVATION_CRITERIA = ("lesson_planning", "delivery", "classroom_management", "assessment")


def default_template() -> dict[str, list[dict[str, Any]]]:
    return {
        "teaching": _as_dicts(TEACHING_CRITERIA),
        "non_teaching": _as_dicts(NON_TEACHING_CRITERIA),
    }


def _as_dicts(criteria: tuple[Criterion, ...]) -> list[dict[str, Any]]:
    return [{"code": c.code, "name": c.name, "weight": c.weight} for c in criteria]


def check_template(template: dict[str, Any]) -> list[str]:
    errors = []
    for category in ("teaching", "non_teaching"):
        items = template.get(category)
        if not isinstance(items, list) or not items:
            errors.append(f"the {category} criteria are missing")
            continue
        codes = [i.get("code") for i in items]
        if len(set(codes)) != len(codes) or not all(codes):
            errors.append(f"the {category} criteria need unique codes")
        weights = [i.get("weight") for i in items]
        if not all(isinstance(w, int) and w > 0 for w in weights) or sum(weights) != 100:
            errors.append(f"the {category} weights must be positive and add up to 100")
    return errors


def score(
    criteria: list[dict[str, Any]], ratings: dict[str, dict[str, Any]]
) -> tuple[Decimal, str, list[str]]:
    """(weighted score in percent, band, problems). Every criterion must be rated 1-5; a rating
    of 1 or 5 needs a comment saying why."""
    problems: list[str] = []
    total = Decimal(0)
    for c in criteria:
        r = ratings.get(c["code"]) or {}
        value = r.get("rating")
        if not isinstance(value, int) or value not in RATING_LABEL:
            problems.append(f"rate '{c['name']}' from 1 to 5")
            continue
        if value in (1, 5) and not str(r.get("comment") or "").strip():
            problems.append(f"say why '{c['name']}' is rated {RATING_LABEL[value]}")
        total += Decimal(c["weight"]) * Decimal(value) / Decimal(5)
    unknown = set(ratings) - {c["code"] for c in criteria}
    if unknown:
        problems.append("unknown criteria: " + ", ".join(sorted(unknown)))
    pct = total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return pct, band(pct), problems


def band(pct: Decimal) -> str:
    return next(label for floor, label in BANDS if pct >= floor)

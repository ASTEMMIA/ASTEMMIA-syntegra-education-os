"""Adult and non-formal education: the Adult level and the adult learning centre type (ADR-020).

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-22 08:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

S = "org"
TYPES_OLD = (
    "'school','university','polytechnic','college_of_education','technical_college',"
    "'college_of_health','other_tertiary'"
)
TYPES_NEW = TYPES_OLD + ",'adult_learning_centre'"
LEVELS_OLD = "'eccde','primary','jss','sss','tvet','nce','nd','hnd','degree','postgraduate'"
LEVELS_NEW = LEVELS_OLD + ",'adult'"


def _swap(types: str, levels: str) -> None:
    op.drop_constraint(
        op.f("ck_institution_institution_type"), "institution", schema=S, type_="check"
    )
    op.create_check_constraint(
        "institution_type", "institution", f"institution_type IN ({types})", schema=S
    )
    op.drop_constraint(
        op.f("ck_institution_level_level"), "institution_level", schema=S, type_="check"
    )
    op.create_check_constraint("level", "institution_level", f"level IN ({levels})", schema=S)


def upgrade() -> None:
    _swap(TYPES_NEW, LEVELS_NEW)


def downgrade() -> None:
    # Refuse rather than delete registered institutions.
    n = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT COUNT(*) FROM org.institution WHERE institution_type = 'adult_learning_centre'"
            )
        )
        .scalar()
    )
    if n:
        raise RuntimeError(f"{n} adult learning centres are registered: remove them first")
    _swap(TYPES_OLD, LEVELS_OLD)

"""Institution register, kindergarten to university (ADR-012).

* rename institution.lga_scope_id -> parent_scope_id (a tertiary institution's parent is its
  body, e.g. the Ministry, not an LGEA)
* add location / location_key (the LGA it is in) and institution_type
* institution_level: tertiary levels (tvet, nce, nd, hnd, degree, postgraduate)

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

S = "org"
TYPES = (
    "'school','university','polytechnic','college_of_education','technical_college',"
    "'college_of_health','other_tertiary'"
)
LEVELS_NEW = "'eccde','primary','jss','sss','tvet','nce','nd','hnd','degree','postgraduate'"
LEVELS_OLD = "'eccde','primary','jss','sss'"


def upgrade() -> None:
    op.add_column("institution", sa.Column("location", sa.String(64), nullable=True), schema=S)
    op.add_column("institution", sa.Column("location_key", sa.String(64), nullable=True), schema=S)
    op.add_column(
        "institution",
        sa.Column("institution_type", sa.String(24), nullable=False, server_default="school"),
        schema=S,
    )
    op.drop_constraint(
        "uq_institution_tenant_id_lga_scope_id_name_key", "institution", schema=S, type_="unique"
    )
    op.drop_index("ix_institution_tenant_id_lga_scope_id", table_name="institution", schema=S)
    op.alter_column(
        "institution",
        "lga_scope_id",
        new_column_name="parent_scope_id",
        existing_type=sa.BINARY(16)
        if op.get_context().dialect.name == "mysql"
        else sa.LargeBinary(16),
        existing_nullable=False,
        schema=S,
    )
    # Existing rows are schools under an LGEA: their location is the LGEA's name without " LGEA".
    op.execute(
        "UPDATE org.institution i JOIN gov.scope_node n ON n.id = i.parent_scope_id "
        "SET i.location = TRIM(TRAILING ' LGEA' FROM n.name), "
        "    i.location_key = LOWER(TRIM(TRAILING ' LGEA' FROM n.name))"
    )
    op.alter_column(
        "institution", "location", existing_type=sa.String(64), nullable=False, schema=S
    )
    op.alter_column(
        "institution", "location_key", existing_type=sa.String(64), nullable=False, schema=S
    )
    op.alter_column(
        "institution",
        "institution_type",
        existing_type=sa.String(24),
        server_default=None,
        schema=S,
    )
    op.create_unique_constraint(
        "uq_institution_tenant_id_parent_scope_id_location_key_name_key",
        "institution",
        ["tenant_id", "parent_scope_id", "location_key", "name_key"],
        schema=S,
    )
    op.create_index(
        "ix_institution_tenant_id_parent_scope_id",
        "institution",
        ["tenant_id", "parent_scope_id"],
        schema=S,
    )
    op.create_index(
        "ix_institution_tenant_id_location_key",
        "institution",
        ["tenant_id", "location_key"],
        schema=S,
    )
    op.create_check_constraint(
        "institution_type", "institution", f"institution_type IN ({TYPES})", schema=S
    )

    op.drop_constraint(
        op.f("ck_institution_level_level"), "institution_level", schema=S, type_="check"
    )
    op.alter_column(
        "institution_level",
        "level",
        existing_type=sa.String(8),
        type_=sa.String(16),
        existing_nullable=False,
        schema=S,
    )
    op.create_check_constraint("level", "institution_level", f"level IN ({LEVELS_NEW})", schema=S)


def downgrade() -> None:
    op.execute("DELETE FROM org.institution_level WHERE level NOT IN (" + LEVELS_OLD + ")")  # noqa: S608 - constants only
    op.drop_constraint(
        op.f("ck_institution_level_level"), "institution_level", schema=S, type_="check"
    )
    op.alter_column(
        "institution_level",
        "level",
        existing_type=sa.String(16),
        type_=sa.String(8),
        existing_nullable=False,
        schema=S,
    )
    op.create_check_constraint("level", "institution_level", f"level IN ({LEVELS_OLD})", schema=S)

    op.drop_constraint(
        op.f("ck_institution_institution_type"), "institution", schema=S, type_="check"
    )
    op.drop_index("ix_institution_tenant_id_location_key", table_name="institution", schema=S)
    op.drop_index("ix_institution_tenant_id_parent_scope_id", table_name="institution", schema=S)
    op.drop_constraint(
        "uq_institution_tenant_id_parent_scope_id_location_key_name_key",
        "institution",
        schema=S,
        type_="unique",
    )
    op.alter_column(
        "institution",
        "parent_scope_id",
        new_column_name="lga_scope_id",
        existing_type=sa.BINARY(16)
        if op.get_context().dialect.name == "mysql"
        else sa.LargeBinary(16),
        existing_nullable=False,
        schema=S,
    )
    op.create_index(
        "ix_institution_tenant_id_lga_scope_id",
        "institution",
        ["tenant_id", "lga_scope_id"],
        schema=S,
    )
    op.create_unique_constraint(
        "uq_institution_tenant_id_lga_scope_id_name_key",
        "institution",
        ["tenant_id", "lga_scope_id", "name_key"],
        schema=S,
    )
    op.drop_column("institution", "institution_type", schema=S)
    op.drop_column("institution", "location_key", schema=S)
    op.drop_column("institution", "location", schema=S)

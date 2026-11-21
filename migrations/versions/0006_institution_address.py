"""Full postal address and postcode for every institution (ADR-013).

* rename institution.address -> address_line1 (existing addresses are kept)
* add address_line2 and postcode (Nigeria: 6-digit NIPOST code), indexed by tenant

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

S = "org"


def upgrade() -> None:
    op.alter_column(
        "institution",
        "address",
        new_column_name="address_line1",
        existing_type=sa.String(255),
        existing_nullable=True,
        schema=S,
    )
    op.add_column(
        "institution", sa.Column("address_line2", sa.String(255), nullable=True), schema=S
    )
    op.add_column("institution", sa.Column("postcode", sa.String(16), nullable=True), schema=S)
    op.create_index(
        "ix_institution_tenant_id_postcode", "institution", ["tenant_id", "postcode"], schema=S
    )


def downgrade() -> None:
    op.drop_index("ix_institution_tenant_id_postcode", table_name="institution", schema=S)
    op.drop_column("institution", "postcode", schema=S)
    op.drop_column("institution", "address_line2", schema=S)
    op.alter_column(
        "institution",
        "address_line1",
        new_column_name="address",
        existing_type=sa.String(255),
        existing_nullable=True,
        schema=S,
    )

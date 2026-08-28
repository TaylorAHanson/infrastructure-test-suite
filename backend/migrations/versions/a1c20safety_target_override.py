"""targets safety enforcement: run_requests.override

Adds the per-run safety-override column consumed by the identity-bounded safety
gate (spec #20 / ADR-0004 §5). The Target identity/safety-class/assumable-roles
columns already exist from the Check/Test/Suite model migration; the only new
schema is ``run_requests.override`` (JSON, captured override payload).

Revision ID: a1c20safetyov
Revises: 58ed76b77312
Create Date: 2026-08-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1c20safetyov"
down_revision: Union[str, None] = "58ed76b77312"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("run_requests", sa.Column("override", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("run_requests", "override")

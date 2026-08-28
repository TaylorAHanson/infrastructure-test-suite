"""retained history: run_requests.event_ref + check_runs.event_ref

Revision ID: wf32retention
Revises: wf22assistant
Create Date: 2026-08-28

Adds the non-deploy **event reference** column (spec #32 / ADR-0015) to the run
header (``run_requests``) and denormalized onto the atomic check-result record
(``check_runs``), so an Overnight finding — fleet drift not attributable to a
deploy — has a provenance spine ("metastore grant change · 21:14") alongside the
commit sha it already carries. The retained-history tables themselves already
exist (spec #21); the retention window is a runtime setting, not schema.
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "wf32retention"
down_revision: Union[str, None] = "wf22assistant"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("run_requests",
                  sa.Column("event_ref", sqlmodel.sql.sqltypes.AutoString(), nullable=True))
    op.add_column("check_runs",
                  sa.Column("event_ref", sqlmodel.sql.sqltypes.AutoString(), nullable=True))


def downgrade() -> None:
    op.drop_column("check_runs", "event_ref")
    op.drop_column("run_requests", "event_ref")

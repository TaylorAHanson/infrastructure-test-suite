"""run requests: run params + dispatch claim token

Revision ID: wf41runparams
Revises: wf40testscripts
Create Date: 2026-10-02

Adds two nullable ``run_requests`` columns: ``params`` (the run params a Test
script reads with ``params`` / ``setting()``) and ``claim_token`` (identifies
the dispatch that owns the request, so a re-queued request's late worker can't
overwrite its result).
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "wf41runparams"
down_revision: Union[str, None] = "wf40testscripts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("run_requests", sa.Column("params", sa.JSON(), nullable=True))
    op.add_column("run_requests",
                  sa.Column("claim_token", sqlmodel.sql.sqltypes.AutoString(), nullable=True))


def downgrade() -> None:
    op.drop_column("run_requests", "claim_token")
    op.drop_column("run_requests", "params")

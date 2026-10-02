"""test scripts: a Test can be a Python script

Revision ID: wf40testscripts
Revises: wf38branding
Create Date: 2026-10-02

Adds the nullable ``tests.script`` text column. A Test with a script is run by
the sandboxed script runtime; one without keeps its structured Checks.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "wf40testscripts"
down_revision: Union[str, None] = "wf38branding"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tests", sa.Column("script", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("tests", "script")

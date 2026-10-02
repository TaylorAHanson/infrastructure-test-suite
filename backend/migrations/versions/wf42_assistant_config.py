"""assistant settings: assistant_config record

Revision ID: wf42assistantcfg
Revises: wf41runparams
Create Date: 2026-10-02

Adds the single global ``assistant_config`` row: which Model Serving endpoint the
assistant uses (empty = the deployment's MODEL_SERVING_ENDPOINT), its reasoning
level, and the per-turn limits (tool calls, output tokens, temperature, wall
clock) plus whether it may read live data or dry-run on the selected target.
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "wf42assistantcfg"
down_revision: Union[str, None] = "wf41runparams"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "assistant_config",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("endpoint", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("reasoning", sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default="off"),
        sa.Column("max_tool_calls", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("max_output_tokens", sa.Integer(), nullable=False, server_default="6000"),
        sa.Column("temperature", sa.Float(), nullable=False, server_default="0.1"),
        sa.Column("turn_timeout_seconds", sa.Integer(), nullable=False, server_default="300"),
        sa.Column("allow_live_reads", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("allow_dry_run", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("updated_by", sqlmodel.sql.sqltypes.AutoString(), nullable=False,
                  server_default="system"),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("assistant_config")

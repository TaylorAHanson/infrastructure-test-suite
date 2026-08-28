"""run-completion webhook: webhook_config sink record

Revision ID: wf39webhook
Revises: wf32retention
Create Date: 2026-08-28

Adds the run-completion webhook sink config (spec #39 / ADR-0016): a single
global ``webhook_config`` row holding the outbound sink URL(s), the on/off
switch, and the fire-on-every-completion toggle. Editable by any authenticated
human and audited as a config-plane mutation (``webhook.config.change``,
ADR-0014) — the audit action is a string column, so no enum migration is needed.
The default install ships the webhook off (no sink, ``enabled=False``).
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "wf39webhook"
down_revision: Union[str, None] = "wf32retention"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "webhook_config",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("urls", sa.JSON(), nullable=True),
        sa.Column("fire_on_all_completions", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        sa.Column("updated_by", sqlmodel.sql.sqltypes.AutoString(), nullable=False,
                  server_default="system"),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("webhook_config")

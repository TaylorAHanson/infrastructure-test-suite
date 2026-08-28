"""branding: one global presentation record (ADR-0013, #38)

Revision ID: wf38branding
Revises: wf32retention
Create Date: 2026-08-28

Adds the single global ``branding`` table — the runtime white-label
presentation layer (ADR-0013): app name, Assistant display name, footer, a
closed named-token theme, and ≤ 1 MB PNG/SVG logo/favicon **blobs**. The
**default row ships seeded in this migration** (ADR-0013 §5) so an un-branded
install renders finished; the app also seeds it on read as a safety net.
"""
from datetime import datetime, timezone
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "wf38branding"
down_revision: Union[str, None] = "wf32retention"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    branding = op.create_table(
        "branding",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("app_name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("assistant_name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("footer_text", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("theme", sa.JSON(), nullable=True),
        sa.Column("logo", sa.LargeBinary(), nullable=True),
        sa.Column("logo_content_type", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("favicon", sa.LargeBinary(), nullable=True),
        sa.Column("favicon_content_type", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("updated_by", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    # Seed the default row (imported from the app so the seed can't drift from
    # the closed-schema defaults / palette the app validates against).
    from app.core.branding import (
        DEFAULT_APP_NAME,
        DEFAULT_ASSISTANT_NAME,
        DEFAULT_FOOTER_TEXT,
        DEFAULT_THEME,
        SINGLETON_ID,
    )

    op.bulk_insert(branding, [{
        "id": SINGLETON_ID,
        "app_name": DEFAULT_APP_NAME,
        "assistant_name": DEFAULT_ASSISTANT_NAME,
        "footer_text": DEFAULT_FOOTER_TEXT,
        "theme": DEFAULT_THEME,
        "logo": None,
        "logo_content_type": None,
        "favicon": None,
        "favicon_content_type": None,
        "updated_at": datetime.now(timezone.utc).replace(tzinfo=None),
        "updated_by": "system",
    }])


def downgrade() -> None:
    op.drop_table("branding")

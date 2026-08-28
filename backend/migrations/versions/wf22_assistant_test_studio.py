"""assistant test studio: worked_examples + test lifecycle columns

Revision ID: wf22assistant
Revises: a1c20safetyov
Create Date: 2026-08-28

Adds the Assistant/Test-Studio schema (spec #22): the ``worked_examples`` table
(the repo-seeded worked-example library the Assistant grounds on, ADR-0006 §9)
and the Test-Studio lifecycle columns on ``tests`` — ``status`` (Draft/Active,
ADR-0006 §8), ``origin`` (human/assistant), and ``derivation`` (the audit-by-a-
stranger record).
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "wf22assistant"
down_revision: Union[str, None] = "a1c20safetyov"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_teststatus = sa.Enum("DRAFT", "ACTIVE", name="teststatus")


def upgrade() -> None:
    bind = op.get_bind()
    _teststatus.create(bind, checkfirst=True)

    op.add_column("tests", sa.Column("status", _teststatus, nullable=False,
                                     server_default="ACTIVE"))
    op.add_column("tests", sa.Column("origin", sqlmodel.sql.sqltypes.AutoString(),
                                     nullable=False, server_default="human"))
    op.add_column("tests", sa.Column("derivation", sa.JSON(), nullable=True))
    op.create_index(op.f("ix_tests_status"), "tests", ["status"], unique=False)

    op.create_table(
        "worked_examples",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("intent", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("title", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("description", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("operation", sa.JSON(), nullable=True),
        sa.Column("handler_key", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("assertion", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("expectation", sa.Enum("PASS", "DENY", name="expectation"), nullable=False),
        sa.Column("counterfactual", sa.JSON(), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column("curated", sa.Boolean(), nullable=False),
        sa.Column("source_test_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_worked_examples_intent"), "worked_examples", ["intent"], unique=False)
    op.create_index(op.f("ix_worked_examples_source_test_id"), "worked_examples",
                    ["source_test_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_worked_examples_source_test_id"), table_name="worked_examples")
    op.drop_index(op.f("ix_worked_examples_intent"), table_name="worked_examples")
    op.drop_table("worked_examples")

    op.drop_index(op.f("ix_tests_status"), table_name="tests")
    op.drop_column("tests", "derivation")
    op.drop_column("tests", "origin")
    op.drop_column("tests", "status")

    _teststatus.drop(op.get_bind(), checkfirst=True)

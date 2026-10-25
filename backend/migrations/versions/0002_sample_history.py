"""sample history flag: cases.seed -> cases.is_sample

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-25
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = '0002'
down_revision: str | None = '0001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('cases') as batch_op:
        batch_op.alter_column('seed', new_column_name='is_sample')


def downgrade() -> None:
    with op.batch_alter_table('cases') as batch_op:
        batch_op.alter_column('is_sample', new_column_name='seed')

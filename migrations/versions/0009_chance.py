"""Шанс сделки от модели: процент и причины

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHANCE_CHECK = "chance is null or chance between 0 and 100"


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.add_column("leads", sa.Column("chance_why", sa.Text(), nullable=True))
    if _postgres():
        # Два шага, как в 0003: NOT VALID не держит таблицу под блокировкой на запись.
        op.add_column("leads", sa.Column("chance", sa.Integer(), nullable=True))
        op.execute(
            f"alter table leads add constraint ck_leads_chance check ({CHANCE_CHECK}) not valid"
        )
        op.execute("alter table leads validate constraint ck_leads_chance")
    else:
        # sqlite принимает CHECK прямо в ADD COLUMN — без пересборки таблицы, которая
        # потеряла бы индекс по выражению created_at DESC.
        op.execute(
            "alter table leads add column chance integer "
            f"constraint ck_leads_chance check ({CHANCE_CHECK})"
        )


def downgrade() -> None:
    if _postgres():
        op.execute("alter table leads drop constraint ck_leads_chance")
        op.drop_column("leads", "chance")
    else:
        # Колонку под CHECK sqlite снимает только пересборкой таблицы.
        op.drop_index("ix_leads_stage_created", table_name="leads")
        with op.batch_alter_table("leads") as batch:
            batch.drop_constraint("ck_leads_chance", type_="check")
            batch.drop_column("chance")
        op.create_index("ix_leads_stage_created", "leads", ["stage", sa.text("created_at DESC")])
    op.drop_column("leads", "chance_why")

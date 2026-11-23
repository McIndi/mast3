# -*- coding: utf-8 -*-
# This file is part of McIndi's Automated Solutions Tool (MAST).
#
# MAST is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 3
# as published by the Free Software Foundation.
#
# MAST is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with MAST.  If not, see <https://www.gnu.org/licenses/>.
#
# Copyright 2015-2026, McIndi Solutions, All rights reserved.
"""Scheduled jobs through the queue (mast.jobs.schedules), and passive
jobs."""
import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

_time = sa.DateTime(timezone=True)


def upgrade():
    op.create_table(
        "schedules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("command", sa.String(200), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("cron", sa.String(100), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(200), nullable=False),
        sa.Column("created_at", _time, nullable=False),
        sa.Column("updated_at", _time, nullable=False),
        sa.Column("last_fired_at", _time, nullable=True),
        sa.Column("last_job_id", sa.String(32), nullable=True),
        sa.Column("last_skip_reason", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_schedules"),
        sa.UniqueConstraint("name", name="uq_schedules_name"))
    with op.batch_alter_table("jobs") as batch:
        batch.add_column(sa.Column("passive", sa.Boolean(), nullable=False,
                                   server_default=sa.false()))
        batch.add_column(sa.Column("schedule_id", sa.Integer(), nullable=True))
        batch.create_index("ix_jobs_schedule_id", ["schedule_id"])
        batch.create_foreign_key(
            "fk_jobs_schedule_id_schedules", "schedules", ["schedule_id"],
            ["id"], ondelete="SET NULL")


def downgrade():
    with op.batch_alter_table("jobs") as batch:
        batch.drop_constraint("fk_jobs_schedule_id_schedules",
                              type_="foreignkey")
        batch.drop_index("ix_jobs_schedule_id")
        batch.drop_column("schedule_id")
        batch.drop_column("passive")
    op.drop_table("schedules")

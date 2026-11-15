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
"""The first tables of the job queue."""
import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

_time = sa.DateTime(timezone=True)


def upgrade():
    op.create_table(
        "jobs",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("command", sa.String(200), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("exclusive", sa.Boolean(), nullable=False),
        sa.Column("submitted_by", sa.String(200), nullable=False),
        sa.Column("parent_id", sa.String(32), nullable=True),
        sa.Column("created_at", _time, nullable=False),
        sa.Column("started_at", _time, nullable=True),
        sa.Column("finished_at", _time, nullable=True),
        sa.Column("worker_id", sa.String(200), nullable=True),
        sa.Column("lease_expires_at", _time, nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_jobs"),
        sa.ForeignKeyConstraint(
            ["parent_id"], ["jobs.id"], name="fk_jobs_parent_id_jobs"))
    op.create_index("ix_jobs_parent_id", "jobs", ["parent_id"])
    op.create_index(
        "ix_jobs_status_created_at", "jobs", ["status", "created_at"])

    op.create_table(
        "job_targets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.String(32), nullable=False),
        sa.Column("appliance", sa.String(255), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_job_targets"),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], ondelete="CASCADE",
            name="fk_job_targets_job_id_jobs"))
    op.create_index("ix_job_targets_job_id", "job_targets", ["job_id"])
    op.create_index(
        "ix_job_targets_appliance", "job_targets", ["appliance"])

    op.create_table(
        "job_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.String(32), nullable=False),
        sa.Column("created_at", _time, nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_job_events"),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], ondelete="CASCADE",
            name="fk_job_events_job_id_jobs"))
    op.create_index(
        "ix_job_events_job_id_id", "job_events", ["job_id", "id"])

    op.create_table(
        "job_results",
        sa.Column("job_id", sa.String(32), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("html", sa.Text(), nullable=False),
        sa.Column("history", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("job_id", name="pk_job_results"),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], ondelete="CASCADE",
            name="fk_job_results_job_id_jobs"))


def downgrade():
    op.drop_table("job_results")
    op.drop_table("job_events")
    op.drop_table("job_targets")
    op.drop_table("jobs")

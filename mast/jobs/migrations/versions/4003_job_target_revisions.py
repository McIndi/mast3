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
"""The id and the revision of each target of a job when it was queued
(MAST 4.0 step 4c, mast.jobs.models.JobTarget). The jobs that are there
already have neither, and are not checked."""
import sqlalchemy as sa
from alembic import op

revision = "4003"
down_revision = "4002"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("job_targets") as batch:
        batch.add_column(sa.Column("target_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("revision", sa.Integer(), nullable=True))


def downgrade():
    with op.batch_alter_table("job_targets") as batch:
        batch.drop_column("revision")
        batch.drop_column("target_id")

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
"""Pinning (step A5): what an appliance showed when its pinned
certificate or SSH host key did not match, for the Appliances page."""
import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

_time = sa.DateTime(timezone=True)


def upgrade():
    with op.batch_alter_table("appliances") as batch:
        batch.add_column(sa.Column("tls_mismatch", sa.String(64), nullable=True))
        batch.add_column(sa.Column("tls_mismatch_at", _time, nullable=True))
        batch.add_column(sa.Column("ssh_mismatch", sa.String(100), nullable=True))
        batch.add_column(sa.Column("ssh_mismatch_at", _time, nullable=True))


def downgrade():
    with op.batch_alter_table("appliances") as batch:
        batch.drop_column("ssh_mismatch_at")
        batch.drop_column("ssh_mismatch")
        batch.drop_column("tls_mismatch_at")
        batch.drop_column("tls_mismatch")

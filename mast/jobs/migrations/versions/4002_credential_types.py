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
"""The type of a stored credential (MAST 4.0 step 4b,
mast.credentials.models): every credential so far is a password. And
the types of credential that each target takes (mast.registry.models),
None for all that its kinds take. A downgrade is refused while there is
a credential of another type, which older versions cannot hold."""
import sqlalchemy as sa
from alembic import op

revision = "4002"
down_revision = "4001"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("credentials") as batch:
        batch.add_column(sa.Column("type", sa.String(16), nullable=False,
                                   server_default="password"))
    # The model gives the type of every new credential
    with op.batch_alter_table("credentials") as batch:
        batch.alter_column("type", server_default=None)
    with op.batch_alter_table("targets") as batch:
        batch.add_column(sa.Column("credential_types", sa.JSON(),
                                   nullable=True))


def downgrade():
    others = op.get_bind().execute(sa.text(
        "SELECT name, type FROM credentials WHERE type <> 'password'"
    )).fetchall()
    if others:
        raise RuntimeError(
            "Older versions of MAST store only passwords. Delete the "
            "credentials of other types ({}) before the downgrade.".format(
                ", ".join("{} ({})".format(*row) for row in others[:5])))
    with op.batch_alter_table("targets") as batch:
        batch.drop_column("credential_types")
    with op.batch_alter_table("credentials") as batch:
        batch.drop_column("type")

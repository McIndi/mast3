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
"""No roles (MAST 4.0 step 6b): what a user may do is the permission sets
of their groups (migration 4004), so the role of each user goes. A
downgrade gives each user the role of the highest group of a role that
they are in (admin, operator, else viewer).

The seeded sets that no admin changed become those of this version: the
rules of admin over core and the credentials, and the crypto inventory
and probe of operator, apply on every target (a rule without a scope
grants no target)."""
import sqlalchemy as sa
from alembic import op

revision = "4006"
down_revision = "4005"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("users") as batch:
        batch.drop_column("role")
    from mast.permissions import SEED
    sets = sa.table("permission_sets", sa.column("name", sa.String),
                    sa.column("description", sa.Text),
                    sa.column("rules", sa.JSON),
                    sa.column("updated_by", sa.String))
    for name, (description, rules) in SEED.items():
        op.execute(sets.update().where(sets.c.name == name)
                   .where(sets.c.updated_by == "mast")
                   .values(rules=rules, description=description))


def downgrade():
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("role", sa.String(16), nullable=False,
                                   server_default="viewer"))
    with op.batch_alter_table("users") as batch:
        batch.alter_column("role", server_default=None)
    bind = op.get_bind()
    for role in ("operator", "admin"):
        bind.execute(sa.text(
            "UPDATE users SET role = :role WHERE id IN ("
            "SELECT m.user_id FROM group_members m JOIN user_groups g "
            "ON g.id = m.group_id WHERE g.name = :role)"), {"role": role})

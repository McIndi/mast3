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
"""Permission sets and groups (MAST 4.0 step 5, mast.permissions.models),
with the sets and groups viewer, operator and admin, and each user in
the groups of their role, and the record of the groups that plugins
define (which MAST makes when it first sees them).

The sets that this makes are those of `mast.permissions.SEED` when it
was written; a fresh baseline (step 7 of docs/design/mast-4.0.md)
replaces this migration."""
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "4004"
down_revision = "4003"
branch_labels = None
depends_on = None

_time = sa.DateTime(timezone=True)


def upgrade():
    # Imported here: what this migration seeds
    from mast.permissions import ROLE_GROUPS, SEED
    op.create_table(
        "permission_sets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("rules", sa.JSON(), nullable=False),
        sa.Column("updated_by", sa.String(200), nullable=False),
        sa.Column("updated_at", _time, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_permission_sets"),
        sa.UniqueConstraint("name", name="uq_permission_sets_name"))
    op.create_table(
        "user_groups",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_user_groups"),
        sa.UniqueConstraint("name", name="uq_user_groups_name"))
    op.create_table(
        "group_permission_sets",
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("set_id", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("group_id", "set_id",
                                name="pk_group_permission_sets"),
        sa.ForeignKeyConstraint(
            ["group_id"], ["user_groups.id"],
            name="fk_group_permission_sets_group_id_user_groups",
            ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["set_id"], ["permission_sets.id"],
            name="fk_group_permission_sets_set_id_permission_sets",
            ondelete="CASCADE"))
    op.create_index("ix_group_permission_sets_set_id",
                    "group_permission_sets", ["set_id"])
    op.create_table(
        "group_members",
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("group_id", "user_id",
                                name="pk_group_members"),
        sa.ForeignKeyConstraint(
            ["group_id"], ["user_groups.id"],
            name="fk_group_members_group_id_user_groups", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            name="fk_group_members_user_id_users", ondelete="CASCADE"))
    op.create_index("ix_group_members_user_id", "group_members", ["user_id"])

    op.create_table(
        "plugin_groups",
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("made_at", _time, nullable=False),
        sa.PrimaryKeyConstraint("name", name="pk_plugin_groups"))

    now = datetime.now(timezone.utc)
    sets = sa.table("permission_sets", sa.column("id", sa.Integer),
                    sa.column("name", sa.String), sa.column("description",
                                                            sa.Text),
                    sa.column("rules", sa.JSON),
                    sa.column("updated_by", sa.String),
                    sa.column("updated_at", _time))
    groups = sa.table("user_groups", sa.column("id", sa.Integer),
                      sa.column("name", sa.String),
                      sa.column("description", sa.Text))
    names = list(SEED)
    op.bulk_insert(sets, [
        {"id": n + 1, "name": name, "description": SEED[name][0],
         "rules": SEED[name][1], "updated_by": "mast", "updated_at": now}
        for n, name in enumerate(names)])
    op.bulk_insert(groups, [
        {"id": n + 1, "name": name, "description": SEED[name][0]}
        for n, name in enumerate(names)])
    op.bulk_insert(
        sa.table("group_permission_sets", sa.column("group_id", sa.Integer),
                 sa.column("set_id", sa.Integer)),
        [{"group_id": n + 1, "set_id": n + 1} for n in range(len(names))])
    bind = op.get_bind()
    members = []
    for user_id, role in bind.execute(sa.text(
            "SELECT id, role FROM users")).fetchall():
        for group in ROLE_GROUPS.get(role, ()):
            members.append({"group_id": names.index(group) + 1,
                            "user_id": user_id})
    if members:
        op.bulk_insert(
            sa.table("group_members", sa.column("group_id", sa.Integer),
                     sa.column("user_id", sa.Integer)), members)
    if bind.dialect.name == "postgresql":
        for table in ("permission_sets", "user_groups"):
            op.execute(
                "SELECT setval(pg_get_serial_sequence('{0}', 'id'), "
                "COALESCE((SELECT MAX(id) FROM {0}), 0) + 1, false)".format(
                    table))


def downgrade():
    op.drop_table("plugin_groups")
    op.drop_index("ix_group_members_user_id", "group_members")
    op.drop_table("group_members")
    op.drop_index("ix_group_permission_sets_set_id", "group_permission_sets")
    op.drop_table("group_permission_sets")
    op.drop_table("user_groups")
    op.drop_table("permission_sets")

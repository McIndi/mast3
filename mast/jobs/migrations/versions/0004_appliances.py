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
"""The appliance registry (mast.registry): appliances and environments."""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

_time = sa.DateTime(timezone=True)


def upgrade():
    op.create_table(
        "appliances",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("address", sa.String(255), nullable=False),
        sa.Column("soma_port", sa.Integer(), nullable=True),
        sa.Column("web_port", sa.Integer(), nullable=True),
        sa.Column("ssh_port", sa.Integer(), nullable=True),
        sa.Column("soma_scheme", sa.String(8), nullable=True),
        sa.Column("soma_uri", sa.String(255), nullable=True),
        sa.Column("soma_spec_file", sa.String(255), nullable=True),
        sa.Column("retry_interval", sa.Integer(), nullable=True),
        sa.Column("tls_mode", sa.String(8), nullable=False),
        sa.Column("ca_bundle", sa.Text(), nullable=True),
        sa.Column("pin_sha256", sa.String(64), nullable=True),
        sa.Column("pinned_at", _time, nullable=True),
        sa.Column("pinned_by", sa.String(200), nullable=True),
        sa.Column("ssh_host_key", sa.Text(), nullable=True),
        sa.Column("ssh_pinned_at", _time, nullable=True),
        sa.Column("ssh_pinned_by", sa.String(200), nullable=True),
        sa.Column("poll", sa.Boolean(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("created_at", _time, nullable=False),
        sa.Column("updated_at", _time, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_appliances"),
        sa.UniqueConstraint("name", name="uq_appliances_name"))
    op.create_table(
        "environments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("production", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_environments"),
        sa.UniqueConstraint("name", name="uq_environments_name"))
    op.create_table(
        "environment_members",
        sa.Column("environment_id", sa.Integer(), nullable=False),
        sa.Column("appliance_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("environment_id", "appliance_id",
                                name="pk_environment_members"),
        sa.ForeignKeyConstraint(
            ["environment_id"], ["environments.id"],
            name="fk_environment_members_environment_id_environments",
            ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["appliance_id"], ["appliances.id"],
            name="fk_environment_members_appliance_id_appliances",
            ondelete="CASCADE"))
    op.create_index("ix_environment_members_appliance_id",
                    "environment_members", ["appliance_id"])


def downgrade():
    op.drop_table("environment_members")
    op.drop_table("environments")
    op.drop_table("appliances")

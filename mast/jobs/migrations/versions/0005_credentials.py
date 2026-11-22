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
"""The credential store (mast.credentials): credentials, their bindings
and uses, and the audit events."""
import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

_time = sa.DateTime(timezone=True)


def upgrade():
    op.create_table(
        "credentials",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("username", sa.String(255), nullable=False),
        sa.Column("token", sa.Text(), nullable=False),
        sa.Column("purpose", sa.String(8), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(200), nullable=False),
        sa.Column("created_at", _time, nullable=False),
        sa.Column("updated_by", sa.String(200), nullable=False),
        sa.Column("updated_at", _time, nullable=False),
        sa.Column("last_used_at", _time, nullable=True),
        sa.Column("last_tested_at", _time, nullable=True),
        sa.Column("last_test_ok", sa.Boolean(), nullable=True),
        sa.Column("can_write", sa.Boolean(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_credentials"),
        sa.UniqueConstraint("name", name="uq_credentials_name"))
    op.create_table(
        "credential_bindings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("credential_id", sa.Integer(), nullable=False),
        sa.Column("appliance_id", sa.Integer(), nullable=True),
        sa.Column("environment_id", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_credential_bindings"),
        sa.ForeignKeyConstraint(
            ["credential_id"], ["credentials.id"],
            name="fk_credential_bindings_credential_id_credentials",
            ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["appliance_id"], ["appliances.id"],
            name="fk_credential_bindings_appliance_id_appliances",
            ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["environment_id"], ["environments.id"],
            name="fk_credential_bindings_environment_id_environments",
            ondelete="CASCADE"))
    for column in ("credential_id", "appliance_id", "environment_id"):
        op.create_index("ix_credential_bindings_{}".format(column),
                        "credential_bindings", [column])
    op.create_table(
        "credential_uses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("credential_id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.String(32), nullable=False),
        sa.Column("appliance", sa.String(255), nullable=False),
        sa.Column("principal", sa.String(200), nullable=False),
        sa.Column("purpose", sa.String(8), nullable=False),
        sa.Column("used_at", _time, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_credential_uses"),
        sa.ForeignKeyConstraint(
            ["credential_id"], ["credentials.id"],
            name="fk_credential_uses_credential_id_credentials",
            ondelete="CASCADE"))
    op.create_index("ix_credential_uses_credential_id", "credential_uses",
                    ["credential_id"])
    op.create_index("ix_credential_uses_job_id", "credential_uses",
                    ["job_id"])
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("at", _time, nullable=False),
        sa.Column("principal", sa.String(200), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("object", sa.String(255), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_audit_events"))
    op.create_index("ix_audit_events_at", "audit_events", ["at"])


def downgrade():
    op.drop_table("audit_events")
    op.drop_table("credential_uses")
    op.drop_table("credential_bindings")
    op.drop_table("credentials")

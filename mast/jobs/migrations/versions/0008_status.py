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
"""The status of the appliances (mast.datapower.status.models)."""
import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

_time = sa.DateTime(timezone=True)


def _appliance(table):
    return sa.ForeignKeyConstraint(
        ["appliance_id"], ["appliances.id"],
        name="fk_{}_appliance_id_appliances".format(table),
        ondelete="CASCADE")


def upgrade():
    op.create_table(
        "status_latest",
        sa.Column("appliance_id", sa.Integer(), nullable=False),
        sa.Column("collected_at", _time, nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("last_ok_at", _time, nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("credential", sa.String(64), nullable=True),
        sa.Column("firmware", sa.String(64), nullable=True),
        sa.Column("delivery", sa.String(16), nullable=True),
        sa.Column("model", sa.String(64), nullable=True),
        sa.Column("serial", sa.String(64), nullable=True),
        sa.Column("uptime_seconds", sa.Integer(), nullable=True),
        sa.Column("appliance_time", sa.String(64), nullable=True),
        sa.Column("cpu_10s", sa.Float(), nullable=True),
        sa.Column("cpu_1m", sa.Float(), nullable=True),
        sa.Column("mem_used_pct", sa.Float(), nullable=True),
        sa.Column("load_pct", sa.Float(), nullable=True),
        sa.Column("fs_free_encrypted_mb", sa.Integer(), nullable=True),
        sa.Column("fs_free_temporary_mb", sa.Integer(), nullable=True),
        sa.Column("fs_total_temporary_mb", sa.Integer(), nullable=True),
        sa.Column("fs_free_internal_mb", sa.Integer(), nullable=True),
        sa.Column("domains_total", sa.Integer(), nullable=True),
        sa.Column("domains_down", sa.Integer(), nullable=True),
        sa.Column("objects_down", sa.Integer(), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("appliance_id", name="pk_status_latest"),
        _appliance("status_latest"))
    op.create_table(
        "status_samples",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("appliance_id", sa.Integer(), nullable=False),
        sa.Column("collected_at", _time, nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("cpu_1m", sa.Float(), nullable=True),
        sa.Column("mem_used_pct", sa.Float(), nullable=True),
        sa.Column("load_pct", sa.Float(), nullable=True),
        sa.Column("fs_free_temporary_mb", sa.Integer(), nullable=True),
        sa.Column("objects_down", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_status_samples"),
        _appliance("status_samples"))
    op.create_index("ix_status_samples_appliance_at", "status_samples",
                    ["appliance_id", "collected_at"])
    op.create_table(
        "status_rollups",
        sa.Column("appliance_id", sa.Integer(), nullable=False),
        sa.Column("period", sa.String(8), nullable=False),
        sa.Column("start", _time, nullable=False),
        sa.Column("samples", sa.Integer(), nullable=False),
        sa.Column("ok_ratio", sa.Float(), nullable=False),
        sa.Column("cpu_avg", sa.Float(), nullable=True),
        sa.Column("cpu_max", sa.Float(), nullable=True),
        sa.Column("mem_avg", sa.Float(), nullable=True),
        sa.Column("mem_max", sa.Float(), nullable=True),
        sa.Column("load_avg", sa.Float(), nullable=True),
        sa.Column("objects_down_max", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("appliance_id", "period", "start",
                                name="pk_status_rollups"),
        _appliance("status_rollups"))


def downgrade():
    op.drop_table("status_rollups")
    op.drop_index("ix_status_samples_appliance_at", "status_samples")
    op.drop_table("status_samples")
    op.drop_table("status_latest")

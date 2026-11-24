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
"""The TLS probe of the crypto inventory, and the operational state and
listening port of each object (mast.datapower.crypto.models)."""
import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

_time = sa.DateTime(timezone=True)


def upgrade():
    with op.batch_alter_table("crypto_objects") as batch:
        batch.add_column(sa.Column("op_state", sa.String(16), nullable=True))
        batch.add_column(sa.Column("status_error", sa.Text(), nullable=True))
        batch.add_column(sa.Column("listen", sa.String(300), nullable=True))
    op.create_table(
        "crypto_probe_results",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("snapshot_id", sa.Integer(), nullable=True),
        sa.Column("appliance_id", sa.Integer(), nullable=True),
        sa.Column("job_id", sa.String(32), nullable=True),
        sa.Column("host", sa.String(255), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("probed_at", _time, nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_crypto_probe_results"),
        sa.ForeignKeyConstraint(
            ["snapshot_id"], ["crypto_snapshots.id"],
            name="fk_crypto_probe_results_snapshot_id_crypto_snapshots",
            ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["appliance_id"], ["appliances.id"],
            name="fk_crypto_probe_results_appliance_id_appliances",
            ondelete="CASCADE"))
    op.create_index("ix_crypto_probe_results_appliance_at",
                    "crypto_probe_results", ["appliance_id", "probed_at"])


def downgrade():
    op.drop_index("ix_crypto_probe_results_appliance_at",
                  "crypto_probe_results")
    op.drop_table("crypto_probe_results")
    with op.batch_alter_table("crypto_objects") as batch:
        batch.drop_column("listen")
        batch.drop_column("status_error")
        batch.drop_column("op_state")

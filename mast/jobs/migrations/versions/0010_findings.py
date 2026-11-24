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
"""The findings of the posture rules, their exceptions, and the objects
that name each file of the crypto inventory
(mast.datapower.crypto.models)."""
import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

_time = sa.DateTime(timezone=True)


def upgrade():
    with op.batch_alter_table("crypto_files") as batch:
        batch.add_column(sa.Column("users", sa.JSON(), nullable=True))
    op.create_table(
        "crypto_exceptions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("rule_id", sa.String(64), nullable=False),
        sa.Column("appliance", sa.String(255), nullable=True),
        sa.Column("domain", sa.String(128), nullable=True),
        sa.Column("object_pattern", sa.String(255), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("expires_at", _time, nullable=False),
        sa.Column("created_by", sa.String(200), nullable=False),
        sa.Column("created_at", _time, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_crypto_exceptions"))
    op.create_table(
        "crypto_findings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("snapshot_id", sa.Integer(), nullable=False),
        sa.Column("rule_id", sa.String(64), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("object_id", sa.Integer(), nullable=True),
        sa.Column("subject", sa.String(800), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("remediation", sa.Text(), nullable=False),
        sa.Column("exception_id", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_crypto_findings"),
        sa.ForeignKeyConstraint(
            ["snapshot_id"], ["crypto_snapshots.id"],
            name="fk_crypto_findings_snapshot_id_crypto_snapshots",
            ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["object_id"], ["crypto_objects.id"],
            name="fk_crypto_findings_object_id_crypto_objects",
            ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["exception_id"], ["crypto_exceptions.id"],
            name="fk_crypto_findings_exception_id_crypto_exceptions",
            ondelete="SET NULL"))
    op.create_index("ix_crypto_findings_snapshot", "crypto_findings",
                    ["snapshot_id"])


def downgrade():
    op.drop_index("ix_crypto_findings_snapshot", "crypto_findings")
    op.drop_table("crypto_findings")
    op.drop_table("crypto_exceptions")
    with op.batch_alter_table("crypto_files") as batch:
        batch.drop_column("users")

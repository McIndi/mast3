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
"""The crypto inventory (mast.datapower.crypto.models)."""
import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

_time = sa.DateTime(timezone=True)


def _snapshot(table):
    return sa.ForeignKeyConstraint(
        ["snapshot_id"], ["crypto_snapshots.id"],
        name="fk_{}_snapshot_id_crypto_snapshots".format(table),
        ondelete="CASCADE")


def _object(table, column):
    return sa.ForeignKeyConstraint(
        [column], ["crypto_objects.id"],
        name="fk_{}_{}_crypto_objects".format(table, column),
        ondelete="CASCADE")


def upgrade():
    op.create_table(
        "crypto_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("appliance_id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.String(32), nullable=True),
        sa.Column("collected_at", _time, nullable=False),
        sa.Column("firmware", sa.String(64), nullable=True),
        sa.Column("spec_file", sa.String(255), nullable=True),
        sa.Column("method", sa.String(16), nullable=True),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("problems", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_crypto_snapshots"),
        sa.ForeignKeyConstraint(
            ["appliance_id"], ["appliances.id"],
            name="fk_crypto_snapshots_appliance_id_appliances",
            ondelete="CASCADE"))
    op.create_index("ix_crypto_snapshots_appliance_at", "crypto_snapshots",
                    ["appliance_id", "collected_at"])
    op.create_table(
        "crypto_objects",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("snapshot_id", sa.Integer(), nullable=False),
        sa.Column("domain", sa.String(128), nullable=False),
        sa.Column("class", sa.String(128), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("admin_state", sa.String(16), nullable=True),
        sa.Column("running", sa.Boolean(), nullable=False),
        sa.Column("saved", sa.Boolean(), nullable=False),
        sa.Column("attrs", sa.JSON(), nullable=True),
        sa.Column("has_plaintext_password", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_crypto_objects"),
        _snapshot("crypto_objects"))
    op.create_index("ix_crypto_objects_snapshot_class", "crypto_objects",
                    ["snapshot_id", "class"])
    op.create_table(
        "crypto_certs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("snapshot_id", sa.Integer(), nullable=False),
        sa.Column("object_id", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("issuer", sa.Text(), nullable=True),
        sa.Column("serial", sa.String(128), nullable=True),
        sa.Column("sig_alg", sa.String(64), nullable=True),
        sa.Column("key_alg", sa.String(64), nullable=True),
        sa.Column("key_bits", sa.Integer(), nullable=True),
        sa.Column("curve", sa.String(64), nullable=True),
        sa.Column("not_before", _time, nullable=True),
        sa.Column("not_after", _time, nullable=True),
        sa.Column("sans", sa.JSON(), nullable=True),
        sa.Column("self_signed", sa.Boolean(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_crypto_certs"),
        _snapshot("crypto_certs"),
        _object("crypto_certs", "object_id"))
    op.create_index("ix_crypto_certs_snapshot", "crypto_certs", ["snapshot_id"])
    op.create_index("ix_crypto_certs_sha256", "crypto_certs", ["sha256"])
    op.create_table(
        "crypto_files",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("snapshot_id", sa.Integer(), nullable=False),
        sa.Column("domain", sa.String(128), nullable=False),
        sa.Column("location", sa.String(16), nullable=False),
        sa.Column("name", sa.String(512), nullable=False),
        sa.Column("size", sa.Integer(), nullable=True),
        sa.Column("modified", sa.String(32), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_crypto_files"),
        _snapshot("crypto_files"))
    op.create_index("ix_crypto_files_snapshot", "crypto_files", ["snapshot_id"])
    op.create_table(
        "crypto_edges",
        sa.Column("snapshot_id", sa.Integer(), nullable=False),
        sa.Column("from_object_id", sa.Integer(), nullable=False),
        sa.Column("to_object_id", sa.Integer(), nullable=False),
        sa.Column("via", sa.String(128), nullable=False),
        sa.PrimaryKeyConstraint("snapshot_id", "from_object_id",
                                "to_object_id", "via",
                                name="pk_crypto_edges"),
        _snapshot("crypto_edges"),
        _object("crypto_edges", "from_object_id"),
        _object("crypto_edges", "to_object_id"))
    op.create_table(
        "crypto_cert_cache",
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("parsed", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("sha256", name="pk_crypto_cert_cache"))


def downgrade():
    op.drop_table("crypto_cert_cache")
    op.drop_table("crypto_edges")
    op.drop_index("ix_crypto_files_snapshot", "crypto_files")
    op.drop_table("crypto_files")
    op.drop_index("ix_crypto_certs_sha256", "crypto_certs")
    op.drop_index("ix_crypto_certs_snapshot", "crypto_certs")
    op.drop_table("crypto_certs")
    op.drop_index("ix_crypto_objects_snapshot_class", "crypto_objects")
    op.drop_table("crypto_objects")
    op.drop_index("ix_crypto_snapshots_appliance_at", "crypto_snapshots")
    op.drop_table("crypto_snapshots")

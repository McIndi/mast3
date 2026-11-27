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
"""Service accounts and tokens (MAST 4.0 step 6, mast.auth.models): the
kind of each user (every user so far is a person), and the tokens of the
command line and the API. A downgrade is refused while there is a
service account."""
import sqlalchemy as sa
from alembic import op

revision = "4005"
down_revision = "4004"
branch_labels = None
depends_on = None

_time = sa.DateTime(timezone=True)


def upgrade():
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("kind", sa.String(16), nullable=False,
                                   server_default="person"))
    with op.batch_alter_table("users") as batch:
        batch.alter_column("kind", server_default=None)
    op.create_table(
        "api_tokens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("secret_hash", sa.String(64), nullable=False),
        sa.Column("created_at", _time, nullable=False),
        sa.Column("created_by", sa.String(200), nullable=False),
        sa.Column("last_used_at", _time, nullable=True),
        sa.Column("expires_at", _time, nullable=True),
        sa.Column("revoked_at", _time, nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_api_tokens"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_api_tokens_user_id_users",
            ondelete="CASCADE"))
    op.create_index("ix_api_tokens_user_id", "api_tokens", ["user_id"])


def downgrade():
    services = op.get_bind().execute(sa.text(
        "SELECT name FROM users WHERE kind <> 'person'")).fetchall()
    if services:
        raise RuntimeError(
            "Older versions of MAST have no service accounts. Delete them "
            "({}) before the downgrade.".format(
                ", ".join(row[0] for row in services[:5])))
    op.drop_index("ix_api_tokens_user_id", "api_tokens")
    op.drop_table("api_tokens")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("kind")

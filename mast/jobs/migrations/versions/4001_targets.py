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
"""Targets and fleets in place of appliances and environments (MAST 4.0
step 4, mast.registry.models).

Each appliance becomes a target of the kind `datapower` with the same
id: its ports, scheme, URI, spec file, retry interval and poll flag
become the attributes of the kind, and its TLS and SSH pins the trust
of the endpoints `soma` and `ssh`. Each environment becomes a fleet of
the kind `datapower`. The tables that point at appliances and
environments (the credential bindings, the status and the crypto
inventory) point at targets and fleets.

The revision is not 0012, the next revision of MAST 3.x: 4.0 starts with
fresh baselines (step 7 of docs/design/mast-4.0.md) and imports a 3.x
database once (step 13). A downgrade is refused while there is a
target of another kind, which 3.x cannot hold.
"""
import json

import sqlalchemy as sa
from alembic import op

revision = "4001"
down_revision = "0011"
branch_labels = None
depends_on = None

_time = sa.DateTime(timezone=True)

# The attributes of the kind datapower that were columns
_ATTRIBUTES = ("soma_port", "web_port", "ssh_port", "soma_scheme",
               "soma_uri", "soma_spec_file", "retry_interval")

# The tables of the plugin that point at appliances, by their column
# appliance_id
_PLUGIN_TABLES = ("status_latest", "status_samples", "status_rollups",
                  "crypto_snapshots", "crypto_probe_results")


def _create_new_tables():
    op.create_table(
        "targets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("address", sa.String(255), nullable=False),
        sa.Column("attributes", sa.JSON(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("created_at", _time, nullable=False),
        sa.Column("updated_at", _time, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_targets"),
        sa.UniqueConstraint("name", name="uq_targets_name"))
    op.create_table(
        "target_extensions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("entry", sa.String(255), nullable=False),
        sa.Column("attributes", sa.JSON(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_target_extensions"),
        sa.ForeignKeyConstraint(
            ["target_id"], ["targets.id"],
            name="fk_target_extensions_target_id_targets",
            ondelete="CASCADE"),
        sa.UniqueConstraint(
            "target_id", "kind", "entry",
            name="uq_target_extensions_target_id"))
    op.create_index("ix_target_extensions_target_id", "target_extensions",
                    ["target_id"])
    op.create_table(
        "target_trust",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("scope", sa.String(320), nullable=False),
        sa.Column("endpoint", sa.String(64), nullable=False),
        sa.Column("mode", sa.String(8), nullable=False),
        sa.Column("ca_bundle", sa.Text(), nullable=True),
        sa.Column("pin", sa.Text(), nullable=True),
        sa.Column("pinned_at", _time, nullable=True),
        sa.Column("pinned_by", sa.String(200), nullable=True),
        sa.Column("mismatch", sa.String(100), nullable=True),
        sa.Column("mismatch_at", _time, nullable=True),
        sa.Column("review", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_target_trust"),
        sa.ForeignKeyConstraint(
            ["target_id"], ["targets.id"],
            name="fk_target_trust_target_id_targets", ondelete="CASCADE"),
        sa.UniqueConstraint("target_id", "scope", "endpoint",
                            name="uq_target_trust_target_id"))
    op.create_index("ix_target_trust_target_id", "target_trust",
                    ["target_id"])
    op.create_table(
        "fleets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("production", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_fleets"),
        sa.UniqueConstraint("name", name="uq_fleets_name"))
    op.create_table(
        "fleet_members",
        sa.Column("fleet_id", sa.Integer(), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("fleet_id", "target_id",
                                name="pk_fleet_members"),
        sa.ForeignKeyConstraint(
            ["fleet_id"], ["fleets.id"],
            name="fk_fleet_members_fleet_id_fleets", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["target_id"], ["targets.id"],
            name="fk_fleet_members_target_id_targets", ondelete="CASCADE"))
    op.create_index("ix_fleet_members_target_id", "fleet_members",
                    ["target_id"])


def _create_old_tables():
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
        sa.Column("tls_mismatch", sa.String(64), nullable=True),
        sa.Column("tls_mismatch_at", _time, nullable=True),
        sa.Column("ssh_mismatch", sa.String(100), nullable=True),
        sa.Column("ssh_mismatch_at", _time, nullable=True),
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


def _sync_sequence(table):
    """After rows were inserted with their ids, PostgreSQL's sequence of
    the id must start after them."""
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "SELECT setval(pg_get_serial_sequence('{0}', 'id'), "
            "COALESCE((SELECT MAX(id) FROM {0}), 0) + 1, false)".format(table))


def _repoint(table, column, old_table, new_table, new_column=None):
    """Point the foreign key `table.column` at `new_table`, renaming the
    column to `new_column`."""
    new_column = new_column or column
    with op.batch_alter_table(table) as batch:
        batch.drop_constraint("fk_{}_{}_{}".format(table, column, old_table),
                              type_="foreignkey")
        if new_column != column:
            batch.alter_column(column, new_column_name=new_column)
    # A key on a column that the same batch renamed would be lost
    with op.batch_alter_table(table) as batch:
        batch.create_foreign_key(
            "fk_{}_{}_{}".format(table, new_column, new_table), new_table,
            [new_column], ["id"], ondelete="CASCADE")


def upgrade():
    _create_new_tables()
    bind = op.get_bind()
    op.execute(
        "INSERT INTO targets (id, name, kind, address, attributes, "
        "schema_version, revision, notes, created_at, updated_at) "
        "SELECT id, name, 'datapower', address, '{}', 1, 1, notes, "
        "created_at, updated_at FROM appliances")
    update = sa.text("UPDATE targets SET attributes = :attributes "
                     "WHERE id = :id").bindparams(
        sa.bindparam("attributes", type_=sa.JSON))
    rows = bind.execute(sa.text(
        "SELECT id, poll, {} FROM appliances".format(
            ", ".join(_ATTRIBUTES)))).mappings().all()
    for row in rows:
        attributes = {name: row[name] for name in _ATTRIBUTES
                      if row[name] is not None}
        attributes["poll"] = bool(row["poll"])
        bind.execute(update, {"id": row["id"], "attributes": attributes})
    op.execute(
        "INSERT INTO target_trust (target_id, scope, endpoint, mode, "
        "ca_bundle, pin, pinned_at, pinned_by, mismatch, mismatch_at) "
        "SELECT id, '', 'soma', tls_mode, ca_bundle, pin_sha256, pinned_at, "
        "pinned_by, tls_mismatch, tls_mismatch_at FROM appliances")
    op.execute(
        "INSERT INTO target_trust (target_id, scope, endpoint, mode, "
        "ca_bundle, pin, pinned_at, pinned_by, mismatch, mismatch_at) "
        "SELECT id, '', 'ssh', CASE WHEN ssh_host_key IS NULL THEN 'none' "
        "ELSE 'pin' END, NULL, ssh_host_key, ssh_pinned_at, ssh_pinned_by, "
        "ssh_mismatch, ssh_mismatch_at FROM appliances")
    op.execute(
        "INSERT INTO fleets (id, name, kind, description, production) "
        "SELECT id, name, 'datapower', description, production "
        "FROM environments")
    op.execute(
        "INSERT INTO fleet_members (fleet_id, target_id, position) "
        "SELECT environment_id, appliance_id, position "
        "FROM environment_members")
    for table in ("targets", "fleets"):
        _sync_sequence(table)

    op.drop_index("ix_credential_bindings_appliance_id", "credential_bindings")
    op.drop_index("ix_credential_bindings_environment_id",
                  "credential_bindings")
    _repoint("credential_bindings", "appliance_id", "appliances", "targets",
             "target_id")
    _repoint("credential_bindings", "environment_id", "environments",
             "fleets", "fleet_id")
    op.create_index("ix_credential_bindings_target_id", "credential_bindings",
                    ["target_id"])
    op.create_index("ix_credential_bindings_fleet_id", "credential_bindings",
                    ["fleet_id"])
    for table in _PLUGIN_TABLES:
        _repoint(table, "appliance_id", "appliances", "targets")

    op.drop_index("ix_environment_members_appliance_id",
                  "environment_members")
    op.drop_table("environment_members")
    op.drop_table("environments")
    op.drop_table("appliances")


def downgrade():
    bind = op.get_bind()
    others = bind.execute(sa.text(
        "SELECT name, kind FROM targets WHERE kind <> 'datapower' "
        "UNION SELECT name, kind FROM fleets WHERE kind <> 'datapower'"
    )).fetchall()
    extended = bind.execute(sa.text(
        "SELECT COUNT(*) FROM target_extensions")).scalar()
    if others or extended:
        raise RuntimeError(
            "MAST 3.x has only DataPower appliances. Remove the targets and "
            "fleets of other kinds ({}) and the extensions of targets before "
            "the downgrade.".format(", ".join(
                "{} ({})".format(*row) for row in others[:5]) or
                "extensions"))
    _create_old_tables()
    rows = bind.execute(sa.text(
        "SELECT id, name, address, attributes FROM targets")).mappings().all()
    insert = sa.text(
        "INSERT INTO appliances (id, name, address, {0}, tls_mode, poll, "
        "notes, created_at, updated_at) SELECT :id, :name, :address, {1}, "
        "'ca', :poll, notes, created_at, updated_at FROM targets "
        "WHERE id = :id".format(
            ", ".join(_ATTRIBUTES),
            ", ".join(":" + name for name in _ATTRIBUTES)))
    for row in rows:
        attributes = row["attributes"] or {}
        if isinstance(attributes, str):
            # SQLite gives the JSON as text
            attributes = json.loads(attributes)
        values = {name: attributes.get(name) for name in _ATTRIBUTES}
        values.update(id=row["id"], name=row["name"], address=row["address"],
                      poll=bool(attributes.get("poll", False)))
        bind.execute(insert, values)
    for endpoint, columns in (
            ("soma", "tls_mode = t.mode, ca_bundle = t.ca_bundle, "
                     "pin_sha256 = t.pin, pinned_at = t.pinned_at, "
                     "pinned_by = t.pinned_by, tls_mismatch = t.mismatch, "
                     "tls_mismatch_at = t.mismatch_at"),
            ("ssh", "ssh_host_key = t.pin, ssh_pinned_at = t.pinned_at, "
                    "ssh_pinned_by = t.pinned_by, ssh_mismatch = t.mismatch, "
                    "ssh_mismatch_at = t.mismatch_at")):
        for column_set in columns.split(", "):
            column, value = column_set.split(" = t.")
            op.execute(
                "UPDATE appliances SET {0} = (SELECT t.{1} FROM target_trust t "
                "WHERE t.target_id = appliances.id AND t.scope = '' AND "
                "t.endpoint = '{2}')".format(column, value, endpoint))
    # A TLS mode that the trust did not have is "ca"
    op.execute("UPDATE appliances SET tls_mode = 'ca' WHERE tls_mode IS NULL")
    op.execute(
        "INSERT INTO environments (id, name, description, production) "
        "SELECT id, name, description, production FROM fleets")
    op.execute(
        "INSERT INTO environment_members (environment_id, appliance_id, "
        "position) SELECT fleet_id, target_id, position FROM fleet_members")
    for table in ("appliances", "environments"):
        _sync_sequence(table)

    for table in _PLUGIN_TABLES:
        _repoint(table, "appliance_id", "targets", "appliances")
    op.drop_index("ix_credential_bindings_target_id", "credential_bindings")
    op.drop_index("ix_credential_bindings_fleet_id", "credential_bindings")
    _repoint("credential_bindings", "target_id", "targets", "appliances",
             "appliance_id")
    _repoint("credential_bindings", "fleet_id", "fleets", "environments",
             "environment_id")
    op.create_index("ix_credential_bindings_appliance_id",
                    "credential_bindings", ["appliance_id"])
    op.create_index("ix_credential_bindings_environment_id",
                    "credential_bindings", ["environment_id"])

    op.drop_index("ix_fleet_members_target_id", "fleet_members")
    op.drop_table("fleet_members")
    op.drop_table("fleets")
    op.drop_index("ix_target_trust_target_id", "target_trust")
    op.drop_table("target_trust")
    op.drop_index("ix_target_extensions_target_id", "target_extensions")
    op.drop_table("target_extensions")
    op.drop_table("targets")

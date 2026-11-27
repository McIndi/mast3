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
"""
_module_: `mast.registry.models`

The tables of the targets and the fleets, in the job database.

* `TargetRecord`: One target: its name, its base kind, its address, the
values of the attributes of its base kind, and a revision that every
change raises.
* `TargetExtension`: One extension of a target (`django` on a Linux
host), with the values of its attributes. A kind that a host has more
than once has one row for each `entry`.
* `TrustRecord`: How the identity of one endpoint of a target is
checked: a certificate authority, a pinned certificate or host key,
or not at all.
* `FleetRecord`: A named group of targets of one kind.
* `FleetMember`: One target in one fleet.

A change to these tables needs a migration in
`mast/jobs/migrations/versions`.
"""
from datetime import datetime

from sqlalchemy import (
    JSON, Boolean, ForeignKey, Integer, String, Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column

from mast.jobs.models import Base, UTCDateTime

__all__ = ["TargetRecord", "TargetExtension", "TrustRecord", "FleetRecord",
           "FleetMember"]


class TargetRecord(Base):
    __tablename__ = "targets"

    # Never changes, also when the target is renamed
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # What users type
    name: Mapped[str] = mapped_column(String(255), unique=True)
    # The base kind, which never changes (mast.plugins.Kind)
    kind: Mapped[str] = mapped_column(String(64))
    # The hostname or the IP address to connect to
    address: Mapped[str] = mapped_column(String(255))
    # The values of the attributes of the base kind that are set
    attributes: Mapped[dict] = mapped_column(JSON)
    # The version of the attributes of the kind that they were checked
    # against (mast.plugins.Kind.version)
    schema_version: Mapped[int] = mapped_column(Integer)
    # Raised by every change to the configuration of the target: its
    # name, address, attributes, extensions or trust (not its notes). A
    # job records it, and does not start if it changed
    revision: Mapped[int] = mapped_column(Integer)
    notes: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)


class TargetExtension(Base):
    __tablename__ = "target_extensions"
    __table_args__ = (UniqueConstraint("target_id", "kind", "entry"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("targets.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(64))
    # "" for a kind that the target has once; otherwise the name of the
    # entry (one Django site of several)
    entry: Mapped[str] = mapped_column(String(255))
    attributes: Mapped[dict] = mapped_column(JSON)
    schema_version: Mapped[int] = mapped_column(Integer)


class TrustRecord(Base):
    __tablename__ = "target_trust"
    __table_args__ = (UniqueConstraint("target_id", "scope", "endpoint"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("targets.id", ondelete="CASCADE"), index=True)
    # "" for an endpoint of the base kind; "KIND" or "KIND:ENTRY" for
    # one of an extension
    scope: Mapped[str] = mapped_column(String(320))
    endpoint: Mapped[str] = mapped_column(String(64))
    # TLS: "ca", "pin" or "none". SSH: "pin" or "none"
    mode: Mapped[str] = mapped_column(String(8))
    # The PEM certificates for "ca" (None: the trust store of the system)
    ca_bundle: Mapped[str | None] = mapped_column(Text)
    # TLS: the SHA-256 of the certificate. SSH: the host key
    # ("<type> <base64>") or its fingerprint ("SHA256:...")
    pin: Mapped[str | None] = mapped_column(Text)
    pinned_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    pinned_by: Mapped[str | None] = mapped_column(String(200))
    # What the endpoint showed the last time it did not match the pin
    mismatch: Mapped[str | None] = mapped_column(String(100))
    mismatch_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # Why the pin must be reviewed before MAST connects again (the
    # address or the port changed), or None
    review: Mapped[str | None] = mapped_column(Text)


class FleetRecord(Base):
    __tablename__ = "fleets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    # Every member is of this kind: its base kind, or one of its
    # extensions, is this kind or extends it
    kind: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(Text)
    production: Mapped[bool] = mapped_column(Boolean)


class FleetMember(Base):
    __tablename__ = "fleet_members"

    fleet_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("fleets.id", ondelete="CASCADE"),
        primary_key=True)
    target_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("targets.id", ondelete="CASCADE"),
        primary_key=True, index=True)
    # The order of the members
    position: Mapped[int] = mapped_column(Integer)

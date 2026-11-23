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

The tables of the appliance registry, in the job database.

* `ApplianceRecord`: One appliance: its name, its address, its ports,
how its TLS certificate and SSH host key are checked.
* `EnvironmentRecord`: A named group of appliances.
* `EnvironmentMember`: One appliance in one environment.

A change to these tables needs a migration in
`mast/jobs/migrations/versions`.
"""
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from mast.jobs.models import Base, UTCDateTime

__all__ = ["ApplianceRecord", "EnvironmentRecord", "EnvironmentMember"]


class ApplianceRecord(Base):
    __tablename__ = "appliances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # What users type: the alias of hosts.conf, or the hostname
    name: Mapped[str] = mapped_column(String(255), unique=True)
    # The hostname or the IP address to connect to
    address: Mapped[str] = mapped_column(String(255))
    # None means the default of [global] in appliances.conf
    soma_port: Mapped[int | None] = mapped_column(Integer)
    web_port: Mapped[int | None] = mapped_column(Integer)
    ssh_port: Mapped[int | None] = mapped_column(Integer)
    soma_scheme: Mapped[str | None] = mapped_column(String(8))
    soma_uri: Mapped[str | None] = mapped_column(String(255))
    soma_spec_file: Mapped[str | None] = mapped_column(String(255))
    retry_interval: Mapped[int | None] = mapped_column(Integer)
    # How the TLS certificate of the appliance is checked: "ca", "pin"
    # or "none" (see step A5)
    tls_mode: Mapped[str] = mapped_column(String(8))
    ca_bundle: Mapped[str | None] = mapped_column(Text)
    pin_sha256: Mapped[str | None] = mapped_column(String(64))
    pinned_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    pinned_by: Mapped[str | None] = mapped_column(String(200))
    # The pinned SSH host key: "<type> <base64>", or the OpenSSH
    # fingerprint "SHA256:..." when it was pinned by fingerprint
    ssh_host_key: Mapped[str | None] = mapped_column(Text)
    ssh_pinned_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    ssh_pinned_by: Mapped[str | None] = mapped_column(String(200))
    # What the appliance showed the last time a pin did not match (the
    # SHA-256 of the certificate, the fingerprint of the host key). A
    # new pin clears it.
    tls_mismatch: Mapped[str | None] = mapped_column(String(64))
    tls_mismatch_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    ssh_mismatch: Mapped[str | None] = mapped_column(String(100))
    ssh_mismatch_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # Collect the status of this appliance (see step B1)
    poll: Mapped[bool] = mapped_column(Boolean)
    notes: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)


class EnvironmentRecord(Base):
    __tablename__ = "environments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    description: Mapped[str] = mapped_column(Text)
    production: Mapped[bool] = mapped_column(Boolean)


class EnvironmentMember(Base):
    __tablename__ = "environment_members"

    environment_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("environments.id", ondelete="CASCADE"),
        primary_key=True)
    appliance_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("appliances.id", ondelete="CASCADE"),
        primary_key=True, index=True)
    # The order of the members, as environments.conf had them
    position: Mapped[int] = mapped_column(Integer)

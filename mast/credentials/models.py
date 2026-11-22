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
_module_: `mast.credentials.models`

The tables of the credential store, in the job database.

* `CredentialRecord`: One stored credential. The user name is in the
clear; the password is only in `token`, encrypted.
* `CredentialBinding`: An appliance or an environment that a
credential may be used for. Exactly one of the two is set.
* `CredentialUse`: One use of a credential by a job, on one appliance.
* `AuditEvent`: One change that an admin made. It never has a secret
in it.

A change to these tables needs a migration in
`mast/jobs/migrations/versions`.
"""
from datetime import datetime

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from mast.jobs.models import Base, UTCDateTime

__all__ = ["CredentialRecord", "CredentialBinding", "CredentialUse",
           "AuditEvent"]


class CredentialRecord(Base):
    __tablename__ = "credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    username: Mapped[str] = mapped_column(String(255))
    # The MultiFernet token of {"v": 1, "id": ..., "username": ...,
    # "password": ...}
    token: Mapped[str] = mapped_column(Text)
    # "collect" (only reads) or "change"
    purpose: Mapped[str] = mapped_column(String(8))
    description: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    updated_by: Mapped[str] = mapped_column(String(200))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_tested_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean)
    # Whether the user can change the appliance (run an action). None
    # until a test finds out.
    can_write: Mapped[bool | None] = mapped_column(Boolean)


class CredentialBinding(Base):
    __tablename__ = "credential_bindings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    credential_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("credentials.id", ondelete="CASCADE"),
        index=True)
    appliance_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("appliances.id", ondelete="CASCADE"),
        index=True)
    environment_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("environments.id", ondelete="CASCADE"),
        index=True)


class CredentialUse(Base):
    __tablename__ = "credential_uses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    credential_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("credentials.id", ondelete="CASCADE"),
        index=True)
    job_id: Mapped[str] = mapped_column(String(32), index=True)
    appliance: Mapped[str] = mapped_column(String(255))
    principal: Mapped[str] = mapped_column(String(200))
    purpose: Mapped[str] = mapped_column(String(8))
    used_at: Mapped[datetime] = mapped_column(UTCDateTime)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    principal: Mapped[str] = mapped_column(String(200))
    # For example "credential.create"
    action: Mapped[str] = mapped_column(String(64))
    # What was changed, for example "credential:dp-collect"
    object: Mapped[str] = mapped_column(String(255))
    detail: Mapped[dict] = mapped_column(JSON)

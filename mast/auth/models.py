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
_module_: `mast.auth.models`

The table of the local users, in the job database.

* `User`: One person who can log in to the web UI.

A change to this table needs a migration in
`mast/jobs/migrations/versions`, as for the tables of the job queue.
"""
from datetime import datetime

from sqlalchemy import Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from mast.jobs.models import Base, UTCDateTime

__all__ = ["User"]


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Always lower case
    name: Mapped[str] = mapped_column(String(64), unique=True)
    # See mast.auth.passwords
    password_hash: Mapped[str] = mapped_column(String(255))
    # "viewer", "operator" or "admin", see mast.auth.roles
    role: Mapped[str] = mapped_column(String(16))
    disabled: Mapped[bool] = mapped_column(Boolean)
    # The user must choose a new password before anything else
    must_change: Mapped[bool] = mapped_column(Boolean)
    # A session of the web UI is valid only with the epoch that it was
    # made with. A new epoch logs out every session of the user.
    session_epoch: Mapped[int] = mapped_column(Integer)
    failed_logins: Mapped[int] = mapped_column(Integer)
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

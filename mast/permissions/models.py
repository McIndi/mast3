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
_module_: `mast.permissions.models`

The tables of the permission sets and the groups, in the job database.

* `PermissionSet`: A named list of rules (see `mast.permissions`). The
scopes of its rules hold the ids of targets and fleets, not their
names.
* `UserGroup`: A named group of users.
* `GroupPermissionSet`: A permission set that a group has.
* `GroupMember`: A user in a group.
* `PluginGroup`: A group that a plugin defines and MAST made once, so
that it is not made again after an admin removed it.

A change to these tables needs a migration in
`mast/jobs/migrations/versions`.
"""
from datetime import datetime

from sqlalchemy import JSON, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from mast.jobs.models import Base, UTCDateTime

__all__ = ["PermissionSet", "UserGroup", "GroupPermissionSet",
           "GroupMember", "PluginGroup"]


class PermissionSet(Base):
    __tablename__ = "permission_sets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    description: Mapped[str] = mapped_column(Text)
    # [{"path": ..., "allow": [...], "deny": [...], "scope": ...}]
    rules: Mapped[list] = mapped_column(JSON)
    updated_by: Mapped[str] = mapped_column(String(200))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)


class UserGroup(Base):
    __tablename__ = "user_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    description: Mapped[str] = mapped_column(Text)


class GroupPermissionSet(Base):
    __tablename__ = "group_permission_sets"

    group_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user_groups.id", ondelete="CASCADE"),
        primary_key=True)
    set_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("permission_sets.id", ondelete="CASCADE"),
        primary_key=True, index=True)


class GroupMember(Base):
    __tablename__ = "group_members"

    group_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user_groups.id", ondelete="CASCADE"),
        primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True, index=True)


class PluginGroup(Base):
    __tablename__ = "plugin_groups"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(64))
    made_at: Mapped[datetime] = mapped_column(UTCDateTime)

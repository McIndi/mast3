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
_module_: `mast.jobs.auth`

Who asks the job queue for something, and whether they may.

There is no authentication yet. Every caller is `LOCAL` and
`authorize` permits everything. The queue already records the
principal of every job and calls `authorize` before every action, so
that authentication and role-based access control can be added here
without a change to the queue or to the stored jobs.
"""
from dataclasses import dataclass

__all__ = ["Principal", "LOCAL", "Forbidden", "authorize"]


@dataclass(frozen=True)
class Principal(object):
    """
    _class_: `mast.jobs.auth.Principal(name)`

    The identity of a caller. `name` is stored with each job that the
    caller submits.
    """
    name: str


LOCAL = Principal("local")


class Forbidden(Exception):
    """The principal may not do what it asked for."""


def authorize(principal, action, command=None, targets=()):
    """
    _function_: `mast.jobs.auth.authorize(principal, action,
    command=None, targets=())`

    Raise `Forbidden` if `principal` may not perform `action`.

    Parameters:

    * `principal`: The `Principal` that asks.
    * `action`: `"submit"`, `"read"` or `"cancel"`.
    * `command`: The id of the command of the job, for example
    `backups.normal-backup`.
    * `targets`: The appliances that the job talks to.
    """
    return None

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

A `Principal` is a name and a role. The queue records the name with
every job, and it calls `authorize` before every action. `authorize`
checks the role against the table in `mast.auth.roles`.

* A user of the web UI is `Principal(<user name>, <role of the user>)`.
* The command line is `LOCAL`: `Principal("os:<account>", "admin")`,
where `<account>` is the name of the operating system account that
runs it. Whoever can run MAST on its host can read its key files and
its database, so the command line is an admin. The name of the
account is what the queue records.

Jobs that MAST 3.15.2 and older queued have the principal `local`.
They stay as they are. An admin can cancel them; an operator cannot,
because they are not the operator's jobs.
"""
import getpass
import os
from dataclasses import dataclass

from mast.auth import roles

__all__ = ["Principal", "LOCAL", "Forbidden", "authorize", "os_principal"]


@dataclass(frozen=True)
class Principal(object):
    """
    _class_: `mast.jobs.auth.Principal(name, role)`

    The identity of a caller. `name` is stored with each job that the
    caller submits. `role` is one of `mast.auth.roles.ROLES`.
    """
    name: str
    role: str


def os_principal():
    """
    _function_: `mast.jobs.auth.os_principal()`

    Return the principal of the command line: the operating system
    account that runs MAST, with the role `admin`.
    """
    try:
        account = getpass.getuser()
    except Exception:
        # No user name in the environment or in the password database,
        # as in some containers
        account = "uid-{}".format(os.getuid()) if hasattr(os, "getuid") \
            else "unknown"
    return Principal("os:{}".format(account), roles.ADMIN)


LOCAL = os_principal()


class Forbidden(Exception):
    """The principal may not do what it asked for."""


def authorize(principal, action, command=None, targets=(), owner=None):
    """
    _function_: `mast.jobs.auth.authorize(principal, action,
    command=None, targets=(), owner=None)`

    Raise `Forbidden` if `principal` may not perform `action`.

    Parameters:

    * `principal`: The `Principal` that asks.
    * `action`: One of `mast.auth.roles.ACTIONS`, or `"cancel"`: an
    admin may cancel any job, and an operator only a job that `owner`
    is the principal of.
    * `command`: The id of the command of the job, for example
    `backups.normal-backup`.
    * `targets`: The appliances that the job talks to.
    * `owner`: For `"cancel"`, the name of the principal that
    submitted the job.
    """
    role = getattr(principal, "role", None)
    name = getattr(principal, "name", None)
    if action == "cancel":
        if roles.allowed(role, "cancel-any"):
            return None
        if roles.allowed(role, "cancel-own") and owner == name:
            return None
        raise Forbidden(
            "{} may not cancel this job: {}".format(
                principal.name,
                "only an admin may cancel the jobs of others"
                if roles.allowed(role, "cancel-own")
                else "the role {} cannot cancel jobs".format(role)))
    if not roles.allowed(role, action):
        raise Forbidden("{} (role {}) may not {}{}".format(
            name, role, action,
            " {}".format(command) if command else ""))
    return None

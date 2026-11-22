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
_module_: `mast.auth.roles`

What each role may do. There are three roles, and each one may do
all that the one before it may do:

* `viewer`: Read the jobs, the status and the inventory.
* `operator`: Also run commands, cancel their own jobs, and use the
stored credentials that an admin made for them.
* `admin`: Also cancel any job, and manage the credentials, the
appliances, the users and the schedules.

The rules are the table `MATRIX`. `mast.jobs.auth.authorize` uses it
for every action of the job queue.
"""

__all__ = ["ROLES", "ACTIONS", "MATRIX", "allowed", "UnknownAction"]

VIEWER = "viewer"
OPERATOR = "operator"
ADMIN = "admin"

ROLES = (VIEWER, OPERATOR, ADMIN)

# Each action, and the roles that may do it.
MATRIX = {
    # Look at jobs, status and inventory
    "read": {VIEWER, OPERATOR, ADMIN},
    # Put a job in the queue. This is the same for a command that only
    # reads and for one that changes the configuration.
    "submit": {OPERATOR, ADMIN},
    # Cancel a job that the same principal submitted
    "cancel-own": {OPERATOR, ADMIN},
    # Cancel any job
    "cancel-any": {ADMIN},
    # Run a job with a stored credential of purpose "collect"
    "use-credential": {OPERATOR, ADMIN},
    # Run a job with a stored credential of purpose "change". An
    # operator may do this only through an approved change plan,
    # which is checked where plans are run, not here.
    "use-change-credential": {ADMIN},
    "manage-credential": {ADMIN},
    "manage-appliance": {ADMIN},
    "manage-user": {ADMIN},
    "manage-schedule": {ADMIN},
    # Approve a change plan. Nobody may approve their own plan, which
    # is checked where plans are approved.
    "approve": {ADMIN},
}

ACTIONS = tuple(MATRIX)


class UnknownAction(ValueError):
    """An action that is not in `MATRIX`. This is a mistake in the
    code, so it is not the same as `Forbidden`."""


def allowed(role, action):
    """
    _function_: `mast.auth.roles.allowed(role, action)`

    Return True if `role` may do `action`. An unknown role may do
    nothing. An unknown action raises `UnknownAction`.

    Parameters:

    * `role`: `"viewer"`, `"operator"` or `"admin"`.
    * `action`: One of `ACTIONS`.
    """
    try:
        roles = MATRIX[action]
    except KeyError:
        raise UnknownAction(action)
    return role in roles

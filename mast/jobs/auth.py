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

Who asks MAST for something, and whether they may (step 6 of
docs/design/mast-4.0.md).

A `Principal` is the name that MAST records (with a job, in the audit
log) and the user of MAST whose permission sets decide
(`mast.permissions`). There are no roles: what a user may do is the
rules of the sets of their groups.

* A user of the web UI, of `mast login` or of a token is
`Principal(<user name>)`.
* The jobs of a schedule are `Principal("schedule:NAME", user=<the user
who made it>)`.
* `SYSTEM` is MAST itself: the worker, the scheduler and the code that
calls the library without a principal. It may do everything, and it is
never the principal of a person: the command line acts only for a user
(`cli_principal`, see `mast.auth.identity`).

`authorize` is the one check of MAST. Its actions are:

* a command id: `core.add-target`, `datapower.save`. It needs `x` on
the path of the command, and `w` for a command whose effect is
`change`, on every target in `targets`.
* `"read"`: the views of the jobs (`core.list-jobs`).
* `"submit"`: run `command` on `targets` (as a command id above). Without
a command, whether the user may run any command of a plugin at all.
* `"cancel"`: `core.cancel-job`, or `mast/core/cancel-own-job` for a job
that `owner`, the principal, submitted.
* `"use-credential"`: `x` on `mast/credentials/NAME` (`resource`) for
every target.

Jobs that MAST 3.x queued have the principal `local` or `os:<account>`:
nobody acts for them any more, so they do not run again; an admin can
still cancel them.
"""
from dataclasses import dataclass

__all__ = ["Principal", "SYSTEM", "Forbidden", "Denied", "authorize", "may",
           "principal_of", "cli_principal", "user_principal"]


@dataclass(frozen=True)
class Principal(object):
    """
    _class_: `mast.jobs.auth.Principal(name, user=None, system=False)`

    The identity of a caller. `name` is stored with each job that the
    caller submits. `user` is the user of MAST whose permissions decide
    (`name` if not given). `system` is MAST itself (`SYSTEM`).
    """
    name: str
    user: str = None
    system: bool = False

    @property
    def acts_for(self):
        """The user of MAST whose permission sets decide."""
        return self.user or self.name


# MAST itself
SYSTEM = Principal("mast", system=True)


class Forbidden(Exception):
    """The principal may not do what it asked for."""


class Denied(Forbidden):
    """The permission sets of the user do not allow it, or there is no
    such user any more: unlike a credential that is not bound, only an
    admin who changes the permissions or the user can fix it."""


def cli_principal():
    """
    _function_: `mast.jobs.auth.cli_principal()`

    Return the `Principal` of the command line: the user of its token
    (`mast login`, `mast --as NAME`, `MAST_TOKEN`), see
    `mast.auth.identity`.
    """
    # Imported here: the identity reads the tokens in the database
    from mast.auth import identity
    return identity.cli_principal()


def user_principal(name):
    """
    _function_: `mast.jobs.auth.user_principal(name)`

    Return the `Principal` of the user `name`, or raise `Forbidden` if
    there is no such user or the user is disabled.
    """
    _user(name)
    return Principal(name)


def _user(name):
    # Imported here: the users are in the job database
    from mast.auth import users
    try:
        found = users.get(name)
    except (users.UserNotFound, users.InvalidName):
        raise Denied("{} is not a user of MAST".format(name))
    if found.disabled:
        raise Denied("the user {} is disabled".format(name))
    return found


def principal_of(name):
    """
    _function_: `mast.jobs.auth.principal_of(name)`

    Return the `Principal` that a job records as `submitted_by`, as it
    is now. Raises `Forbidden` if nobody may act for it any more: a
    user who was removed or disabled, a schedule whose maker was, or a
    job of MAST 3.x (`local`, `os:<account>`).
    """
    # Imported here: these use the job database
    from mast.jobs import schedules
    if name == SYSTEM.name:
        return SYSTEM
    if name == "local" or name.startswith("os:"):
        raise Denied(
            "{} submitted it without an identity of MAST (before MAST 4.0); "
            "submit it again".format(name))
    if name.startswith(schedules.PRINCIPAL_PREFIX):
        try:
            info = schedules.get(name[len(schedules.PRINCIPAL_PREFIX):],
                                 principal=SYSTEM)
        except schedules.UnknownSchedule:
            raise Denied("the schedule of {} no longer exists".format(name))
        who, reason = schedules.principal_for(info.created_by)
        if who is None:
            raise Denied(reason)
        return Principal(name, user=who.acts_for)
    return user_principal(name)


def _fail(principal, what, decisions):
    denied = [d for d in decisions if not d.allowed]
    reasons = sorted({d.reason for d in denied})
    raise Denied("{} may not {}: {}".format(
        principal.name, what, "; ".join(reasons[:3]) +
        (" (and {} more)".format(len(reasons) - 3) if len(reasons) > 3
         else "")))


def _check_command(principal, command, targets):
    from mast import permissions
    decisions = permissions.check_command(principal.acts_for, command,
                                          list(targets or ()))
    if not all(d.allowed for d in decisions):
        _fail(principal, "run {}".format(command), decisions)


def authorize(principal, action, command=None, targets=(), owner=None,
              resource=None):
    """
    _function_: `mast.jobs.auth.authorize(principal, action,
    command=None, targets=(), owner=None, resource=None)`

    Raise `Forbidden` if `principal` may not perform `action` (see the
    module).

    Parameters:

    * `principal`: The `Principal` that asks.
    * `action`: A command id, or `"read"`, `"submit"`, `"cancel"` or
    `"use-credential"`.
    * `command`: For `"submit"`, the id of the command of the job.
    * `targets`: The names of the targets and fleets that it is for.
    * `owner`: For `"cancel"`, the name of the principal that submitted
    the job.
    * `resource`: For `"use-credential"`, the name of the credential.
    """
    if principal is None or not isinstance(principal, Principal):
        raise Denied("there is no identity of MAST")
    if principal.system:
        return None
    from mast import permissions
    _user(principal.acts_for)
    if action == "read":
        return _check_command(principal, "core.list-jobs", ())
    if action == "submit":
        if command is None:
            if permissions.may_run_any(principal.acts_for):
                return None
            raise Denied("{} may not run any command of a plugin".format(
                principal.name))
        return _check_command(principal, command, targets)
    if action == "cancel":
        any_job = permissions.decide(principal.acts_for, "x",
                                     "mast/core/cancel-job")
        if any_job.allowed:
            return None
        own = permissions.decide(principal.acts_for, "x",
                                 "mast/core/cancel-own-job")
        if own.allowed and owner == principal.name:
            return None
        raise Denied("{} may not cancel this job: {}".format(
            principal.name, "it may cancel only its own jobs"
            if own.allowed else any_job.reason))
    if action == "use-credential":
        path = "mast/credentials/{}".format(resource)
        names = _target_names(targets) or [None]
        rules = permissions.rules_of(principal.acts_for)
        decisions = [permissions.decide(principal.acts_for, "x", path, name,
                                        rules) for name in names]
        if not all(d.allowed for d in decisions):
            _fail(principal, "use the credential {}".format(resource),
                  decisions)
        return None
    if "." in action:
        return _check_command(principal, action, targets)
    raise ValueError("authorize: no action {!r}".format(action))


def _target_names(targets):
    if not targets:
        return []
    from mast import registry
    return [t.name for t in registry.resolve(list(targets))]


def may(principal, action, **kwargs):
    """
    _function_: `mast.jobs.auth.may(principal, action, **kwargs)`

    Return True if `authorize` lets `principal` perform `action`: for
    what a page shows.
    """
    try:
        authorize(principal, action, **kwargs)
    except Forbidden:
        return False
    return True

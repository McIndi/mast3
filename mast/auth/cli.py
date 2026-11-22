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
_module_: `mast.auth.cli`

`mast-users`: manage the local users of the web UI.

* `add`: Add a user, with a temporary password.
* `set-password`: Give a user a new password.
* `set-role`: Give a user another role.
* `disable` and `enable`: Stop a user from logging in, or let them
again.
* `delete`: Remove a user.
* `list`: The users.

A password is never an option, so that it is not in the history of
the shell or in the list of processes. `mast-users` asks for it.

Whoever can run `mast-users` on the MAST host can read the database
and the key files, so `mast-users` needs no login.
"""
import getpass
import sys
from typing import Literal

from mast.auth import passwords, roles, users
from mast.cli import Cli, Option, Params, Table, Text

__all__ = ["cli"]

cli = Cli(description="Manage the users of the MAST web UI.",
          prog="mast-users")

Role = Literal[roles.VIEWER, roles.OPERATOR, roles.ADMIN]


def _when(time):
    return "" if time is None else time.astimezone().strftime(
        "%Y-%m-%d %H:%M:%S")


def _fail(message):
    print("mast-users: {}".format(message), file=sys.stderr)
    sys.exit(1)


def _ask_password(name):
    """Ask for a new password twice. Exit if the two are not the same
    or the password is too weak."""
    first = getpass.getpass("New password for {}: ".format(name))
    second = getpass.getpass("The same password again: ")
    if first != second:
        _fail("The two passwords are not the same. Nothing changed.")
    try:
        passwords.check_strength(first, name)
    except passwords.WeakPassword as exc:
        _fail(exc)
    return first


def _do(change, *args):
    try:
        return change(*args)
    except users.UserNotFound as exc:
        _fail("There is no user {}.".format(exc))
    except (users.UserExists, users.InvalidName, users.InvalidRole,
            passwords.WeakPassword) as exc:
        if isinstance(exc, users.UserExists):
            _fail("There is already a user {}.".format(exc))
        _fail(exc)


class UserParams(Params):
    user: str = Option(short="u", help="The name of the user.")


class Add(UserParams):
    role: Role = Option(
        short="r", help="viewer: reads only. operator: also runs commands. "
                        "admin: also manages users, appliances and "
                        "credentials.")


class SetPassword(UserParams):
    temporary: bool = Option(
        False, short="t",
        help="The user must choose a new password at the next login.")


class SetRole(UserParams):
    role: Role = Option(short="r", help="The new role.")


@cli.command("add")
def add(params: Add):
    """Add a user. mast-users asks for the first password, which is
    temporary: the user chooses a new one at the first login.

    To make the first admin of a new MAST home:

        mast-users add -u NAME -r admin"""
    name = _do(users.normalize, params.user)
    password = _ask_password(name)
    info = _do(users.add, name, password, params.role)
    return Text("Added the user {} with the role {}. They choose a new "
                "password when they first log in.".format(
                    info.name, info.role))


@cli.command("set-password")
def set_password(params: SetPassword):
    """Give a user a new password, and log out every session of the
    user. This also ends a lock after failed logins."""
    name = _do(users.normalize, params.user)
    _do(users.get, name)
    password = _ask_password(name)
    _do(users.set_password, name, password, params.temporary)
    return Text("Set a new {}password for {}.".format(
        "temporary " if params.temporary else "", name))


@cli.command("set-role")
def set_role(params: SetRole):
    """Give a user another role, and log out every session of the
    user."""
    info = _do(users.set_role, params.user, params.role)
    return Text("{} now has the role {}.".format(info.name, info.role))


@cli.command("disable")
def disable(params: UserParams):
    """Stop a user from logging in, and log out every session of the
    user. The user and their jobs stay."""
    info = _do(users.disable, params.user)
    return Text("Disabled {}.".format(info.name))


@cli.command("enable")
def enable(params: UserParams):
    """Let a disabled user log in again."""
    info = _do(users.enable, params.user)
    return Text("Enabled {}.".format(info.name))


@cli.command("delete")
def delete(params: UserParams):
    """Remove a user. The jobs of the user stay, with the name of the
    user."""
    _do(users.delete, params.user)
    return Text("Deleted {}.".format(users.normalize(params.user)))


@cli.command("list")
def list_users(params: Params):
    """List the users."""
    found = users.list_users()
    if not found:
        return Text("There are no users. Add the first admin with "
                    "mast-users add -u NAME -r admin")
    return Table(
        ("User", "Role", "State", "Created", "Last login"),
        [(user.name, user.role,
          "disabled" if user.disabled
          else "must change password" if user.must_change else "",
          _when(user.created_at), _when(user.last_login_at))
         for user in found])

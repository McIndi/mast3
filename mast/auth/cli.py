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

`mast users`: manage the local users of the web UI.

* `add`: Add a user, with a temporary password.
* `set-password`: Give a user a new password.
* `set-role`: Give a user another role.
* `disable` and `enable`: Stop a user from logging in, or let them
again.
* `delete`: Remove a user.
* `list`: The users.
* `add-service`: Add a service account, for cron jobs, scripts and
services, with a token file (`mast --as NAME ...`).
* `rotate-token`: Give a service account a new token file.
* `tokens`, `revoke-token`: The tokens of `mast login` and of the
service accounts.

A password is never an option, so that it is not in the history of
the shell or in the list of processes. `mast users` asks for it.

Whoever can run `mast users` on the MAST host can read the database
and the key files, so `mast users` needs no login.
"""
import getpass
import sys
from typing import Literal

from mast.auth import passwords, roles, users
from mast.cli import Cli, Option, Params, Table, Text
from mast.roots import CORE

__all__ = ["cli"]

cli = Cli(description="Manage the users of MAST.", provider=CORE,
          prog="mast users", queued=False)

Role = Literal[roles.VIEWER, roles.OPERATOR, roles.ADMIN]


def _when(time):
    return "" if time is None else time.astimezone().strftime(
        "%Y-%m-%d %H:%M:%S")


def _fail(message):
    print("mast users: {}".format(message), file=sys.stderr)
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


@cli.command("add", effect="change", id="add-user")
def add(params: Add):
    """Add a user. mast users asks for the first password, which is
    temporary: the user chooses a new one at the first login.

    To make the first admin of a new MAST home:

        mast users add -u NAME -r admin"""
    name = _do(users.normalize, params.user)
    password = _ask_password(name)
    info = _do(users.add, name, password, params.role)
    return Text("Added the user {} with the role {}. They choose a new "
                "password when they first log in.".format(
                    info.name, info.role))


@cli.command("set-password", effect="change", id="set-user-password")
def set_password(params: SetPassword):
    """Give a user a new password, and log out every session of the
    user. This also ends a lock after failed logins."""
    name = _do(users.normalize, params.user)
    _do(users.get, name)
    password = _ask_password(name)
    _do(users.set_password, name, password, params.temporary)
    return Text("Set a new {}password for {}.".format(
        "temporary " if params.temporary else "", name))


@cli.command("set-role", effect="change", id="set-user-role")
def set_role(params: SetRole):
    """Give a user another role, and log out every session of the
    user."""
    info = _do(users.set_role, params.user, params.role)
    return Text("{} now has the role {}.".format(info.name, info.role))


@cli.command("disable", effect="change", id="disable-user")
def disable(params: UserParams):
    """Stop a user from logging in, and log out every session of the
    user. The user and their jobs stay."""
    info = _do(users.disable, params.user)
    return Text("Disabled {}.".format(info.name))


@cli.command("enable", effect="change", id="enable-user")
def enable(params: UserParams):
    """Let a disabled user log in again."""
    info = _do(users.enable, params.user)
    return Text("Enabled {}.".format(info.name))


@cli.command("delete", effect="change", id="delete-user")
def delete(params: UserParams):
    """Remove a user. The jobs of the user stay, with the name of the
    user."""
    _do(users.delete, params.user)
    return Text("Deleted {}.".format(users.normalize(params.user)))


@cli.command("list", effect="read", id="list-users")
def list_users(params: Params):
    """List the users."""
    found = users.list_users()
    if not found:
        return Text("There are no users. Add the first admin with "
                    "mast users add -u NAME -r admin")
    return Table(
        ("User", "Kind", "Role", "State", "Created", "Last login"),
        [(user.name, user.kind, user.role,
          "disabled" if user.disabled
          else "must change password" if user.must_change else "",
          _when(user.created_at), _when(user.last_login_at))
         for user in found])


class AddService(UserParams):
    role: Role = Option(
        short="r", help="viewer, operator or admin (until the permission "
                        "sets decide alone).")
    group: list[str] = Option(
        default_factory=list, short="g",
        help="A group to put it in (mast groups list), such as "
             "datapower-reader. Give it again for each.")
    print_token: bool = Option(
        False, help="Print the token instead of writing the token file, "
                    "for a service on another host. It is shown only "
                    "once.")


def _issue_service_token(name, print_token):
    """Make a token for the service account, write its file (or print
    it) and return the lines to show."""
    from mast.auth import identity, tokens
    _, token = tokens.issue(name, "service account {}".format(name),
                            principal=_who())
    if print_token:
        return ["The token of {} (shown only now):".format(name), token]
    path = identity.service_token_file(name)
    identity.write_token(path, token)
    return ["Wrote its token to {}, which only you can read.".format(path),
            "Run MAST as {} with: mast --as {} ROOT COMMAND ...".format(
                name, name),
            "for example in a crontab: mast --as {} datapower "
            "normal-backup -a lab ...".format(name)]


def _who():
    from mast.jobs.auth import cli_principal
    return cli_principal().name


@cli.command("add-service", effect="change", id="add-service-account")
def add_service(params: AddService):
    """Add a service account: an identity for cron jobs, scripts and
    services, with no password. Its token is written to
    etc/local/tokens/NAME.token, which only the owner of the home can
    read, and mast --as NAME uses it. Put it in the groups whose
    permissions it needs."""
    from mast import permissions
    name = _do(users.normalize, params.user)
    info = _do(users.add_service, name, params.role)
    for group in params.group:
        try:
            permissions.add_member(group, name)
        except permissions.PermissionError as exc:
            _fail(exc)
    lines = ["Added the service account {} with the role {}{}.".format(
        info.name, info.role, " in " + ", ".join(params.group)
        if params.group else "")]
    return Text("\n".join(lines + _issue_service_token(
        name, params.print_token)))


class RotateToken(UserParams):
    print_token: bool = Option(
        False, help="Print the new token instead of writing the file.")


@cli.command("rotate-token", effect="change", id="rotate-service-token")
def rotate_token(params: RotateToken):
    """Revoke every token of a service account and give it a new token
    file."""
    from mast.auth import tokens
    info = _do(users.get, params.user)
    if info.kind != users.SERVICE:
        _fail("{} is not a service account: a person logs in with mast "
              "login.".format(info.name))
    count = tokens.revoke_all(info.name)
    return Text("\n".join(["Revoked {} token(s) of {}.".format(
        count, info.name)] + _issue_service_token(info.name,
                                                  params.print_token)))


class Tokens(Params):
    user: str | None = Option(None, short="u", help="Only of this user.")
    all: bool = Option(False, short="a",
                       help="Also the revoked and expired tokens.")


@cli.command("tokens", effect="read", id="list-tokens")
def list_tokens(params: Tokens):
    """List the tokens (never their secrets): of mast login, and of the
    service accounts."""
    from mast.auth import tokens
    found = [t for t in tokens.list_tokens(params.user)
             if params.all or t.active]
    return Table(("Id", "User", "For", "Created", "Last used", "Expires",
                  "State"),
                 [(t.id, t.user, t.name, _when(t.created_at),
                   _when(t.last_used_at), _when(t.expires_at),
                   "active" if t.active else "revoked"
                   if t.revoked_at else "expired") for t in found])


class RevokeToken(Params):
    id: int = Option(short="i", help="The id of the token (mast users "
                                     "tokens).")


@cli.command("revoke-token", effect="change", id="revoke-token")
def revoke_token(params: RevokeToken):
    """Revoke a token. Whoever uses it is refused from now on."""
    from mast.auth import tokens
    try:
        found = tokens.revoke(params.id)
    except tokens.BadToken as exc:
        _fail(exc)
    return Text("Revoked the token {} of {}.".format(found.id, found.user))

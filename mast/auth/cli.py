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

`mast users`: manage the local users of MAST.

* `add-first-admin`: Make the first admin of a home, or a new one when
no user can manage the users any more.
* `add`: Add a user, with a temporary password, in groups.
* `set-password`: Give a user a new password.
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

Each command needs what its path allows (`mast/core/add-user`, ...),
like every command of core: an admin. Only `add-first-admin` works
without an identity, and only while no enabled user may add users:
whoever can run MAST on its host can read its database and key files,
so this is no new power, and it is how a home gets its first admin.
What a user may do is the permission sets of their groups: `mast
groups` puts users in groups.
"""
import getpass
import sys
from mast import permissions
from mast.auth import passwords, users
from mast.cli import Cli, Option, Params, Table, Text
from mast.roots import CORE

__all__ = ["cli"]

cli = Cli(description="Manage the users of MAST.", provider=CORE,
          prog="mast users", queued=False)

# The groups of the first admin: everything that core can do, and the
# views of a viewer
FIRST_ADMIN_GROUPS = ("viewer", "operator", "admin")


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
    except (users.UserExists, users.InvalidName, permissions.UnknownName,
            passwords.WeakPassword) as exc:
        if isinstance(exc, users.UserExists):
            _fail("There is already a user {}.".format(exc))
        _fail(exc)


class UserParams(Params):
    user: str = Option(short="u", help="The name of the user.")


class Add(UserParams):
    group: list[str] = Option(
        default_factory=list, short="g",
        help="A group to put the user in (mast groups list): viewer, "
             "operator, admin, datapower-reader, ... Give it again for "
             "each. Without one, the user may do nothing yet.")


class SetPassword(UserParams):
    temporary: bool = Option(
        False, short="t",
        help="The user must choose a new password at the next login.")


@cli.command("add", effect="change", id="add-user")
def add(params: Add):
    """Add a user, in groups. mast users asks for the first password,
    which is temporary: the user chooses a new one at the first login.

    To make the first admin of a new MAST home, use add-first-admin."""
    name = _do(users.normalize, params.user)
    for group in params.group:
        _do(permissions.get_group, group)
    password = _ask_password(name)
    info = _do(users.add, name, password, params.group)
    return Text("Added the user {} in {}. They choose a new password when "
                "they first log in.".format(
                    info.name, ", ".join(params.group) or
                    "no group (they may do nothing yet)"))


class FirstAdmin(UserParams):
    service: bool = Option(
        False, help="Make a service account instead, with a token file "
                    "(mast --as NAME): for a home that is set up by a "
                    "script, such as a container or a playbook.")


def _somebody_manages_users():
    for user in users.list_users():
        if not user.disabled and permissions.decide(
                user.name, "x", "mast/core/add-user").allowed:
            return user.name
    return None


@cli.command("add-first-admin", effect="change", id="add-first-admin")
def add_first_admin(params: FirstAdmin):
    """Make the first admin of this home: a user in the groups viewer,
    operator and admin. It needs no login, and works only while no
    enabled user may add users, so it is also how to get back in when
    every admin is gone. mast users asks for the password, which the
    admin chose: they need not change it. Then log in with mast login.

    With --service, it makes a service account with a token file
    instead, for a home that a script sets up."""
    found = _somebody_manages_users()
    if found is not None:
        _fail("{} can already manage the users of this home: ask them, or "
              "log in as them (mast login).".format(found))
    name = _do(users.normalize, params.user)
    if params.service:
        _do(users.add_service, name, list(FIRST_ADMIN_GROUPS))
        return Text("\n".join(
            ["Added the service account {} in {}.".format(
                name, ", ".join(FIRST_ADMIN_GROUPS))] +
            _issue_service_token(name, False, issuer=name)) +
            _plugin_hint(name))
    password = _ask_password(name)
    _do(users.add, name, password, list(FIRST_ADMIN_GROUPS), False)
    return Text("Added the first admin {} in {}. Log in with: mast login "
                "-u {}{}".format(name, ", ".join(FIRST_ADMIN_GROUPS), name,
                                 _plugin_hint(name)))


def _plugin_hint(name):
    """What to run for the commands of the plugins, which no group of
    core grants."""
    found = [g.name for g in permissions.list_groups()
             if g.name not in FIRST_ADMIN_GROUPS
             and g.sets == (g.name,) and not g.members]
    if not found:
        return ""
    return ("\nThe commands of the plugins are in their own groups ({}): "
            "mast groups add-member -n GROUP -u {}".format(", ".join(found),
                                                          name))


add_first_admin_command = cli.commands["add-first-admin"]
add_first_admin_command.anonymous = True


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
                    "mast users add-first-admin -u NAME")
    return Table(
        ("User", "Kind", "Groups", "State", "Created", "Last login"),
        [(user.name, user.kind, ", ".join(permissions.groups_of(user.name)),
          "disabled" if user.disabled
          else "must change password" if user.must_change else "",
          _when(user.created_at), _when(user.last_login_at))
         for user in found])


class AddService(UserParams):
    group: list[str] = Option(
        default_factory=list, short="g",
        help="A group to put it in (mast groups list), such as "
             "datapower-reader. Give it again for each.")
    print_token: bool = Option(
        False, help="Print the token instead of writing the token file, "
                    "for a service on another host. It is shown only "
                    "once.")


def _issue_service_token(name, print_token, issuer=None):
    """Make a token for the service account, write its file (or print
    it) and return the lines to show."""
    from mast.auth import identity, tokens
    _, token = tokens.issue(name, "service account {}".format(name),
                            principal=issuer or _who())
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
    name = _do(users.normalize, params.user)
    info = _do(users.add_service, name, params.group)
    lines = ["Added the service account {} in {}.".format(
        info.name, ", ".join(params.group) or
        "no group (it may do nothing yet)")]
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

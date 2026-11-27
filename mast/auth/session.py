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
_module_: `mast.auth.session`

`mast login`, `mast logout` and `mast whoami`: the identity of a person
on the command line (see `mast.auth.identity`).

* `mast login -u NAME`: asks for the password, and keeps a token that
expires after the `max_hours` of `[auth]` in `server.conf` (12 hours by
default) in the account of the person, for this home.
* `mast logout`: revokes that token and removes it.
* `mast whoami`: who the command line acts for, and why.
"""
import argparse
import getpass
import os
import socket
import sys

__all__ = ["login", "logout", "whoami"]


def _hours():
    try:
        from mast.config import get_configs_dict
        conf = get_configs_dict().get("server.conf", {})
        return float(conf.get("auth", {}).get("max_hours", 12))
    except Exception:
        return 12.0


def login(argv):
    """`mast login`: see the module."""
    parser = argparse.ArgumentParser(
        prog="mast login",
        description="Log in on the command line: ask for the password of a "
                    "user of MAST, and keep a token that expires, for this "
                    "home, in your account. MAST then acts for that user.")
    parser.add_argument("-u", "--user", required=True, help="The user.")
    parser.add_argument("--password-stdin", action="store_true",
                        help="Read the password from the first line of "
                             "standard input, for scripts.")
    args = parser.parse_args(argv)
    from mast.auth import identity, tokens, users
    if args.password_stdin:
        password = sys.stdin.readline().rstrip("\r\n")
    else:
        password = getpass.getpass("Password of {}: ".format(args.user))
    try:
        user = users.authenticate(args.user, password)
    except users.LoginFailed:
        # The same message for every reason: it does not tell whether
        # the user exists
        sys.stderr.write(
            "mast login: The user name or the password is not correct, or "
            "the user is disabled or locked. A service account has no "
            "password: use mast --as NAME.\n")
        return 1
    if user.must_change:
        sys.stderr.write("mast login: {} must choose a new password first "
                         "(in the web UI, or mast users set-password).\n"
                         .format(user.name))
        return 1
    hours = _hours()
    try:
        account = getpass.getuser()
    except Exception:
        account = "?"
    info, token = tokens.issue(
        user.name, "mast login on {} by {}".format(socket.gethostname(),
                                                  account),
        hours=hours, principal=user.name)
    identity.write_token(identity.login_file(), token)
    identity.reset()
    print("Logged in as {} until {}.".format(
        user.name, info.expires_at.astimezone().strftime("%Y-%m-%d %H:%M")))
    return 0


def logout(argv):
    """`mast logout`: see the module."""
    argparse.ArgumentParser(
        prog="mast logout",
        description="Revoke the token of mast login, and remove it.").parse_args(
            argv)
    from mast.auth import identity, tokens
    path = identity.login_file()
    if not os.path.exists(path):
        print("You are not logged in.")
        return 0
    with open(path, encoding="ascii") as fin:
        token = fin.read().strip()
    try:
        tokens.revoke(int(token.split("_")[1]))
    except (tokens.BadToken, IndexError, ValueError):
        pass
    os.remove(path)
    identity.reset()
    print("Logged out.")
    return 0


def whoami(argv):
    """`mast whoami`: see the module."""
    argparse.ArgumentParser(
        prog="mast whoami",
        description="Show who the command line acts for, and where its "
                    "token comes from.").parse_args(argv)
    from mast.auth import identity
    token, source = identity.current_token()
    try:
        who = identity.cli_principal()
    except identity.IdentityError as exc:
        sys.stderr.write("mast whoami: {}\n".format(exc))
        return 1
    if token is None:
        print("{} ({}): no token, so the command line acts for the account "
              "of the operating system. Log in with mast login, or use a "
              "service account (mast --as NAME).".format(who.name, who.role))
    else:
        print("{} ({}), with the token of {}.".format(who.name, who.role,
                                                     source))
    return 0

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
_module_: `mast.auth.identity`

Who the command line acts for (step 6 of docs/design/mast-4.0.md).

The command line gives a token of a user (`mast.auth.tokens`), found in
this order:

1. `MAST_TOKEN`: the token itself, for a script that has it.
2. `MAST_TOKEN_FILE`: a file with the token. `mast --as NAME ...` sets
it to the token file of the service account `NAME`
(`etc/local/tokens/NAME.token` in the home), which
`mast users add-service` writes and only the owner of the home can
read: the simple way for cron jobs, scripts and services.
3. The token of `mast login`, which is kept for each home in the
account of the person (`~/.config/mast/tokens`).

A token that is given but does not work is an error, never a reason to
act as someone else. Without a token, the command line acts for nobody:
what needs an identity is refused (`NoIdentity`), with what to do.
`mast users add-first-admin` makes the first admin of a home without
one.
"""
import hashlib
import os
import sys

from mast.jobs.auth import Principal

__all__ = ["IdentityError", "NoIdentity", "cli_principal", "current_token",
           "login_file", "service_token_file", "write_token", "reset"]


class IdentityError(Exception):
    """A token was given and does not work. The message says why and
    what to do."""


class NoIdentity(IdentityError):
    """There is no token: the command line acts for nobody."""

    def __init__(self):
        super().__init__(
            "MAST does not know who you are. Log in with mast login -u NAME, "
            "or run as a service account with mast --as NAME. In a new home, "
            "make the first admin with mast users add-first-admin -u NAME.")


# Who the command line acts for without a token: nobody (NoIdentity).
# The tests set it to mast.jobs.auth.SYSTEM.
WITHOUT_TOKEN = None


_cached = None


def reset():
    """Forget the identity of this process (for the tests, and after
    mast login or mast logout)."""
    global _cached
    _cached = None


def _home():
    from mast.config import MAST_HOME
    return os.path.realpath(MAST_HOME)


def login_file(home=None):
    """
    _function_: `mast.auth.identity.login_file(home=None)`

    The file where `mast login` keeps the token of the person, for the
    MAST home `home` (this one by default).
    """
    key = hashlib.sha256((home or _home()).encode("utf-8")).hexdigest()[:16]
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        folder = os.path.join(base, "mast", "tokens")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
            os.path.expanduser("~"), ".config")
        folder = os.path.join(base, "mast", "tokens")
    return os.path.join(folder, key + ".token")


def service_token_file(name, home=None):
    """
    _function_: `mast.auth.identity.service_token_file(name, home=None)`

    The token file of the service account `name` in the home.
    """
    return os.path.join(home or _home(), "etc", "local", "tokens",
                        name + ".token")


def write_token(path, token):
    """
    _function_: `mast.auth.identity.write_token(path, token)`

    Write `token` to `path`, readable only by its owner.
    """
    folder = os.path.dirname(path)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(path, flags, 0o600)
    try:
        os.write(fd, (token + "\n").encode("ascii"))
    finally:
        os.close(fd)
    if hasattr(os, "chmod"):
        os.chmod(path, 0o600)


def _read(path):
    try:
        with open(path, encoding="ascii") as fin:
            return fin.read().strip()
    except (OSError, UnicodeDecodeError) as exc:
        raise IdentityError("Cannot read the token file {}: {}".format(
            path, exc))


def current_token():
    """
    _function_: `mast.auth.identity.current_token()`

    Return `(token, source)`: the token that the command line gives and
    where it comes from, or `(None, None)`.
    """
    if os.environ.get("MAST_TOKEN"):
        return os.environ["MAST_TOKEN"].strip(), "MAST_TOKEN"
    if os.environ.get("MAST_TOKEN_FILE"):
        path = os.environ["MAST_TOKEN_FILE"]
        return _read(path), path
    path = login_file()
    if os.path.exists(path):
        return _read(path), path
    return None, None


def cli_principal():
    """
    _function_: `mast.auth.identity.cli_principal()`

    Return the `mast.jobs.auth.Principal` of the command line: the user
    of its token. Raises `IdentityError` for a token that does not work,
    and `NoIdentity` without a token.
    """
    global _cached
    if _cached is not None:
        return _cached
    token, source = current_token()
    if token is None:
        if WITHOUT_TOKEN is not None:
            return WITHOUT_TOKEN
        raise NoIdentity()
    from mast.auth import tokens
    try:
        user = tokens.authenticate(token)
    except tokens.BadToken as exc:
        hint = " Log in again with mast login." \
            if source == login_file() else ""
        raise IdentityError("The token of {} does not work: {}{}".format(
            source, exc, hint)) from None
    _cached = Principal(user.name)
    return _cached

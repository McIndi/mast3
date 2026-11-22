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
_module_: `mast.auth.passwords`

Hash the password of a user, and check a password against its hash.

A hash is a string of six fields, separated by `$`:

    scrypt$<n>$<r>$<p>$<salt, base64>$<hash, base64>

The parameters of scrypt are kept in the hash, so that they can be
raised later. A login with a hash that has older parameters makes a
new hash (see `needs_rehash`).
"""
import base64
import hashlib
import hmac
import secrets

__all__ = [
    "hash_password", "verify_password", "needs_rehash", "check_strength",
    "WeakPassword", "MIN_LENGTH",
]

# The cost of scrypt. A hash takes about 32 MiB of memory (128 * N * R
# bytes) and a fraction of a second.
N = 2 ** 15
R = 8
P = 1
SALT_BYTES = 16
HASH_BYTES = 32
MAXMEM = 64 * 1024 * 1024

MIN_LENGTH = 12

_SCHEME = "scrypt"


class WeakPassword(ValueError):
    """The password does not meet the rules. The message says why."""


def check_strength(password, name=None):
    """
    _function_: `mast.auth.passwords.check_strength(password, name=None)`

    Raise `WeakPassword` if `password` is shorter than `MIN_LENGTH`
    characters, or if it is the name of the user.

    Parameters:

    * `password`: The new password.
    * `name`: The name of the user, if known.
    """
    if not isinstance(password, str) or len(password) < MIN_LENGTH:
        raise WeakPassword(
            "A password must have at least {} characters.".format(MIN_LENGTH))
    if name and password.strip().lower() == name.strip().lower():
        raise WeakPassword("A password must not be the name of the user.")


def _b64(data):
    return base64.b64encode(data).decode("ascii")


def _scrypt(password, salt, n, r, p, length):
    return hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=n, r=r, p=p,
        maxmem=MAXMEM, dklen=length)


def hash_password(password):
    """
    _function_: `mast.auth.passwords.hash_password(password)`

    Return the hash of `password` with a new random salt, as a string
    to store. It does not check the strength of the password (see
    `check_strength`).
    """
    salt = secrets.token_bytes(SALT_BYTES)
    digest = _scrypt(password, salt, N, R, P, HASH_BYTES)
    return "$".join(
        (_SCHEME, str(N), str(R), str(P), _b64(salt), _b64(digest)))


def _parse(stored):
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != _SCHEME:
            return None
        return (int(n), int(r), int(p), base64.b64decode(salt),
                base64.b64decode(digest))
    except (AttributeError, ValueError):
        return None


def verify_password(password, stored):
    """
    _function_: `mast.auth.passwords.verify_password(password, stored)`

    Return True if `password` matches the hash `stored`, and False if
    it does not or if `stored` is not a hash.

    Parameters:

    * `password`: The password that the user typed.
    * `stored`: A hash from `hash_password`.
    """
    parsed = _parse(stored)
    if parsed is None or not isinstance(password, str):
        return False
    n, r, p, salt, digest = parsed
    try:
        candidate = _scrypt(password, salt, n, r, p, len(digest))
    except ValueError:
        return False
    return hmac.compare_digest(candidate, digest)


def needs_rehash(stored):
    """
    _function_: `mast.auth.passwords.needs_rehash(stored)`

    Return True if `stored` was made with weaker parameters than
    `hash_password` uses now. A login then stores a new hash.
    """
    parsed = _parse(stored)
    if parsed is None:
        return True
    n, r, p, salt, digest = parsed
    return n < N or r < R or p < P or len(salt) < SALT_BYTES \
        or len(digest) < HASH_BYTES

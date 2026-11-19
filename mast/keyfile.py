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
_module_: `mast.keyfile`

Keys that MAST makes the first time it needs them.

A key is kept in a file in `etc/local`, so that it is the same after a
restart and in every process of the same MAST home. The job queue
keeps the key that encrypts the credentials of a waiting job in
`etc/local/jobs.key`, and the web GUI keeps the key that signs its
session cookie in `etc/local/server.key`.
"""
import base64
import os
import secrets
import time

__all__ = ["read_or_create", "KeyFileError"]


class KeyFileError(Exception):
    """A key file is there, but it has no key."""


def _new_key():
    # 32 random bytes, URL-safe base64: the form of a Fernet key, which
    # also suits any other use of a secret key.
    return base64.urlsafe_b64encode(secrets.token_bytes(32))


def _read(path):
    # Another process can have made the file and not yet written the key
    for _ in range(50):
        with open(path, "rb") as fin:
            key = fin.read().strip()
        if key:
            return key
        time.sleep(0.02)
    raise KeyFileError("the key file {} is empty".format(path))


def read_or_create(path):
    """
    _function_: `mast.keyfile.read_or_create(path)`

    Return the key in the file `path` (bytes). If there is no such
    file, make a new random key, write it to `path` and return it.

    The file is made readable only by its owner (on POSIX). If two
    processes start together, only one of them makes the key, and both
    return it. Raises `KeyFileError` if the file stays empty.

    Parameters:

    * `path`: The path of the key file. Its directory is made if it is
    not there.
    """
    try:
        return _read(path)
    except FileNotFoundError:
        pass
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    try:
        # O_EXCL: if two processes start together, only one makes the key
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return _read(path)
    key = _new_key()
    with os.fdopen(fd, "wb") as fout:
        fout.write(key)
    return key

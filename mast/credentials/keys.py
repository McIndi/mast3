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
_module_: `mast.credentials.keys`

Where the keys of the credential store come from.

A `KeyProvider` has three methods:

* `keys()`: The keys (Fernet keys, as bytes), the newest first. The
store encrypts with the first one and decrypts with any of them.
* `rotate()`: Make a new key and put it first. The older keys stay.
Return the keys.
* `prune(count)`: Keep only the `count` newest keys. The store calls
it after it encrypted every credential again with the new key.

`FileKeyProvider` keeps the keys in a file (`[credentials] key_file`
in `jobs.conf`, `etc/local/credentials.key` by default). It is the
only provider so far. A provider for a vault or a key management
service has the same two methods; `provider()` then picks it from
`[credentials] key_provider`.

On POSIX, the file and its directory must not be readable by the
group or by others. `check_permissions` refuses them otherwise, and
says which `chmod` fixes it. When the first key is made, the directory
is made private (an older MAST made `etc/local` readable by all).
"""
import os
import stat
import typing

from mast import keyfile
from mast.jobs import config

__all__ = ["KeyProvider", "FileKeyProvider", "provider", "check_permissions",
           "KeysUnavailable", "InsecureKeyFile"]


class KeysUnavailable(Exception):
    """The keys of the credential store cannot be read."""


class InsecureKeyFile(KeysUnavailable):
    """The key file, or its directory, can be read by others."""


class KeyProvider(typing.Protocol):
    """
    _class_: `mast.credentials.keys.KeyProvider`

    What the credential store needs from a source of keys.
    """
    def keys(self) -> list[bytes]:
        ...

    def rotate(self) -> list[bytes]:
        ...

    def prune(self, count: int) -> list[bytes]:
        ...


def check_permissions(path):
    """
    _function_: `mast.credentials.keys.check_permissions(path)`

    Raise `InsecureKeyFile` if the file `path`, or its directory, can
    be read (or, for the directory, entered) by the group or by
    others. The message says which `chmod` fixes it. Does nothing on
    Windows, where the file inherits the access list of `etc/local`.
    """
    if os.name != "posix":
        return
    directory = os.path.dirname(os.path.abspath(path))
    problems = []
    for target, mode in ((directory, 0o700), (path, 0o600)):
        try:
            current = stat.S_IMODE(os.stat(target).st_mode)
        except FileNotFoundError:
            continue
        if current & 0o077:
            problems.append("chmod {:o} {}".format(mode, target))
    if problems:
        raise InsecureKeyFile(
            "The keys of the credential store must be readable only by "
            "the account that runs MAST. To fix it: {}".format(
                "; ".join(problems)))


def _make_private(directory):
    """Before the first key is written to `directory` (`etc/local`,
    which an older MAST made readable by all), make it readable only by
    its owner, if this account owns it. Otherwise `check_permissions`
    says what to do."""
    if os.name != "posix":
        return
    info = os.stat(directory)
    mode = stat.S_IMODE(info.st_mode)
    if mode & 0o077 and info.st_uid == os.getuid():
        os.chmod(directory, mode & 0o700)
        # Imported here: mast.logging reads the configuration
        from mast.logging import make_logger
        make_logger("mast.credentials").info(
            "Made {} readable only by its owner (it was {:o}), for the "
            "keys of the credential store".format(directory, mode))


class FileKeyProvider(object):
    """
    _class_: `mast.credentials.keys.FileKeyProvider(path)`

    The keys in the file `path`, one on each line, the newest first.
    The file is made, with one key, the first time it is needed.
    """
    def __init__(self, path):
        self.path = path

    def _prepare(self):
        directory = os.path.dirname(os.path.abspath(self.path))
        if not os.path.isdir(directory):
            os.makedirs(directory, mode=0o700)
        elif not os.path.exists(self.path):
            _make_private(directory)
        check_permissions(self.path)

    def keys(self):
        self._prepare()
        try:
            return keyfile.read_keys(self.path)
        except (OSError, keyfile.KeyFileError) as exc:
            raise KeysUnavailable(str(exc)) from None

    def rotate(self):
        self._prepare()
        try:
            return keyfile.add_key(self.path)
        except (OSError, keyfile.KeyFileError) as exc:
            raise KeysUnavailable(str(exc)) from None

    def prune(self, count):
        self._prepare()
        try:
            return keyfile.keep_keys(self.path, count)
        except (OSError, keyfile.KeyFileError) as exc:
            raise KeysUnavailable(str(exc)) from None


def provider():
    """
    _function_: `mast.credentials.keys.provider()`

    Return the `KeyProvider` that `jobs.conf` names.
    """
    name = config.credentials_key_provider()
    if name == "file":
        return FileKeyProvider(config.credentials_key_file())
    raise KeysUnavailable(
        "[credentials] key_provider = {} is not supported. The only "
        "provider is file.".format(name))

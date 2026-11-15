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
_module_: `mast.jobs.secrets`

Encryption of the secret parameters of a queued job.

A job waits in the database with its parameters. A parameter that is
declared with `Option(secret=True)` (the credentials, for example) is
stored encrypted, and is removed when the job finishes.

The key is in the file that `jobs.conf` names (`etc/local/jobs.key`
by default). It is made the first time it is needed. Every process
that submits or runs jobs must read the same key file.
"""
import json
import os
import time

from cryptography.fernet import Fernet, InvalidToken

from mast.cli import params as cli_params
from mast.jobs import config

__all__ = ["seal", "unseal", "scrub", "SecretsError"]

_MARK = "$secret"


class SecretsError(Exception):
    """A secret parameter cannot be decrypted."""


def _load_key(path):
    # Another process can have made the file and not yet written the key
    for _ in range(50):
        with open(path, "rb") as fin:
            key = fin.read().strip()
        if key:
            return key
        time.sleep(0.02)
    raise SecretsError("the key file {} is empty".format(path))


def _key():
    path = config.key_file()
    try:
        return _load_key(path)
    except FileNotFoundError:
        pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        # O_EXCL: if two processes start together, only one makes the key
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return _load_key(path)
    key = Fernet.generate_key()
    with os.fdopen(fd, "wb") as fout:
        fout.write(key)
    return key


def _secret_fields(model):
    return [spec.name for spec in cli_params.options(model) if spec.secret]


def seal(model, values):
    """
    _function_: `mast.jobs.secrets.seal(model, values)`

    Return a copy of `values` (the parameters of a job as JSON types)
    in which each secret parameter of `model` is encrypted.
    """
    fernet = Fernet(_key())
    sealed = dict(values)
    for name in _secret_fields(model):
        if name in sealed:
            token = fernet.encrypt(json.dumps(sealed[name]).encode())
            sealed[name] = {_MARK: token.decode()}
    return sealed


def unseal(values):
    """
    _function_: `mast.jobs.secrets.unseal(values)`

    Return a copy of `values` with each encrypted parameter decrypted.
    Raises `SecretsError` if a parameter was scrubbed or was encrypted
    with another key.
    """
    fernet = None
    opened = dict(values)
    for name, value in values.items():
        if not _is_sealed(value):
            continue
        if value[_MARK] is None:
            raise SecretsError(
                "the parameter {} was removed when the job "
                "finished".format(name))
        fernet = fernet or Fernet(_key())
        try:
            opened[name] = json.loads(fernet.decrypt(value[_MARK].encode()))
        except InvalidToken:
            raise SecretsError(
                "the parameter {} was encrypted with another key. Every "
                "process must use the same key file ({})".format(
                    name, config.key_file())) from None
    return opened


def scrub(values):
    """
    _function_: `mast.jobs.secrets.scrub(values)`

    Return a copy of `values` with each encrypted parameter removed.
    The name of the parameter stays, so that the record shows that a
    value was given.
    """
    return {
        name: {_MARK: None} if _is_sealed(value) else value
        for name, value in values.items()}


def _is_sealed(value):
    return isinstance(value, dict) and set(value) == {_MARK}

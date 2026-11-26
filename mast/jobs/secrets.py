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

A command can repeat a secret parameter in what it reports: the
request that sets a password has the password in it. `redactor`
removes the secret values from the progress, the result and the
history of a job before they are stored.

The key is in the file that `jobs.conf` names (`etc/local/jobs.key`
by default). It is made the first time it is needed (see
`mast.keyfile`). Every process that submits or runs jobs must read the
same key file.
"""
import base64
import html
import json
import re

from cryptography.fernet import Fernet, InvalidToken

from mast import keyfile
from mast.cli import params as cli_params
from mast.jobs import config

__all__ = ["seal", "unseal", "scrub", "redactor", "redact_data", "Redactor",
           "SecretsError", "REDACTED", "CREDENTIALS_WIDGET"]

_MARK = "$secret"

# The widget of the credentials, see mast.plugin_utils.params.CREDENTIALS
CREDENTIALS_WIDGET = "credentials"

# What stands in the place of a secret value in the record of a job
REDACTED = "********"


class SecretsError(Exception):
    """A secret parameter cannot be decrypted."""


def _key():
    try:
        return keyfile.read_or_create(config.key_file())
    except keyfile.KeyFileError as e:
        raise SecretsError(str(e)) from None


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


def _escaped(values):
    return {html.escape(value, quote=quote)
            for value in values for quote in (False, True)}


def _forms(value):
    """The ways in which a secret value appears in text: as it is,
    base64 encoded, in XML or HTML, and in XML that is shown as HTML
    (escaped twice)."""
    once = _escaped({value})
    return {value, base64.b64encode(value.encode()).decode()} \
        | once | _escaped(once)


_TAG = re.compile(r"(<[^>]*>)")


class Redactor(object):
    """
    _class_: `mast.jobs.secrets.Redactor`

    Removes the values of the secret parameters of a job from what
    the job reports. `redactor` makes one.

    * `redactor(text)`: Return `text` with each secret value replaced
    by `REDACTED`. For plain text and for XML that is only read.
    * `redactor.html(text)`: The same for HTML that a browser shows.
    Only the text between the tags is changed, so a secret that is
    also part of a tag (`a`, `table`, `class`) does not break the
    page. The results of the commands put what they show between
    tags, never in an attribute. A secret that the page shows with a
    tag in the middle of it (a line break, for example) is not found.

    A secret is replaced only where it stands alone: not where a
    letter, a digit or `_` comes just before it or just after it. A
    password such as `admin` so does not change `administrator` or
    `sysadmin`. A secret that is part of a longer word is not found
    there. Text that is the same as the secret (a user named `admin`
    whose password is `admin`) is replaced all the same.
    """
    def __init__(self, secrets):
        # The longest first: one secret can be the start of another.
        # The class of characters is ASCII, so that the rule is the
        # same for any text.
        alternatives = "|".join(
            re.escape(secret)
            for secret in sorted(secrets, key=len, reverse=True) if secret)
        self._pattern = re.compile(
            r"(?<![A-Za-z0-9_])(?:{})(?![A-Za-z0-9_])".format(alternatives)) \
            if alternatives else None

    def __call__(self, text):
        if not text or self._pattern is None:
            return text
        return self._pattern.sub(REDACTED, text)

    def _between_tags(self, text):
        """Redact text that is not markup. It is decoded first, so
        that a secret which is also the name of an entity (`amp`)
        does not break the entities."""
        plain = html.unescape(text)
        redacted = self(plain)
        if redacted == plain:
            return text
        return html.escape(redacted, quote=False)

    def html(self, text):
        if not text or self._pattern is None:
            return text
        parts = _TAG.split(text)
        for index, part in enumerate(parts):
            # Every second part is a tag
            if index % 2 == 0:
                parts[index] = self._between_tags(part)
        return "".join(parts)


def redact_data(redact, data):
    """
    _function_: `mast.jobs.secrets.redact_data(redact, data)`

    Return a copy of `data` (JSON types: dicts, lists, strings,
    numbers) with `redact` (a `Redactor`) applied to every string in
    it, keys included.
    """
    if isinstance(data, str):
        return redact(data)
    if isinstance(data, list):
        return [redact_data(redact, item) for item in data]
    if isinstance(data, dict):
        return {redact_data(redact, key): redact_data(redact, value)
                for key, value in data.items()}
    return data


def redactor(model, values, extra=()):
    """
    _function_: `mast.jobs.secrets.redactor(model, values, extra=())`

    Return a `Redactor` for the secret parameters of `model`. `values`
    has the parameters of the job, not encrypted. `extra` has more
    secrets to redact, in the same forms (the password of a stored
    credential, which is also found where it stands alone).

    A value is found as it was given, base64 encoded, and escaped for
    XML or HTML (once or twice).

    Credentials can be given xor encoded (the output of `mast datapower
    xor`), and the command decodes them before it uses them. Such a
    value is decoded here too, and the decoded `user:password` is
    found in the same forms. It is decoded with the key in `xor.conf`
    of the host that runs the job. That must be the key of the host
    that gave the value, or the job could not use the credentials.
    """
    found = set()
    for item in extra:
        if isinstance(item, str) and item:
            found.update(_forms(item))
    for spec in cli_params.options(model):
        if not spec.secret:
            continue
        value = values.get(spec.name)
        for item in value if isinstance(value, list) else [value]:
            if not isinstance(item, str) or not item:
                continue
            found.update(_forms(item))
            if spec.widget == CREDENTIALS_WIDGET and ":" not in item:
                decoded = _xor_decoded(item)
                if decoded:
                    found.update(_forms(decoded))
    return Redactor(found)


def _xor_decoded(value):
    """Return the credentials in `value`, which is xor encoded, or
    `None` if it is not."""
    # Imported here: mast.xor reads xor.conf
    from mast.xor import xordecode
    try:
        decoded = xordecode(value)
        if isinstance(decoded, bytes):
            decoded = decoded.decode("utf-8")
    except Exception:
        return None
    return decoded if ":" in decoded else None

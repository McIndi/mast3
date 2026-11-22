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
_module_: `mast.web.uploads`

The files that users upload for the `upload` option of a command.

Each upload is kept in a directory of its own,
`var/www/uploads/<token>/`, with the name that the browser gave (made
safe) and a file `.owner` with the name of the user. The token is
random. A job gets the path on the server that the token maps to,
never a path that the browser chose, and only the user who uploaded a
file can use its token.
"""
import os
import re
import secrets

from werkzeug.utils import secure_filename

__all__ = ["save", "path_for", "UPLOADS_DIR", "UnknownUpload"]

UPLOADS_DIR = os.path.join("var", "www", "uploads")

_TOKEN = re.compile(r"^[0-9a-f]{32}$")
_OWNER = ".owner"


class UnknownUpload(LookupError):
    """There is no upload with this token for this user."""


def save(storage, owner, root=None):
    """
    _function_: `mast.web.uploads.save(storage, owner, root=None)`

    Save an uploaded file (a `werkzeug.datastructures.FileStorage`)
    and return `(token, name)`.

    Parameters:

    * `storage`: The file from `flask.request.files`.
    * `owner`: The name of the user who uploads it.
    * `root`: The directory of the uploads. The default is
    `UPLOADS_DIR` in MAST_HOME.
    """
    root = root or UPLOADS_DIR
    token = secrets.token_hex(16)
    directory = os.path.join(root, token)
    os.makedirs(directory)
    name = secure_filename(storage.filename or "") or "upload"
    if name == _OWNER.lstrip("."):
        name = "upload"
    storage.save(os.path.join(directory, name))
    with open(os.path.join(directory, _OWNER), "w", encoding="utf-8") as fout:
        fout.write(owner)
    return token, name


def path_for(token, owner, root=None):
    """
    _function_: `mast.web.uploads.path_for(token, owner, root=None)`

    Return the path of the file that was uploaded with `token` by
    `owner`. Raises `UnknownUpload` for a token that is not well
    formed, not known, or of another user.
    """
    root = root or UPLOADS_DIR
    if not isinstance(token, str) or not _TOKEN.match(token):
        raise UnknownUpload(token)
    directory = os.path.join(root, token)
    try:
        with open(os.path.join(directory, _OWNER), encoding="utf-8") as fin:
            if fin.read() != owner:
                raise UnknownUpload(token)
        names = [name for name in os.listdir(directory) if name != _OWNER]
    except OSError:
        raise UnknownUpload(token) from None
    if len(names) != 1:
        raise UnknownUpload(token)
    return os.path.join(directory, names[0])

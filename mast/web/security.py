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
_module_: `mast.web.security`

How the web UI defends itself in the browser. The new web UI and the
classic GUI (`/classic/`) use the same functions:

* `secret_key`: The key that signs the session cookie.
* `csrf_token` and `csrf_ok`: The CSRF token of the session.
* `add_security_headers`: The Content Security Policy, HSTS and the
other headers of every response.
"""
import hmac
import os
import secrets

import flask

from mast import keyfile
from mast.config import MAST_HOME
from mast.logging import make_logger

__all__ = [
    "secret_key", "csrf_token", "csrf_ok", "add_security_headers",
    "CONTENT_SECURITY_POLICY", "CSRF_HEADER", "CSRF_FIELD",
    "UNSAFE_METHODS",
]

# The values that MAST 3.15.2 and older shipped as secret_key. They are
# not secret, so a server.conf that still has one gets a key file.
_PUBLIC_SECRET_KEYS = {"", "INSECURE_SECRET_KEY_CHANGE_ME", "changeme"}

# The methods that can change something. A request with one of them must
# carry the CSRF token of the session, in the header CSRF_HEADER (the
# requests that scripts make) or in the form field CSRF_FIELD (the forms
# that the browser submits).
UNSAFE_METHODS = frozenset(("POST", "PUT", "DELETE", "PATCH"))
CSRF_HEADER = "X-CSRF-Token"
CSRF_FIELD = "csrf_token"

# Scripts, style sheets, images and frames only from the web UI itself,
# and no inline script or style. The upload form of the classic GUI
# posts into an iframe of the page, so the page may frame itself, but no
# other site may frame it.
CONTENT_SECURITY_POLICY = "; ".join((
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self'",
    "img-src 'self' data:",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'self'",
    "frame-ancestors 'self'",
))


def secret_key(server_config):
    """
    _function_: `mast.web.security.secret_key(server_config)`

    Return the key that signs the session cookie of the web UI: the
    `secret_key` of the `[server]` stanza if it is set, or else the key
    in `secret_key_file` (`etc/local/server.key`), which is made with a
    random key the first time.

    A `secret_key` that MAST once shipped as its default is not secret.
    It is ignored, with a warning in `gui.log`.

    Parameters:

    * `server_config`: The `[server]` stanza of `server.conf`.
    """
    key = (server_config.get("secret_key") or "").strip()
    if key and key not in _PUBLIC_SECRET_KEYS:
        return key
    if key:
        make_logger("gui").warning(
            "Ignoring the secret_key in server.conf: it is the default "
            "of an older MAST and is not secret. Remove it from "
            "etc/local/server.conf; MAST uses the key in secret_key_file.")
    path = server_config.get("secret_key_file") or "etc/local/server.key"
    if not os.path.isabs(path):
        path = os.path.join(MAST_HOME, path)
    return keyfile.read_or_create(path)


def csrf_token(new=False):
    """
    _function_: `mast.web.security.csrf_token(new=False)`

    Return the CSRF token of the session, and make one if the session
    has none, or if `new` is True. The token stays the same for the
    session, so that every tab of the web UI can use it. Templates call
    it as `{{ csrf_token() }}`.
    """
    token = flask.session.get("csrf")
    if new or not isinstance(token, str) or len(token) < 32:
        token = secrets.token_urlsafe(32)
        flask.session["csrf"] = token
    return token


def csrf_ok(request=None):
    """
    _function_: `mast.web.security.csrf_ok(request=None)`

    Return True if the request may go on: it cannot change anything
    (GET, HEAD, OPTIONS), or it carries the CSRF token of the session.
    Another site can make the browser send a request with the cookies
    of the web UI, but it cannot read the token.
    """
    request = request or flask.request
    if request.method not in UNSAFE_METHODS:
        return True
    expected = flask.session.get("csrf")
    sent = request.headers.get(CSRF_HEADER) or request.form.get(CSRF_FIELD)
    return isinstance(expected, str) and isinstance(sent, str) \
        and hmac.compare_digest(sent.encode(), expected.encode())


def add_security_headers(response, secure, hsts_max_age):
    """
    _function_: `mast.web.security.add_security_headers(response, secure,
    hsts_max_age)`

    Add the headers with which the browser defends the web UI: the
    Content Security Policy, no guessing of content types, no referrer
    to other sites, and HSTS when the web UI uses TLS (`secure`).
    """
    headers = response.headers
    headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
    headers.setdefault("X-Content-Type-Options", "nosniff")
    headers.setdefault("Referrer-Policy", "same-origin")
    if secure:
        headers.setdefault(
            "Strict-Transport-Security", "max-age={}".format(hsts_max_age))
    return response

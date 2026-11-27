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
_module_: `mast.web.app`

The Flask application of the web UI.

`create_app()` makes it. Every page needs a login (see
`mast.web.auth`), and every request that can change something needs
the CSRF token of the session (see `mast.web.security`).

The pages are those of core (Jobs, Commands and Admin) and those that
the manifest of each plugin names (see `mast.web.page`). The JSON API
is `mast.web.api`, under `/api/v1`.
"""
import flask

from mast.auth import roles
from mast.config import get_configs_dict
from mast.logging import make_logger
from mast.web import api, auth, security
# The routes of the credential store, on the blueprint of api
from mast.web import credentials as _credentials  # noqa: F401
from mast.web import permissions as _permissions  # noqa: F401
from mast.web import schedules as _schedules  # noqa: F401
from mast.web.page import load_pages

__all__ = ["create_app", "settings"]


def _flag(value, default=False):
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def settings(server_conf=None):
    """
    _function_: `mast.web.app.settings(server_conf=None)`

    Return the settings of the web UI from `server.conf` as a dict:
    `secure`, `host`, `port`, `hsts_max_age`, `idle_seconds` and
    `max_seconds`.

    Parameters:

    * `server_conf`: The parsed `server.conf`, as a dict of stanzas.
    The default is the merged `etc/default` and `etc/local` file.
    """
    conf = server_conf if server_conf is not None \
        else get_configs_dict()["server.conf"]
    server = conf.get("server", {})
    auth_conf = conf.get("auth", {}) if "auth" in conf else {}
    return {
        "server": server,
        "secure": _flag(server.get("secure"), True),
        "host": server.get("host") or "127.0.0.1",
        "port": int(server.get("port") or 5000),
        "hsts_max_age": int(server.get("hsts_max_age") or 31536000),
        "idle_seconds": int(float(
            auth_conf.get("idle_minutes") or auth.IDLE_SECONDS / 60) * 60),
        "max_seconds": int(float(
            auth_conf.get("max_hours") or auth.MAX_SECONDS / 3600) * 3600),
    }


def configure_session(app, values):
    """
    _function_: `mast.web.app.configure_session(app, values)`

    Give `app` the session settings of the web UI: the key, the cookie
    flags and the session limits.
    """
    app.secret_key = security.secret_key(values["server"])
    app.config.update(
        SESSION_COOKIE_NAME="session",
        SESSION_COOKIE_PATH="/",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=values["secure"],
        SESSION_COOKIE_SAMESITE="Lax",
        MAST_IDLE_SECONDS=values["idle_seconds"],
        MAST_MAX_SECONDS=values["max_seconds"])


def _is_api():
    return flask.request.path.startswith(api.blueprint.url_prefix + "/")


def create_app(server_conf=None, pages=None):
    """
    _function_: `mast.web.app.create_app(server_conf=None, pages=None)`

    Make the Flask application of the web UI.

    Parameters:

    * `server_conf`: The parsed `server.conf`. The default is the one
    of the MAST home.
    * `pages`: The `mast.web.page.Page`s. The default is those of core
    and of the plugins (`mast.web.page.load_pages`).
    """
    values = settings(server_conf)
    app = flask.Flask(__name__)
    configure_session(app, values)
    app.config["MAST_SECURE"] = values["secure"]
    app.config["MAST_HSTS_MAX_AGE"] = values["hsts_max_age"]
    app.register_blueprint(auth.blueprint)
    app.register_blueprint(api.blueprint)
    pages = load_pages() if pages is None else list(pages)
    for page in pages:
        app.register_blueprint(page.blueprint)
    app.config["MAST_PAGES"] = pages

    @app.before_request
    def log_access():
        request = flask.request
        make_logger("mast.web.access").info(
            "method: {}, url: {}, client: {}".format(
                request.method, request.path, request.remote_addr))

    app.before_request(auth.require_login(
        exempt=("auth.login", "auth.login_post")))

    @app.before_request
    def check_csrf_token():
        if security.csrf_ok():
            return None
        request = flask.request
        make_logger("gui").warning(
            "Refused {} {} from {}: no valid CSRF token".format(
                request.method, request.path, request.remote_addr))
        message = "This request has no valid CSRF token. Reload the " \
                  "page and try again."
        if _is_api():
            return api.error(400, message)
        return flask.Response(message + "\n", status=400,
                              mimetype="text/plain")

    @app.after_request
    def security_headers(response):
        return security.add_security_headers(
            response, app.config["MAST_SECURE"],
            app.config["MAST_HSTS_MAX_AGE"])

    @app.context_processor
    def template_functions():
        user = flask.g.get("mast_user")
        return {
            "csrf_token": security.csrf_token,
            "nav_pages": [page for page in pages if user is not None
                          and roles.at_least(user.role, page.min_role)],
            "may_submit": user is not None
            and roles.allowed(user.role, "submit"),
        }

    @app.route("/")
    def index():
        """
        _function_: `mast.web.app.index()`

        The home page: the first page that the user may see.

        Exposed at: `/` via GET
        """
        user = flask.g.mast_user
        for page in pages:
            if roles.at_least(user.role, page.min_role):
                return flask.redirect(page.path)
        return flask.render_template("home.html", user=user)

    return app

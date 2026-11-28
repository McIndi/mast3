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
_module_: `mast.web.auth`

Login, logout and the change of a password in the web UI, and the
check of the session on every request.

The session is in the signed session cookie. It holds:

* `user_id` and `epoch`: The user, and the session epoch of the user
at the login. A new epoch (a new password, a new role, a disable, or
a logout) ends every session of the user.
* `login_at` and `seen_at`: When the user logged in, and when the
session was last used. A session ends `idle_minutes` after it was last
used, and `max_hours` after the login (`[auth]` in `server.conf`).
* `csrf`: The CSRF token, see `mast.web.security`.

Each request loads the user again, so a change to the user takes
effect at once.

Every login, failed login, lockout and logout is logged to
`mast.web.auth.log`, with the user name and the address of the
client. A password is never logged.
"""
import time
from urllib.parse import quote, urlsplit

import flask

from mast.auth import passwords, users
from mast.jobs.auth import Principal
from mast.logging import make_logger
from mast.web import security

__all__ = [
    "blueprint", "session_user", "require_login", "principal",
    "IDLE_SECONDS", "MAX_SECONDS",
]

# The defaults of [auth] in server.conf
IDLE_SECONDS = 30 * 60
MAX_SECONDS = 12 * 60 * 60

LOGIN_PATH = "/login"
API_PREFIX = "/api/"
PASSWORD_PATH = "/account/password"

blueprint = flask.Blueprint("auth", __name__)


def _log():
    return make_logger("mast.web.auth")


def _client():
    return flask.request.remote_addr or "unknown"


def _limits():
    config = flask.current_app.config
    return (config.get("MAST_IDLE_SECONDS", IDLE_SECONDS),
            config.get("MAST_MAX_SECONDS", MAX_SECONDS))


def session_user():
    """
    _function_: `mast.web.auth.session_user()`

    Return the `UserInfo` of the user of this request's session, or
    None if there is no valid session. A session that is no longer
    valid is cleared: the user was deleted or disabled, the epoch
    changed, or it was idle or open for too long. A valid session is
    marked as used now.
    """
    session = flask.session
    user_id = session.get("user_id")
    if user_id is None:
        return None
    now = time.time()
    idle, longest = _limits()
    user = users.get_by_id(user_id)
    valid = (
        user is not None
        and not user.disabled
        and session.get("epoch") == user.session_epoch
        and now - float(session.get("seen_at") or 0) <= idle
        and now - float(session.get("login_at") or 0) <= longest)
    if not valid:
        session.clear()
        return None
    session["seen_at"] = now
    return user


def principal():
    """
    _function_: `mast.web.auth.principal()`

    Return the `mast.jobs.Principal` of the user of this request. Only
    call it in a request that `require_login` let through.
    """
    return flask.g.mast_principal


def _safe_next(target):
    """Return `target` if it is a path on this server, else `/`. A link
    to the login page must not send the user to another site after the
    login."""
    if not target or not isinstance(target, str):
        return "/"
    parts = urlsplit(target)
    if parts.scheme or parts.netloc or not target.startswith("/") \
            or target.startswith("//") or "\\" in target:
        return "/"
    return target


def _full_path():
    request = flask.request
    path = request.script_root + request.path
    if request.query_string:
        path += "?" + request.query_string.decode("latin-1")
    return path


def require_login(exempt=()):
    """
    _function_: `mast.web.auth.require_login(exempt=())`

    Return a `before_request` function that lets a request through
    only with a valid session, or, for the API, a token in the header
    `Authorization: Bearer TOKEN` (`mast.auth.tokens`). Without one, it
    redirects to the login page (302). A user who must change their password is sent to the
    page for that first. The function puts the user in `flask.g.mast_user`
    and the principal in `flask.g.mast_principal`.

    Parameters:

    * `exempt`: The endpoints that need no session (the login page).
    Static files never need one.
    """
    exempt = set(exempt)
    may_change = {"auth.password", "auth.logout", "auth.password_post"}

    def check():
        endpoint = flask.request.endpoint or ""
        if endpoint == "static" or endpoint.endswith(".static") \
                or endpoint in exempt:
            return None
        api = flask.request.path.startswith(API_PREFIX)
        header = flask.request.headers.get("Authorization", "")
        if api and header[:7].lower() == "bearer ":
            # A token of mast login or of a service account
            # (mast.auth.tokens), for scripts that use the API
            from mast.auth import tokens
            try:
                user = tokens.authenticate(header[7:].strip())
            except tokens.BadToken as exc:
                return _json_error(401, str(exc))
            flask.g.mast_user = user
            flask.g.mast_principal = Principal(user.name, user.role)
            flask.g.mast_by_token = True
            return None
        user = session_user()
        if user is None:
            if api:
                return _json_error(401, "Log in first.")
            target = _full_path() if flask.request.method == "GET" else "/"
            return flask.redirect("{}?next={}".format(
                LOGIN_PATH, quote(target, safe="/")))
        flask.g.mast_user = user
        flask.g.mast_principal = Principal(user.name, user.role)
        if user.must_change and endpoint not in may_change:
            if api:
                return _json_error(
                    403, "Change your password first, at {}.".format(
                        PASSWORD_PATH))
            return flask.redirect(PASSWORD_PATH)
        return None
    return check


def _json_error(status, message):
    return flask.jsonify({"error": message}), status


def _no_users():
    return flask.render_template("setup.html"), 503


@blueprint.route(LOGIN_PATH, methods=["GET"])
def login():
    """
    _function_: `mast.web.auth.login()`

    The login page. With no users yet, a page that says how to add the
    first admin.

    Exposed at: `/login` via GET
    """
    if not users.any_users():
        return _no_users()
    security.csrf_token()
    return flask.render_template(
        "login.html", next=_safe_next(flask.request.args.get("next")))


@blueprint.route(LOGIN_PATH, methods=["POST"])
def login_post():
    """
    _function_: `mast.web.auth.login_post()`

    Check the user name and password of the login form. On success,
    start a new session (with a new CSRF token, so that a session that
    someone made before the login is not used after it) and go to the
    page in `next`.

    Exposed at: `/login` via POST
    """
    if not users.any_users():
        return _no_users()
    form = flask.request.form
    name = (form.get("username") or "").strip()
    target = _safe_next(form.get("next"))
    try:
        user = users.authenticate(name, form.get("password") or "")
    except users.LoginFailed as exc:
        if exc.reason == "locked-now":
            _log().warning("Locked the user {!r} for {} minutes after {} "
                           "failed logins, from {}".format(
                               name, users.LOCK_MINUTES, users.MAX_FAILURES,
                               _client()))
        _log().warning("Failed login for {!r} from {} ({})".format(
            name, _client(), exc.reason))
        return flask.render_template(
            "login.html", next=target, error=str(exc),
            username=name), 401
    session = flask.session
    session.clear()
    now = time.time()
    session["user_id"] = user.id
    session["epoch"] = user.session_epoch
    session["login_at"] = now
    session["seen_at"] = now
    security.csrf_token(new=True)
    _log().info("Login of {} ({}) from {}".format(
        user.name, user.role, _client()))
    if user.must_change:
        return flask.redirect(PASSWORD_PATH)
    return flask.redirect(target)


@blueprint.route("/logout", methods=["POST"])
def logout():
    """
    _function_: `mast.web.auth.logout()`

    Log out: end every session of the user (a copy of the session
    cookie no longer works either), and go to the login page.

    Exposed at: `/logout` via POST
    """
    user = flask.g.mast_user
    users.new_epoch(user.id)
    flask.session.clear()
    _log().info("Logout of {} from {}".format(user.name, _client()))
    return flask.redirect(LOGIN_PATH)


@blueprint.route(PASSWORD_PATH, methods=["GET"])
def password():
    """
    _function_: `mast.web.auth.password()`

    The form to change the password of the user.

    Exposed at: `/account/password` via GET
    """
    return flask.render_template(
        "password.html", user=flask.g.mast_user,
        min_length=passwords.MIN_LENGTH)


@blueprint.route(PASSWORD_PATH, methods=["POST"])
def password_post():
    """
    _function_: `mast.web.auth.password_post()`

    Change the password of the user. The user types the password that
    they have now, and the new one twice. Every other session of the
    user ends; this one goes on.

    Exposed at: `/account/password` via POST
    """
    user = flask.g.mast_user
    form = flask.request.form
    new = form.get("new") or ""
    error = None
    if new != (form.get("confirm") or ""):
        error = "The two new passwords are not the same."
    else:
        try:
            changed = users.change_password(
                user.name, form.get("current") or "", new)
        except users.LoginFailed:
            error = "The current password is not correct."
            _log().warning("Failed change of the password of {} from {}"
                           .format(user.name, _client()))
        except passwords.WeakPassword as exc:
            error = str(exc)
    if error is not None:
        return flask.render_template(
            "password.html", user=user, error=error,
            min_length=passwords.MIN_LENGTH), 400
    flask.session["epoch"] = changed.session_epoch
    security.csrf_token(new=True)
    _log().info("{} changed their password, from {}".format(
        user.name, _client()))
    return flask.redirect("/")

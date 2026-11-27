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
_module_: `mast.web.pages.admin`

The Admin pages, for admins only: Users (list the users, add one with
a temporary password, change a role, disable and enable, set a new
temporary password, and delete), Appliances (the appliance registry,
whose JSON routes are in `mast.web.api`), Credentials (the
credential store, whose JSON routes are in `mast.web.credentials`),
Schedules (the scheduled jobs, whose JSON routes are in
`mast.web.schedules`) and Plugins (the installed plugins, and those that
were left out, with what to do).

The JSON routes:

* `GET /api/v1/users`: The users (never a password or a hash).
* `POST /api/v1/users`: `{name, role, password}`. The password is
temporary: the user chooses a new one at the first login.
* `PATCH /api/v1/users/<name>`: Any of `{role}`, `{disabled}` or
`{password}` (a new temporary password).
* `DELETE /api/v1/users/<name>`.
* `GET /api/v1/plugins`: `{plugins, left_out, kinds}`, what `mast
plugins list` and `mast plugins kinds` show.

An admin cannot change their own role, disable or delete themselves
here, so that the web UI always keeps an admin who can log in. On the
command line, `mast users` can.

Every change is logged to `mast.web.auth.log`, without the password.
"""
import flask

from mast import plugins
from mast.auth import passwords, roles, users
from mast.logging import make_logger
from mast.web.page import Page

__all__ = ["page"]

blueprint = flask.Blueprint("admin", __name__)


def _error(status, message, fields=None):
    body = {"error": message}
    if fields:
        body["fields"] = fields
    return flask.jsonify(body), status


def _log(message):
    make_logger("mast.web.auth").info("{} (by {} from {})".format(
        message, flask.g.mast_user.name, flask.request.remote_addr))


def _user_json(info):
    return {
        "name": info.name,
        "role": info.role,
        "disabled": info.disabled,
        "must_change": info.must_change,
        "locked": info.locked_until is not None,
        "created_at": info.created_at.isoformat(),
        "last_login_at": info.last_login_at.isoformat()
        if info.last_login_at else None,
    }


def _body():
    body = flask.request.get_json(silent=True)
    return body if isinstance(body, dict) else {}


@blueprint.get("/admin")
def admin():
    """
    _function_: `mast.web.pages.admin.admin()`

    The first Admin page.

    Exposed at: `/admin` via GET
    """
    return flask.redirect("/admin/users")


@blueprint.get("/admin/users")
def users_page():
    """
    _function_: `mast.web.pages.admin.users_page()`

    Admin → Users.

    Exposed at: `/admin/users` via GET
    """
    return flask.render_template(
        "admin_users.html", roles=roles.ROLES,
        min_length=passwords.MIN_LENGTH)


@blueprint.get("/api/v1/users")
def list_users():
    """
    _function_: `mast.web.pages.admin.list_users()`

    Exposed at: `/api/v1/users` via GET
    """
    return flask.jsonify({"users": [_user_json(info)
                                    for info in users.list_users()]})


@blueprint.post("/api/v1/users")
def add_user():
    """
    _function_: `mast.web.pages.admin.add_user()`

    Exposed at: `/api/v1/users` via POST
    """
    body = _body()
    try:
        info = users.add(str(body.get("name") or ""),
                         str(body.get("password") or ""),
                         str(body.get("role") or ""))
    except users.UserExists:
        return _error(409, "There is already a user with this name.",
                      {"name": "There is already a user with this name."})
    except users.InvalidName as exc:
        return _error(422, str(exc), {"name": str(exc)})
    except users.InvalidRole as exc:
        return _error(422, str(exc), {"role": str(exc)})
    except passwords.WeakPassword as exc:
        return _error(422, str(exc), {"password": str(exc)})
    _log("Added the user {} with the role {}".format(info.name, info.role))
    return flask.jsonify(_user_json(info)), 201


@blueprint.patch("/api/v1/users/<name>")
def change_user(name):
    """
    _function_: `mast.web.pages.admin.change_user(name)`

    Exposed at: `/api/v1/users/<name>` via PATCH
    """
    body = _body()
    try:
        target = users.get(name)
    except (users.UserNotFound, users.InvalidName):
        return _error(404, "There is no user {}.".format(name))
    own = target.id == flask.g.mast_user.id
    if own and ("role" in body or body.get("disabled")):
        return _error(400, "You cannot change your own role or disable "
                           "yourself here. Use mast users on the MAST host.")
    info = target
    try:
        if "role" in body:
            info = users.set_role(target.name, str(body["role"]))
            _log("Gave {} the role {}".format(info.name, info.role))
        if "disabled" in body:
            if body["disabled"]:
                info = users.disable(target.name)
                _log("Disabled {}".format(info.name))
            else:
                info = users.enable(target.name)
                _log("Enabled {}".format(info.name))
        if "password" in body:
            info = users.set_password(
                target.name, str(body["password"] or ""), temporary=True)
            _log("Set a temporary password for {}".format(info.name))
    except users.InvalidRole as exc:
        return _error(422, str(exc), {"role": str(exc)})
    except passwords.WeakPassword as exc:
        return _error(422, str(exc), {"password": str(exc)})
    return flask.jsonify(_user_json(info))


@blueprint.delete("/api/v1/users/<name>")
def delete_user(name):
    """
    _function_: `mast.web.pages.admin.delete_user(name)`

    Exposed at: `/api/v1/users/<name>` via DELETE
    """
    try:
        target = users.get(name)
    except (users.UserNotFound, users.InvalidName):
        return _error(404, "There is no user {}.".format(name))
    if target.id == flask.g.mast_user.id:
        return _error(400, "You cannot delete yourself.")
    users.delete(target.name)
    _log("Deleted the user {}".format(target.name))
    return "", 204


@blueprint.get("/admin/credentials")
def credentials_page():
    """
    _function_: `mast.web.pages.admin.credentials_page()`

    Admin → Credentials: the credential store (`/api/v1/credentials`,
    see `mast.web.credentials`).

    Exposed at: `/admin/credentials` via GET
    """
    from mast.credentials import store
    return flask.render_template("admin_credentials.html",
                                 purposes=store.PURPOSES, types=store.TYPES)


@blueprint.get("/admin/targets")
def targets_page():
    """
    _function_: `mast.web.pages.admin.targets_page()`

    Admin → Targets: the targets and the fleets of the registry, their
    extensions and the trust of their endpoints (`/api/v1/targets`,
    `/api/v1/fleets`, `/api/v1/kinds`).

    Exposed at: `/admin/targets` via GET
    """
    return flask.render_template("admin_targets.html")


@blueprint.get("/admin/schedules")
def schedules_page():
    """
    _function_: `mast.web.pages.admin.schedules_page()`

    Admin → Schedules: the scheduled jobs (`/api/v1/schedules`, see
    `mast.web.schedules`), with the form of each command.

    Exposed at: `/admin/schedules` via GET
    """
    return flask.render_template("admin_schedules.html")


def _plugins():
    """The plugins that are used, and those that were left out."""
    # Imported here: the registry loads the commands of the plugins,
    # which leaves out a plugin whose commands break the rules
    from mast.jobs import registry
    registry.commands()
    used = [{"provider": m.provider, "title": m.title, "version": m.version,
             "distribution": plugins.distribution(m.provider), "api": m.api,
             "core": m.core, "kinds": [kind.name for kind in m.kinds],
             "commands": len(registry.commands(m.provider)),
             "unavailable": [{"id": command.id, "reason": reason}
                             for command, reason
                             in registry.unavailable(m.provider)]}
            for _, m in sorted(plugins.manifests().items())]
    left_out = [{"provider": f.name, "distribution": f.distribution,
                 "problem": f.problem, "reason": f.reason, "fix": f.fix}
                for f in plugins.failures()]
    return used, left_out


def _kinds():
    """Every kind of target that can be used."""
    return [{"name": name, "title": kind.title,
             "provider": plugins.provider_of(name), "extends": kind.extends,
             "capabilities": sorted(kind.capabilities),
             "inherited": sorted(plugins.capabilities(name)
                                 - set(kind.capabilities))}
            for name, kind in sorted(plugins.kinds().items())]


@blueprint.get("/admin/plugins")
def plugins_page():
    """
    _function_: `mast.web.pages.admin.plugins_page()`

    Admin → Plugins: the installed plugins, and each plugin that was
    left out, with what is wrong and what to do.

    Exposed at: `/admin/plugins` via GET
    """
    used, left_out = _plugins()
    return flask.render_template("admin_plugins.html", plugins=used,
                                 left_out=left_out, kinds=_kinds(),
                                 api=plugins.API)


@blueprint.get("/api/v1/plugins")
def list_plugins():
    """
    _function_: `mast.web.pages.admin.list_plugins()`

    The installed plugins, and those that were left out.

    Exposed at: `/api/v1/plugins` via GET
    """
    used, left_out = _plugins()
    return flask.jsonify({"api": plugins.API, "plugins": used,
                          "left_out": left_out, "kinds": _kinds()})


page = Page(name="admin", title="Admin", order=90, min_role="admin",
            blueprint=blueprint, nav=(("Users", "/admin/users"),
                                      ("Targets", "/admin/targets"),
                                      ("Credentials", "/admin/credentials"),
                                      ("Schedules", "/admin/schedules"),
                                      ("Plugins", "/admin/plugins")))

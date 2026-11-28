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
_module_: `mast.web.permissions`

The JSON routes of the permission sets and the groups
(`mast.permissions`), on the blueprint of `mast.web.api`. Admins only.

* `GET /api/v1/permission-sets`, `POST` `{name, description, rules}`.
* `GET`, `PATCH` `{description, rules}`, `DELETE`
`/api/v1/permission-sets/<name>`.
* `GET /api/v1/groups`, `POST` `{name, description, sets}`.
* `GET`, `PATCH` `{description, sets, members}` (the lists replace the
sets and the members), `DELETE` `/api/v1/groups/<name>`.
* `GET /api/v1/users/<name>/permissions`: the groups of a user and
every rule that applies to them.
* `POST /api/v1/permissions/check`: `{user, path, capability, targets}`
or `{user, command, targets}`: the decisions, with the rules that
decide them.

Every change is logged to `mast.permissions`.
"""
import flask

from mast import permissions
from mast.jobs import auth as jobs_auth
from mast.logging import make_logger
from mast.web.api import blueprint, error
from mast.web.auth import principal

__all__ = []


def _body():
    body = flask.request.get_json(silent=True)
    return body if isinstance(body, dict) else {}


def _admin(command):
    jobs_auth.authorize(principal(), "core." + command)


def _changed(message):
    make_logger("mast.permissions").info("{} (by {} from {})".format(
        message, principal().name, flask.request.remote_addr))


def _failed(exc):
    from mast import registry
    from mast.jobs import registry as commands
    if isinstance(exc, permissions.UnknownName):
        return error(404, str(exc))
    if isinstance(exc, permissions.NameTaken):
        return error(409, str(exc), {"name": str(exc)})
    if isinstance(exc, (permissions.PermissionError, registry.RegistryError,
                        commands.UnknownCommand)):
        return error(422, str(exc))
    raise exc


def _list(value, what):
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(v, str)
                                              for v in value):
        raise permissions.InvalidRule("{} is a list of names".format(what))
    return value


@blueprint.get("/permission-sets")
def list_sets():
    """
    _function_: `mast.web.permissions.list_sets()`

    Exposed at: `/api/v1/permission-sets` via GET
    """
    _admin("list-permission-sets")
    return flask.jsonify({"permission_sets": [
        found.json() for found in permissions.list_sets()]})


@blueprint.post("/permission-sets")
def add_set():
    """
    _function_: `mast.web.permissions.add_set()`

    Exposed at: `/api/v1/permission-sets` via POST
    """
    _admin("add-permission-set")
    body = _body()
    try:
        rules = body.get("rules") or []
        if not isinstance(rules, list):
            raise permissions.InvalidRule("rules is a list of rules")
        found = permissions.add_set(
            str(body.get("name") or ""), rules,
            str(body.get("description") or ""), principal=principal())
    except Exception as exc:
        return _failed(exc)
    _changed("Added the permission set {}".format(found.name))
    return flask.jsonify(found.json()), 201


@blueprint.get("/permission-sets/<name>")
def get_set(name):
    """
    _function_: `mast.web.permissions.get_set(name)`

    Exposed at: `/api/v1/permission-sets/<name>` via GET
    """
    _admin("show-permission-set")
    try:
        return flask.jsonify(permissions.get_set(name).json())
    except Exception as exc:
        return _failed(exc)


@blueprint.patch("/permission-sets/<name>")
def change_set(name):
    """
    _function_: `mast.web.permissions.change_set(name)`

    Exposed at: `/api/v1/permission-sets/<name>` via PATCH
    """
    _admin("update-permission-set")
    body = _body()
    try:
        rules = body.get("rules")
        if rules is not None and not isinstance(rules, list):
            raise permissions.InvalidRule("rules is a list of rules")
        found = permissions.update_set(
            name, rules, body.get("description"), principal=principal())
    except Exception as exc:
        return _failed(exc)
    _changed("Changed the permission set {}".format(name))
    return flask.jsonify(found.json())


@blueprint.delete("/permission-sets/<name>")
def remove_set(name):
    """
    _function_: `mast.web.permissions.remove_set(name)`

    Exposed at: `/api/v1/permission-sets/<name>` via DELETE
    """
    _admin("remove-permission-set")
    try:
        permissions.remove_set(name)
    except Exception as exc:
        return _failed(exc)
    _changed("Removed the permission set {}".format(name))
    return "", 204


@blueprint.get("/groups")
def list_groups():
    """
    _function_: `mast.web.permissions.list_groups()`

    Exposed at: `/api/v1/groups` via GET
    """
    _admin("list-groups")
    return flask.jsonify({"groups": [
        found.json() for found in permissions.list_groups()]})


@blueprint.post("/groups")
def add_group():
    """
    _function_: `mast.web.permissions.add_group()`

    Exposed at: `/api/v1/groups` via POST
    """
    _admin("add-group")
    body = _body()
    try:
        found = permissions.add_group(
            str(body.get("name") or ""), str(body.get("description") or ""),
            _list(body.get("sets"), "sets") or [])
    except Exception as exc:
        return _failed(exc)
    _changed("Added the group {}".format(found.name))
    return flask.jsonify(found.json()), 201


@blueprint.get("/groups/<name>")
def get_group(name):
    """
    _function_: `mast.web.permissions.get_group(name)`

    Exposed at: `/api/v1/groups/<name>` via GET
    """
    _admin("show-group")
    try:
        return flask.jsonify(permissions.get_group(name).json())
    except Exception as exc:
        return _failed(exc)


@blueprint.patch("/groups/<name>")
def change_group(name):
    """
    _function_: `mast.web.permissions.change_group(name)`

    `{description, sets, members}`; a list replaces what the group has.

    Exposed at: `/api/v1/groups/<name>` via PATCH
    """
    _admin("update-group")
    body = _body()
    try:
        sets = _list(body.get("sets"), "sets")
        members = _list(body.get("members"), "members")
        found = permissions.get_group(name)
        if body.get("description") is not None:
            found = permissions.update_group(name, str(body["description"]))
        if sets is not None:
            for gone in set(found.sets) - set(sets):
                permissions.revoke(name, gone)
            for new in set(sets) - set(found.sets):
                permissions.grant(name, new)
        if members is not None:
            for gone in set(found.members) - set(members):
                permissions.remove_member(name, gone)
            for new in set(members) - set(found.members):
                permissions.add_member(name, new)
        found = permissions.get_group(name)
    except Exception as exc:
        return _failed(exc)
    _changed("Changed the group {}".format(name))
    return flask.jsonify(found.json())


@blueprint.delete("/groups/<name>")
def remove_group(name):
    """
    _function_: `mast.web.permissions.remove_group(name)`

    Exposed at: `/api/v1/groups/<name>` via DELETE
    """
    _admin("remove-group")
    try:
        permissions.remove_group(name)
    except Exception as exc:
        return _failed(exc)
    _changed("Removed the group {}".format(name))
    return "", 204


@blueprint.get("/users/<name>/permissions")
def user_permissions(name):
    """
    _function_: `mast.web.permissions.user_permissions(name)`

    The groups of a user and every rule that applies to them.

    Exposed at: `/api/v1/users/<name>/permissions` via GET
    """
    _admin("show-effective-permissions")
    try:
        return flask.jsonify({
            "user": name, "groups": permissions.groups_of(name),
            "rules": [m.json() for m in permissions.rules_of(name)]})
    except Exception as exc:
        return _failed(exc)


@blueprint.post("/permissions/check")
def check():
    """
    _function_: `mast.web.permissions.check()`

    Whether a user may do something, with the rules that decide:
    `{user, path, capability, targets}` or `{user, command, targets}`.
    The answer is `{allowed, decisions}`.

    Exposed at: `/api/v1/permissions/check` via POST
    """
    _admin("check-permission")
    body = _body()
    try:
        user = str(body.get("user") or "")
        targets = _list(body.get("targets"), "targets") or []
        if body.get("command"):
            decisions = permissions.check_command(
                user, str(body["command"]), targets)
        else:
            from mast import registry
            names = [t.name for t in registry.resolve(targets)] \
                if targets else [None]
            rules = permissions.rules_of(user)
            decisions = [permissions.decide(
                user, str(body.get("capability") or "x"),
                str(body.get("path") or ""), name, rules) for name in names]
    except Exception as exc:
        return _failed(exc)
    return flask.jsonify({"allowed": all(d.allowed for d in decisions),
                          "decisions": [d.json() for d in decisions]})

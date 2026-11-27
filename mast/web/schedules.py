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
_module_: `mast.web.schedules`

The JSON routes of the scheduled jobs (`mast.jobs.schedules`), on the
blueprint of `mast.web.api`:

* `GET /schedules`: Every schedule, with its next run (`read`).
* `GET /schedules/preview?cron=...`: The next five runs of a cron
expression (`read`).
* `POST /schedules`: Add one: `{name, command, cron, params,
description, enabled}`.
* `PATCH /schedules/<name>`: Change `cron`, `params`, `description` or
`enabled`.
* `DELETE /schedules/<name>`: Remove it. Its jobs stay.
* `POST /schedules/<name>/run`: Queue its job now.

The changes need `manage-schedule` (admins). A schedule holds no
secret: a command that talks to appliances uses a stored credential,
and an uploaded file cannot be scheduled.
"""
import flask

from mast.cli import params as cli_params
from mast.jobs import schedules
from mast.logging import make_logger
from mast.web import catalog
from mast.web.api import _body, blueprint, error
from mast.web.auth import principal


def schedule_json(info):
    data = info.model_dump(mode="json")
    try:
        upcoming = schedules.next_runs(info.cron, count=1)
        data["next_run"] = upcoming[0].isoformat() if upcoming else None
    except schedules.ScheduleError:
        data["next_run"] = None
    return data


def _schedule_error(exc):
    if isinstance(exc, schedules.UnknownSchedule):
        return error(404, str(exc))
    if isinstance(exc, schedules.ScheduleTaken):
        return error(409, str(exc))
    return error(422, str(exc))


def _params(command_id, params):
    """The parameters from the browser, as a schedule keeps them, and a
    dict of problems."""
    if not isinstance(params, dict):
        return {}, {"": "The parameters must be an object."}
    try:
        command = catalog.find(command_id)
    except catalog.CommandNotFound:
        return {}, {"": "There is no command {}.".format(command_id)}
    specs = {spec.name: spec for spec in cli_params.options(command.model)}
    values, problems = {}, {}
    for name, value in params.items():
        spec = specs.get(name)
        if value is None or value == "" or value == []:
            continue
        if spec is not None and spec.widget in (catalog.OUT_DIR, catalog.OUT_FILE):
            continue
        if spec is not None and spec.widget == catalog.UPLOAD:
            problems[name] = "A schedule cannot use an uploaded file."
            continue
        values[name] = value
    return values, problems


def _log(message):
    make_logger("mast.web.access").info("{} (by {} from {})".format(
        message, principal().name, flask.request.remote_addr))


@blueprint.get("/schedules")
def list_schedules():
    """
    _function_: `mast.web.schedules.list_schedules()`

    Exposed at: `/api/v1/schedules` via GET
    """
    return flask.jsonify({"schedules": [
        schedule_json(info) for info in schedules.list_schedules(principal())]})


@blueprint.get("/schedules/preview")
def preview():
    """
    _function_: `mast.web.schedules.preview()`

    The next five runs (UTC) of `?cron=`.

    Exposed at: `/api/v1/schedules/preview` via GET
    """
    from mast.jobs import auth as jobs_auth
    jobs_auth.authorize(principal(), "read")
    try:
        runs = schedules.next_runs(flask.request.args.get("cron", ""))
    except schedules.ScheduleError as exc:
        return error(422, str(exc))
    return flask.jsonify({"runs": [when.isoformat() for when in runs]})


@blueprint.post("/schedules")
def add_schedule():
    """
    _function_: `mast.web.schedules.add_schedule()`

    Exposed at: `/api/v1/schedules` via POST
    """
    from mast.jobs import auth as jobs_auth
    jobs_auth.authorize(principal(), "manage-schedule")
    body = _body()
    command = str(body.get("command") or "")
    values, problems = _params(command, body.get("params") or {})
    if problems:
        return error(422, "Some parameters cannot be scheduled.", problems)
    try:
        info = schedules.add(
            str(body.get("name") or ""), command, values,
            str(body.get("cron") or ""), principal=principal(),
            description=str(body.get("description") or ""),
            enabled=body.get("enabled", True) is not False)
    except schedules.ScheduleError as exc:
        return _schedule_error(exc)
    _log("Added the schedule {}".format(info.name))
    return flask.jsonify(schedule_json(info)), 201


@blueprint.patch("/schedules/<name>")
def change_schedule(name):
    """
    _function_: `mast.web.schedules.change_schedule(name)`

    Exposed at: `/api/v1/schedules/<name>` via PATCH
    """
    from mast.jobs import auth as jobs_auth
    jobs_auth.authorize(principal(), "manage-schedule")
    body = _body()
    params = None
    try:
        if "params" in body:
            current = schedules.get(name, principal())
            params, problems = _params(current.command, body.get("params") or {})
            if problems:
                return error(422, "Some parameters cannot be scheduled.", problems)
        info = schedules.update(
            name, principal=principal(),
            cron_text=body.get("cron") if "cron" in body else None,
            params=params,
            description=body.get("description") if "description" in body else None,
            enabled=body.get("enabled") if "enabled" in body else None)
    except schedules.ScheduleError as exc:
        return _schedule_error(exc)
    _log("Changed the schedule {}".format(name))
    return flask.jsonify(schedule_json(info))


@blueprint.delete("/schedules/<name>")
def remove_schedule(name):
    """
    _function_: `mast.web.schedules.remove_schedule(name)`

    Exposed at: `/api/v1/schedules/<name>` via DELETE
    """
    try:
        schedules.remove(name, principal())
    except schedules.ScheduleError as exc:
        return _schedule_error(exc)
    _log("Removed the schedule {}".format(name))
    return "", 204


@blueprint.post("/schedules/<name>/run")
def run_schedule(name):
    """
    _function_: `mast.web.schedules.run_schedule(name)`

    Exposed at: `/api/v1/schedules/<name>/run` via POST
    """
    try:
        job_id = schedules.run_now(name, principal())
    except schedules.ScheduleError as exc:
        return _schedule_error(exc)
    _log("Ran the schedule {} now (job {})".format(name, job_id))
    return flask.jsonify({"job_id": job_id}), 201

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
_module_: `mast.web.api`

Version 1 of the JSON API of the web UI, under `/api/v1`.

Every route needs a login, and every route that is not a GET needs
the CSRF token of the session in the `X-CSRF-Token` header (see
`mast.web.app`). An error is `{"error": "...", "fields": {...}}`, where
`fields` says what is wrong with each parameter, if that is the
problem.

* `GET /me`: The user, the role, the CSRF token and the pages.
* `GET /commands` and `GET /commands/<id>`: The commands and their
options (`mast.web.catalog`).
* `POST /choices/<widget>`: The values for a choice widget
(`status_provider`, `object_class`, `configured:<Class>`). The body
has the appliances and credentials that `configured:` asks.
* `POST /jobs`: `{command, params}`. Validates the parameters with the
model of the command and puts the job in the queue.
* `POST /uploads`: A file for an `upload` option. Returns a token.
* `GET /jobs`, `GET /jobs/<id>`, `GET /jobs/<id>/events?after=N`,
`GET /jobs/<id>/result`, `GET /jobs/<id>/history`,
`POST /jobs/<id>/cancel`, `GET /jobs/<id>/files` and
`GET /jobs/<id>/files/<name>`: The jobs.
* `GET /workers`: The workers that are alive.
* `GET /appliances` and `GET /environments`: The appliance registry
(`mast.registry`). `POST`, `PATCH /<name>` and `DELETE /<name>` change
it, which needs the action `manage-appliance` (an admin).
* `/credentials`: The credential store, see `mast.web.credentials`.

The routes for jobs are thin wrappers over `mast.jobs.queue`, which
checks the role of the user for each action. No route goes around the
queue.
"""
import io
import os
import zipfile
from datetime import datetime, timezone

import flask
from pydantic import ValidationError

from mast import jobs
from mast.cli import Result, captured
from mast.cli import params as cli_params
from mast.jobs import auth as jobs_auth
from mast.jobs import client as jobs_client
from mast.jobs import queue, secrets
from mast.logging import make_logger
from mast.web import catalog, security, uploads
from mast.web.auth import principal

__all__ = ["blueprint", "error", "secret_free"]

blueprint = flask.Blueprint("api", __name__, url_prefix="/api/v1")

# The most jobs that one request lists
MAX_LIMIT = 500


def error(status, message, fields=None):
    """
    _function_: `mast.web.api.error(status, message, fields=None)`

    Return a JSON error response.
    """
    body = {"error": message}
    if fields:
        body["fields"] = fields
    return flask.jsonify(body), status


@blueprint.errorhandler(jobs.Forbidden)
def _forbidden(exc):
    return error(403, str(exc) or "Your role may not do this.")


@blueprint.errorhandler(jobs.JobNotFound)
def _not_found(exc):
    return error(404, "There is no job {}.".format(exc))


@blueprint.errorhandler(catalog.CommandNotFound)
def _no_command(exc):
    return error(404, "There is no command {}.".format(exc))


def _body():
    body = flask.request.get_json(silent=True)
    return body if isinstance(body, dict) else {}


def secret_free(params):
    """
    _function_: `mast.web.api.secret_free(params)`

    Return the parameters of a job (as `mast.jobs.JobInfo.params` has
    them, scrubbed) with each secret value as `********`.
    """
    return {name: secrets.REDACTED if isinstance(value, dict)
            and "$secret" in value else value
            for name, value in secrets.scrub(params).items()}


def _job_json(info):
    data = info.model_dump(mode="json")
    data["params"] = secret_free(info.params)
    data["finished"] = info.finished
    return data


@blueprint.get("/me")
def me():
    """
    _function_: `mast.web.api.me()`

    The user of the session: `name`, `role`, `csrf_token`, the
    `pages` that the user may see, and what the role `may` do.

    Exposed at: `/api/v1/me` via GET
    """
    from mast.auth import roles
    user = flask.g.mast_user
    pages = flask.current_app.config.get("MAST_PAGES", [])
    return flask.jsonify({
        "name": user.name,
        "role": user.role,
        "csrf_token": security.csrf_token(),
        "pages": [{"name": page.name, "title": page.title,
                   "path": page.path, "nav": [list(item) for item in page.nav]}
                  for page in pages
                  if roles.at_least(user.role, page.min_role)],
        "may": {action: roles.allowed(user.role, action)
                for action in roles.ACTIONS},
    })


@blueprint.get("/commands")
def commands():
    """
    _function_: `mast.web.api.commands()`

    Every command, with its options (see `mast.web.catalog`).

    Exposed at: `/api/v1/commands` via GET
    """
    return flask.jsonify({"commands": catalog.all_commands()})


@blueprint.get("/commands/<command_id>")
def command(command_id):
    """
    _function_: `mast.web.api.command(command_id)`

    One command, with its options.

    Exposed at: `/api/v1/commands/<id>` via GET
    """
    namespace, found = catalog.find(command_id)
    return flask.jsonify(catalog.command_json(namespace, found))


@blueprint.post("/choices/<path:widget>")
def choices(widget):
    """
    _function_: `mast.web.api.choices(widget)`

    The values of a choice widget, as `{"choices": [...]}`. For
    `configured:<Class>`, the body has the `appliances`, their
    `credentials` and `no_check_hostname`, and the appliances are asked
    for the objects of that class that all of them have. That needs a
    role that may run commands.

    It is a POST, not a GET, so that the credentials are never in a
    URL (and so never in a log of a proxy or in the history of the
    browser).

    Exposed at: `/api/v1/choices/<widget>` via POST
    """
    # Imported here: the choices are of the DataPower commands
    from mast.plugin_utils import choices as dp_choices
    if not dp_choices.is_choice(widget):
        return error(404, "{} is not a choice widget.".format(widget))
    env = None
    if widget.startswith("configured:"):
        jobs_auth.authorize(principal(), "submit")
        body = _body()
        appliances = [str(a) for a in body.get("appliances") or []]
        if not appliances:
            return flask.jsonify({"choices": []})
        from mast.datapower.datapower import Environment
        try:
            env = Environment(
                appliances, [str(c) for c in body.get("credentials") or []],
                timeout=int(body.get("timeout") or 30),
                check_hostname=not body.get("no_check_hostname"))
            found = dp_choices.choices(widget, env)
        except Exception as exc:
            make_logger("gui").warning(
                "Could not list {} on {}: {}".format(
                    widget, ", ".join(appliances), type(exc).__name__))
            return error(502, "The appliances could not be asked for "
                              "{}: {}".format(widget.split(":", 1)[1],
                                              type(exc).__name__))
        return flask.jsonify({"choices": found})
    return flask.jsonify({"choices": dp_choices.choices(widget)})


def _upload_path(token):
    return uploads.path_for(token, principal().name)


def _validation_fields(model, exc):
    """The messages of a ValidationError by field."""
    specs = {spec.name for spec in cli_params.options(model)}
    fields = {}
    for item in exc.errors():
        location = item["loc"][0] if item["loc"] else ""
        name = location if location in specs else ""
        message = item["msg"]
        fields[name] = "; ".join(filter(None, (fields.get(name), message)))
    return fields


@blueprint.post("/jobs")
def submit():
    """
    _function_: `mast.web.api.submit()`

    Run a command. The body is `{"command": "<id>", "params": {...}}`.
    The parameters are checked with the model of the command, as the
    command line and the classic GUI check them: on a problem the
    answer is 422, with a message for each field, and nothing is
    queued. Otherwise the job is queued and the answer is
    `{"job_id": "..."}`.

    A command that never runs as a job (`queued=False`) runs now, and
    the answer is `{"result": {text, html, data}}`.

    Exposed at: `/api/v1/jobs` via POST
    """
    body = _body()
    namespace, found = catalog.find(str(body.get("command") or ""))
    command_id = "{}.{}".format(namespace, found.name)
    who = principal()
    jobs_auth.authorize(who, "submit", command=command_id)
    job_id = jobs.new_job_id()
    values, problems = catalog.job_values(
        found, body.get("params"), job_id, _upload_path)
    if problems:
        return error(422, "Some parameters are not valid.", problems)
    try:
        params = found.model(**values)
    except ValidationError as exc:
        return error(422, "; ".join(cli_params.error_messages(
            found.model, exc, flags=False)),
            _validation_fields(found.model, exc))
    if not getattr(found, "queued", True):
        return _run_now(found, params, values)
    jobs_client.start(command_id, params, principal=who, job_id=job_id)
    make_logger("mast.web.access").info(
        "{} submitted the job {} ({})".format(who.name, job_id, command_id))
    return flask.jsonify({"job_id": job_id}), 201


def _run_now(command, params, values):
    """Run a command that is not a job, here, and return its result
    without its secrets. Nothing is stored."""
    redact = secrets.redactor(command.model, values)
    with captured():
        result = command.fn(params)
    if not isinstance(result, Result):
        return flask.jsonify({"result": {"text": "", "html": "",
                                         "data": None}})
    return flask.jsonify({"result": {
        "text": redact(result.text()),
        "html": redact.html(result.html()),
        "data": secrets.redact_data(redact, result.data()),
    }})


@blueprint.post("/uploads")
def upload():
    """
    _function_: `mast.web.api.upload()`

    Keep an uploaded file (the form field `file`) for an `upload`
    option, and return `{"token": "...", "name": "..."}`. A job is
    given the token, which the server turns into the path of the file.

    Exposed at: `/api/v1/uploads` via POST
    """
    who = principal()
    jobs_auth.authorize(who, "submit")
    storage = flask.request.files.get("file")
    if storage is None:
        return error(400, "There is no file in the field 'file'.")
    token, name = uploads.save(storage, who.name)
    return flask.jsonify({"token": token, "name": name}), 201


def _when(text):
    try:
        when = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


@blueprint.get("/jobs")
def list_jobs():
    """
    _function_: `mast.web.api.list_jobs()`

    The newest jobs first. Filters (all optional): `status` (repeat it
    for several), `mine=1`, `command`, `appliance`, `before` (an ISO
    time, for the next page) and `limit` (50 by default).

    Exposed at: `/api/v1/jobs` via GET
    """
    args = flask.request.args
    who = principal()
    try:
        limit = max(1, min(MAX_LIMIT, int(args.get("limit") or 50)))
    except ValueError:
        return error(400, "limit must be a number.")
    before = None
    if args.get("before"):
        before = _when(args.get("before"))
        if before is None:
            return error(400, "before must be an ISO time.")
    found = queue.list_jobs(
        status=args.getlist("status") or None, limit=limit, principal=who,
        submitted_by=who.name if args.get("mine") in ("1", "true") else None,
        command=args.get("command") or None,
        appliance=args.get("appliance") or None,
        before=before, parent_id=None)
    return flask.jsonify({"jobs": [_job_json(info) for info in found]})


@blueprint.get("/jobs/<job_id>")
def job(job_id):
    """
    _function_: `mast.web.api.job(job_id)`

    One job, with its parameters (secrets as `********`), its
    appliances and its child jobs.

    Exposed at: `/api/v1/jobs/<id>` via GET
    """
    who = principal()
    info = queue.reap_if_expired(queue.get(job_id, who), who)
    data = _job_json(info)
    data["children"] = [_job_json(child) for child in queue.list_jobs(
        limit=MAX_LIMIT, principal=who, parent_id=job_id)]
    data["files"] = _files(info)
    return flask.jsonify(data)


@blueprint.get("/jobs/<job_id>/events")
def events(job_id):
    """
    _function_: `mast.web.api.events(job_id)`

    The events of a job after the event `after`: its progress (`say`)
    and its changes of state (`state`).

    Exposed at: `/api/v1/jobs/<id>/events` via GET
    """
    try:
        after = int(flask.request.args.get("after") or 0)
    except ValueError:
        return error(400, "after must be a number.")
    found = queue.events(job_id, after, principal())
    return flask.jsonify({
        "events": [event.model_dump(mode="json") for event in found],
        "last_event": found[-1].id if found else after})


@blueprint.get("/jobs/<job_id>/result")
def result(job_id):
    """
    _function_: `mast.web.api.result(job_id)`

    The result of a finished job: `text`, `html` and `data` (None for
    a result that is not a table). 404 while it has none.

    Exposed at: `/api/v1/jobs/<id>/result` via GET
    """
    found = queue.result(job_id, principal())
    if found is None:
        return error(404, "The job {} has no result.".format(job_id))
    return flask.jsonify({"text": found.text, "html": found.html,
                          "data": found.data})


@blueprint.get("/jobs/<job_id>/history")
def history(job_id):
    """
    _function_: `mast.web.api.history(job_id)`

    The requests and responses of a job, or the traceback of a failed
    job, as a text download. The secrets of the job are redacted.

    Exposed at: `/api/v1/jobs/<id>/history` via GET
    """
    found = queue.result(job_id, principal())
    if found is None:
        return error(404, "The job {} has no result.".format(job_id))
    return flask.Response(
        found.history, mimetype="text/plain",
        headers={"Content-Disposition":
                 'attachment; filename="{}-history.txt"'.format(job_id)})


@blueprint.post("/jobs/<job_id>/cancel")
def cancel(job_id):
    """
    _function_: `mast.web.api.cancel(job_id)`

    Cancel a job. An operator may cancel only their own jobs; an admin
    any job.

    Exposed at: `/api/v1/jobs/<id>/cancel` via POST
    """
    return flask.jsonify(_job_json(queue.cancel(job_id, principal())))


def _within(path, directory):
    path = os.path.realpath(path)
    directory = os.path.realpath(directory)
    return path == directory or path.startswith(directory + os.sep)


def _files(info):
    """The downloads of a finished job: `out.zip` for its `out_dir`,
    and its `out_file`, if they are there. Only paths in the directory
    of the job are offered."""
    if not info.finished:
        return []
    directory = catalog.job_dir(info.id)
    found = []
    for name, value in info.params.items():
        if not isinstance(value, str) or not _within(value, directory):
            continue
        if os.path.isdir(value) and any(
                files for _, _, files in os.walk(value)):
            found.append({"name": "out.zip", "param": name})
        elif os.path.isfile(value):
            found.append({"name": os.path.basename(value), "param": name,
                          "size": os.path.getsize(value)})
    return found


@blueprint.get("/jobs/<job_id>/files")
def files(job_id):
    """
    _function_: `mast.web.api.files(job_id)`

    The downloads of a job.

    Exposed at: `/api/v1/jobs/<id>/files` via GET
    """
    return flask.jsonify({"files": _files(queue.get(job_id, principal()))})


@blueprint.get("/jobs/<job_id>/files/<name>")
def download(job_id, name):
    """
    _function_: `mast.web.api.download(job_id, name)`

    One download of a job: `out.zip` (what the job wrote to its
    `out_dir`, as a zip file), or its `out_file`.

    Exposed at: `/api/v1/jobs/<id>/files/<name>` via GET
    """
    info = queue.get(job_id, principal())
    for item in _files(info):
        if item["name"] != name:
            continue
        path = info.params[item["param"]]
        if name == "out.zip" and os.path.isdir(path):
            return flask.send_file(
                _zip(path), mimetype="application/zip", as_attachment=True,
                download_name="{}-{}.zip".format(
                    info.command.replace(".", "-"), job_id[:8]))
        return flask.send_file(
            os.path.abspath(path), as_attachment=True, download_name=name)
    return error(404, "The job {} has no file {}.".format(job_id, name))


def _zip(directory):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for root, _, names in os.walk(directory):
            for name in names:
                path = os.path.join(root, name)
                archive.write(path, os.path.relpath(path, directory))
    buffer.seek(0)
    return buffer


@blueprint.get("/workers")
def workers():
    """
    _function_: `mast.web.api.workers()`

    The workers that are alive.

    Exposed at: `/api/v1/workers` via GET
    """
    return flask.jsonify({"workers": [
        worker.model_dump(mode="json")
        for worker in queue.workers(principal=principal())]})


def _registry_error(exc):
    from mast import registry
    if isinstance(exc, registry.UnknownName):
        return error(404, str(exc))
    if isinstance(exc, registry.NameTaken):
        return error(409, str(exc), {"name": str(exc)})
    return error(422, str(exc))


def _appliance_json(appliance):
    return appliance.model_dump(mode="json")


@blueprint.get("/appliances")
def appliances():
    """
    _function_: `mast.web.api.appliances()`

    The appliances of the registry.

    Exposed at: `/api/v1/appliances` via GET
    """
    from mast import registry
    jobs_auth.authorize(principal(), "read")
    return flask.jsonify({"appliances": [
        _appliance_json(a) for a in registry.list_appliances()]})


@blueprint.post("/appliances")
def add_appliance():
    """
    _function_: `mast.web.api.add_appliance()`

    Add an appliance: `{name, address, <settings>}`.

    Exposed at: `/api/v1/appliances` via POST
    """
    from mast import registry
    jobs_auth.authorize(principal(), "manage-appliance")
    body = _body()
    fields = {key: body[key] for key in registry.FIELDS
              if key in body and key != "address"}
    try:
        found = registry.add(str(body.get("name") or ""),
                             body.get("address") or None, **fields)
    except registry.RegistryError as exc:
        return _registry_error(exc)
    _changed("Added the appliance {}".format(found.name))
    return flask.jsonify(_appliance_json(found)), 201


@blueprint.patch("/appliances/<name>")
def change_appliance(name):
    """
    _function_: `mast.web.api.change_appliance(name)`

    Change the settings of an appliance (null sets one back to its
    default), or rename it with `rename`.

    Exposed at: `/api/v1/appliances/<name>` via PATCH
    """
    from mast import registry
    jobs_auth.authorize(principal(), "manage-appliance")
    body = _body()
    fields = {key: body[key] for key in registry.FIELDS if key in body}
    try:
        found = registry.update(name, **fields)
        if body.get("rename"):
            found = registry.rename(name, str(body["rename"]))
    except registry.RegistryError as exc:
        return _registry_error(exc)
    _changed("Changed the appliance {}".format(found.name))
    return flask.jsonify(_appliance_json(found))


@blueprint.delete("/appliances/<name>")
def remove_appliance(name):
    """
    _function_: `mast.web.api.remove_appliance(name)`

    Exposed at: `/api/v1/appliances/<name>` via DELETE
    """
    from mast import registry
    jobs_auth.authorize(principal(), "manage-appliance")
    try:
        registry.remove(name)
    except registry.RegistryError as exc:
        return _registry_error(exc)
    _changed("Removed the appliance {}".format(name))
    return "", 204


@blueprint.get("/environments")
def environments():
    """
    _function_: `mast.web.api.environments()`

    The environments of the registry, with their members.

    Exposed at: `/api/v1/environments` via GET
    """
    from mast import registry
    jobs_auth.authorize(principal(), "read")
    return flask.jsonify({"environments": [
        e.model_dump(mode="json") for e in registry.env_list()]})


@blueprint.post("/environments")
def add_environment():
    """
    _function_: `mast.web.api.add_environment()`

    Add an environment: `{name, description, production, members}`.

    Exposed at: `/api/v1/environments` via POST
    """
    from mast import registry
    jobs_auth.authorize(principal(), "manage-appliance")
    body = _body()
    try:
        found = registry.env_add(
            str(body.get("name") or ""), str(body.get("description") or ""),
            bool(body.get("production")),
            [str(m) for m in body.get("members") or []])
    except registry.RegistryError as exc:
        return _registry_error(exc)
    _changed("Added the environment {}".format(found.name))
    return flask.jsonify(found.model_dump(mode="json")), 201


@blueprint.patch("/environments/<name>")
def change_environment(name):
    """
    _function_: `mast.web.api.change_environment(name)`

    Change `description`, `production` or `members` of an environment.

    Exposed at: `/api/v1/environments/<name>` via PATCH
    """
    from mast import registry
    jobs_auth.authorize(principal(), "manage-appliance")
    body = _body()
    try:
        found = registry.env_update(
            name, body.get("description"),
            None if "production" not in body else bool(body["production"]))
        if "members" in body:
            found = registry.env_set_members(
                name, [str(m) for m in body.get("members") or []])
    except registry.RegistryError as exc:
        return _registry_error(exc)
    _changed("Changed the environment {}".format(name))
    return flask.jsonify(found.model_dump(mode="json"))


@blueprint.delete("/environments/<name>")
def remove_environment(name):
    """
    _function_: `mast.web.api.remove_environment(name)`

    Exposed at: `/api/v1/environments/<name>` via DELETE
    """
    from mast import registry
    jobs_auth.authorize(principal(), "manage-appliance")
    try:
        registry.env_remove(name)
    except registry.RegistryError as exc:
        return _registry_error(exc)
    _changed("Removed the environment {}".format(name))
    return "", 204


def _changed(message):
    make_logger("mast.registry").info("{} (by {} from {})".format(
        message, principal().name, flask.request.remote_addr))

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
_module_: `mast.web.catalog`

The commands of MAST as JSON, for the web UI, and the values of a job
from what a form sent.

Each command is a function of one `mast.cli.Params` model, and
`mast.cli.params.options` describes each of its options. The web UI
makes a form for every command from these descriptions, so no command
needs code of its own in the browser.
"""
import os

from mast.cli import params as cli_params
from mast.jobs import registry

__all__ = [
    "option_json", "command_json", "all_commands", "find", "job_values",
    "out_paths", "job_dir", "CommandNotFound",
    "APPLIANCES", "CREDENTIALS", "UPLOAD", "OUT_DIR", "OUT_FILE",
]

# The widgets that the web UI fills itself (the target bar)
APPLIANCES = "appliances"
CREDENTIALS = "credentials"
UPLOAD = "upload"
OUT_DIR = "out_dir"
OUT_FILE = "out_file"

# Where the files that a job of the web UI writes are kept, relative
# to MAST_HOME
JOBS_DIR = os.path.join("tmp", "web", "jobs")


class CommandNotFound(LookupError):
    """There is no command with that id."""


def _json_value(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return str(value)


def option_json(spec):
    """
    _function_: `mast.web.catalog.option_json(spec)`

    Return a `mast.cli.params.OptionSpec` as JSON types. The type is
    its name (`str`, `int`, `float` or `bool`). The default of a secret
    option is never sent.
    """
    return {
        "name": spec.name,
        "kind": spec.kind,
        "type": spec.type.__name__,
        "default": None if spec.secret else _json_value(spec.default),
        "required": spec.required,
        "choices": None if spec.choices is None else [
            _json_value(choice) for choice in spec.choices],
        "help": spec.help,
        "short": spec.short,
        "widget": spec.widget,
        "secret": spec.secret,
    }


def command_json(namespace, command):
    """
    _function_: `mast.web.catalog.command_json(namespace, command)`

    Return a `mast.cli.Command` as JSON types: `id`, `namespace`,
    `name`, `category`, `doc` (markdown), `exclusive`, `queued` and
    `options`.
    """
    return {
        "id": registry.command_id(namespace, command),
        "namespace": namespace,
        "name": command.name,
        "category": command.category or "",
        "doc": command.doc,
        "exclusive": bool(getattr(command, "exclusive", True)),
        "queued": bool(getattr(command, "queued", True)),
        "options": [option_json(spec)
                    for spec in cli_params.options(command.model)],
    }


def all_commands():
    """
    _function_: `mast.web.catalog.all_commands()`

    Return every command of `mast.jobs.registry` as `command_json`, by
    namespace, then in the order of their `Cli`.
    """
    found = []
    for namespace in registry.namespaces():
        cli = registry.cli_for(namespace)
        for command in cli.commands.values():
            if getattr(command, "internal", False):
                continue
            found.append(command_json(namespace, command))
    return found


def find(command_id):
    """
    _function_: `mast.web.catalog.find(command_id)`

    Return `(namespace, command)` for an id such as
    `system.get-status`. Raises `CommandNotFound`, also for an
    internal command (`mast.cli.Command.internal`).
    """
    try:
        command = registry.resolve(command_id)
    except registry.UnknownCommand:
        raise CommandNotFound(command_id) from None
    if getattr(command, "internal", False):
        # A part of MAST runs it itself (Command.internal)
        raise CommandNotFound(command_id)
    return command_id.partition(".")[0], command


def job_dir(job_id):
    """
    _function_: `mast.web.catalog.job_dir(job_id)`

    Return the directory (relative to MAST_HOME) for the files that a
    job of the web UI writes.
    """
    return os.path.join(JOBS_DIR, job_id)


def out_paths(command, job_id):
    """
    _function_: `mast.web.catalog.out_paths(command, job_id)`

    Return `{name: path}` for the `out_dir` and `out_file` options of
    `command`: a directory or a file under `job_dir(job_id)`. The web
    UI chooses these paths, never the browser, so that a job writes
    only there and the download offers only that.
    """
    paths = {}
    for spec in cli_params.options(command.model):
        if spec.widget == OUT_DIR:
            paths[spec.name] = os.path.join(job_dir(job_id), "out")
        elif spec.widget == OUT_FILE:
            extension = os.path.splitext(str(spec.default or ""))[1]
            paths[spec.name] = os.path.join(
                job_dir(job_id), "{}-{}{}".format(
                    command.name, job_id[:8], extension))
    return paths


def job_values(command, params, job_id, upload_path):
    """
    _function_: `mast.web.catalog.job_values(command, params, job_id,
    upload_path)`

    Return `(values, problems)`: the values to build the model of
    `command` from, and a dict of field name to message for what is
    wrong with `params`.

    * A value that is missing, None or "" is left out, so that the
    model uses its default.
    * `out_dir` and `out_file` are set by `out_paths`; what the browser
    sent for them is ignored.
    * An `upload` option holds the token of an upload, which
    `upload_path(token)` turns into the path on the server. It raises
    `LookupError` for a token that is not known.

    Parameters:

    * `command`: The `mast.cli.Command`.
    * `params`: The dict that the browser sent.
    * `job_id`: The id that the job will have.
    * `upload_path`: The function that maps a token to a path.
    """
    problems = {}
    if not isinstance(params, dict):
        return {}, {"": "The parameters must be an object."}
    specs = {spec.name: spec for spec in cli_params.options(command.model)}
    values = {}
    for name, value in params.items():
        spec = specs.get(name)
        if spec is not None and spec.widget in (OUT_DIR, OUT_FILE):
            continue
        if value is None or value == "" or value == []:
            continue
        if spec is not None and spec.widget == UPLOAD:
            tokens = value if isinstance(value, list) else [value]
            try:
                paths = [upload_path(token) for token in tokens]
            except LookupError:
                problems[name] = "The uploaded file is not known. " \
                                 "Upload it again."
                continue
            value = paths if spec.kind == "list" else paths[0]
        values[name] = value
    values.update(out_paths(command, job_id))
    return values, problems

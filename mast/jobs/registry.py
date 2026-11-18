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
_module_: `mast.jobs.registry`

The commands that can run as jobs.

A job names its command with an id of the form `namespace.name`, for
example `backups.normal-backup`. The namespace is a MAST plugin
(`mast.datapower.backups`) and the name is the name of the command on
the command line. These ids are stable. They are stored with each job,
and they are the names that permissions will refer to.
"""
import importlib

__all__ = ["register", "unregister", "resolve", "command_id",
           "UnknownCommand"]

_clis = {}


class UnknownCommand(Exception):
    """There is no command with that id."""


def register(namespace, cli):
    """
    _function_: `mast.jobs.registry.register(namespace, cli)`

    Make the commands of `cli` (a `mast.cli.Cli`) available as
    `namespace.<name>`. The MAST plugins do not need this, `resolve`
    finds them.
    """
    _clis[namespace] = cli


def unregister(namespace):
    """
    _function_: `mast.jobs.registry.unregister(namespace)`

    Undo `register`.
    """
    _clis.pop(namespace, None)


def command_id(namespace, command):
    """
    _function_: `mast.jobs.registry.command_id(namespace, command)`

    Return the id of `command` (a `mast.cli.Command`) in `namespace`.
    """
    return "{}.{}".format(namespace, command.name)


def _cli(namespace):
    if namespace in _clis:
        return _clis[namespace]
    if not namespace.isidentifier():
        raise UnknownCommand(namespace)
    try:
        module = importlib.import_module("mast.datapower." + namespace)
    except ImportError:
        raise UnknownCommand(namespace) from None
    cli = getattr(module, "cli", None)
    if cli is None:
        raise UnknownCommand(namespace)
    return cli


def resolve(command_id):
    """
    _function_: `mast.jobs.registry.resolve(command_id)`

    Return the `mast.cli.Command` with the id `command_id`. Raises
    `UnknownCommand` if there is none.
    """
    namespace, _, name = command_id.partition(".")
    try:
        return _cli(namespace).commands[name]
    except KeyError:
        raise UnknownCommand(command_id) from None
    except UnknownCommand:
        raise UnknownCommand(command_id) from None

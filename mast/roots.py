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
_module_: `mast.roots`

The roots of the `mast` command that core owns. A plugin cannot use
one of these names as its provider.

A root is one of two kinds:

* a `Cli` root (`mast targets add ...`): its commands belong to the
  provider `core`, and each one has a canonical name (`core.add-target`).
* a program root (`mast web`): it starts a part of MAST, and has no
  commands of its own.

Nothing here is imported until it is used, so `mast --help` and the
check of a plugin's name stay fast.
"""
from dataclasses import dataclass

__all__ = ["Root", "ROOTS", "RESERVED", "CORE", "load"]

# The provider of the commands of core
CORE = "core"


@dataclass(frozen=True)
class Root(object):
    """
    _class_: `mast.roots.Root(target, summary, program=False)`

    * `target`: `module:attribute`. For a `Cli` root, the attribute is
    the `mast.cli.Cli`. For a program, it is a function that takes the
    list of arguments after the root.
    * `summary`: One line for `mast --help`.
    * `program`: True for a program root.
    """
    target: str
    summary: str
    program: bool = False


ROOTS = {
    "crypto": Root(
        "mast.crypto.commands:cli",
        "The crypto inventory: collect, assess, export, probe."),
    "targets": Root(
        "mast.registry.cli:targets",
        "The registered targets, and their TLS and SSH pins."),
    "fleets": Root(
        "mast.registry.cli:fleets",
        "Named groups of targets."),
    "credentials": Root(
        "mast.credentials.cli:cli",
        "The stored credentials."),
    "jobs": Root(
        "mast.jobs.cli:jobs_cli",
        "The job queue and its workers."),
    "schedules": Root(
        "mast.jobs.cli:schedules_cli",
        "Jobs that run at set times."),
    "users": Root(
        "mast.auth.cli:cli",
        "The users of MAST."),
    "plugins": Root(
        "mast.plugins_cli:cli",
        "The installed plugins."),
    "web": Root(
        "mast.web.server:program",
        "Start the web server.", program=True),
    "worker": Root(
        "mast.jobs.worker:program",
        "Start a worker of the job queue.", program=True),
    "daemon": Root(
        "mast._daemon:program",
        "Start, stop or install mastd (web, workers and crontab).",
        program=True),
    "version": Root(
        "mast._version:program",
        "Show the version of MAST and of each plugin.", program=True),
    "selftest": Root(
        "mast.testsuite:program",
        "Run the tests of this installation.", program=True),
}

# Names that no plugin may take as its provider: the roots, the
# provider of core, and the roots that later releases of core use
RESERVED = frozenset(ROOTS) | {CORE, "groups", "permissions", "setup",
                               "mast", "help"}


def load(target):
    """
    _function_: `mast.roots.load(target)`

    Import `module:attribute` and return the attribute.
    """
    import importlib
    module, _, attribute = target.partition(":")
    value = importlib.import_module(module)
    for part in attribute.split("."):
        value = getattr(value, part)
    return value

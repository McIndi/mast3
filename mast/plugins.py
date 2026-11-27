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
_module_: `mast.plugins`

The plugins of MAST.

A plugin is a Python distribution with one entry point in the group
`mast_plugin`. Its name is the provider, and its value is a `Manifest`
in a module that imports nothing else of the plugin:

    :::toml
    [project.entry-points.mast_plugin]
    datapower = "mast.datapower.plugin:manifest"

The commands of the plugin are in the `mast.cli.Cli` that
`Manifest.commands` names. Core imports it only when a command of the
plugin is used. All the commands of a plugin are `mast PROVIDER NAME`
on the command line, with the id `PROVIDER.NAME`.

A plugin that cannot be loaded is left out, and `mast plugins list`
says why. The rest of MAST works without it.

This module imports nothing that needs a MAST home, so that `mast
--help` works before there is one.
"""
import importlib.metadata
from dataclasses import dataclass

__all__ = ["Manifest", "Failure", "GROUP", "manifests", "failures",
           "fail", "reset"]

# The group of the entry points of the plugins
GROUP = "mast_plugin"


@dataclass(frozen=True)
class Manifest(object):
    """
    _class_: `mast.plugins.Manifest(provider, title, version, commands)`

    What a plugin is, without its code.

    * `provider`: The name of the plugin, its root on the command line,
    and the first part of its ids and permission paths (`datapower`).
    * `title`: Its name for people (`IBM DataPower`).
    * `version`: Its version.
    * `commands`: `module:attribute` of its `mast.cli.Cli`, whose
    `provider` is `provider`.
    """
    provider: str
    title: str
    version: str
    commands: str


@dataclass(frozen=True)
class Failure(object):
    """
    _class_: `mast.plugins.Failure(name, distribution, reason)`

    A plugin that was left out, and why.
    """
    name: str
    distribution: str
    reason: str


_state = None


def _distribution(entry_point):
    dist = getattr(entry_point, "dist", None)
    return dist.name if dist is not None else ""


def _check(entry_point, manifest):
    """The reason to leave the plugin out, or None."""
    # Imported here: mast.roots imports nothing, but keep this module
    # cheap for the callers that only want the manifests
    from mast import roots
    if not isinstance(manifest, Manifest):
        return "{} is not a mast.plugins.Manifest".format(entry_point.value)
    if manifest.provider != entry_point.name:
        return "its manifest names the provider {!r}, its entry point " \
               "{!r}".format(manifest.provider, entry_point.name)
    if not manifest.provider.isidentifier() or \
            manifest.provider != manifest.provider.lower():
        return "the provider {!r} is not a lower-case name".format(
            manifest.provider)
    if manifest.provider in roots.RESERVED:
        return "the provider {!r} is a name of core".format(manifest.provider)
    return None


def _load():
    found, failed = {}, []
    entry_points = sorted(
        importlib.metadata.entry_points(group=GROUP),
        key=lambda ep: (ep.name, _distribution(ep)))
    for entry_point in entry_points:
        distribution = _distribution(entry_point)
        if entry_point.name in found:
            failed.append(Failure(
                entry_point.name, distribution,
                "the provider {!r} is already given by {}".format(
                    entry_point.name, found[entry_point.name][1])))
            continue
        try:
            manifest = entry_point.load()
        except Exception as exc:
            failed.append(Failure(entry_point.name, distribution,
                                  "its manifest cannot be loaded: {}".format(
                                      exc)))
            continue
        reason = _check(entry_point, manifest)
        if reason:
            failed.append(Failure(entry_point.name, distribution, reason))
            continue
        found[manifest.provider] = (manifest, distribution)
    return {name: value[0] for name, value in found.items()}, failed


def manifests():
    """
    _function_: `mast.plugins.manifests()`

    Return the `Manifest` of each plugin that is installed, by provider.
    """
    global _state
    if _state is None:
        _state = _load()
    return dict(_state[0])


def failures():
    """
    _function_: `mast.plugins.failures()`

    Return the `Failure`s: the plugins that were left out. The command
    registry adds a plugin whose commands cannot be loaded.
    """
    manifests()
    return list(_state[1])


def fail(name, reason):
    """
    _function_: `mast.plugins.fail(name, reason)`

    Leave the plugin `name` out from now on, for `reason`.
    """
    manifests()
    found, failed = _state
    found.pop(name, None)
    failed.append(Failure(name, "", reason))


def reset():
    """
    _function_: `mast.plugins.reset()`

    Look for the plugins again on the next call (for the tests).
    """
    global _state
    _state = None


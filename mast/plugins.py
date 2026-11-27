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

The plugins of MAST, and plugin API 1.

A plugin is a Python distribution with one entry point in the group
`mast_plugin`. Its name is the provider, and its value is a `Manifest`
in a module that imports nothing else of the plugin:

    :::toml
    [project.entry-points.mast_plugin]
    datapower = "mast.datapower.plugin:manifest"

The manifest says which plugin API and which versions of MAST the
plugin is made for, and which kinds of target it gives. Core reads it
without importing the code of the plugin.

The commands of the plugin are in the `mast.cli.Cli` that
`Manifest.commands` names. Core imports it only when a command of the
plugin is used. All the commands of a plugin are `mast PROVIDER NAME`
on the command line, with the id `PROVIDER.NAME`. Each command declares
its effect, its kinds of target, and its target and secret parameters
(see `mast.cli.Command`). The command registry checks them all before
it registers any (`mast.jobs.registry`).

A plugin that cannot be used is left out whole, with none of its
commands, and the rest of MAST works without it. `mast plugins list`
and the Plugins page of the web UI show why, and what to do (a
`Failure`).

This module imports nothing that needs a MAST home, so that `mast
--help` works before there is one.
"""
import importlib.metadata
from dataclasses import dataclass, field

__all__ = ["Manifest", "Failure", "GROUP", "API", "APIS", "CORE_KINDS",
           "BROKEN", "INCOMPATIBLE", "CONFLICT", "INVALID",
           "manifests", "failures", "failure", "distribution", "fail",
           "reset"]

# The group of the entry points of the plugins
GROUP = "mast_plugin"

# The plugin API of this MAST, and every API that it can load
API = 1
APIS = frozenset({1})

# The kinds of target of core itself, which no plugin may give. Core
# has `endpoint` for a TCP or TLS port that belongs to no plugin.
CORE_KINDS = frozenset({"endpoint"})

# Why a plugin was left out (Failure.kind)
# Its manifest or its commands cannot be imported
BROKEN = "broken"
# It is made for another plugin API or another version of MAST
INCOMPATIBLE = "incompatible"
# It takes a name that core or another plugin has
CONFLICT = "conflict"
# Its manifest or one of its commands breaks the rules of the API
INVALID = "invalid"


@dataclass(frozen=True)
class Manifest(object):
    """
    _class_: `mast.plugins.Manifest(provider, title, version, api, core,
    commands, target_kinds=(), pages=())`

    What a plugin is, without its code.

    * `provider`: The name of the plugin, its root on the command line,
    and the first part of its ids and permission paths (`datapower`).
    * `title`: Its name for people (`IBM DataPower`).
    * `version`: Its version.
    * `api`: The plugin API that it is made for (`1`, see `API`).
    * `core`: The versions of MAST (the distribution `mcindi-mast`)
    that it works with, as a version specifier: `">=4.0,<5"`.
    * `commands`: `module:attribute` of its `mast.cli.Cli`, whose
    `provider` is `provider`.
    * `target_kinds`: The kinds of target that it gives, such as
    `("datapower",)`. Its commands accept only these kinds. A kind is
    a lower-case name that no other plugin gives.
    * `pages`: `module:attribute` of each `mast.web.page.Page` that it
    adds to the web UI. The web UI loads them only while the plugin is
    used (see `mast.web.page`).
    """
    provider: str
    title: str
    version: str
    api: int
    core: str
    commands: str
    target_kinds: tuple = field(default=())
    pages: tuple = field(default=())


@dataclass(frozen=True)
class Failure(object):
    """
    _class_: `mast.plugins.Failure(name, distribution, kind, reason, fix)`

    A plugin that was left out.

    * `name`: The name of its entry point: its provider.
    * `distribution`: The Python distribution that installed it.
    * `kind`: `BROKEN`, `INCOMPATIBLE`, `CONFLICT` or `INVALID`.
    * `reason`: What is wrong.
    * `fix`: What to do about it.
    """
    name: str
    distribution: str
    kind: str
    reason: str
    fix: str


_state = None


def _distribution(entry_point):
    dist = getattr(entry_point, "dist", None)
    return dist.name if dist is not None else ""


def _core_version():
    # Imported here: mast imports nothing heavy, but keep this module
    # cheap to import
    from mast import __version__
    return __version__


def _named(distribution, name):
    """How a fix names the distribution of a plugin."""
    return distribution or "the plugin {}".format(name)


def _fix(kind, distribution, name):
    """The usual fix for a failure of `kind`."""
    who = _named(distribution, name)
    if kind == BROKEN:
        return "Reinstall {}, or uninstall it.".format(who)
    if kind == INVALID:
        return ("Report this to the authors of {}, and install a version "
                "without the fault or uninstall it.".format(who))
    if kind == INCOMPATIBLE:
        return ("Install a version of {} for MAST {} (plugin API {}), "
                "or uninstall it.".format(who, _core_version(), API))
    return "Uninstall {}.".format(who)


def _lower_name(value):
    return isinstance(value, str) and value.isidentifier() and \
        value == value.lower()


def _check(entry_point, manifest, distribution):
    """
    Return `(kind, reason, fix)` to leave the plugin out, or None.
    """
    # Imported here: packaging and mast.roots are only needed now
    from packaging.specifiers import InvalidSpecifier, SpecifierSet
    from packaging.version import Version
    from mast import roots
    who = _named(distribution, entry_point.name)
    if not isinstance(manifest, Manifest):
        return (BROKEN, "{} is not a mast.plugins.Manifest".format(
            entry_point.value), None)
    if manifest.provider != entry_point.name:
        return (INVALID, "its manifest names the provider {!r}, its entry "
                "point {!r}".format(manifest.provider, entry_point.name),
                None)
    if not _lower_name(manifest.provider):
        return (INVALID, "the provider {!r} is not a lower-case "
                "name".format(manifest.provider), None)
    if manifest.provider in roots.RESERVED:
        return (CONFLICT, "the provider {!r} is a name of core".format(
            manifest.provider), "Uninstall {}.".format(who))
    if manifest.api not in APIS:
        return (INCOMPATIBLE, "it is made for plugin API {!r}, and this "
                "MAST has plugin API {}".format(manifest.api, API), None)
    try:
        supported = SpecifierSet(manifest.core)
    except (InvalidSpecifier, TypeError):
        return (INVALID, "its core versions {!r} are not a version "
                "specifier".format(manifest.core), None)
    version = _core_version()
    if not supported.contains(Version(version), prereleases=True):
        return (INCOMPATIBLE, "it needs MAST {}, and this is MAST "
                "{}".format(manifest.core, version),
                "Install a version of {} for MAST {}, or a MAST {}.".format(
                    who, version, manifest.core))
    if isinstance(manifest.target_kinds, str) or not all(
            _lower_name(kind) for kind in manifest.target_kinds):
        return (INVALID, "its target kinds {!r} are not a tuple of "
                "lower-case names".format(manifest.target_kinds), None)
    if isinstance(manifest.pages, str) or not all(
            isinstance(page, str) and ":" in page for page in manifest.pages):
        return (INVALID, "its pages {!r} are not a tuple of "
                "module:attribute".format(manifest.pages), None)
    for kind in manifest.target_kinds:
        if kind in CORE_KINDS:
            return (CONFLICT, "the target kind {!r} is a kind of "
                    "core".format(kind), "Uninstall {}.".format(who))
    return None


def _load():
    found, failed = {}, []

    def leave_out(entry_point, distribution, kind, reason, fix=None):
        failed.append(Failure(
            entry_point.name, distribution, kind, reason,
            fix or _fix(kind, distribution, entry_point.name)))

    entry_points = sorted(
        importlib.metadata.entry_points(group=GROUP),
        key=lambda ep: (ep.name, _distribution(ep)))
    for entry_point in entry_points:
        distribution = _distribution(entry_point)
        if entry_point.name in found:
            other = found[entry_point.name][1]
            leave_out(entry_point, distribution, CONFLICT,
                      "the provider {!r} is already given by {}".format(
                          entry_point.name, other),
                      "Uninstall {} or {}.".format(
                          _named(distribution, entry_point.name), other))
            continue
        try:
            manifest = entry_point.load()
        except Exception as exc:
            leave_out(entry_point, distribution, BROKEN,
                      "its manifest cannot be loaded: {}".format(exc))
            continue
        problem = _check(entry_point, manifest, distribution)
        if problem:
            leave_out(entry_point, distribution, *problem)
            continue
        found[manifest.provider] = (manifest, distribution)
    # A kind of target that two plugins give belongs to neither: which
    # one is installed first does not decide
    givers = {}
    for name, (manifest, _) in found.items():
        for kind in manifest.target_kinds:
            givers.setdefault(kind, []).append(name)
    for kind, names in sorted(givers.items()):
        if len(names) < 2:
            continue
        who = [_named(found[n][1], n) for n in names if n in found]
        for name in names:
            if name not in found:
                continue
            distribution = found.pop(name)[1]
            failed.append(Failure(
                name, distribution, CONFLICT,
                "the target kind {!r} is given by {}".format(
                    kind, " and ".join(
                        "the plugin " + n for n in sorted(names))),
                "Uninstall all but one of {}.".format(", ".join(who))))
    return found, failed


def _loaded():
    global _state
    if _state is None:
        _state = _load()
    return _state


def manifests():
    """
    _function_: `mast.plugins.manifests()`

    Return the `Manifest` of each plugin that can be used, by provider.
    """
    return {name: value[0] for name, value in _loaded()[0].items()}


def distribution(name):
    """
    _function_: `mast.plugins.distribution(name)`

    Return the name of the distribution that installed the plugin
    `name`, or `""`.
    """
    found, failed = _loaded()
    if name in found:
        return found[name][1]
    for item in failed:
        if item.name == name:
            return item.distribution
    return ""


def failures():
    """
    _function_: `mast.plugins.failures()`

    Return the `Failure`s: the plugins that were left out. The command
    registry adds a plugin whose commands cannot be loaded, or break the
    rules of the API.
    """
    return list(_loaded()[1])


def failure(name):
    """
    _function_: `mast.plugins.failure(name)`

    Return the `Failure` of the plugin `name`, or None.
    """
    for item in failures():
        if item.name == name:
            return item
    return None


def fail(name, kind, reason, fix=None):
    """
    _function_: `mast.plugins.fail(name, kind, reason, fix=None)`

    Leave the plugin `name` out from now on, for `reason`. The default
    `fix` is the usual one for `kind`.
    """
    found, failed = _loaded()
    dist = distribution(name)
    found.pop(name, None)
    failed.append(Failure(name, dist, kind, reason,
                          fix or _fix(kind, dist, name)))


def reset():
    """
    _function_: `mast.plugins.reset()`

    Look for the plugins again on the next call (for the tests).
    """
    global _state
    _state = None

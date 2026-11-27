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
plugin is made for, and which kinds of target it gives (`Kind`). Core
reads it without importing the code of the plugin.

Kinds of target
---------------

A target has one base kind (`linux`, `datapower`) and any number of
extensions. An extension is a kind that extends another kind, which may
be of another plugin: `django` extends `linux`, `apic-gateway` extends
`datapower`. A target that is `linux` + `django` + `postgres` is still a
Linux host: every command that accepts `linux` accepts it, and the
commands that accept `django` accept it too. An extension inherits the
capabilities of the kind that it extends, and adds its own.

A capability (`collect-crypto`, `test-credentials`) says what MAST can
do with a target of a kind. A command of core asks for capabilities
instead of naming kinds (`mast.cli.Command.needs`), so that it works with
every kind that a plugin gives, and with the kinds of plugins that are
installed later. Two plugins never give the same kind. A plugin whose
kind extends a kind that no installed plugin gives is left out until
that plugin is installed.

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
import re
from dataclasses import dataclass, field

__all__ = ["Manifest", "Kind", "Failure", "GROUP", "API", "APIS",
           "CORE_KINDS", "COLLECT_CRYPTO", "TEST_CREDENTIALS",
           "BROKEN", "INCOMPATIBLE", "CONFLICT", "INVALID", "MISSING",
           "manifests", "failures", "failure", "distribution", "fail",
           "kinds", "provider_of", "lineage", "capabilities", "accepting",
           "is_name", "reset"]

# The group of the entry points of the plugins
GROUP = "mast_plugin"

# The plugin API of this MAST, and every API that it can load
API = 1
APIS = frozenset({1})

# The capabilities that core asks for
# Its crypto inventory can be read (mast crypto inventory)
COLLECT_CRYPTO = "collect-crypto"
# A stored credential can be tested on it (mast credentials test)
TEST_CREDENTIALS = "test-credentials"

# Why a plugin was left out (Failure.problem)
# Its manifest or its commands cannot be imported
BROKEN = "broken"
# It is made for another plugin API or another version of MAST
INCOMPATIBLE = "incompatible"
# It takes a name that core or another plugin has
CONFLICT = "conflict"
# Its manifest or one of its commands breaks the rules of the API
INVALID = "invalid"
# It needs a kind of target that no installed plugin gives
MISSING = "missing"


@dataclass(frozen=True)
class Kind(object):
    """
    _class_: `mast.plugins.Kind(name, title, extends=None,
    capabilities=())`

    A kind of target that a plugin gives.

    * `name`: A lower-case name, with hyphens (`apic-gateway`). No two
    plugins give one name.
    * `title`: Its name for people (`IBM DataPower appliance`).
    * `extends`: None for a base kind. For an extension, the kind that
    it extends, of this plugin or another one.
    * `capabilities`: What MAST can do with a target of this kind,
    besides what the kind that it extends can. See `COLLECT_CRYPTO` and
    `TEST_CREDENTIALS`.
    """
    name: str
    title: str
    extends: str = None
    capabilities: tuple = field(default=())


# The kinds of target of core itself, which no plugin may give: a TCP or
# TLS port that belongs to no plugin
CORE_KINDS = (Kind("endpoint", "A TCP or TLS port"),)


@dataclass(frozen=True)
class Manifest(object):
    """
    _class_: `mast.plugins.Manifest(provider, title, version, api, core,
    commands, kinds=(), pages=())`

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
    * `kinds`: The `Kind`s of target that it gives: base kinds, and
    extensions of its own kinds or of those of other plugins. Its
    commands may accept these and the kinds of other plugins.
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
    kinds: tuple = field(default=())
    pages: tuple = field(default=())


@dataclass(frozen=True)
class Failure(object):
    """
    _class_: `mast.plugins.Failure(name, distribution, problem, reason,
    fix)`

    A plugin that was left out.

    * `name`: The name of its entry point: its provider.
    * `distribution`: The Python distribution that installed it.
    * `problem`: `BROKEN`, `INCOMPATIBLE`, `CONFLICT`, `INVALID` or
    `MISSING`.
    * `reason`: What is wrong.
    * `fix`: What to do about it.
    """
    name: str
    distribution: str
    problem: str
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


def _fix(problem, distribution, name):
    """The usual fix for a failure of `problem`."""
    who = _named(distribution, name)
    if problem == BROKEN:
        return "Reinstall {}, or uninstall it.".format(who)
    if problem == INVALID:
        return ("Report this to the authors of {}, and install a version "
                "without the fault or uninstall it.".format(who))
    if problem == INCOMPATIBLE:
        return ("Install a version of {} for MAST {} (plugin API {}), "
                "or uninstall it.".format(who, _core_version(), API))
    return "Uninstall {}.".format(who)


def _lower_name(value):
    return isinstance(value, str) and value.isidentifier() and \
        value == value.lower()


_NAME = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")


def is_name(value):
    """
    _function_: `mast.plugins.is_name(value)`

    True if `value` can be the name of a kind or a capability: lower
    case, digits and single hyphens, such as `apic-gateway`.
    """
    return isinstance(value, str) and bool(_NAME.match(value))


def _kinds_problem(manifest):
    """What is wrong with the kinds of a manifest by themselves, or
    None."""
    if isinstance(manifest.kinds, (str, Kind)) or not all(
            isinstance(kind, Kind) for kind in manifest.kinds):
        return "its kinds {!r} are not a tuple of mast.plugins.Kind".format(
            manifest.kinds)
    names = [kind.name for kind in manifest.kinds]
    for kind in manifest.kinds:
        if not is_name(kind.name):
            return "the kind {!r} is not a lower-case name".format(kind.name)
        if kind.extends is not None and not is_name(kind.extends):
            return "the kind {} extends {!r}, which is not a name".format(
                kind.name, kind.extends)
        if isinstance(kind.capabilities, str) or not all(
                is_name(c) for c in kind.capabilities):
            return "the capabilities {!r} of the kind {} are not a tuple " \
                   "of names".format(kind.capabilities, kind.name)
    if len(names) != len(set(names)):
        return "it gives one kind twice"
    return None


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
    problem = _kinds_problem(manifest)
    if problem:
        return (INVALID, problem, None)
    if isinstance(manifest.pages, str) or not all(
            isinstance(page, str) and ":" in page for page in manifest.pages):
        return (INVALID, "its pages {!r} are not a tuple of "
                "module:attribute".format(manifest.pages), None)
    core = {kind.name for kind in CORE_KINDS}
    for kind in manifest.kinds:
        if kind.name in core:
            return (CONFLICT, "the kind {!r} is a kind of core".format(
                kind.name), "Uninstall {}.".format(who))
    return None


def _resolve_kinds(found, failed):
    """
    Leave out the plugins whose kinds cannot be used, until all that
    are left can: a kind that two plugins give, an extension of a kind
    that no plugin gives (also one of a plugin that was left out), and
    extensions that extend each other in a circle.
    """
    changed = True
    while changed:
        changed = False
        givers = {}
        for name, (manifest, _) in found.items():
            for kind in manifest.kinds:
                givers.setdefault(kind.name, []).append(name)
        # A kind that two plugins give belongs to neither: which one is
        # installed first does not decide
        for kind, names in sorted(givers.items()):
            if len(names) < 2:
                continue
            who = [_named(found[n][1], n) for n in names]
            for name in names:
                failed.append(Failure(
                    name, found.pop(name)[1], CONFLICT,
                    "the kind {!r} is given by {}".format(kind, " and ".join(
                        "the plugin " + n for n in sorted(names))),
                    "Uninstall all but one of {}.".format(", ".join(who))))
            changed = True
        if changed:
            continue
        parents = {kind.name: kind.extends for kind in CORE_KINDS}
        for manifest, _ in found.values():
            for kind in manifest.kinds:
                parents[kind.name] = kind.extends
        for name in sorted(found):
            manifest, distribution = found[name]
            for kind in manifest.kinds:
                missing = _missing_ancestor(kind.name, parents)
                if missing is None:
                    continue
                found.pop(name)
                if missing is _CIRCLE:
                    failed.append(Failure(
                        name, distribution, INVALID,
                        "its kind {} extends itself through other "
                        "kinds".format(kind.name),
                        _fix(INVALID, distribution, name)))
                else:
                    failed.append(Failure(
                        name, distribution, MISSING,
                        "its kind {} extends the kind {!r}, which no "
                        "installed plugin gives".format(kind.name, missing),
                        "Install the plugin that gives the kind {!r}, or "
                        "uninstall {}.".format(
                            missing, _named(distribution, name))))
                changed = True
                break


_CIRCLE = object()


def _missing_ancestor(name, parents):
    """The first ancestor of the kind `name` that is not known, `_CIRCLE`
    for a circle, or None."""
    seen = set()
    while name is not None:
        if name in seen:
            return _CIRCLE
        seen.add(name)
        if name not in parents:
            return name
        name = parents[name]
    return None


def _load():
    found, failed = {}, []

    def leave_out(entry_point, distribution, problem, reason, fix=None):
        failed.append(Failure(
            entry_point.name, distribution, problem, reason,
            fix or _fix(problem, distribution, entry_point.name)))

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
    _resolve_kinds(found, failed)
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


def fail(name, problem, reason, fix=None):
    """
    _function_: `mast.plugins.fail(name, problem, reason, fix=None)`

    Leave the plugin `name` out from now on, for `reason`. The default
    `fix` is the usual one for `problem`. A plugin whose kind extends a
    kind of `name` is left out too.
    """
    found, failed = _loaded()
    dist = distribution(name)
    found.pop(name, None)
    failed.append(Failure(name, dist, problem, reason,
                          fix or _fix(problem, dist, name)))
    _resolve_kinds(found, failed)


def kinds():
    """
    _function_: `mast.plugins.kinds()`

    Return every `Kind` that can be used, by name: those of core and
    those of the plugins that are used.
    """
    found = {kind.name: kind for kind in CORE_KINDS}
    for manifest, _ in _loaded()[0].values():
        for kind in manifest.kinds:
            found[kind.name] = kind
    return found


def provider_of(kind):
    """
    _function_: `mast.plugins.provider_of(kind)`

    Return the provider that gives the kind `kind` (`core` for a kind of
    core), or None.
    """
    if kind in {k.name for k in CORE_KINDS}:
        return "core"
    for name, (manifest, _) in _loaded()[0].items():
        if kind in {k.name for k in manifest.kinds}:
            return name
    return None


def lineage(kind):
    """
    _function_: `mast.plugins.lineage(kind)`

    Return `[kind, the kind that it extends, ...]` up to its base kind,
    or `[]` for a kind that cannot be used.
    """
    known = kinds()
    chain = []
    while kind is not None and kind in known and kind not in chain:
        chain.append(kind)
        kind = known[kind].extends
    return chain


def capabilities(kind):
    """
    _function_: `mast.plugins.capabilities(kind)`

    Return the capabilities of the kind `kind`: its own and those of
    every kind that it extends.
    """
    known = kinds()
    found = set()
    for name in lineage(kind):
        found.update(known[name].capabilities)
    return frozenset(found)


def accepting(targets=(), needs=()):
    """
    _function_: `mast.plugins.accepting(targets=(), needs=())`

    Return the names of the kinds, sorted, that a command which accepts
    the kinds `targets` and needs the capabilities `needs` takes (see
    `mast.cli.Command`): the kinds in `targets` and every kind that
    extends one of them, that have every capability in `needs`.
    """
    if not targets and not needs:
        return []
    found = []
    for name in kinds():
        chain = lineage(name)
        if targets and not set(targets) & set(chain):
            continue
        if not set(needs) <= capabilities(name):
            continue
        found.append(name)
    return sorted(found)


def reset():
    """
    _function_: `mast.plugins.reset()`

    Look for the plugins again on the next call (for the tests).
    """
    global _state
    _state = None

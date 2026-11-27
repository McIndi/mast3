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

The one registry of the commands of MAST: those of core and those of
each plugin.

A command has the id `PROVIDER.NAME`, for example
`datapower.normal-backup`. The provider is `core` or the name of a
plugin (see `mast.plugins`). The ids are stored with each job and each
schedule, and the permission paths are made from them
(`mast/datapower/normal-backup`).

The CLI, the web UI and the API all find commands here. Two commands
with the same id, or two plugins with the same provider, make the
registration fail, always in the same way (`RegistrationError`).

Registration is all or nothing. Before any command of a provider is
registered, every one is checked against plugin API 1: it declares its
effect, its kinds of target are those of its plugin, and its target
parameters agree with them (`mast.cli.Command.problems`). A plugin
with one fault is left out whole, and `mast plugins list` says why
(`mast.plugins.failures`). A fault in core is a `RegistrationError`.

A command of a plugin may accept the kinds of other plugins. If none of
the kinds that it accepts is given by an installed plugin, the command
is not registered (`unavailable`), and the other commands of its plugin
are. A command of core accepts only the kinds of core, or asks for
capabilities (`mast.cli.Command.needs`).
"""
from mast import plugins, roots

__all__ = ["register", "unregister", "ensure", "resolve", "command_id",
           "providers", "commands", "cli_for", "clis", "missing",
           "leave_out", "unavailable", "accepted_kinds", "reset",
           "UnknownCommand", "RegistrationError"]

CORE = roots.CORE

# Clis that register() gave (the tests, and scripts), by provider
_registered = {}
# The Clis that were loaded, by provider: core has several
_loaded = {}
# The commands by id, made from _loaded and _registered
_index = None
# The commands that accept only kinds that are not installed: id ->
# (command, reason)
_unavailable = {}


class UnknownCommand(Exception):
    """There is no command with that id. The message says why, and
    what to do if a plugin is missing."""


class RegistrationError(Exception):
    """Two commands, or two plugins, claim the same name."""


def register(provider, cli):
    """
    _function_: `mast.jobs.registry.register(provider, cli)`

    Make the commands of `cli` (a `mast.cli.Cli` whose provider is
    `provider`) known. The plugins and core do not need this: their
    commands are found. The tests use it.
    """
    if cli.provider != provider:
        raise RegistrationError("{}: the Cli has the provider {!r}".format(
            provider, cli.provider))
    _registered[provider] = cli
    _invalidate()


def unregister(provider):
    """
    _function_: `mast.jobs.registry.unregister(provider)`

    Undo `register`.
    """
    _registered.pop(provider, None)
    _invalidate()


def ensure(cli):
    """
    _function_: `mast.jobs.registry.ensure(cli)`

    Register `cli` if MAST does not know its provider.
    """
    if cli.provider is None:
        return
    if cli.provider in providers():
        return
    register(cli.provider, cli)


def reset():
    """
    _function_: `mast.jobs.registry.reset()`

    Forget what was loaded, and look for the plugins again (for the
    tests). What `register` gave is kept.
    """
    _loaded.clear()
    plugins.reset()
    _invalidate()


def _invalidate():
    global _index
    _index = None


def _core_clis():
    if CORE not in _loaded:
        found = []
        for name, root in roots.ROOTS.items():
            if not root.program:
                found.append(roots.load(root.target))
        _loaded[CORE] = found
    return _loaded[CORE]


def missing(provider):
    """
    _function_: `mast.jobs.registry.missing(provider)`

    Return why there is no plugin `provider`, and what to do: for a job
    or a schedule of a plugin that is not installed, or was left out.
    """
    failed = plugins.failure(provider)
    if failed is not None:
        return "the plugin {} was left out: {}. {}".format(
            provider, failed.reason, failed.fix)
    return ("no installed plugin gives the provider {!r}. Install the "
            "plugin; `mast plugins list` shows those that are "
            "installed.".format(provider))


def _plugin_cli(provider):
    if provider not in _loaded:
        manifest = plugins.manifests().get(provider)
        if manifest is None:
            raise UnknownCommand(missing(provider))
        try:
            cli = roots.load(manifest.commands)
        except Exception as exc:
            plugins.fail(provider, plugins.BROKEN,
                         "its commands cannot be loaded: {}".format(exc))
            raise UnknownCommand(missing(provider)) from None
        if getattr(cli, "provider", None) != provider:
            plugins.fail(provider, plugins.INVALID,
                         "{} is not a Cli of the provider {!r}".format(
                             manifest.commands, provider))
            raise UnknownCommand(missing(provider))
        _loaded[provider] = [cli]
    return _loaded[provider]


def leave_out(provider, problem, reason):
    """
    _function_: `mast.jobs.registry.leave_out(provider, problem, reason)`

    Leave the plugin `provider` out from now on in this process, with
    all its commands: for a fault found after its commands were
    registered, such as a page of the web UI that does not load. `problem`
    and `reason` are those of `mast.plugins.fail`. The plugins whose
    kinds extend its kinds are left out too.
    """
    plugins.fail(provider, problem, reason)
    _loaded.pop(provider, None)
    _invalidate()


def providers():
    """
    _function_: `mast.jobs.registry.providers()`

    Return the providers, sorted: `core`, then each plugin, and each
    provider given to `register`.
    """
    names = set(plugins.manifests()) | set(_registered)
    names.discard(CORE)
    return [CORE] + sorted(names)


def clis(provider):
    """
    _function_: `mast.jobs.registry.clis(provider)`

    Return the `mast.cli.Cli`s of `provider`: several for core, one for
    a plugin. Raises `UnknownCommand`.
    """
    if provider in _registered:
        return [_registered[provider]]
    if provider == CORE:
        return _core_clis()
    return _plugin_cli(provider)


def cli_for(provider):
    """
    _function_: `mast.jobs.registry.cli_for(provider)`

    Return the one `mast.cli.Cli` of a plugin. Raises `UnknownCommand`,
    also for `core`, which has several (see `clis`).
    """
    found = clis(provider)
    if len(found) != 1:
        raise UnknownCommand(provider)
    return found[0]


def _problems(provider, command):
    """What is wrong with one command of `provider`."""
    found = []
    if command.provider != provider:
        found.append("it has the provider {!r}".format(command.provider))
    if provider != CORE and command.id_name != command.name:
        found.append("the id of a plugin command is its name, not "
                     "{!r}".format(command.id_name))
    found += command.problems()
    if provider == CORE:
        core = {kind.name for kind in plugins.CORE_KINDS}
        for kind in command.targets:
            if kind not in core:
                found.append("it accepts the kind {!r}, which is not a "
                             "kind of core: a command of core asks for "
                             "capabilities (needs)".format(kind))
    return found


def _missing_kinds(command):
    """The reason why `command` accepts no kind that is installed, or
    None."""
    if not command.targets:
        return None
    known = plugins.kinds()
    if any(kind in known for kind in command.targets):
        return None
    return ("{} accepts only the kinds {}, which no installed plugin "
            "gives. Install the plugin that gives {}.".format(
                command.id, ", ".join(command.targets),
                "it" if len(command.targets) == 1 else "one of them"))


def accepted_kinds(command):
    """
    _function_: `mast.jobs.registry.accepted_kinds(command)`

    Return the names of the kinds of target, sorted, that `command`
    accepts on this installation: those that it names and every kind
    that extends one of them, or every kind with the capabilities that
    it needs.
    """
    return plugins.accepting(command.targets, command.needs)


# How many faults a RegistrationError names
_SHOWN = 3


def _provider_index(provider, found):
    """The commands of one provider by id. Raises `RegistrationError`
    with every fault, if there is one."""
    index, faults = {}, []
    for cli in found:
        for command in cli.commands.values():
            for problem in _problems(provider, command):
                faults.append("{}: {}".format(command.invocation, problem))
            if command.id in index:
                faults.append("{}: the id {} is also {}".format(
                    command.invocation, command.id,
                    index[command.id].invocation))
            index[command.id] = command
    if faults:
        message = "; ".join(faults[:_SHOWN])
        if len(faults) > _SHOWN:
            message += " (and {} more)".format(len(faults) - _SHOWN)
        raise RegistrationError(message)
    return index


def _build_once():
    index = {}
    _unavailable.clear()
    for provider in providers():
        try:
            found = clis(provider)
        except UnknownCommand:
            # A plugin that cannot be loaded (see mast.plugins.failures)
            continue
        try:
            index.update(_provider_index(provider, found))
        except RegistrationError as exc:
            if provider == CORE or provider in _registered:
                raise
            # None of the commands of the plugin are registered
            plugins.fail(provider, plugins.INVALID, str(exc))
            _loaded.pop(provider, None)
    for command_id, command in list(index.items()):
        reason = _missing_kinds(command)
        if reason:
            _unavailable[command_id] = (index.pop(command_id), reason)
    return index


def _build():
    # A plugin that is left out takes its kinds with it, which can make
    # the commands of other plugins unavailable: build again until
    # nothing changes
    while True:
        failed = len(plugins.failures())
        index = _build_once()
        if len(plugins.failures()) == failed:
            return index


def unavailable(provider=None):
    """
    _function_: `mast.jobs.registry.unavailable(provider=None)`

    Return `(command, reason)` for each command (of `provider`, if
    given) that is not registered because it accepts only kinds that no
    installed plugin gives.
    """
    _commands()
    return [value for value in _unavailable.values()
            if provider is None or value[0].provider == provider]


def _commands():
    global _index
    if _index is None:
        _index = _build()
    return _index


def commands(provider=None):
    """
    _function_: `mast.jobs.registry.commands(provider=None)`

    Return every `mast.cli.Command` (of `provider`, if given), by
    provider, then in the order of their `Cli`.
    """
    return [command for command in _commands().values()
            if provider is None or command.provider == provider]


def resolve(command_id):
    """
    _function_: `mast.jobs.registry.resolve(command_id)`

    Return the `mast.cli.Command` with the id `command_id`. Raises
    `UnknownCommand`. The queue refuses a command that never runs as a
    job (`queued=False`, such as `core.add-target`).
    """
    try:
        return _commands()[command_id]
    except (KeyError, TypeError):
        pass
    if command_id in _unavailable:
        raise UnknownCommand(_unavailable[command_id][1])
    provider = command_id.partition(".")[0] \
        if isinstance(command_id, str) else None
    if provider and provider not in providers():
        raise UnknownCommand("{}: {}".format(command_id, missing(provider)))
    raise UnknownCommand("there is no command {}".format(command_id))


def command_id(command):
    """
    _function_: `mast.jobs.registry.command_id(command)`

    Return the id of `command` (a `mast.cli.Command`).
    """
    return command.id

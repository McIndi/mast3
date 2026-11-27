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
"""
from mast import plugins, roots

__all__ = ["register", "unregister", "ensure", "resolve", "command_id", "providers", "commands", "cli_for", "clis",
           "reset", "UnknownCommand", "RegistrationError"]

CORE = roots.CORE

# Clis that register() gave (the tests, and scripts), by provider
_registered = {}
# The Clis that were loaded, by provider: core has several
_loaded = {}
# The commands by id, made from _loaded and _registered
_index = None


class UnknownCommand(Exception):
    """There is no command with that id."""


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


def _plugin_cli(provider):
    if provider not in _loaded:
        manifest = plugins.manifests().get(provider)
        if manifest is None:
            raise UnknownCommand(provider)
        try:
            cli = roots.load(manifest.commands)
        except Exception as exc:
            plugins.fail(provider, "its commands cannot be loaded: {}".format(
                exc))
            raise UnknownCommand(provider) from None
        if getattr(cli, "provider", None) != provider:
            plugins.fail(provider, "{} is not a Cli of the provider {!r}".format(
                manifest.commands, provider))
            raise UnknownCommand(provider)
        _loaded[provider] = [cli]
    return _loaded[provider]


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


def _provider_index(provider, found):
    """The commands of one provider by id. Raises `RegistrationError`."""
    index = {}
    for cli in found:
        for command in cli.commands.values():
            if command.provider != provider:
                raise RegistrationError(
                    "{}: the command {} has the provider {!r}".format(
                        provider, command.name, command.provider))
            if provider != CORE and command.id_name != command.name:
                raise RegistrationError(
                    "{}: the id of a plugin command is its name, not "
                    "{!r}".format(command.invocation, command.id_name))
            if command.id in index:
                raise RegistrationError(
                    "{}: the id {} is also {}".format(
                        command.invocation, command.id,
                        index[command.id].invocation))
            index[command.id] = command
    return index


def _build():
    index = {}
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
            plugins.fail(provider, str(exc))
            _loaded.pop(provider, None)
    return index


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
        raise UnknownCommand(command_id) from None


def command_id(command):
    """
    _function_: `mast.jobs.registry.command_id(command)`

    Return the id of `command` (a `mast.cli.Command`).
    """
    return command.id

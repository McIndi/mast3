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
_module_: `mast.plugins_cli`

`mast plugins`: the installed plugins.
"""
from mast import plugins
from mast.cli import Cli, Params, Table
from mast.roots import CORE

__all__ = ["cli"]

cli = Cli(description="The plugins of MAST.", provider=CORE,
          prog="mast plugins", queued=False)


class ListPlugins(Params):
    pass


@cli.command("list", effect="read", id="list-plugins")
def list_plugins(params: ListPlugins):
    """Show the installed plugins: their version, the plugin API and the
versions of MAST that they are made for, and the kinds of target that
they give. A plugin that was left out is shown with what is wrong and
what to do. None of its commands can be used. A command that accepts
only kinds that no installed plugin gives is shown on a line of its
own."""
    # Load the commands of every plugin, so that a plugin whose
    # commands cannot be loaded, or break the rules, is left out
    from mast.jobs import registry
    registry.commands()
    rows = []
    for provider, m in sorted(plugins.manifests().items()):
        rows.append((m.provider, m.title, m.version, m.api, m.core,
                     ", ".join(kind.name for kind in m.kinds), "loaded",
                     "", ""))
        for command, reason in registry.unavailable(provider):
            rows.append((command.id, "", "", "", "", "", "unavailable",
                         "it accepts only kinds that are not installed",
                         reason.rpartition(". ")[2]))
    rows += [(f.name, f.distribution, "", "", "", "",
              "left out ({})".format(f.problem), f.reason, f.fix)
             for f in plugins.failures()]
    return Table(["Provider", "Title", "Version", "API", "MAST",
                  "Kinds", "State", "Problem", "What to do"], rows)


class ListKinds(Params):
    pass


@cli.command("kinds", effect="read", id="list-kinds")
def list_kinds(params: ListKinds):
    """Show the kinds of target that can be used: those of core and of
the installed plugins. A target has one base kind and any number of
extensions; an extension extends another kind and has its capabilities
as well as its own. A command that accepts a kind accepts every kind
that extends it."""
    rows = []
    for name, kind in sorted(plugins.kinds().items()):
        own = set(kind.capabilities)
        inherited = sorted(plugins.capabilities(name) - own)
        rows.append((name, kind.title, plugins.provider_of(name),
                     kind.extends or "(base kind)",
                     ", ".join(sorted(own)), ", ".join(inherited)))
    return Table(["Kind", "Title", "Plugin", "Extends", "Capabilities",
                  "Inherited capabilities"], rows)

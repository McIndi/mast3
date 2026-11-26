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


@cli.command("list", id="list-plugins")
def list_plugins(params: ListPlugins):
    """Show the installed plugins, and each plugin that was left out
with the reason."""
    # Load the commands of every plugin, so that a plugin whose
    # commands cannot be loaded is shown as left out
    from mast.jobs import registry
    registry.commands()
    rows = [(m.provider, m.title, m.version, "loaded", "")
            for m in plugins.manifests().values()]
    rows += [(f.name, "", "", "left out", f.reason) for f in plugins.failures()]
    return Table(["Provider", "Title", "Version", "State", "Reason"], rows)

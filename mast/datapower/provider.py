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
_module_: `mast.datapower.provider`

The one `mast.cli.Cli` of the DataPower plugin. Every DataPower command
is `mast datapower NAME`, with the id `datapower.NAME`. Each module of
the plugin registers its commands in its own section (`cli.section`):
`system`, `backups`, `crypto` and so on are categories, not parts
of the name.

`mast.datapower.commands` imports every module, so that the `Cli` has
all the commands.
"""
from mast.cli import Cli

__all__ = ["PROVIDER", "cli"]

PROVIDER = "datapower"

cli = Cli(
    description="Manage IBM DataPower appliances: system, backups, "
                "deployment, accounts, network, developer, crypto, status "
                "and terminal commands.",
    provider=PROVIDER)

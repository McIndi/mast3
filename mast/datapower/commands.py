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
_module_: `mast.datapower.commands`

The commands of the DataPower plugin, in the order of `mast datapower
--help` and of the web UI.
"""
from mast.datapower.provider import cli

# Each import registers the commands of a category
from mast.datapower.system import system  # noqa: E402,F401
from mast.datapower.backups import backups  # noqa: E402,F401
from mast.datapower.deployment import deployment  # noqa: E402,F401
from mast.datapower.accounts import accounts  # noqa: E402,F401
from mast.datapower.network import network  # noqa: E402,F401
from mast.datapower.developer import developer  # noqa: E402,F401
from mast.datapower import crypto  # noqa: E402,F401
from mast.datapower.crypto import inventory  # noqa: E402,F401
from mast.datapower.status import collector  # noqa: E402,F401
from mast.datapower.ssh import terminal, interactive  # noqa: E402,F401

__all__ = ["cli", "CATEGORIES"]

# The order of the categories in the catalog and the web UI. A module
# can import another (system imports backups), so the order of the
# imports above is not the order in which the commands registered.
CATEGORIES = ("system", "backups", "deployment", "accounts", "network",
              "developer", "crypto", "status", "terminal")

cli.commands = dict(sorted(
    cli.commands.items(),
    key=lambda item: CATEGORIES.index(item[1].category)))

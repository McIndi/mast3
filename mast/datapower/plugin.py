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
_module_: `mast.datapower.plugin`

The manifest of the DataPower plugin: the entry point `mast_plugin`
names it. It imports nothing else of the plugin.

Until the plugin is its own distribution (mcindi-mast-datapower), it
has the version of MAST and works only with that version.
"""
from mast import __version__
from mast.plugins import Manifest

__all__ = ["manifest"]

manifest = Manifest(
    provider="datapower",
    title="IBM DataPower",
    version=__version__,
    api=1,
    core="=={}".format(__version__),
    commands="mast.datapower.commands:cli",
    target_kinds=("datapower",),
    pages=("mast.datapower.status.page:page",
           "mast.datapower.crypto.page:page",
           "mast.datapower.ssh.page:page"))

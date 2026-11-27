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
_module_: `mast.crypto.commands`

The commands of `mast crypto`.

The code of the crypto inventory is still in `mast.datapower.crypto`.
It moves to this package with the collector interface (design step 8).
"""
from mast.crypto.cli import cli

from mast.datapower.crypto import inventory  # noqa: E402,F401
from mast.datapower.crypto import posture, export, probe  # noqa: E402,F401

__all__ = ["cli"]

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
_module_: `mast.crypto.cli`

The `mast.cli.Cli` of `mast crypto`. Its commands belong to core, so
each one has a canonical name: `mast crypto inventory` is
`core.crypto-inventory`.
"""
from mast.cli import Cli
from mast.roots import CORE

__all__ = ["cli", "section"]

cli = Cli(
    description="The crypto inventory: read the crypto of the targets, "
                "assess it, keep exceptions, export a CBOM and probe TLS "
                "endpoints.",
    provider=CORE, prog="mast crypto")

# The commands of mast crypto are in the category crypto
section = cli.section("crypto")

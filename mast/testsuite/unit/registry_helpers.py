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
"""Targets for the tests that run commands: jobs run only on registered
targets of a kind that the command accepts."""
from mast import registry


def register(*names, kind="datapower"):
    """Register each name as a target of `kind` in the current database,
    unless it is there already. The address is a reserved one."""
    for name in names:
        if registry.by_name(name) is None:
            registry.add(name, kind, "192.0.2.1")

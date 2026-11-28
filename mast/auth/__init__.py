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
_module_: `mast.auth`

The local users of MAST, their passwords and tokens, and who the command
line acts for. What a user may do is in `mast.permissions`.

* `mast.auth.passwords`: Hash a password and check it (scrypt).
* `mast.auth.users`: Add, change and remove users and service accounts,
and check a login.
* `mast.auth.tokens`: The tokens of the command line and of the API.
* `mast.auth.identity`: Who the command line acts for.

The users are kept in the job database (the table `users`), so that
every process of a MAST home sees the same users. `mast users`
manages them on the command line.
"""

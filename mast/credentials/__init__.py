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
_module_: `mast.credentials`

The credential store: user names and passwords for appliances, which
an admin stores once (`mast credentials`, or Admin → Credentials in the
web UI) and operators then use by name. A job names its credential
(`--credential NAME`, `-C NAME`); the worker decrypts it just before
the command runs, and the password is never in the job's parameters,
its progress, its result, a log or an answer of the API.

* `mast.credentials.store`: The credentials, their bindings to
appliances and environments, their uses, and the audit trail.
* `mast.credentials.keys`: Where the keys come from (a key file in
`etc/local`, see `[credentials]` in `jobs.conf`).
* `mast.credentials.cli`: The `mast credentials` program.

A credential is tested with the command `TEST_COMMAND`: it reads the
firmware version of the appliance, and tries an action that changes
nothing, to find out whether the user can change the appliance. Its
result is a table with the columns `appliance`, `works` and `can
change` (`yes` or `no` in each).
"""

__all__ = ["TEST_COMMAND"]

# The command that tests a credential, see mast.datapower.system
TEST_COMMAND = "datapower.check-credentials"

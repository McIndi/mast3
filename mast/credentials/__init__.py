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

The credential store: user names with a password or an SSH private key,
which an admin stores once (`mast credentials`, or Admin → Credentials
in the web UI) and operators then use by name. A job names its
credential (`--credential NAME`); the worker decrypts it just before
the command runs, and the secret is never in the job's parameters, its
progress, its result, a log or an answer of the API.

* `mast.credentials.store`: The credentials, their bindings to targets
and fleets, their uses, and the audit trail.
* `mast.credentials.keys`: Where the keys come from (a key file in
`etc/local`, see `[credentials]` in `jobs.conf`).
* `mast.credentials.testing`: The test of a credential, with the
command that the kind of the target names (`credential_test`).
* `mast.credentials.cli`: The `mast credentials` program.
"""

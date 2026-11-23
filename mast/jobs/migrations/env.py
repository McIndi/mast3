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
The Alembic environment for the job database. `mast.jobs.db.upgrade`
runs the migrations with a connection that it supplies. To make a
migration after a change to `mast.jobs.models`, add a file to
`versions` whose `down_revision` is the current newest revision.
"""
from alembic import context

from mast.jobs.models import Base
# The other tables in the job database, so that they are in Base.metadata
import mast.auth.models  # noqa: F401
import mast.registry.models  # noqa: F401
import mast.credentials.models  # noqa: F401
import mast.datapower.status.models  # noqa: F401
import mast.datapower.crypto.models  # noqa: F401

def run():
    context.configure(
        connection=context.config.attributes["connection"],
        target_metadata=Base.metadata,
        # SQLite cannot alter most things in place
        render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


# Alembic executes this file to run the migrations. Anything else that
# imports it (a tool that walks the packages) must find nothing to do.
try:
    context.config
except (AttributeError, NameError):
    pass
else:
    run()

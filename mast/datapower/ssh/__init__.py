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
# Copyright 2015-2024, McIndi Solutions, All rights reserved.
"""
mast ssh: the DataPower CLI over SSH, on several appliances at once:
`mast-ssh` on the command line (`__main__`), and the Terminal page of
the web UI (`page`, `sessions`), whose sessions are jobs of the command
`ssh.terminal` (`terminal`).
"""
from mast import __version__  # noqa: F401

# The job of a session of the Terminal page (ssh.terminal)
from .terminal import cli  # noqa: E402,F401

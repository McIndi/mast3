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
_module_: `mast.datapower.ssh.terminal`

The command `ssh.terminal`: the job that records a session of the
Terminal page (`mast.datapower.ssh.sessions`). The web server makes the
job and does it itself; it is not offered on the Commands page, and no
worker runs it.
"""
from mast.cli import Cli, Option, Params
from mast.plugin_utils.params import APPLIANCES, STORED_CREDENTIAL

__all__ = ["cli", "Terminal", "terminal"]

from mast.datapower.provider import cli as _provider  # noqa: E402

cli = _provider.section("terminal")


class Terminal(Params):
    appliances: list[str] = Option(
        default_factory=list, short="a", widget=APPLIANCES,
        help="The appliances of the session.")
    credential: str | None = Option(
        None, widget=STORED_CREDENTIAL,
        help="The stored credential of the session, if it has one.")


@cli.command("terminal", group="terminal", exclusive=False, passive=True,
             internal=True)
def terminal(params: Terminal):
    """A session of the Terminal page of the web UI: its lines and the
answers of the appliances. The web server records it; it cannot be run
as a command."""
    raise RuntimeError("ssh.terminal is the record of a session of the "
                       "Terminal page; only the web server runs it.")

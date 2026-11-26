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
import os
import sys
import platform

from mast import home


USAGE = "usage: mast daemon {start | stop | restart | status}"


if "Windows" in platform.system():

    def main(argv=None):
        import win32serviceutil
        # mast.bat sets MAST_HOME. The service manager does not: it
        # starts pythonservice.exe, which is under
        # MAST_HOME/python/<version>/Lib/site-packages/win32.
        if os.environ.get("MAST_HOME"):
            home.enter()
        else:
            home.enter(home.find_from(sys.executable))

        from .mast_daemon import MASTd

        # The service manager runs this with its own arguments, and
        # `mast daemon install` with those after `daemon`
        if argv is None:
            win32serviceutil.HandleCommandLine(MASTd)
        else:
            win32serviceutil.HandleCommandLine(
                MASTd, argv=["mast daemon"] + list(argv))

else:

    def main(argv=None):
        from .mast_daemon import MASTd
        from mast.config import MAST_HOME as mast_home

        pid_file = os.path.join(
            mast_home,
            "var",
            "run",
            "mastd.pid")
        mastd = MASTd(pid_file)

        argv = sys.argv[1:] if argv is None else list(argv)
        if len(argv) != 1:
            print(USAGE)
            sys.exit(1)

        command = argv[0]
        if command == "start":
            mastd.start()
        elif command == "stop":
            mastd.stop()
        elif command == "restart":
            mastd.restart()
        elif command == "status":
            sys.exit(mastd.status())
        else:
            print(USAGE)
            sys.exit(1)

        sys.exit(0)

def program(argv):
    """
    _function_: `mast._daemon.program.program(argv)`

    `mast daemon`: start, stop, restart or show mastd. On Windows, the
    arguments of the service (`install`, `start`, `remove`, ...).
    """
    main(argv)
    return 0

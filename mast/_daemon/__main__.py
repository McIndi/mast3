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


if "Windows" in platform.system():
    import win32serviceutil

    def main():
        # mastd.bat sets MAST_HOME. The service manager does not: it
        # starts pythonservice.exe, which is under
        # MAST_HOME/python/<version>/Lib/site-packages/win32.
        if os.environ.get("MAST_HOME"):
            home.enter()
        else:
            home.enter(home.find_from(sys.executable))

        from .mast_daemon import MASTd

        win32serviceutil.HandleCommandLine(MASTd)

else:

    def main():
        from .mast_daemon import MASTd
        from mast.config import MAST_HOME as mast_home

        pid_file = os.path.join(
            mast_home,
            "var",
            "run",
            "mastd.pid")
        mastd = MASTd(pid_file)

        if len(sys.argv) != 2:
            print("USAGE: python -m mastd.daemon { start | stop | restart | status }")
            sys.exit(1)

        if "start" in sys.argv[1]:
            mastd.start()
        elif "stop" in sys.argv[1]:
            mastd.stop()
        elif "restart" in sys.argv[1]:
            mastd.restart()
        elif "status" in sys.argv[1]:
            print(mastd.status())
        else:
            print("USAGE: python -m mast.daemon { start | stop | restart | status }")
            sys.exit(1)

        sys.exit(0)

if __name__ == "__main__":
    print("Calling main")
    main()
    print("main returned")

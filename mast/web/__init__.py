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
_module_: `mast.web`

The web UI of MAST.

* `mast.web.app`: The Flask application, `create_app()`.
* `mast.web.auth`: Login, logout, and the check of the session.
* `mast.web.security`: The CSRF token, the session key and the
security headers.
* `mast.web.server`: Serve the application with CherryPy.

There are two ways to start the web UI:

1. `mastd` (a service on Windows, a daemon on Linux and macOS) hosts it
in a thread, with the `mastd_plugin` `Plugin` below.
2. `mast-web` (`mast-web.bat` on Windows) serves it until Ctrl-C.

Every page needs a login. Add the first user with
`mast-users add -u NAME -r admin`.
"""
import threading

from mast.logging import make_logger

__all__ = ["Plugin"]


class Plugin(threading.Thread):
    """
    _class_: `mast.web.Plugin(threading.Thread)`

    The `mastd_plugin` that serves the web UI in a thread. It runs
    until `mastd` stops.

    `mastd` stops it last, so that the web UI shows the jobs that `mastd`
    waits for while it stops.
    """
    stop_last = True

    def __init__(self):
        super(Plugin, self).__init__()
        self.daemon = True

    def stop(self):
        """
        _method_: `mast.web.Plugin.stop(self)`

        Stop the web server. `run` then returns.
        """
        from mast.web.server import stop_server
        stop_server()

    def run(self):
        """
        _method_: `mast.web.Plugin.run(self)`

        Serve the web UI. If it cannot start (see
        `mast.web.server.StartupError`), log why; `mastd` goes on with
        its other plugins.

        Logs to: `mast.datapower.web.log`
        """
        from mast.web.server import StartupError, main
        logger = make_logger("mast.datapower.web")
        logger.info("Attempting to start web gui.")
        try:
            main()
        except StartupError as e:
            logger.error("The web GUI did not start. {}".format(e))
            return
        logger.info("web gui stopped")

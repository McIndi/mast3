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
_module_: `mast.datapower.web`

This module provides a web gui to `mast.datapower`. There are two ways
to start the web gui:

1. Using `mastd` which is a service on Windows or a daemon on Linux. This
module provides a `mastd_plugin` which will host the web gui in it's own
thread.
2. Using the command line interface. If you run `$MAST_HOME/mast-web`
on Linux or `%MAST_HOME%\\mast-web.bat` on windows, the web gui will be
started and the address at which it is accessible will be printed to the
screen.
"""
import threading
from mast.logging import make_logger
from .gui import *
import os
from mast import __version__

class Plugin(threading.Thread):
    """
    _class_: `mast.datapower.web.Plugin(threading.Thread)`

    This class is a `mastd_plugin` which is a subclass of `threading.Thread`
    It will be started by mastd and continue to run until mastd is stopped.

    `mastd` stops it last, so that the web GUI shows the jobs that `mastd`
    waits for while it stops.
    """
    stop_last = True

    def __init__(self):
        """
        _method_: `mast.datapower.web.Plugin.__init__(self)`

        Plugin is a SubClass of threading.Thread and is responsible
        for serving the Web GUI over https on the configured port
        """
        super(Plugin, self).__init__()
        self.daemon = True

    def stop(self):
        """
        _method_: `mast.datapower.web.Plugin.stop(self)`

        Stop the web server. `run` then returns.
        """
        stop_server()

    def run(self):
        """
        _method_: `mast.datapower.web.Plugin.run(self)`

        This method is responsible for starting the web gui. It will log
        to `mast.datapower.web.log` When starting and/or stopping.
        """
        logger = make_logger("mast.datapower.web")
        logger.info("Attempting to start web gui.")
        try:
            main()
        except CertificateNotFound as e:
            # mastd goes on with its other plugins, without the web GUI
            logger.error("The web GUI did not start. {}".format(e))
            return
        logger.info("web gui stopped")

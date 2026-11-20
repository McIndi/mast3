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
_module_: `mast.daemon`

This module implements a daemon on Linux platforms or a
Service on Windows platforms. It checks for any modules
which have a registered [entry point](https://packaging.python.org/en/latest/specifications/entry-points/)
called `mastd_plugin` which should be a subclass of `threading.Thread`.

When `mastd` is started, it will search for and find `mastd_plugin`s and
attempt to start them. After one minute and each minute after that,
each thread will be checked. If it is alive, it will be left alone, but
if it is dead, it will be restarted. This process will continue until
`mastd` is stopped.

When `mastd` is stopped, it takes no more jobs and starts no more
scheduled tasks, and it exits when the running ones end. On Linux,
stopping it a second time (a second SIGTERM, or Ctrl-C while
`mastd stop` waits) makes it exit now; its running jobs then become
lost.
"""
import os
import sys
import platform
import threading
from importlib import metadata

from mast import home
from mast._daemon.plugins import PluginHost

try:
    import win32serviceutil
    import servicemanager
    import win32service
    import win32event
    import socket
except ImportError:
    from .daemon import Daemon

# The Windows service manager starts pythonservice.exe without MAST_HOME.
# It is under MAST_HOME/python/<version>/Lib/site-packages/win32.
if "Windows" in platform.system() and not os.environ.get("MAST_HOME"):
    mast_home = home.enter(home.find_from(sys.executable))
else:
    mast_home = home.enter()


# This import needs os.environ["mastd"] to be set
os.environ["mastd"] = "true"
from mast.logging import make_logger, logged


@logged("mast.daemon")
def get_plugins():
    """
    _function_: `mast.daemon.get_plugins()`

    This function will use [importlib.metadata.entry_points](https://docs.python.org/3/library/importlib.metadata.html#entry-points)
    to find `entry_points` for `mastd_plugin` and return them.
    """
    named_objects = {}
    entry_points = metadata.entry_points()
    if hasattr(entry_points, "select"):
        candidates = entry_points.select(group='mastd_plugin')
    else:
        candidates = entry_points.get('mastd_plugin', [])
    for ep in candidates:
        try:
            named_objects.update({ep.name: ep.load()})
        except Exception:
            pass
    return named_objects

PLUGINS = get_plugins()

if "Windows" in platform.system():
    class MASTd(win32serviceutil.ServiceFramework):
        """
        _class_: `mast.daemon.MASTd(win32serviceutil.ServiceFramework)`

        This is the Windows version of the MASTd class. It uses
        `win32serviceutil.ServiceFramework` to setup a Windows
        service to host mastd.
        """
        _svc_name_ = "mastd"
        _svc_display_name_ = "mastd"

        def __init__(self, args):
            """
            _method_: `mast.daemon.MASTd.__init__(self, args)`

            Initialize the service.

            Parameters:

            * `args`: These are the args being passed to
            `win32serviceutil.ServiceFramework`. These args usually
            come from the user via the command line, and should not
            need to be called directly.
            """
            logger = make_logger("mast.daemon")
            logger.debug("mastd running in {}".format(os.getcwd()))
            servicemanager.LogInfoMsg("In __init__ args: {}".format(str(args)))
            win32serviceutil.ServiceFramework.__init__(self, args)
            socket.setdefaulttimeout(60)
            self.timeout = 60000
            self.hWaitStop = win32event.CreateEvent(None, 0, 0, None)
            self.stop_requested = False

        def SvcStop(self):
            """
            _method_: `mast.daemon.MASTd.SvcStop(self)`

            Stop the service. `run` then stops the plugins and waits
            for the running jobs and scheduled tasks to end.
            """
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            self.stop_requested = True
            win32event.SetEvent(self.hWaitStop)

        def SvcDoRun(self):
            """
            _method_: `mast.daemon.MASTd.SvcDoRun(self)`

            Run the service.
            """
            servicemanager.LogInfoMsg("In SvcDoRun")
            self.ReportServiceStatus(win32service.SERVICE_START_PENDING)
            servicemanager.LogMsg(
                servicemanager.EVENTLOG_INFORMATION_TYPE,
                servicemanager.PYS_SERVICE_STARTED,
                (self._svc_name_, '')
            )
            self.ReportServiceStatus(win32service.SERVICE_RUNNING)
            servicemanager.LogInfoMsg("Running run")
            self.run()

        def _stopping(self):
            # Tell Windows that the service is still stopping
            self.ReportServiceStatus(
                win32service.SERVICE_STOP_PENDING, waitHint=30000)

        @logged("mast.daemon")
        def run(self):
            """
            _method_: `mast.daemon.MASTd.run(self)`

            Start the plugins, and each minute start again the ones
            that ended. When the service is stopped, stop the plugins
            and wait for them, see `mast._daemon.plugins`.
            """
            logger = make_logger("mast.daemon")
            servicemanager.LogInfoMsg("Plugins: {}".format(PLUGINS))
            host = PluginHost(PLUGINS, logger)
            try:
                while not self.stop_requested:
                    host.start_dead()
                    rc = win32event.WaitForSingleObject(
                        self.hWaitStop, self.timeout)
                    if rc == win32event.WAIT_OBJECT_0:
                        break
                logger.info(
                    "mastd is stopping. It waits for the running jobs and "
                    "scheduled tasks to end.")
                host.stop(heartbeat=self._stopping)
                logger.info("mastd stopped")
            except Exception:
                logger.exception(
                    "An unhandled exception occurred during execution")
                raise

else:
    class MASTd(Daemon):
        """
        _class_: `mast.daemon.MASTd(daemon)`

        This class is the Linux version of MASTd, It acts like a
        well-behaved Linux daemon. It will write a pid file to
        `$MAST_HOME/var/run/mastd.pid` and it will fork into the
        background twice causing it to become a child of init.

        After that initilization, it will find `mastd_plugins` and
        attempt to start them. After all the plugins are started,
        mastd will check every minute each of the plugins, restarting
        them if they are not alive.

        On SIGTERM it stops gracefully, see `request_stop`.
        """
        def get_plugins(self):
            """
            _method_: `mast.daemon.MASTd.get_plugins(self)`

            This method uses `importlib.metadata.entry_points` to locate
            all `mastd_plugin`s and return them.
            """
            logger = make_logger("mast.daemon")
            self.named_objects = {}
            entry_points = metadata.entry_points()
            if hasattr(entry_points, "select"):
                candidates = entry_points.select(group='mastd_plugin')
            else:
                candidates = entry_points.get('mastd_plugin', [])
            for ep in candidates:
                try:
                    self.named_objects.update({ep.name: ep.load()})
                except Exception:
                    logger.exception(
                        "An unhandled exception occurred during execution.")
                    pass
            logger.info(
                "Collected plugins {}".format(
                    str(list(self.named_objects.keys()))))

        waiting_message = (
            "Waiting for mastd (pid %d) to stop. It stops when its running "
            "jobs and scheduled tasks end. Press Ctrl-C to stop it now: "
            "its running jobs then become lost.")
        stop_now_message = "Stopping mastd now."

        def __init__(self, *args, **kwargs):
            super(MASTd, self).__init__(*args, **kwargs)
            self._stopping = threading.Event()
            self._abort = threading.Event()

        def request_stop(self):
            """
            _method_: `mast.daemon.MASTd.request_stop(self)`

            Called on SIGTERM and SIGINT. The first time, `mastd` takes
            no more jobs and starts no more scheduled tasks, and exits
            when the running ones end. The second time, it exits now:
            its running jobs become lost.
            """
            if self._stopping.is_set():
                self._abort.set()
            else:
                self._stopping.set()

        @logged("mast.daemon")
        def run(self):
            """
            _method_: `mast.daemon.MASTd.run(self)`

            This method will be called when mastd has successfully been
            spawned and forked. It starts the plugins, and each minute
            starts again the ones that ended. When `mastd` is told to
            stop, it stops the plugins and waits for them, see
            `mast._daemon.plugins`.
            """
            logger = make_logger("mast.daemon")
            os.chdir(mast_home)
            try:
                if not hasattr(self, "named_objects"):
                    self.get_plugins()
                host = PluginHost(self.named_objects, logger)
                while not self._stopping.is_set():
                    host.start_dead()
                    self._stopping.wait(60)
                logger.info(
                    "mastd is stopping. It waits for the running jobs and "
                    "scheduled tasks to end. Stop it again to stop it now.")
                if host.stop(abort=self._abort):
                    logger.info("mastd stopped")
                else:
                    logger.warning("mastd stopped without waiting for "
                                   "all of its plugins")
            except Exception:
                logger.exception(
                    "An unhandled exception occurred during execution")
                raise

        def status(self):
            """
            _method_: `mast.daemon.MASTd.status(self)`

            Print whether `mastd` runs. Return 0 if it runs, 1 if it
            does not run but its pid file remains, 3 if it does not
            run.
            """
            return super(MASTd, self).status()

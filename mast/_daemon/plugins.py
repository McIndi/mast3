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
_module_: `mast._daemon.plugins`

Starts the `mastd_plugin`s of `mastd`, starts again the ones that
died, and stops them all when `mastd` stops.

A plugin is a `threading.Thread`. It can also have these:

* `stop()`: take no more work, and end the thread when the running
work ends. `mastd` waits for the thread to end.
* `abort()`: end the thread now. `mastd` calls it when it is told to
stop a second time.
* `stop_last = True`: stop this plugin only after the others have
ended. The web GUI uses this, so that it shows the jobs that `mastd`
waits for.
"""
import threading
import time

from mast.logging import make_logger

__all__ = ["PluginHost"]

# How long to wait for the plugins after abort() before mastd exits
ABORT_SECONDS = 5.0


class PluginHost(object):
    """
    _class_: `mast._daemon.plugins.PluginHost(plugins, logger=None)`

    Runs the plugins of `mastd`.

    Parameters:

    * `plugins`: A dict of the name of each plugin to its class.
    * `logger`: The logger. The default is the log `mast.daemon`.
    """
    def __init__(self, plugins, logger=None):
        self.plugins = dict(plugins)
        self.threads = {}
        self.logger = logger or make_logger("mast.daemon")

    def start_dead(self):
        """
        _method_: `mast._daemon.plugins.PluginHost.start_dead(self)`

        Start each plugin that does not run: the ones that never
        started, and the ones whose thread ended.
        """
        for name, cls in self.plugins.items():
            thread = self.threads.get(name)
            if thread is not None and thread.is_alive():
                continue
            if thread is None:
                self.logger.info("Starting the plugin {}".format(name))
            else:
                self.logger.warning(
                    "The plugin {} ended. Starting it again".format(name))
            try:
                thread = cls()
                thread.start()
            except Exception:
                self.logger.exception(
                    "The plugin {} could not be started".format(name))
                continue
            self.threads[name] = thread

    def _alive(self, last):
        return [
            (name, thread) for name, thread in self.threads.items()
            if thread.is_alive() and hasattr(thread, "stop")
            and bool(getattr(thread, "stop_last", False)) == last]

    def _call(self, threads, method):
        for name, thread in threads:
            if not hasattr(thread, method):
                continue
            try:
                getattr(thread, method)()
            except Exception:
                self.logger.exception(
                    "The plugin {} could not {}".format(name, method))

    def stop(self, abort=None, heartbeat=None, report_seconds=60.0):
        """
        _method_: `mast._daemon.plugins.PluginHost.stop(self, abort=None,
        heartbeat=None, report_seconds=60.0)`

        Stop the plugins, and return when the ones that have `stop()`
        have ended: first the others, then the ones with
        `stop_last`.

        Parameters:

        * `abort`: A `threading.Event`. When it is set, call `abort()`
        on the plugins that still run, and return within a few seconds.
        * `heartbeat`: A function called about once a second while
        this waits. The Windows service uses it to tell Windows that
        it is still stopping.
        * `report_seconds`: How often to log what this waits for.

        Return True if the plugins ended, False if this stopped
        waiting for some of them after `abort`.
        """
        abort = abort or threading.Event()
        for last in (False, True):
            self._call(self._alive(last), "stop")
            if not self._wait(last, abort, heartbeat, report_seconds):
                return False
        return True

    def _wait(self, last, abort, heartbeat, report_seconds):
        aborted_at = None
        next_report = time.monotonic()
        next_beat = time.monotonic()
        while True:
            alive = self._alive(last)
            if not alive:
                return True
            now = time.monotonic()
            if abort.is_set():
                if aborted_at is None:
                    aborted_at = now
                    self.logger.warning(
                        "Stopping now: {}".format(
                            ", ".join(name for name, _ in alive)))
                    self._call(alive, "abort")
                elif now - aborted_at > ABORT_SECONDS:
                    self.logger.error(
                        "These plugins did not end: {}".format(
                            ", ".join(name for name, _ in alive)))
                    return False
            elif now >= next_report:
                self.logger.info(
                    "Waiting for these plugins to end: {}".format(
                        ", ".join(name for name, _ in alive)))
                next_report = now + report_seconds
            if heartbeat is not None and now >= next_beat:
                heartbeat()
                next_beat = now + 1.0
            alive[0][1].join(0.1)

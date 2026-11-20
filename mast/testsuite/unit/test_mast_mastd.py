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
Tests for `mastd`: the scheduler (`mast.cron`), how `mastd` starts and
stops its plugins (`mast._daemon.plugins`), and the daemon on Linux and
macOS (`mast._daemon.daemon`).
"""
import calendar
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from unittest import mock

from mast import cron
from mast._daemon import daemon as daemon_module
from mast._daemon.plugins import PluginHost

POSIX = os.name == "posix"


def minute_of(year, month, day, hour, minute):
    """Minutes since 1970 of a UTC time."""
    return calendar.timegm((year, month, day, hour, minute, 0)) // 60


def python(code):
    """A shell command that runs Python code, on Linux and on Windows."""
    return '"{}" -c "{}"'.format(sys.executable, code)


WAIT = """\
import os, sys, time
deadline = time.time() + 20
while not os.path.exists(sys.argv[1]) and time.time() < deadline:
    time.sleep(0.02)
"""


class CronTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mast-cron-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.crontab = os.path.join(self.tmp, "crontab")
        self.plugin = cron.Plugin(crontab=self.crontab)
        self.plugin.logger = mock.Mock()
        self.addCleanup(self.wait_for_tasks)
        # A task that runs until the file gate exists
        self.gate = os.path.join(self.tmp, "gate")
        self.addCleanup(lambda: open(self.gate, "w").close())
        script = os.path.join(self.tmp, "wait.py")
        with open(script, "w") as fout:
            fout.write(WAIT)
        self.wait = '"{}" "{}" "{}"'.format(sys.executable, script, self.gate)

    def wait_for_tasks(self, timeout=20.0):
        deadline = time.monotonic() + timeout
        while self.plugin.running():
            if time.monotonic() > deadline:
                self.fail("the tasks did not end: {}".format(
                    self.plugin.running()))
            time.sleep(0.02)
        for thread in list(self.plugin._running.values()):
            thread.join(timeout)

    def write(self, text):
        with open(self.crontab, "w") as fout:
            fout.write(textwrap.dedent(text))

    def logged(self, level):
        return [c.args[0] for c in getattr(self.plugin.logger, level).call_args_list]


class TestScheduler(CronTestCase):
    def test_a_due_task_runs_and_its_output_is_logged(self):
        self.write("5 3 * * * {}\n".format(python("print('hello there')")))
        started = self.plugin.check(minute_of(2026, 11, 20, 3, 5))
        self.assertEqual(len(started), 1)
        self.wait_for_tasks()
        succeeded = [m for m in self.logged("info") if "succeeded" in m]
        self.assertEqual(len(succeeded), 1, self.logged("info"))
        self.assertIn("hello there", succeeded[0])
        self.assertNotIn("b'", succeeded[0])
        self.assertEqual(self.logged("exception"), [])

    def test_a_task_that_is_not_due_does_not_run(self):
        self.write("5 3 * * * {}\n".format(python("print(1)")))
        self.assertEqual(self.plugin.check(minute_of(2026, 11, 20, 3, 6)), [])

    def test_the_times_are_utc(self):
        self.write("0 2 * * * {}\n".format(python("print(1)")))
        self.assertEqual(
            len(self.plugin.check(minute_of(2026, 11, 20, 2, 0))), 1)

    def test_a_failed_task_logs_its_exit_status_and_output(self):
        self.write("* * * * * {}\n".format(
            python("import sys; print('it broke'); sys.exit(3)")))
        self.plugin.check(minute_of(2026, 11, 20, 3, 5))
        self.wait_for_tasks()
        errors = self.logged("error")
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("exit status 3", errors[0])
        self.assertIn("it broke", errors[0])

    def test_empty_lines_and_comments_are_ignored(self):
        self.write("""
            # The nightly backup

            5 3 * * * {}
            """.format(python("print(1)")))
        self.assertEqual(
            len(self.plugin.check(minute_of(2026, 11, 20, 3, 5))), 1)
        self.assertEqual(self.logged("error"), [])

    def test_a_bad_line_is_logged_once_and_the_others_run(self):
        self.write("""
            61 * * * * {0}
            not a task
            * * * * *
            * * * * * {0}
            """.format(python("print(1)")))
        self.assertEqual(
            len(self.plugin.check(minute_of(2026, 11, 20, 3, 5))), 1)
        self.wait_for_tasks()
        errors = self.logged("error")
        self.assertEqual(len(errors), 3, errors)
        self.assertIn("Line 2 ", errors[0])
        self.assertIn("Line 4 ", errors[2])
        self.assertIn("no command", errors[2])
        self.plugin.check(minute_of(2026, 11, 20, 3, 6))
        self.assertEqual(len(self.logged("error")), 3)

    def test_a_change_to_the_crontab_is_read_at_the_next_minute(self):
        self.write("* * * * * {}\n".format(python("print(1)")))
        self.plugin.check(minute_of(2026, 11, 20, 3, 5))
        self.write("")
        self.assertEqual(self.plugin.check(minute_of(2026, 11, 20, 3, 6)), [])

    def test_no_crontab_is_logged_once(self):
        self.plugin.check(minute_of(2026, 11, 20, 3, 5))
        self.plugin.check(minute_of(2026, 11, 20, 3, 6))
        self.assertEqual(
            len([m for m in self.logged("info") if "no crontab" in m]), 1)

    def test_a_minute_is_checked_only_once(self):
        self.write("* * * * * {}\n".format(python("print(1)")))
        minute = minute_of(2026, 11, 20, 3, 5)
        self.assertEqual(len(self.plugin.check(minute)), 1)
        self.wait_for_tasks()
        self.assertEqual(self.plugin.check(minute), [])

    def test_when_the_clock_goes_back_no_minute_is_run_again(self):
        self.write("* * * * * {}\n".format(python("print(1)")))
        self.plugin.check(minute_of(2026, 11, 20, 3, 5))
        self.wait_for_tasks()
        self.assertEqual(
            self.plugin.check(minute_of(2026, 11, 20, 3, 2)), [])
        self.assertEqual(
            self.plugin.check(minute_of(2026, 11, 20, 3, 5)), [])
        self.assertEqual(
            len(self.plugin.check(minute_of(2026, 11, 20, 3, 6))), 1)

    def test_a_missed_minute_is_caught_up(self):
        self.write("5 3 * * * {}\n".format(python("print(1)")))
        self.plugin.check(minute_of(2026, 11, 20, 3, 4))
        self.assertEqual(
            len(self.plugin.check(minute_of(2026, 11, 20, 3, 7))), 1)
        self.assertTrue(any("late" in m for m in self.logged("warning")))

    def test_a_task_due_in_several_missed_minutes_runs_once(self):
        self.write("* * * * * {}\n".format(python("print(1)")))
        self.plugin.check(minute_of(2026, 11, 20, 3, 4))
        self.wait_for_tasks()
        self.assertEqual(
            len(self.plugin.check(minute_of(2026, 11, 20, 3, 9))), 1)

    def test_a_long_gap_is_not_caught_up(self):
        self.write("5 3 * * * {}\n".format(python("print(1)")))
        self.plugin.check(minute_of(2026, 11, 20, 1, 0))
        self.assertEqual(self.plugin.check(minute_of(2026, 11, 20, 3, 6)), [])
        self.assertTrue(
            any("were not started" in m for m in self.logged("warning")))

    def test_tasks_run_at_the_same_time(self):
        wait = self.wait
        quick = python("print('quick')")
        self.write("* * * * * {}\n* * * * * {}\n".format(wait, quick))
        self.assertEqual(
            len(self.plugin.check(minute_of(2026, 11, 20, 3, 5))), 2)
        deadline = time.monotonic() + 20
        while self.plugin.running() != [wait]:
            self.assertLess(time.monotonic(), deadline,
                            "the quick task waited for the slow one")
            time.sleep(0.02)
        self.assertTrue(any("quick" in m for m in self.logged("info")))

    def test_a_task_that_still_runs_is_not_started_again(self):
        gate, wait = self.gate, self.wait
        self.write("* * * * * {}\n".format(wait))
        self.assertEqual(
            len(self.plugin.check(minute_of(2026, 11, 20, 3, 5))), 1)
        self.assertEqual(self.plugin.check(minute_of(2026, 11, 20, 3, 6)), [])
        self.assertTrue(
            any("still runs" in m for m in self.logged("warning")))
        open(gate, "w").close()
        self.wait_for_tasks()
        self.assertEqual(
            len(self.plugin.check(minute_of(2026, 11, 20, 3, 7))), 1)


class TestSchedulerThread(CronTestCase):
    def test_it_wakes_at_the_start_of_the_next_minute(self):
        plugin = cron.Plugin(crontab=self.crontab,
                             clock=lambda: 1000 * 60 + 59.5)
        plugin.logger = mock.Mock()
        plugin._stopping = mock.Mock()
        plugin._stopping.is_set.side_effect = [False, True]
        plugin.run()
        (delay,), _ = plugin._stopping.wait.call_args
        self.assertAlmostEqual(delay, 0.55, places=6)

    def test_stop_ends_the_thread(self):
        self.plugin.start()
        self.plugin.stop()
        self.plugin.join(5)
        self.assertFalse(self.plugin.is_alive())

    def test_stop_waits_for_the_running_tasks_and_abort_does_not(self):
        gate, wait = self.gate, self.wait
        self.write("* * * * * {}\n".format(wait))
        self.plugin.start()
        deadline = time.monotonic() + 10
        while not self.plugin.running():
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.02)
        self.plugin.stop()
        self.plugin.join(0.5)
        self.assertTrue(self.plugin.is_alive())
        self.plugin.abort()
        self.plugin.join(5)
        self.assertFalse(self.plugin.is_alive())


class FakePlugin(threading.Thread):
    """A plugin that ends when `release` is set after `stop()`."""
    events = []

    def __init__(self):
        super(FakePlugin, self).__init__(daemon=True)
        self.stopped = threading.Event()
        self.release = threading.Event()
        self.aborted = False

    def stop(self):
        self.events.append(("stop", type(self).__name__))
        self.stopped.set()

    def abort(self):
        self.aborted = True
        self.release.set()

    def run(self):
        self.stopped.wait()
        self.release.wait(20)
        self.events.append(("ended", type(self).__name__))


class Worker(FakePlugin):
    pass


class Web(FakePlugin):
    stop_last = True

    def __init__(self):
        super(Web, self).__init__()
        self.release.set()


class Forever(threading.Thread):
    """A plugin without stop(): mastd does not wait for it."""
    def __init__(self):
        super(Forever, self).__init__(daemon=True)

    def run(self):
        threading.Event().wait()


class TestPluginHost(unittest.TestCase):
    def setUp(self):
        FakePlugin.events = []
        self.host = PluginHost(
            {"worker": Worker, "web": Web, "forever": Forever},
            logger=mock.Mock())
        self.host.start_dead()

    def stop_in_thread(self, **kwargs):
        result = {}
        thread = threading.Thread(
            target=lambda: result.update(ended=self.host.stop(**kwargs)),
            daemon=True)
        thread.start()
        return thread, result

    def test_stop_waits_for_the_plugins_then_stops_the_web_gui(self):
        thread, result = self.stop_in_thread()
        thread.join(0.5)
        self.assertTrue(thread.is_alive())
        self.assertEqual(FakePlugin.events, [("stop", "Worker")])
        self.host.threads["worker"].release.set()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertTrue(result["ended"])
        self.assertEqual(FakePlugin.events, [
            ("stop", "Worker"), ("ended", "Worker"),
            ("stop", "Web"), ("ended", "Web")])

    def test_abort_stops_the_plugins_now(self):
        abort = threading.Event()
        thread, result = self.stop_in_thread(abort=abort)
        thread.join(0.3)
        self.assertTrue(thread.is_alive())
        abort.set()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertTrue(self.host.threads["worker"].aborted)

    def test_abort_gives_up_on_a_plugin_that_does_not_end(self):
        worker = self.host.threads["worker"]
        worker.abort = lambda: None
        abort = threading.Event()
        abort.set()
        with mock.patch("mast._daemon.plugins.ABORT_SECONDS", 0.2):
            self.assertFalse(self.host.stop(abort=abort))
        worker.release.set()

    def test_heartbeat_is_called_while_it_waits(self):
        heartbeat = mock.Mock()
        thread, _ = self.stop_in_thread(heartbeat=heartbeat)
        thread.join(0.3)
        self.host.threads["worker"].release.set()
        thread.join(5)
        self.assertTrue(heartbeat.called)

    def test_a_plugin_that_ended_is_started_again(self):
        first = self.host.threads["worker"]
        first.stop()
        first.release.set()
        first.join(5)
        self.host.start_dead()
        self.assertIsNot(self.host.threads["worker"], first)
        self.assertTrue(self.host.threads["worker"].is_alive())
        self.host.threads["worker"].abort()

    def test_a_plugin_that_cannot_start_is_logged(self):
        def broken():
            raise RuntimeError("no")
        host = PluginHost({"broken": broken}, logger=mock.Mock())
        host.start_dead()
        self.assertTrue(host.logger.exception.called)
        self.assertEqual(host.threads, {})

    def tearDown(self):
        for thread in self.host.threads.values():
            if isinstance(thread, FakePlugin):
                thread.stopped.set()
                thread.release.set()


class TestWorkerPlugin(unittest.TestCase):
    def test_abort_aborts_the_worker(self):
        from mast.jobs import worker
        with mock.patch.object(worker.config, "mastd_worker",
                               return_value=True), \
                mock.patch.object(worker, "Worker") as Worker_:
            plugin = worker.Plugin()
        plugin.abort()
        Worker_.return_value.abort.assert_called_once_with()


@unittest.skipUnless(POSIX, "the daemon is for Linux and macOS")
class TestDaemon(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mast-daemon-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.pidfile = os.path.join(self.tmp, "mastd.pid")
        self.daemon = daemon_module.Daemon(self.pidfile, verbose=0)

    def write_pid(self, pid):
        with open(self.pidfile, "w") as fout:
            fout.write("{}\n".format(pid))

    def dead_pid(self):
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        return proc.pid

    def test_status(self):
        with mock.patch("sys.stdout"):
            self.assertEqual(self.daemon.status(), 3)
            self.write_pid(os.getpid())
            self.assertEqual(self.daemon.status(), 0)
            self.write_pid(self.dead_pid())
            self.assertEqual(self.daemon.status(), 1)

    def test_start_refuses_when_it_runs(self):
        self.write_pid(os.getpid())
        with mock.patch.object(self.daemon, "daemonize") as daemonize, \
                mock.patch("sys.stderr"):
            with self.assertRaises(SystemExit):
                self.daemon.start()
        daemonize.assert_not_called()

    def test_start_removes_a_stale_pidfile(self):
        self.write_pid(self.dead_pid())
        with mock.patch.object(self.daemon, "daemonize"), \
                mock.patch.object(self.daemon, "run") as run, \
                mock.patch("sys.stderr"):
            self.daemon.start()
        run.assert_called_once_with()
        self.assertFalse(os.path.exists(self.pidfile))

    def test_stop_sends_sigterm_once_and_waits(self):
        counted = os.path.join(self.tmp, "signals")
        ready = os.path.join(self.tmp, "ready")
        code = textwrap.dedent("""
            import signal, sys, time
            count = []
            def on_term(signum, frame):
                count.append(signum)
            signal.signal(signal.SIGTERM, on_term)
            open(sys.argv[2], "w").close()
            while not count:
                time.sleep(0.01)
            time.sleep(1)
            with open(sys.argv[1], "w") as fout:
                fout.write(str(len(count)))
            """)
        proc = subprocess.Popen([sys.executable, "-c", code, counted, ready])
        self.addCleanup(proc.kill)
        while not os.path.exists(ready):
            time.sleep(0.01)
        # Reap it, so that it does not stay a zombie
        threading.Thread(target=proc.wait, daemon=True).start()
        self.write_pid(proc.pid)
        started = time.monotonic()
        with mock.patch("sys.stdout"):
            self.daemon.stop(poll_seconds=0.05)
        self.assertGreater(time.monotonic() - started, 0.9)
        self.assertIsNotNone(proc.poll())
        with open(counted) as fin:
            self.assertEqual(fin.read(), "1")
        self.assertFalse(os.path.exists(self.pidfile))

    def test_ctrl_c_while_stop_waits_sends_sigterm_again(self):
        self.write_pid(12345)
        with mock.patch.object(daemon_module, "alive", return_value=True), \
                mock.patch.object(daemon_module, "wait_gone",
                                  side_effect=[KeyboardInterrupt, None]), \
                mock.patch.object(daemon_module.os, "kill") as kill, \
                mock.patch("sys.stdout"):
            self.daemon.stop()
        self.assertEqual(kill.call_args_list, [
            mock.call(12345, signal.SIGTERM),
            mock.call(12345, signal.SIGTERM)])

    def test_stop_when_it_does_not_run(self):
        self.write_pid(self.dead_pid())
        with mock.patch("sys.stderr"):
            self.daemon.stop()
        self.assertFalse(os.path.exists(self.pidfile))


@unittest.skipUnless(POSIX, "the daemon is for Linux and macOS")
class TestMASTd(unittest.TestCase):
    def setUp(self):
        FakePlugin.events = []
        from mast._daemon.mast_daemon import MASTd
        self.tmp = tempfile.mkdtemp(prefix="mast-daemon-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.mastd = MASTd(os.path.join(self.tmp, "mastd.pid"), verbose=0)
        self.mastd.named_objects = {"worker": Worker, "web": Web}

    def run_mastd(self):
        chdir = mock.patch("mast._daemon.mast_daemon.os.chdir")
        chdir.start()
        self.addCleanup(chdir.stop)
        thread = threading.Thread(target=self.mastd.run, daemon=True)
        thread.start()
        deadline = time.monotonic() + 5
        while len(FakePlugin.events) == 0 and not self.started():
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.01)
        return thread

    def started(self):
        return any(t.is_alive() for t in threading.enumerate()
                   if isinstance(t, Worker))

    def test_the_first_signal_waits_for_the_running_work(self):
        thread = self.run_mastd()
        self.mastd.request_stop()
        thread.join(0.5)
        self.assertTrue(thread.is_alive())
        for t in threading.enumerate():
            if isinstance(t, Worker):
                t.release.set()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(FakePlugin.events[-1], ("ended", "Web"))

    def test_the_second_signal_stops_it_now(self):
        thread = self.run_mastd()
        self.mastd.request_stop()
        thread.join(0.3)
        self.mastd.request_stop()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertTrue(any(
            t.aborted for t in threading.enumerate()
            if isinstance(t, Worker)) or ("ended", "Worker") in
            FakePlugin.events)

    def test_status_does_not_raise(self):
        with mock.patch("sys.stdout"):
            self.assertEqual(self.mastd.status(), 3)


if __name__ == "__main__":
    unittest.main()

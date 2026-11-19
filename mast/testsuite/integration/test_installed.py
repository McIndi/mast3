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
Tests of the installed product: its launchers, the exit statuses of its
commands, cancelled jobs and workers that stop.

Set MAST_TEST_HOME to the home under test: an unzipped installer, or a
development home (`./mast-setup --development-mode`). The tests run a
private copy of it, in a folder whose name has a space, and start every
program through its launcher, as a user does. They need no DataPower:
the appliances are `FakeSoma` servers, which answer after a delay that
each test sets, so that a test can cancel a job or stop a worker while
the job runs.

The appliances of the environment `lab` are asked one after another,
each one `SLOW` seconds. The job queue has its default settings: a
lease of 60 seconds, so the heartbeats are 20 seconds apart, and a look
for a cancel every second. A cancelled job must stop before it asks the
second appliance, so it cannot wait for a heartbeat.

POSIX only: the Windows launchers are batch files, and Windows has no
SIGINT or SIGTERM to send to one process. See manual-tests.md.
"""
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request

from mast.testsuite.integration.fake_soma import DOMAINS, FakeSoma
from mast.testsuite.integration.homes import TEST_HOME, copy_home

# The launchers of the commands, and the name each one gives in its help
COMMANDS = (
    "mast-accounts", "mast-backups", "mast-crypto", "mast-deployment",
    "mast-developer", "mast-network", "mast-ssh", "mast-system",
    "mast-jobs", "mast-worker", "test-mast",
)

CREDENTIALS = "admin:admin"
# Long enough that a cancel is noticed (within cancel_poll_seconds, one
# second) before the job asks the second appliance.
SLOW = 4.0
TIMEOUT = 120


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@unittest.skipUnless(TEST_HOME, "MAST_TEST_HOME is not set")
@unittest.skipIf(sys.platform == "win32", "the POSIX launchers")
class InstalledTestCase(unittest.TestCase):
    """A private copy of MAST_TEST_HOME, with the environment `lab` of
    three fake appliances, and `down`, an appliance that refuses
    connections."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp(prefix="mast-installed-")
        cls.home = os.path.join(cls._tmp, "mast home")
        copy_home(os.path.abspath(TEST_HOME), cls.home)
        cls.fakes = [FakeSoma().__enter__() for _ in range(3)]
        names = ["dp{}".format(n) for n in range(1, 4)]
        hosts = ["[hosts]"] + ["{}: 127.0.0.1".format(n) for n in names + ["down"]]
        appliances = []
        for name, fake in zip(names, cls.fakes):
            appliances += ["[{}]".format(name), "soma_port: {}".format(fake.port)]
        appliances += ["[down]", "soma_port: {}".format(_free_port())]
        cls.write("hosts.conf", hosts)
        cls.write("appliances.conf", appliances)
        cls.write("environments.conf", ["[lab]", "appliances: " + " ".join(names)])
        cls.write("jobs.conf", ["[worker]", "poll_seconds = 1"])
        cls.env = {k: v for k, v in os.environ.items() if k != "MAST_HOME"}

    @classmethod
    def tearDownClass(cls):
        for fake in cls.fakes:
            fake.__exit__(None, None, None)
        shutil.rmtree(cls._tmp, ignore_errors=True)

    @classmethod
    def write(cls, name, lines):
        with open(os.path.join(cls.home, "etc", "local", name), "w") as fout:
            fout.write("\n".join(lines) + "\n")

    def setUp(self):
        self.slow(0)

    def slow(self, seconds):
        for fake in self.fakes:
            fake.delay = seconds

    def command(self, launcher, *args):
        return [os.path.join(self.home, launcher)] + list(args)

    def run_mast(self, launcher, *args, timeout=TIMEOUT):
        """Run a launcher from another directory, without MAST_HOME."""
        return subprocess.run(
            self.command(launcher, *args), cwd=self._tmp, env=self.env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            timeout=timeout)

    def start(self, launcher, *args):
        """Start a launcher in a process group of its own. The tests
        signal only its process id, as `kill` or systemd do. Cleanup
        kills the whole group, so that a launcher which did not pass a
        signal on leaves no process behind."""
        proc = subprocess.Popen(
            self.command(launcher, *args), cwd=self._tmp, env=self.env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            start_new_session=True)
        self.addCleanup(self._stop, proc)
        return proc

    @staticmethod
    def _stop(proc):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.communicate()

    def communicate(self, proc, timeout):
        """Wait for `proc` to end and return its output. Fail if it does
        not end in time."""
        try:
            return proc.communicate(timeout=timeout)[0]
        except subprocess.TimeoutExpired:
            self.fail("{} did not stop within {} seconds".format(
                os.path.basename(proc.args[0]), timeout))

    def list_domains(self, *args, environment="lab"):
        return ("mast-system", "list-domains", "-a", environment,
                "-c", CREDENTIALS, "-n", "-t", "30") + args

    def newest_job(self):
        out = self.run_mast("mast-jobs", "list", "-n", "1").stdout
        rows = [line.split() for line in out.splitlines()[2:] if line.strip()]
        return rows[0][0] if rows else None

    def show(self, job_id):
        """The fields of `mast-jobs show`, as a dict."""
        out = self.run_mast("mast-jobs", "show", "-j", job_id).stdout
        return dict(re.findall(r"^(\w+):\s+(.*?)\s*$", out, re.M))

    def status(self, job_id):
        return self.show(job_id).get("Status", "").split()[0]

    def wait_until(self, condition, what, timeout=60):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = condition()
            if value:
                return value
            time.sleep(0.2)
        self.fail("timed out waiting for {}".format(what))

    def wait_running(self, since):
        """Wait until a job newer than `since` runs. Return its id."""
        def running():
            job_id = self.newest_job()
            if job_id and job_id != since and self.status(job_id) == "running":
                return job_id
        return self.wait_until(running, "a job that runs")

    def requests(self):
        return [fake.requests for fake in self.fakes]

    def reset_requests(self):
        for fake in self.fakes:
            fake.requests = 0


class TestLaunchers(InstalledTestCase):

    def test_each_command_runs_from_another_directory(self):
        for launcher in COMMANDS:
            with self.subTest(launcher=launcher):
                proc = self.run_mast(launcher, "-h")
                self.assertEqual(proc.returncode, 0, proc.stdout)
                self.assertIn("usage: {}".format(launcher), proc.stdout)

    def test_mast_version(self):
        proc = self.run_mast("mast-version")
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertRegex(proc.stdout, r"Version \d+\.\d+\.\d+")

    def test_mast_runs_the_python_of_the_home_in_the_home(self):
        proc = self.run_mast(
            "mast", "-c", "import os, sys; print(sys.prefix); print(os.getcwd()); "
                          "print(os.environ['MAST_HOME'])")
        self.assertEqual(proc.returncode, 0, proc.stdout)
        prefix, cwd, mast_home = proc.stdout.strip().splitlines()[-3:]
        home = os.path.realpath(self.home)
        self.assertTrue(os.path.realpath(prefix).startswith(
            os.path.realpath(os.path.join(TEST_HOME, "python"))), prefix)
        self.assertEqual(os.path.realpath(cwd), home)
        # An absolute path, also when the launcher is started as ./name
        self.assertTrue(os.path.isabs(mast_home), mast_home)
        self.assertEqual(os.path.realpath(mast_home), home)

    def test_a_launcher_becomes_the_python_process(self):
        # The launcher replaces itself with Python (exec), so a signal
        # sent to its process id reaches MAST, as `kill`, systemd and
        # `docker stop` send it.
        proc = self.start("mast", "-c", "import os, time; print(os.getpid(), flush=True); "
                                        "time.sleep(60)")
        self.assertEqual(int(proc.stdout.readline()), proc.pid)

    def test_the_web_gui_answers_and_stops_on_sigterm(self):
        port = _free_port()
        self.write("server.conf", ["[server]", "secure = false",
                                   "port = {}".format(port)])
        self.addCleanup(os.remove, os.path.join(self.home, "etc", "local", "server.conf"))
        proc = self.start("mast-web")

        def answers():
            try:
                with urllib.request.urlopen(
                        "http://127.0.0.1:{}/".format(port), timeout=5) as resp:
                    return resp.status
            except OSError:
                return None
        self.assertEqual(self.wait_until(answers, "the web GUI"), 200)
        proc.send_signal(signal.SIGTERM)
        self.communicate(proc, 30)
        self.assertFalse(answers())


class TestExitStatus(InstalledTestCase):

    def test_a_command_that_succeeds_exits_0(self):
        proc = self.run_mast(*self.list_domains())
        self.assertEqual(proc.returncode, 0, proc.stdout)
        for name in DOMAINS:
            self.assertIn(name, proc.stdout)
        self.assertEqual(self.status(self.newest_job()), "succeeded")

    def test_an_appliance_that_cannot_be_reached_exits_1(self):
        proc = self.run_mast(*self.list_domains(environment="down"))
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertEqual(self.status(self.newest_job()), "failed")

    def test_a_bad_option_exits_2_and_makes_no_job(self):
        before = self.newest_job()
        proc = self.run_mast("mast-system", "list-domains", "-a", "lab",
                             "-c", CREDENTIALS, "-t", "not-a-number")
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertIn("--timeout", proc.stdout)
        self.assertEqual(self.newest_job(), before)

    def test_mast_jobs_exits_1_for_a_job_that_is_not_there(self):
        proc = self.run_mast("mast-jobs", "show", "-j", "0" * 32)
        self.assertEqual(proc.returncode, 1, proc.stdout)


class TestCancel(InstalledTestCase):

    def test_ctrl_c_cancels_the_job(self):
        self.slow(SLOW)
        before = self.newest_job()
        self.reset_requests()
        proc = self.start(*self.list_domains())
        job_id = self.wait_running(before)
        proc.send_signal(signal.SIGINT)
        out = self.communicate(proc, TIMEOUT)
        self.assertEqual(proc.returncode, 130, out)
        self.assertEqual(self.status(job_id), "cancelled")
        self.assertEqual(self.requests()[1:], [0, 0], "the job went on after the cancel")

    def test_ctrl_c_twice_stops_at_once_and_the_job_is_lost(self):
        self.slow(SLOW)
        before = self.newest_job()
        proc = self.start(*self.list_domains())
        job_id = self.wait_running(before)
        proc.send_signal(signal.SIGINT)
        time.sleep(0.5)
        proc.send_signal(signal.SIGINT)
        out = self.communicate(proc, SLOW * 2)
        self.assertEqual(proc.returncode, 130, out)
        self.assertEqual(self.status(job_id), "lost")

    def test_mast_jobs_cancel_stops_a_running_job(self):
        self.slow(SLOW)
        before = self.newest_job()
        self.reset_requests()
        proc = self.start(*self.list_domains())
        job_id = self.wait_running(before)
        cancel = self.run_mast("mast-jobs", "cancel", "-j", job_id[:8])
        self.assertEqual(cancel.returncode, 0, cancel.stdout)
        out = self.communicate(proc, TIMEOUT)
        self.assertNotEqual(proc.returncode, 0, out)
        self.assertEqual(self.status(job_id), "cancelled")
        self.assertEqual(self.requests()[1:], [0, 0], "the job went on after the cancel")

    def test_a_job_that_waits_is_cancelled_at_once(self):
        # No worker runs, so a detached job waits in the queue.
        self.assertIn("No worker", self.run_mast("mast-jobs", "workers").stdout)
        proc = self.run_mast(*self.list_domains("--detach"))
        self.assertEqual(proc.returncode, 0, proc.stdout)
        job_id = proc.stdout.split()[0]
        self.assertEqual(self.status(job_id), "queued")
        cancel = self.run_mast("mast-jobs", "cancel", "-j", job_id)
        self.assertIn("cancelled", cancel.stdout)
        self.assertEqual(self.status(job_id), "cancelled")


class TestWorkers(InstalledTestCase):

    def start_worker(self, *args):
        worker = self.start("mast-worker", *args)
        first = worker.stdout.readline()
        self.assertIn("runs up to", first)
        worker_id = first.split()[1]
        self.wait_until(
            lambda: worker_id in self.run_mast("mast-jobs", "workers").stdout,
            "the worker")
        return worker, worker_id

    def detach(self):
        proc = self.run_mast(*self.list_domains("--detach"))
        self.assertEqual(proc.returncode, 0, proc.stdout)
        return proc.stdout.split()[0]

    def test_a_worker_does_a_detached_job(self):
        worker, worker_id = self.start_worker()
        job_id = self.detach()
        follow = self.run_mast("mast-jobs", "follow", "-j", job_id)
        self.assertEqual(follow.returncode, 0, follow.stdout)
        self.assertIn(DOMAINS[1], follow.stdout)
        # The worker id, and the slot of the worker that ran the job
        self.assertTrue(self.show(job_id)["Worker"].startswith(worker_id + "/"))
        worker.send_signal(signal.SIGTERM)
        self.communicate(worker, 30)
        self.assertEqual(worker.returncode, 0)

    def test_sigterm_lets_the_running_job_end(self):
        self.slow(2)
        worker, worker_id = self.start_worker()
        before = self.newest_job()
        self.reset_requests()
        self.detach()
        job_id = self.wait_running(before)
        worker.send_signal(signal.SIGTERM)
        out = self.communicate(worker, TIMEOUT)
        self.assertEqual(worker.returncode, 0, out)
        self.assertIn("stopping when 1 running job(s) end", out)
        self.assertEqual(self.status(job_id), "succeeded")
        self.assertEqual(self.requests(), [1, 1, 1])

    def test_sigterm_twice_stops_at_once_and_the_job_is_lost(self):
        self.slow(SLOW)
        worker, worker_id = self.start_worker()
        before = self.newest_job()
        self.detach()
        job_id = self.wait_running(before)
        worker.send_signal(signal.SIGTERM)
        time.sleep(0.5)
        worker.send_signal(signal.SIGTERM)
        out = self.communicate(worker, SLOW * 2)
        self.assertEqual(worker.returncode, 1, out)
        self.assertEqual(self.status(job_id), "lost")
        self.assertNotIn(worker_id, self.run_mast("mast-jobs", "workers").stdout)

    def test_drain_stops_when_the_queue_is_empty(self):
        job_id = self.detach()
        worker = self.start("mast-worker", "--drain")
        out = self.communicate(worker, TIMEOUT)
        self.assertEqual(worker.returncode, 0, out)
        self.assertEqual(self.status(job_id), "succeeded")


if __name__ == "__main__":
    unittest.main()

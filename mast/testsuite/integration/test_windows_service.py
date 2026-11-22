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
Tests of `mastd` as a Windows service, with the installed product.

They install the service `mastd` from the home in MAST_TEST_HOME (an
unzipped installer), start it, run jobs through it and stop it, as an
administrator does with `mastd.bat` and `sc.exe`. They change the
computer: they install and remove a service, and they write
`etc/local` and `etc/crontab` in the home. So they only run when
MAST_TEST_WINDOWS_SERVICE is `true`, as an administrator, on a
computer without a service named `mastd`. GitHub Actions runs them on
a Windows runner.

The appliances are `FakeSoma` servers, as in `test_installed.py`.
"""
import os
import re
import shutil
import subprocess
import sys
import time
import unittest

from mast.testsuite.integration.fake_soma import DOMAINS, FakeSoma
from mast.testsuite.integration.homes import TEST_HOME

ENABLED = os.environ.get("MAST_TEST_WINDOWS_SERVICE", "").lower() == "true"
SERVICE = "mastd"
CREDENTIALS = "admin:admin"
TIMEOUT = 120


def sc(*args):
    """Run sc.exe and return its output."""
    return subprocess.run(
        ["sc.exe"] + list(args), stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, errors="replace").stdout


def state():
    """The state of the service: RUNNING, STOPPED, START_PENDING, ...,
    or None if it is not installed."""
    match = re.search(r"STATE\s+:\s+\d+\s+(\w+)", sc("query", SERVICE))
    return match.group(1) if match else None


@unittest.skipUnless(sys.platform == "win32", "Windows only")
@unittest.skipUnless(TEST_HOME, "MAST_TEST_HOME is not set")
@unittest.skipUnless(ENABLED, "MAST_TEST_WINDOWS_SERVICE is not true")
class TestWindowsService(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.home = os.path.abspath(TEST_HOME)
        if state() is not None:
            raise unittest.SkipTest(
                "A service named {} is already installed".format(SERVICE))
        cls.fakes = [FakeSoma().__enter__() for _ in range(3)]
        names = ["dp{}".format(n) for n in range(1, 4)]
        local = os.path.join(cls.home, "etc", "local")
        os.makedirs(local, exist_ok=True)
        cls.write(local, "hosts.conf",
                  ["[hosts]"] + ["{}: 127.0.0.1".format(n) for n in names])
        appliances = []
        for name, fake in zip(names, cls.fakes):
            appliances += ["[{}]".format(name),
                           "soma_port: {}".format(fake.port)]
        cls.write(local, "appliances.conf", appliances)
        cls.write(local, "environments.conf",
                  ["[lab]", "appliances: " + " ".join(names)])
        cls.write(local, "jobs.conf", ["[worker]", "poll_seconds = 1"])
        cls.marker = os.path.join(cls.home, "tmp", "scheduled.txt")
        # Due every minute, so it runs when the service starts. It runs
        # in the home, with cmd.exe.
        cls.write(os.path.join(cls.home, "etc"), "crontab", [
            "* * * * * echo scheduled-42 && echo done> tmp\\scheduled.txt"])
        cls.env = {k: v for k, v in os.environ.items() if k != "MAST_HOME"}
        cls.cwd = os.path.dirname(cls.home)
        # MAST keeps the appliances in its database
        out = cls.run_mast("mast-appliances.bat", "import")
        if "Added the environment lab" not in out.stdout:
            raise AssertionError("mast-appliances import failed:\n"
                                 + out.stdout)

        out = cls.run_mast("mastd.bat", "install")
        if state() is None:
            raise AssertionError(
                "mastd.bat install did not install the service:\n{}".format(
                    out.stdout))
        print(out.stdout)
        print(sc("qc", SERVICE))

    @classmethod
    def tearDownClass(cls):
        if state() not in (None, "STOPPED"):
            sc("stop", SERVICE)
            cls.wait_state("STOPPED", TIMEOUT, fail=False)
        print(cls.run_mast("mastd.bat", "remove").stdout)
        for fake in getattr(cls, "fakes", []):
            fake.__exit__(None, None, None)
        os.remove(os.path.join(cls.home, "etc", "crontab"))
        cls.print_logs()

    @classmethod
    def print_logs(cls):
        """Print the logs of the service, so that a failure in GitHub
        Actions can be understood."""
        log_dir = os.path.join(cls.home, "var", "log")
        for root, _, files in os.walk(log_dir):
            for name in files:
                if not (name.startswith("mast.daemon")
                        or name.startswith("mast.cron")
                        or name.startswith("mast.jobs")):
                    continue
                path = os.path.join(root, name)
                print("==== {}".format(os.path.relpath(path, cls.home)))
                with open(path, errors="replace") as fin:
                    print(fin.read()[-20000:])

    @staticmethod
    def write(directory, name, lines):
        with open(os.path.join(directory, name), "w") as fout:
            fout.write("\n".join(lines) + "\n")

    @classmethod
    def run_mast(cls, launcher, *args, timeout=TIMEOUT):
        """Run a launcher from another directory, without MAST_HOME."""
        return subprocess.run(
            [os.path.join(cls.home, launcher)] + list(args), cwd=cls.cwd,
            env=cls.env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, errors="replace", timeout=timeout)

    @classmethod
    def wait_state(cls, wanted, timeout, fail=True):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if state() == wanted:
                return True
            time.sleep(0.5)
        if fail:
            raise AssertionError(
                "The service is {}, not {}, after {} seconds:\n{}".format(
                    state(), wanted, timeout, sc("queryex", SERVICE)))
        return False

    def wait_until(self, condition, what, timeout=TIMEOUT):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = condition()
            if value:
                return value
            time.sleep(0.5)
        self.fail("timed out waiting for {}".format(what))

    def setUp(self):
        for fake in self.fakes:
            fake.delay = 0
        if state() != "RUNNING":
            print(sc("start", SERVICE))
            self.wait_state("RUNNING", TIMEOUT)

    def slow(self, seconds):
        for fake in self.fakes:
            fake.delay = seconds

    def workers(self):
        return self.run_mast("mast-jobs.bat", "workers").stdout

    def wait_for_worker(self):
        return self.wait_until(
            lambda: "Jobs at a time" in self.workers(),
            "the worker of the service")

    def newest_job(self):
        out = self.run_mast("mast-jobs.bat", "list", "-n", "1").stdout
        rows = [line.split() for line in out.splitlines()[2:]
                if line.strip()]
        return rows[0][0] if rows else None

    def status(self, job_id):
        out = self.run_mast("mast-jobs.bat", "show", "-j", job_id).stdout
        fields = dict(re.findall(r"^(\w+):\s+(.*?)\s*$", out, re.M))
        return fields.get("Status", "").split()[0] if fields.get(
            "Status") else None

    def detach(self):
        proc = self.run_mast(
            "mast-system.bat", "list-domains", "-a", "lab", "-c",
            CREDENTIALS, "-n", "-t", "30", "--detach")
        self.assertEqual(proc.returncode, 0, proc.stdout)
        return proc.stdout.split()[0]

    def test_1_the_service_runs_a_job(self):
        self.wait_for_worker()
        job_id = self.detach()
        follow = self.run_mast("mast-jobs.bat", "follow", "-j", job_id)
        self.assertEqual(follow.returncode, 0, follow.stdout)
        self.assertIn(DOMAINS[1], follow.stdout)

    def test_2_the_scheduler_runs_a_task_and_logs_its_output(self):
        self.wait_until(lambda: os.path.exists(self.marker), "the task")
        log_dir = os.path.join(self.home, "var", "log")

        def logged():
            for root, _, files in os.walk(log_dir):
                for name in files:
                    if name.startswith("mast.cron"):
                        with open(os.path.join(root, name),
                                  errors="replace") as fin:
                            text = fin.read()
                        if "scheduled-42" in text:
                            return text
        text = self.wait_until(logged, "the output of the task in the log")
        self.assertIn("succeeded", text)

    def test_3_stop_lets_the_running_job_end(self):
        self.wait_for_worker()
        self.slow(3)
        for fake in self.fakes:
            fake.requests = 0
        before = self.newest_job()
        self.detach()
        job_id = self.wait_until(
            lambda: (self.newest_job() not in (None, before)
                     and self.status(self.newest_job()) == "running"
                     and self.newest_job()),
            "a job that runs")
        out = sc("stop", SERVICE)
        self.assertIn("STOP_PENDING", out)
        self.wait_state("STOPPED", TIMEOUT)
        self.assertEqual(self.status(job_id), "succeeded")
        self.assertEqual([fake.requests for fake in self.fakes], [1, 1, 1])
        self.assertNotIn("Jobs at a time", self.workers())

    def test_4_the_service_starts_again(self):
        # setUp started it again after test_3 stopped it
        self.wait_for_worker()
        self.assertEqual(state(), "RUNNING")


if __name__ == "__main__":
    unittest.main()

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
"""`mast selftest` runs the tests in a copy of the home, never in the
home itself: there they would use its database, its appliances and its
workers."""
import contextlib
import io
import os
import shutil
import tempfile
import unittest
from unittest import mock

from mast import home as mast_home
from mast.testsuite import selftest


def touch(root, path, text="x"):
    path = os.path.join(root, *path.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fout:
        fout.write(text)


class TestSelftest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = os.path.join(self.tmp, "home")
        for path in ("etc/default/server.conf", "etc/hosts.conf",
                     "etc/crypto/cert/mast.crt", "contrib/script.py",
                     "etc/local/credentials.key", "etc/local/server.conf",
                     "etc/crypto/private/mast.key", "var/jobs/jobs.db",
                     "var/log/mast.log", "tmp/work", "python/bin/python3",
                     "mast", "mast-python"):
            touch(self.home, path)

    def test_the_copy_has_the_configuration_and_nothing_of_use(self):
        copy = selftest.copy_home(self.home, os.path.join(self.tmp, "copy"))
        self.assertTrue(mast_home.is_home(copy))
        for path in ("etc/default/server.conf", "etc/hosts.conf",
                     "etc/crypto/cert/mast.crt", "contrib/script.py"):
            self.assertTrue(os.path.isfile(os.path.join(copy, path)), path)
        # Not the database, the logs, the local settings and keys, the
        # private key, the interpreter or the launchers
        for path in ("etc/local/credentials.key", "etc/local/server.conf",
                     "etc/crypto/private/mast.key", "var/jobs/jobs.db",
                     "var/log/mast.log", "tmp/work", "python", "mast",
                     "mast-python"):
            self.assertFalse(os.path.exists(os.path.join(copy, path)), path)
        for path in ("etc/local", "var/jobs", "var/log", "tmp"):
            self.assertEqual(os.listdir(os.path.join(copy, path)), [])

    def test_the_tests_run_in_the_copy(self):
        seen = {}

        def run(args, cwd, env):
            seen.update(args=args, cwd=cwd, env=env,
                        copied=os.path.isfile(os.path.join(
                            cwd, "etc", "default", "server.conf")))
            return mock.Mock(returncode=1)
        environ = {k: v for k, v in os.environ.items()
                   if k != selftest.ISOLATED}
        environ["MAST_HOME"] = self.home
        out = io.StringIO()
        with mock.patch.dict(os.environ, environ, clear=True), \
                mock.patch.object(selftest.subprocess, "run", run), \
                contextlib.redirect_stdout(out):
            code = selftest.program(["-u"])
        self.assertEqual(code, 1)
        self.assertEqual(seen["env"]["MAST_HOME"], seen["cwd"])
        self.assertNotEqual(seen["cwd"], self.home)
        self.assertEqual(seen["env"][selftest.ISOLATED], self.home)
        self.assertTrue(seen["copied"])
        self.assertEqual(seen["args"][1:], ["-m", "mast.testsuite.selftest",
                                            "-o", "stdout", "-u"])
        # The copy is removed when the tests end
        self.assertFalse(os.path.exists(seen["cwd"]))
        self.assertIn("in a copy of this home", out.getvalue())

    def test_no_suite_runs_nothing(self):
        out = io.StringIO()
        environ = {k: v for k, v in os.environ.items()
                   if k != selftest.ISOLATED}
        with mock.patch.dict(os.environ, environ, clear=True), \
                mock.patch.object(selftest.subprocess, "run") as run, \
                contextlib.redirect_stdout(out):
            self.assertEqual(selftest.program([]), 0)
        run.assert_not_called()
        self.assertIn("Give -u, -i, -r or -A", out.getvalue())

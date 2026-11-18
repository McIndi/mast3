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
Tests for `mast.home`: how MAST finds MAST_HOME without its launchers.
"""
import os
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import unittest
from unittest import mock

import mast.config
from mast import home


def make_home(parent):
    """Make the smallest folder that `mast.home` takes for a home."""
    path = os.path.join(parent, "mast_home")
    os.makedirs(os.path.join(path, "etc", "default"))
    open(os.path.join(path, "etc", "default", "server.conf"), "w").close()
    return path


# Run code in a new interpreter as if that interpreter were not in a
# MAST home: in an installer, the interpreter of the tests is in one.
OUTSIDE_INTERPRETER = "import sys; sys.executable = sys.argv[1]; "


def clean_environment():
    """The environment of the test without MAST_HOME."""
    env = dict(os.environ)
    env.pop("MAST_HOME", None)
    return env


class HomeTestCase(unittest.TestCase):

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="mast-home-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = make_home(self.tmp)
        self.outside = os.path.join(self.tmp, "outside")
        os.makedirs(self.outside)
        cwd = os.getcwd()
        self.addCleanup(os.chdir, cwd)


class TestFindHome(HomeTestCase):

    def test_environment_variable_wins(self):
        for cwd in (self.outside, self.home):
            with self.subTest(cwd=cwd):
                os.chdir(cwd)
                with mock.patch.dict(os.environ, {"MAST_HOME": self.home}):
                    self.assertEqual(home.find_home(), self.home)

    def test_relative_environment_variable_is_made_absolute(self):
        os.chdir(self.tmp)
        with mock.patch.dict(os.environ, {"MAST_HOME": "mast_home"}):
            self.assertEqual(home.find_home(), self.home)

    def test_found_from_a_subfolder_of_the_home(self):
        subfolder = os.path.join(self.home, "var", "log")
        os.makedirs(subfolder)
        os.chdir(subfolder)
        with mock.patch.dict(os.environ, clean_environment(), clear=True):
            self.assertEqual(home.find_home(), self.home)

    def test_found_from_the_interpreter(self):
        # The Python of an installer is in MAST_HOME/python, so its console
        # scripts work from any directory.
        os.chdir(self.outside)
        python = os.path.join(self.home, "python", "3.14.10", "bin", "python3")
        with mock.patch.dict(os.environ, clean_environment(), clear=True), \
                mock.patch.object(sys, "executable", python):
            self.assertEqual(home.find_home(), self.home)

    def test_current_directory_wins_over_the_interpreter(self):
        other = make_home(os.path.join(self.tmp, "other"))
        os.chdir(other)
        python = os.path.join(self.home, "python", "3.14.10", "bin", "python3")
        with mock.patch.dict(os.environ, clean_environment(), clear=True), \
                mock.patch.object(sys, "executable", python):
            self.assertEqual(home.find_home(), other)

    def test_not_found_outside_a_home(self):
        os.chdir(self.outside)
        python = os.path.join(self.outside, "bin", "python3")
        with mock.patch.dict(os.environ, clean_environment(), clear=True), \
                mock.patch.object(sys, "executable", python):
            with self.assertRaises(home.MastHomeNotFound) as cm:
                home.find_home()
        self.assertIn("Set MAST_HOME", str(cm.exception))
        self.assertIn("./mast-setup --development-mode", str(cm.exception))

    def test_import_fails_with_the_message_outside_a_home(self):
        # mast.config is already imported in this process, so import it
        # in a new one.
        result = subprocess.run(
            [sys.executable, "-c", OUTSIDE_INTERPRETER + "import mast.config",
             os.path.join(self.outside, "python3")],
            cwd=self.outside, env=clean_environment(),
            capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("MastHomeNotFound", result.stderr)
        self.assertIn(
            "Set MAST_HOME, run MAST from its folder with its launchers, or "
            "make a development home with ./mast-setup --development-mode.",
            result.stderr)

    def test_import_sets_the_environment_variable(self):
        # Programs that MAST starts read MAST_HOME from the environment.
        result = subprocess.run(
            [sys.executable, "-c",
             "import os, mast.config; print(os.environ['MAST_HOME'])"],
            cwd=self.home, env=clean_environment(),
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), self.home)


class TestFindFrom(HomeTestCase):

    def _touch(self, *parts):
        path = os.path.join(self.home, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w").close()
        return path

    def test_bundled_python(self):
        python = self._touch("python", "3.14.10", "python.exe")
        self.assertEqual(home.find_from(python), self.home)

    def test_windows_service_executable(self):
        # The service manager starts pythonservice.exe from pywin32.
        service = self._touch("python", "3.14.10", "Lib", "site-packages",
                              "win32", "pythonservice.exe")
        self.assertEqual(home.find_from(service), self.home)

    def test_not_found(self):
        with self.assertRaises(home.MastHomeNotFound):
            home.find_from(os.path.join(self.outside, "python.exe"))


class TestEnter(HomeTestCase):

    def test_enter_sets_the_variable_and_the_directory(self):
        os.chdir(self.outside)
        with mock.patch.dict(os.environ, clean_environment(), clear=True):
            self.assertEqual(home.enter(self.home), self.home)
            self.assertEqual(os.environ["MAST_HOME"], self.home)
        self.assertEqual(os.path.realpath(os.getcwd()), self.home)


class TestSomaSpecFile(HomeTestCase):

    @mock.patch("mast.datapower.datapower.DataPower.Request")
    def test_relative_spec_file_is_in_mast_home(self, request):
        from mast.datapower.datapower import DataPower
        os.chdir(self.outside)
        DataPower("test", "user:pass", test_case="etc/v11.0.0.3-C.xml")
        spec_file = request.call_args.args[-1]
        self.assertEqual(spec_file, os.path.join(
            mast.config.MAST_HOME, "etc", "v11.0.0.3-C.xml"))
        self.assertTrue(os.path.isfile(spec_file))

    @mock.patch("mast.datapower.datapower.DataPower.Request")
    def test_absolute_spec_file_is_unchanged(self, request):
        from mast.datapower.datapower import DataPower
        spec_file = os.path.join(self.outside, "spec.xml")
        DataPower("test", "user:pass", test_case=spec_file)
        self.assertEqual(request.call_args.args[-1], spec_file)


class TestConsoleScript(HomeTestCase):

    def test_mast_version_runs_outside_the_home(self):
        name = "mast-version.exe" if sys.platform == "win32" else "mast-version"
        script = os.path.join(sysconfig.get_path("scripts"), name)
        if not os.path.isfile(script):
            self.skipTest("the console scripts are not installed")
        env = clean_environment()
        env["MAST_HOME"] = mast.config.MAST_HOME
        result = subprocess.run(
            [script], cwd=self.outside, env=env,
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Version {}".format(mast.__version__), result.stdout)

    def test_console_script_of_an_installer_needs_no_mast_home(self):
        # In an installer or a development home, the console scripts find
        # the home from their interpreter. test-mast runs this test there.
        try:
            home.find_from(sys.executable)
        except home.MastHomeNotFound:
            self.skipTest("the interpreter of the tests is not in a MAST home")
        name = "mast-version.exe" if sys.platform == "win32" else "mast-version"
        script = os.path.join(sysconfig.get_path("scripts"), name)
        self.assertTrue(os.path.isfile(script), script)
        result = subprocess.run(
            [script], cwd=self.outside, env=clean_environment(),
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Version {}".format(mast.__version__), result.stdout)

    def test_console_script_without_a_home_exits_with_the_message(self):
        result = subprocess.run(
            [sys.executable, "-c",
             OUTSIDE_INTERPRETER + "import mast._entry; mast._entry.version()",
             os.path.join(self.outside, "python3")],
            cwd=self.outside, env=clean_environment(),
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("Set MAST_HOME", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()

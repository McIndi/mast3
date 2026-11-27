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
Checks that the installed MAST package is complete: the version, the
plugin entry points, the packages and the web docroot files.

These tests read the installed package metadata, so they run against an
installed MAST (the build output, or `pip install .`). They skip when MAST
is only on the path and not installed.

The plugin loaders in mast.daemon and mast.web.page hide every load
error. These tests load each entry point and fail with the error.
"""
import importlib
import importlib.metadata as metadata
import pkgutil
import re
import unittest
from pathlib import Path
from unittest import mock

import mast

EXPECTED_WEB_PAGES = {
    "jobs": "mast.web.pages.jobs:page",
    "commands": "mast.web.pages.commands:page",
    "admin": "mast.web.pages.admin:page",
    "appliances": "mast.datapower.status.page:page",
    "crypto": "mast.datapower.crypto.page:page",
    "terminal": "mast.datapower.ssh.page:page",
}

EXPECTED_DAEMON_PLUGINS = {
    "mastd_web_plugin": "mast.web:Plugin",
    "mastd_cron_plugin": "mast.cron:Plugin",
    "mastd_worker_plugin": "mast.jobs.worker:Plugin",
}

# Packages that must have docroot/* files (the web GUI pages).
# The plugins whose pages of the web UI have templates and static files
PAGE_PACKAGES = ("crypto", "ssh", "status")


def _installed_distribution():
    try:
        return metadata.distribution("mast")
    except metadata.PackageNotFoundError:
        return None


def _entry_points(group):
    return {
        ep.name: ep
        for ep in metadata.entry_points().select(group=group)
        if ep.dist is not None and ep.dist.metadata["Name"] == "mast"
    }


def _installed_from_site_packages():
    return "site-packages" in Path(mast.__file__).parts


class TestMASTMetadataExists(unittest.TestCase):

    def test_installed_package_has_metadata(self):
        # Without this test, a renamed project would skip every test in
        # TestMASTPackaging and look like a pass.
        if _installed_distribution() is None:
            self.assertFalse(
                _installed_from_site_packages(),
                "mast is in site-packages but has no 'mast' package metadata")


@unittest.skipIf(_installed_distribution() is None,
                 "MAST is not installed, so there is no package metadata")
class TestMASTPackaging(unittest.TestCase):

    def test_version_is_not_the_fallback(self):
        # mast/__init__.py sets "0.0.0" when the metadata is missing. Every
        # other test passes with that value, so check it here.
        self.assertNotEqual(mast.__version__, "0.0.0")
        self.assertEqual(mast.__version__, metadata.version("mast"))

    def test_version_looks_like_a_version(self):
        # From 4.0, MAST has its own versions: 4.0.0, and 4.0.0.dev0,
        # 4.0.0rc1 before a release
        self.assertRegex(
            mast.__version__, r"^\d+\.\d+\.\d+((a|b|rc)\d+)?(\.dev\d+)?$")

    def test_license_file_is_in_the_metadata(self):
        # Read the License-File field, not the list of installed files.
        # When the tests run from a source checkout that was built
        # before, Python finds `mast.egg-info` there first, and its
        # file list has `LICENSE` where an installed MAST has
        # `licenses/LICENSE`. The field is the same in both.
        self.assertEqual(
            metadata.metadata("mast").get_all("License-File"), ["LICENSE"],
            "LICENSE is not in the metadata")
        self.assertEqual(metadata.metadata("mast")["License-Expression"],
                         "GPL-3.0-only")

    def test_the_classic_plugin_group_is_gone(self):
        # The tabs of the classic GUI were removed in R-1
        self.assertEqual(_entry_points("mast_web_plugin"), {})

    def test_daemon_plugin_entry_points(self):
        found = {name: ep.value for name, ep in _entry_points("mastd_plugin").items()}
        self.assertEqual(found, EXPECTED_DAEMON_PLUGINS)

    def test_web_page_entry_points(self):
        found = {name: ep.value for name, ep in _entry_points("mast_web_page").items()}
        self.assertEqual(found, EXPECTED_WEB_PAGES)

    def test_every_entry_point_loads(self):
        for group in ("mast_web_page", "mastd_plugin"):
            for name, ep in _entry_points(group).items():
                with self.subTest(group=group, name=name):
                    # load() raises on a bad import or a missing attribute.
                    loaded = ep.load()
                    if group == "mast_web_page":
                        from mast.web.page import Page
                        self.assertIsInstance(loaded, Page)
                    else:
                        self.assertTrue(callable(loaded))

    def test_the_console_script_is_mast(self):
        # The one command, also in a development home and a pip install,
        # and the plugin of DataPower
        scripts = _entry_points("console_scripts")
        self.assertEqual(scripts["mast"].value, "mast.main:entry")
        plugins = {name: ep.value for name, ep in _entry_points("mast_plugin").items()}
        self.assertEqual(plugins, {"datapower": "mast.datapower.plugin:manifest"})

    def test_the_launchers_are_mast_and_mast_python(self):
        # The installer has two launchers: `mast` runs `python -m mast`,
        # and `mast-python` is the bundled interpreter (for contrib and
        # pip)
        root = Path(mast.__file__).resolve().parents[1] / "files" / "invocation_scripts"
        if not root.is_dir():
            self.skipTest("the launchers are only in a source checkout")
        for platform in ("linux", "darwin"):
            with self.subTest(platform=platform):
                names = sorted(p.name for p in (root / platform).iterdir())
                self.assertEqual(names, ["mast", "mast-python", "set-env"])
                self.assertRegex((root / platform / "mast").read_text(),
                                 r'bin/python3" -m mast "\$@"')
                self.assertRegex((root / platform / "mast-python").read_text(),
                                 r'bin/python3" "\$@"')
        names = sorted(p.name for p in (root / "windows").iterdir())
        self.assertEqual(names, ["mast-python.bat", "mast.bat", "set-env.bat"])

    def test_every_windows_launcher_is_safe_to_call(self):
        # Each Windows launcher calls set-env.bat and Python by their full,
        # quoted paths: cmd.exe may not look in the current folder
        # (NoDefaultCurrentDirectoryInExePath), and a home under a folder
        # with spaces must work. setlocal keeps the cd and MAST_HOME out
        # of the console of the user, and the exit status of Python is
        # passed on.
        launchers = Path(mast.__file__).resolve().parents[1] / "files" / "invocation_scripts" / "windows"
        if not launchers.is_dir():
            self.skipTest("the launchers are only in a source checkout")
        for launcher in sorted(launchers.glob("*.bat")):
            if launcher.name == "set-env.bat":
                continue
            text = launcher.read_text()
            with self.subTest(launcher=launcher.name):
                self.assertIn("\nsetlocal\n", text)
                self.assertIn('SET "MAST_HOME=%~dp0"', text)
                self.assertIn('cd /d "%MAST_HOME%"', text)
                self.assertNotRegex(text, r"(?m)^\s*(call\s+)?(mast|set-env)\.bat")
                self.assertIn('call "%MAST_HOME%set-env.bat"', text)
                self.assertIn("endlocal & exit /b %MAST_EXIT_CODE%", text)
                if launcher.name == "mast.bat":
                    self.assertIn('"%MAST_HOME%python\\%PYTHON_VERSION%\\python" -m mast %*', text)
                else:
                    self.assertIn('"%MAST_HOME%python\\%PYTHON_VERSION%\\python" %*', text)

    def test_every_subpackage_imports(self):
        # Walk the installed package and import every package and module,
        # so that a package missing from the wheel fails here.
        names = [
            m.name for m in pkgutil.walk_packages(mast.__path__, "mast.")
            # The unit tests are in the package. Do not import them here.
            if not m.name.startswith("mast.testsuite.")
            # A __main__ module is a script. Importing it runs the command
            # line parser on the arguments of this test run.
            and not m.name.endswith(".__main__")
        ]
        self.assertGreater(len(names), 30)
        for name in names:
            with self.subTest(module=name):
                importlib.import_module(name)

    def test_every_dependency_is_installed(self):
        # The installer bundles the dependencies that uv.lock lists. A
        # dependency that is declared in pyproject.toml but missing from
        # the bundle fails here, not when a user runs the command that
        # imports it. The extras of a dependency (psycopg[binary]) are
        # checked too.
        try:
            from packaging.requirements import Requirement
        except ImportError:
            self.skipTest("packaging is not installed")
        pending = [(Requirement(r), {"extra": ""})
                   for r in metadata.requires("mast") or []]
        checked = set()
        while pending:
            requirement, environment = pending.pop()
            if requirement.marker and not requirement.marker.evaluate(environment):
                continue
            key = (requirement.name.lower(), tuple(sorted(requirement.extras)))
            if key in checked:
                continue
            checked.add(key)
            with self.subTest(dependency=str(requirement)):
                try:
                    metadata.version(requirement.name)
                except metadata.PackageNotFoundError:
                    self.fail("{} is declared but not installed".format(requirement))
                for extra in requirement.extras:
                    for r in metadata.requires(requirement.name) or []:
                        pending.append((Requirement(r), {"extra": extra}))
        # The walk reached the direct dependencies and the extras.
        names = {name for name, _ in checked}
        self.assertIn("pydantic", names)
        self.assertIn("psycopg-binary", names)

    def test_expected_subpackages_are_present(self):
        names = {m.name for m in pkgutil.walk_packages(mast.__path__, "mast.")
                 if m.ispkg}
        expected = {
            "mast.cli", "mast.config", "mast.cron", "mast._daemon",
            "mast.datapower", "mast.hashes", "mast.jobs", "mast.jobs.migrations",
            "mast.jobs.migrations.versions", "mast.logging",
            "mast.plugin_utils", "mast.pprint", "mast.test", "mast.testsuite",
            "mast.testsuite.unit", "mast.testsuite.integration",
            "mast.testsuite.regression", "mast.timestamp", "mast.xor",
        } | {"mast.datapower." + n for n in (
            "accounts", "backups", "crypto", "datapower", "deployment",
            "developer", "network", "ssh", "status", "system")}
        self.assertEqual(expected - names, set(), "packages missing from the install")

    def test_the_files_of_the_pages_are_installed(self):
        for name in PAGE_PACKAGES:
            module = importlib.import_module("mast.datapower." + name)
            for folder in ("templates", "static"):
                with self.subTest(package=name, folder=folder):
                    path = Path(module.__file__).parent / folder
                    files = [p for p in path.glob("*") if p.is_file()]
                    self.assertTrue(files, "no files in {}".format(path))

    def test_no_classic_files_are_installed(self):
        for name in ("accounts", "backups", "crypto", "deployment",
                     "developer", "network", "ssh", "status", "system"):
            module = importlib.import_module("mast.datapower." + name)
            with self.subTest(package=name):
                self.assertFalse(
                    (Path(module.__file__).parent / "docroot").exists())


if __name__ == "__main__":
    unittest.main()

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

The plugin loaders in mast.daemon and mast.datapower.web.gui hide every
load error. These tests load each entry point and fail with the error.
"""
import importlib
import importlib.metadata as metadata
import pkgutil
import unittest
from pathlib import Path

import mast

EXPECTED_WEB_PLUGINS = {
    "accounts": "mast.datapower.accounts:WebPlugin",
    "backups": "mast.datapower.backups:WebPlugin",
    "crypto": "mast.datapower.crypto:WebPlugin",
    "deployment": "mast.datapower.deployment:WebPlugin",
    "developer": "mast.datapower.developer:WebPlugin",
    "network": "mast.datapower.network:WebPlugin",
    "ssh": "mast.datapower.ssh:WebPlugin",
    "status": "mast.datapower.status:WebPlugin",
    "system": "mast.datapower.system:WebPlugin",
}

EXPECTED_DAEMON_PLUGINS = {
    "mastd_web_plugin": "mast.datapower.web:Plugin",
    "mastd_cron_plugin": "mast.cron:Plugin",
}

# Packages that must have docroot/* files (the web GUI pages).
DOCROOT_PACKAGES = (
    "accounts", "backups", "crypto", "deployment", "developer",
    "network", "ssh", "status", "system",
)


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
        parts = mast.__version__.split(".")
        self.assertEqual(len(parts), 3, mast.__version__)
        for part in parts:
            self.assertTrue(part.isdigit(), mast.__version__)

    def test_license_file_is_in_the_metadata(self):
        files = [str(f) for f in metadata.files("mast")]
        self.assertTrue(
            any(f.replace("\\", "/").endswith("licenses/LICENSE") for f in files),
            "LICENSE is not in the installed metadata")
        self.assertEqual(metadata.metadata("mast")["License-Expression"],
                         "GPL-3.0-only")

    def test_web_plugin_entry_points(self):
        found = {name: ep.value for name, ep in _entry_points("mast_web_plugin").items()}
        self.assertEqual(found, EXPECTED_WEB_PLUGINS)

    def test_daemon_plugin_entry_points(self):
        found = {name: ep.value for name, ep in _entry_points("mastd_plugin").items()}
        self.assertEqual(found, EXPECTED_DAEMON_PLUGINS)

    def test_every_entry_point_loads(self):
        for group in ("mast_web_plugin", "mastd_plugin"):
            for name, ep in _entry_points(group).items():
                with self.subTest(group=group, name=name):
                    # load() raises on a bad import or a missing attribute.
                    self.assertTrue(callable(ep.load()))

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

    def test_expected_subpackages_are_present(self):
        names = {m.name for m in pkgutil.walk_packages(mast.__path__, "mast.")
                 if m.ispkg}
        expected = {
            "mast.cli", "mast.config", "mast.cron", "mast._daemon",
            "mast.datapower", "mast.hashes", "mast.logging", "mast.plugins",
            "mast.plugin_utils", "mast.pprint", "mast.test", "mast.testsuite",
            "mast.testsuite.unit", "mast.testsuite.integration",
            "mast.testsuite.regression", "mast.timestamp", "mast.xor",
        } | {"mast.datapower." + n for n in (
            "accounts", "backups", "crypto", "datapower", "deployment",
            "developer", "network", "ssh", "status", "system", "web")}
        self.assertEqual(expected - names, set(), "packages missing from the install")

    def test_docroot_files_are_installed(self):
        for name in DOCROOT_PACKAGES:
            with self.subTest(package=name):
                module = importlib.import_module("mast.datapower." + name)
                docroot = Path(module.__file__).parent / "docroot"
                files = [p for p in docroot.glob("*") if p.is_file()]
                self.assertTrue(files, "no files in {}".format(docroot))


if __name__ == "__main__":
    unittest.main()

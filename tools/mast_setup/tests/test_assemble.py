"""Tests for the layout of a home and the check before it is zipped."""
import json
import os
import stat
import sys
import unittest
from pathlib import Path

from mast_setup import assemble
from mast_setup.assemble import BundleError

from .helpers import FakeRepoTestCase, git


class TestCheckBundle(FakeRepoTestCase):

    def setUp(self):
        super().setUp()
        self.bundle = self.tmp / "bundle"
        site = self.bundle / "python" / "3.14.10" / "lib" / "python3.14" / "site-packages"
        site.mkdir(parents=True)
        self.site = site
        bin_dir = self.bundle / "python" / "3.14.10" / "bin"
        bin_dir.mkdir()
        (bin_dir / "python3.14").write_text("")
        # The interpreter has relative links inside itself.
        os.symlink("python3.14", bin_dir / "python3")
        (site / "inside.pth").write_text("# comment\nimport os\nsub\n")
        (site / "sub").mkdir()
        dist_info = site / "pip-26.3.dist-info"
        dist_info.mkdir()
        # pip in python-build-standalone was installed from a wheel file.
        (dist_info / "direct_url.json").write_text(json.dumps({
            "archive_info": {}, "url": "file:///build/pip-26.3-py3-none-any.whl"}))

    def test_clean_tree_passes(self):
        assemble.check_bundle(self.bundle)

    def assertFails(self, text):
        with self.assertRaises(BundleError) as cm:
            assemble.check_bundle(self.bundle)
        self.assertIn(text, str(cm.exception))

    @unittest.skipIf(sys.platform == "win32", "symlinks need privileges")
    def test_symlink_outside_fails(self):
        os.symlink("../../../../../../outside", self.site / "rel")
        self.assertFails("a symlink to ../../../../../../outside")

    @unittest.skipIf(sys.platform == "win32", "symlinks need privileges")
    def test_absolute_symlink_fails(self):
        os.symlink(self.repo / "files", self.bundle / "files")
        self.assertFails("a symlink to")

    def test_editable_direct_url_fails(self):
        dist_info = self.site / "mast-3.14.10.dist-info"
        dist_info.mkdir()
        (dist_info / "direct_url.json").write_text(json.dumps({
            "url": "file:///home/me/mast3", "dir_info": {"editable": True}}))
        self.assertFails("an editable install of file:///home/me/mast3")

    def test_install_from_a_folder_fails(self):
        dist_info = self.site / "mast-3.14.10.dist-info"
        dist_info.mkdir()
        (dist_info / "direct_url.json").write_text(json.dumps({
            "url": "file:///home/me/mast3", "dir_info": {}}))
        self.assertFails("installed from the folder file:///home/me/mast3")

    def test_pth_outside_fails(self):
        (self.site / "outside.pth").write_text(str(self.repo) + "\n")
        self.assertFails("outside.pth: a path outside the bundle")

    def test_setuptools_editable_finder_fails(self):
        (self.site / "__editable___mast_3_14_10_finder.py").write_text("")
        self.assertFails("an editable install")


class TestLayout(FakeRepoTestCase):

    def test_set_env_and_launchers(self):
        home = self.tmp / "home"
        home.mkdir()
        assemble.write_launchers(self.repo, home, "3.14.10", "linux")
        self.assertEqual((home / "set-env").read_text(),
                         "export PYTHON_VERSION=3.14.10\n")
        self.assertEqual((home / "mast-version").read_text(),
                         (self.repo / "files/invocation_scripts/linux/mast-version").read_text())
        if sys.platform != "win32":
            self.assertEqual(stat.S_IMODE((home / "mast-version").stat().st_mode), 0o750)

    def test_archive_name(self):
        self.assertEqual(assemble.archive_name("3.14.10", "3.14.10", "linux", "x86_64"),
                         "MAST-3.14.10_py3.14.10_linux")
        self.assertEqual(assemble.archive_name("3.14.10", "3.14.10", "windows", "AMD64"),
                         "MAST-3.14.10_py3.14.10_windows")
        self.assertEqual(assemble.archive_name("3.14.10", "3.14.10", "darwin", "arm64"),
                         "MAST-3.14.10_py3.14.10_darwin-arm64")

    def test_copy_home_files_copies_only_tracked_files(self):
        self.write_home_file("var/log/flask.log", "a local log\n")
        home = self.tmp / "home"
        assemble.copy_home_files(self.repo, home)
        self.assertTrue((home / "etc/default/server.conf").is_file())
        self.assertFalse((home / "etc/default/server.conf").is_symlink())
        self.assertTrue((home / "etc/local/.empty").is_file())
        self.assertFalse((home / "var/log/flask.log").exists())

    def test_a_file_of_etc_local_is_refused(self):
        self.write_home_file("etc/local/server.key", "a key of one home\n")
        git(self.repo, "add", "-A")
        with self.assertRaises(assemble.HomeFilesError) as found:
            assemble.copy_home_files(self.repo, self.tmp / "home")
        self.assertIn("etc/local/server.key", str(found.exception))

    def test_the_checkout_ships_nothing_of_etc_local(self):
        repo = Path(__file__).resolve().parents[3]
        local = [p for p in assemble.tracked_home_files(repo)
                 if p.startswith("etc/local/")]
        self.assertEqual(local, ["etc/local/.empty"])

    def test_file_at_the_top_is_refused(self):
        self.write_home_file("README", "")
        git(self.repo, "add", "-A")
        with self.assertRaises(assemble.HomeFilesError):
            assemble.copy_home_files(self.repo, self.tmp / "home")


if __name__ == "__main__":
    unittest.main()

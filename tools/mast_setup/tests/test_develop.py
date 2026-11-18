"""
Tests for ./mast-setup --development-mode.

The interpreter step is replaced with sys.executable and the package
step with a stub, so these tests need no download.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

from mast_setup import assemble, develop

from .helpers import FakeRepoTestCase, git

needs_symlinks = unittest.skipIf(
    sys.platform == "win32", "symlinks need developer mode on Windows")


class DevelopTestCase(FakeRepoTestCase):

    def setUp(self):
        super().setUp()
        self.home = self.tmp / "dev" / "mast_home"
        self.installs = 0
        self.warnings = []
        self.said = []

    def prepare_python(self, home):
        return Path(sys.executable), "3.14.10"

    def install(self, repo, python, home):
        self.installs += 1

    def develop(self, **kwargs):
        return develop.develop(
            self.repo, self.home, "linux", self.prepare_python,
            install=self.install, say=self.said.append,
            warn=self.warnings.append, **kwargs)

    def source(self, relative):
        return self.repo / "files" / "mast_home" / relative


@needs_symlinks
class TestDevelop(DevelopTestCase):

    def test_tracked_files_are_links_and_runtime_folders_are_real(self):
        self.develop()
        for relative in ("etc/default/server.conf", "var/www/templates/index.html",
                         "etc/v11.0.0.3-C.xml"):
            path = self.home / relative
            self.assertTrue(path.is_symlink(), relative)
            self.assertEqual(Path(os.readlink(path)), self.source(relative))
        for relative in ("etc/local", "var/jobs", "var/log", "var/run", "tmp"):
            path = self.home / relative
            self.assertTrue(path.is_dir() and not path.is_symlink(), relative)
            self.assertEqual(list(path.iterdir()), [], relative)
        # The folders that hold links are real folders.
        self.assertFalse((self.home / "etc" / "default").is_symlink())
        self.assertTrue((self.home / "mast-version").is_file())
        self.assertEqual(self.warnings, [])

    def test_an_edit_in_the_checkout_is_live(self):
        self.develop()
        self.source("var/www/static/js/mast-util.js").write_text("// changed\n")
        self.assertEqual((self.home / "var/www/static/js/mast-util.js").read_text(),
                         "// changed\n")

    def test_a_second_run_keeps_runtime_files(self):
        self.develop()
        (self.home / "etc/local/hosts.conf").write_text("[hosts]\n")
        (self.home / "var/jobs/jobs.db").write_text("data")
        self.develop()
        self.assertEqual((self.home / "etc/local/hosts.conf").read_text(), "[hosts]\n")
        self.assertEqual((self.home / "var/jobs/jobs.db").read_text(), "data")

    def test_new_tracked_files_are_linked_and_removed_ones_unlinked(self):
        self.develop()
        self.write_home_file("var/www/templates/new.html", "<new/>")
        git(self.repo, "add", "-A")
        git(self.repo, "rm", "-q", "-f", "files/mast_home/var/www/templates/index.html")
        self.develop()
        self.assertTrue((self.home / "var/www/templates/new.html").is_symlink())
        self.assertFalse(os.path.lexists(self.home / "var/www/templates/index.html"))

    def test_an_untracked_file_is_not_linked(self):
        self.write_home_file("var/log/flask.log", "local")
        self.develop()
        self.assertFalse(os.path.lexists(self.home / "var/log/flask.log"))

    def test_a_dangling_link_is_removed(self):
        self.develop()
        os.remove(self.source("etc/default/appliances.conf"))
        self.develop()
        self.assertFalse(os.path.lexists(self.home / "etc/default/appliances.conf"))

    def test_a_real_file_is_not_replaced(self):
        target = self.home / "etc" / "default" / "server.conf"
        target.parent.mkdir(parents=True)
        target.write_text("written by MAST")
        self.develop()
        self.assertFalse(target.is_symlink())
        self.assertEqual(target.read_text(), "written by MAST")
        self.assertEqual(len(self.warnings), 1)

    def test_bundle_check_fails_on_the_development_home(self):
        self.develop()
        with self.assertRaises(assemble.BundleError):
            assemble.check_bundle(self.home)

    def test_packages_are_installed_when_the_lock_changes(self):
        self.develop()
        self.develop()
        self.assertEqual(self.installs, 1)
        (self.repo / "uv.lock").write_text("version = 2\n")
        self.develop()
        self.assertEqual(self.installs, 2)

    def test_recreate_says_what_it_deletes(self):
        self.develop()
        (self.home / "etc/local/hosts.conf").write_text("[hosts]\n")
        self.develop(recreate=True)
        self.assertIn("--recreate deletes the development home", self.said[0])
        self.assertIn("  etc (folder)", self.said)
        self.assertFalse((self.home / "etc/local/hosts.conf").exists())
        self.assertEqual(self.installs, 2)


class TestSymlinkFallback(DevelopTestCase):

    def test_files_are_copied_with_one_warning(self):
        with mock.patch("os.symlink", side_effect=OSError("not allowed")):
            self.develop()
            path = self.home / "etc/default/server.conf"
            self.assertFalse(path.is_symlink())
            self.assertEqual(path.read_text(), "[server]\n")
            self.assertEqual(len(self.warnings), 1)
            self.assertIn("run ./mast-setup --development-mode again", self.warnings[0])
            # A second run refreshes the copies.
            self.source("etc/default/server.conf").write_text("[server]\nnew\n")
            self.develop()
            self.assertEqual(path.read_text(), "[server]\nnew\n")


class TestUsage(unittest.TestCase):

    def test_usage_names_the_home(self):
        lines = develop.usage(Path.cwd(), Path.cwd() / ".dev" / "mast_home", "linux")
        self.assertTrue(lines[0].startswith("Development home: "))
        self.assertIn(".dev/mast_home/mast-system list-domains", lines[1])


if __name__ == "__main__":
    unittest.main()

"""
Slow tests that run ./mast-setup for real. They download the interpreter
(or use build-cache/) and the dependencies, so they are skipped unless
MAST_SETUP_TEST_BUILD=true.

To compare the installer with one built before, set
MAST_SETUP_REFERENCE_ZIP to the path of that zip.
"""
import os
import re
import subprocess
import sys
import tempfile
import tomllib
import unittest
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SLOW = os.environ.get("MAST_SETUP_TEST_BUILD", "").lower() == "true"


def mast_version():
    with (REPO / "pyproject.toml").open("rb") as fp:
        return tomllib.load(fp)["project"]["version"]


def normalize(name):
    """
    A file name of the installer without what may differ between two
    builds of the same layout: the versions of the dependencies.
    """
    return re.sub(r"-[0-9][^/-]*\.dist-info", "-V.dist-info", name)


def may_differ(name):
    """
    Files that are not part of the layout: byte code that pip or Python
    writes into the standard library while the build runs, and the
    provenance files of uv.
    """
    if re.match(r"python/[^/]+/(lib/python3\.\d+|Lib)/(?!site-packages/).*__pycache__/", name):
        return True
    return re.search(r"\.dist-info/uv_[a-z]+\.json$", name) is not None


def run_setup(*args, **kwargs):
    return subprocess.run([sys.executable, str(kwargs.pop("repo", REPO) / "mast-setup"), *args],
                          check=True, capture_output=True, text=True, **kwargs)


def clean_environment():
    env = dict(os.environ)
    env.pop("MAST_HOME", None)
    return env


@unittest.skipUnless(SLOW, "set MAST_SETUP_TEST_BUILD=true to run the slow tests")
class TestBuild(unittest.TestCase):

    def test_full_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            run_setup("--build-directory", tmp / "build", "--dist-directory", tmp / "dist",
                      "--log-level", "30")
            zips = list((tmp / "dist").glob("*.zip"))
            self.assertEqual(len(zips), 1)
            self.assertRegex(zips[0].name, r"^MAST-{}_py3\.\d+\.\d+_(linux|windows|darwin-\w+)\.zip$".format(
                re.escape(mast_version())))
            names = set(zipfile.ZipFile(zips[0]).namelist())
            launcher = "mast.bat" if sys.platform == "win32" else "mast"
            for expected in (launcher, "etc/default/server.conf", "etc/default/ssh.conf",
                             "etc/local/.empty", "contrib/getconfig.py"):
                self.assertIn(expected, names)
            # The classic GUI was removed in R-1
            self.assertEqual([n for n in names if n.startswith("var/www/")], [])
            self.assertTrue(any(re.match(r"python/[^/]+/.*site-packages/mast/home\.py$", n)
                                for n in names))

            reference = os.environ.get("MAST_SETUP_REFERENCE_ZIP")
            if reference:
                old = {normalize(n) for n in zipfile.ZipFile(reference).namelist()
                       if not may_differ(n)}
                new = {normalize(n) for n in names if not may_differ(n)}
                self.assertEqual(sorted(old - new), [], "files missing from the new installer")
                self.assertEqual(sorted(new - old), [], "files new in the installer")

            if sys.platform != "win32":
                # Move the build folder away, so that nothing in the
                # installer can work through a path into it.
                (tmp / "build").rename(tmp / "build.moved")
                home = tmp / "unzipped"
                subprocess.run(["unzip", "-q", str(zips[0]), "-d", str(home)], check=True)
                scripts = next(home.glob("python/*/bin"))
                for command in ([home / "mast", "version"], [scripts / "mast", "version"],
                                [scripts / "pip3", "--version"]):
                    with self.subTest(command=command[0].name):
                        result = subprocess.run([str(c) for c in command], capture_output=True,
                                                text=True, cwd="/", env=clean_environment())
                        self.assertEqual(result.returncode, 0, result.stderr)
                        if command[0].name == "mast":
                            self.assertIn("Version {}".format(mast_version()), result.stdout)


@unittest.skipUnless(SLOW, "set MAST_SETUP_TEST_BUILD=true to run the slow tests")
@unittest.skipIf(sys.platform == "win32", "the test runs the Linux and macOS launchers")
class TestDevelopmentMode(unittest.TestCase):

    def test_launchers_run_the_code_of_the_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            # Work in a clone, so that the test can edit the code.
            clone = tmp / "clone"
            subprocess.run(["git", "clone", "-q", "--no-hardlinks", str(REPO), str(clone)], check=True)
            home = tmp / "home"
            run_setup("--development-mode", "--home", home,
                      "--cache-directory", REPO / "build-cache",
                      "--build-directory", tmp / "build", repo=clone)

            result = subprocess.run([str(home / "mast"), "version"], capture_output=True,
                                    text=True, cwd="/", env=clean_environment())
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Version {}".format(mast_version()), result.stdout)

            system = clone / "mast" / "datapower" / "system" / "system.py"
            text = system.read_text()
            old = '    """This will xor encode and base64 encode the given string'
            self.assertIn(old, text)
            system.write_text(text.replace(
                old, '    """Live docstring check. This will xor encode the given string', 1))
            result = subprocess.run([str(home / "mast"), "datapower", "xor", "-h"],
                                    capture_output=True,
                                    text=True, cwd="/", env=clean_environment())
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Live docstring check", result.stdout)

            # Nothing that the run wrote is in the clone.
            status = subprocess.run(["git", "status", "--porcelain"], cwd=clone,
                                    capture_output=True, text=True, check=True).stdout
            self.assertEqual(status.split("\n")[0].strip(), "M mast/datapower/system/system.py")
            self.assertEqual(len(status.strip().splitlines()), 1, status)


if __name__ == "__main__":
    unittest.main()

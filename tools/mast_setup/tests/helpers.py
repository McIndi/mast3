"""Test helpers: a small fake MAST checkout in a git repository."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

LAUNCHER = "#!/bin/bash\n\nexport MAST_HOME=$(dirname $0)\n\ncd $MAST_HOME\n\n./mast -m mast\n"

HOME_FILES = {
    "etc/default/server.conf": "[server]\n",
    "etc/default/appliances.conf": "[appliance]\n",
    "etc/v11.0.0.3-C.xml": "<spec/>\n",
    "etc/local/.empty": "",
    "var/log/.empty": "",
    "var/run/.empty": "",
    "var/jobs/.empty": "",
    "var/www/templates/index.html": "<html></html>\n",
    "var/www/static/js/mast-util.js": "// util\n",
    "tmp/.empty": "",
}


def git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class FakeRepoTestCase(unittest.TestCase):
    """Make `self.repo`, a git repository with the layout of a checkout."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="mast-setup-test-")
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(os.path.realpath(tmp.name))
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        for relative, contents in HOME_FILES.items():
            self.write_home_file(relative, contents)
        scripts = self.repo / "files" / "invocation_scripts" / "linux"
        scripts.mkdir(parents=True)
        (scripts / "set-env").write_text("export PYTHON_VERSION={PYTHON_VERSION}\n")
        (scripts / "mast-version").write_text(LAUNCHER)
        (self.repo / "uv.lock").write_text("version = 1\n")
        (self.repo / "pyproject.toml").write_text("[project]\nname = 'mast'\n")
        git(self.repo, "add", "-A")

    def write_home_file(self, relative, contents=""):
        path = self.repo / "files" / "mast_home" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)
        return path

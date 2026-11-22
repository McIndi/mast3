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
Integration tests for the MAST command line programs.

These tests run the same commands as `manual-tests.md`, each one in a
new Python process, against the shared DataPower container. Each test
then asks the gateway whether the command did what it reported.

The commands run with a private MAST_HOME, so that the tests can add
the container to `hosts.conf`, `appliances.conf` and
`environments.conf` and leave the real configuration alone.

By default the commands run as `python -m mast.datapower.<module>` with
the interpreter of the tests. If MAST_TEST_HOME names a MAST home (a
development home made by `./mast-setup --development-mode`, or an
unzipped installer), the commands run through the launchers of that
home (`<home>/mast-<module>`). The private home is then a copy of that
home without its runtime files. The interpreter is linked, not copied.
"""
import glob
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

from mast.testsuite.integration.container import (
    PASSWORD,
    SOMA_PORT,
    SSH_PORT,
    USERNAME,
    WEB_PORT,
)
from mast.testsuite.integration.homes import TEST_HOME, copy_home
from mast.testsuite.integration.test_datapower_container import (
    ContainerTestCase,
    GOOD_PASSWORD,
)

ALIAS = "dp1"
ENVIRONMENT = "lab"
CREDENTIALS = "{}:{}".format(USERNAME, PASSWORD)


class CLITestCase(ContainerTestCase):
    """Run MAST commands against the container."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.mast_home = os.path.join(cls.workdir, "mast_home")
        if TEST_HOME:
            copy_home(os.path.abspath(TEST_HOME), cls.mast_home)
        else:
            shutil.copytree(
                os.path.join(os.environ["MAST_HOME"], "etc"),
                os.path.join(cls.mast_home, "etc"),
                ignore=shutil.ignore_patterns("local"))
        local = os.path.join(cls.mast_home, "etc", "local")
        for name in (local, "tmp", os.path.join("var", "log"),
                     os.path.join("var", "run")):
            os.makedirs(os.path.join(cls.mast_home, name), exist_ok=True)
        ports = cls.container.ports
        cls._write(local, "hosts.conf", "[hosts]\n{}: 127.0.0.1\n".format(ALIAS))
        cls._write(local, "environments.conf", "[{}]\nappliances: {}\n".format(
            ENVIRONMENT, ALIAS))
        cls._write(local, "appliances.conf", (
            "[{}]\nsoma_port: {}\nweb_port: {}\nssh_port: {}\n".format(
                ALIAS, ports[SOMA_PORT], ports[WEB_PORT], ports[SSH_PORT])))
        cls._import()

    @classmethod
    def _import(cls):
        """Move the three files into the appliance registry of the home,
        as a user of an older MAST does once."""
        if TEST_HOME:
            command = [os.path.join(cls.mast_home, "mast-appliances"
                                    + (".bat" if sys.platform == "win32"
                                       else ""))]
            env = {k: v for k, v in os.environ.items() if k != "MAST_HOME"}
        else:
            command = [sys.executable, "-m", "mast.registry"]
            env = dict(os.environ, MAST_HOME=cls.mast_home)
        proc = subprocess.run(
            command + ["import"], cwd=cls.mast_home, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            universal_newlines=True, timeout=120)
        if proc.returncode != 0 or "Added the environment" not in proc.stdout:
            raise AssertionError("mast-appliances import failed:\n"
                                 + proc.stdout)

    def launcher(self, name):
        """The path of a launcher of the private home."""
        if sys.platform == "win32":
            name += ".bat"
        return os.path.join(self.mast_home, name)

    @staticmethod
    def _write(directory, name, contents):
        with open(os.path.join(directory, name), "w") as fout:
            fout.write(contents)

    def mast(self, module, *args, credentials=CREDENTIALS, appliance=ENVIRONMENT):
        """Run `mast-<module> <args>` and return its output. The test
        fails if the command does not exit with status 0."""
        if TEST_HOME:
            # The launcher sets MAST_HOME and changes to the home.
            command = [self.launcher("mast-" + module)]
            env = dict(os.environ)
            env.pop("MAST_HOME", None)
        else:
            command = [sys.executable, "-m", "mast.datapower." + module]
            env = dict(os.environ, MAST_HOME=self.mast_home)
        name = "mast-{} {}".format(module, " ".join(args))
        command += list(args)
        if appliance:
            command += ["-a", appliance, "-c", credentials, "-n"]
        proc = subprocess.run(
            command,
            cwd=self.mast_home,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            timeout=300)
        self.assertEqual(
            0, proc.returncode,
            "{} failed:\n{}".format(name, proc.stdout))
        return proc.stdout

    def path(self, *parts):
        return os.path.join(self.mast_home, *parts)

    def domain_names(self):
        status = self.dp.get_status("DomainStatus")
        return [x.text for x in status.xml.findall(".//Domain")]


class TestSystemCLI(CLITestCase):

    @unittest.skipUnless(TEST_HOME, "MAST_TEST_HOME is not set")
    def test_commands_run_through_the_launchers(self):
        # The launcher `mast` runs the interpreter of the home with its
        # arguments, after it changed to the home.
        proc = subprocess.run(
            [self.launcher("mast"), "-c",
             "import os, sys; print(sys.executable); print(os.getcwd())"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            universal_newlines=True, timeout=60)
        self.assertEqual(0, proc.returncode, proc.stdout)
        executable, cwd = proc.stdout.strip().splitlines()[-2:]
        home = os.path.realpath(self.mast_home)
        self.assertTrue(
            os.path.realpath(os.path.dirname(executable)).startswith(
                os.path.realpath(os.path.join(TEST_HOME, "python"))),
            executable)
        self.assertTrue(executable.startswith(self.mast_home), executable)
        self.assertEqual(os.path.realpath(cwd), home)

    def test_xor(self):
        encoded = self.mast(
            "system", "xor", "-s", CREDENTIALS, appliance=None).strip()
        self.assertNotIn(PASSWORD, encoded)
        # The encoded form is accepted in place of the credentials.
        out = self.mast("system", "list-domains", credentials=encoded)
        self.assertIn("default", out)

    def test_domain_commands(self):
        self.addCleanup(self.dp.del_domain, "cli_domain")
        if "cli_domain" in self.domain_names():
            self.dp.del_domain("cli_domain")

        self.mast("system", "add-domain", "-d", "cli_domain", "-s")
        self.assertIn("cli_domain", self.domain_names())
        out = self.mast("system", "list-domains")
        self.assertIn("cli_domain", out)
        self.assertIn("Common", out)

        out = self.mast("system", "save", "-D", "cli_domain")
        self.assertIn("OK", out)

        self.mast("system", "del-domain", "-D", "cli_domain", "-s")
        self.assertNotIn("cli_domain", self.domain_names())

    def test_file_commands(self):
        self.fresh_domain("cli_files")
        local = self.local_file("readme.txt", b"read me\n")

        out = self.mast(
            "system", "create-dir", "-D", "cli_files", "-d", "local:///dir/")
        self.assertIn("OK", out)
        self.mast(
            "system", "set-file", "-D", "cli_files",
            "-f", local, "-d", "local:///dir/")
        self.assertEqual(
            b"read me\n",
            self.dp.getfile("cli_files", "local:///dir/readme.txt"))

        self.mast(
            "system", "get-filestore", "-D", "cli_files",
            "-l", "local:", "-o", "tmp")
        listings = glob.glob(self.path("tmp", "*", ALIAS + "-get-filestore.xml"))
        self.assertEqual(1, len(listings))
        with open(listings[0]) as fin:
            self.assertIn("readme.txt", fin.read())

        self.mast(
            "system", "get-file", "-D", "cli_files",
            "-l", "local:///dir/readme.txt", "-o", "tmp/got")
        found = glob.glob(self.path("tmp", "got", ALIAS + "-*-readme.txt"))
        self.assertEqual(1, len(found))
        with open(found[0], "rb") as fin:
            self.assertEqual(b"read me\n", fin.read())

        self.mast(
            "system", "del-file", "-D", "cli_files",
            "-f", "local:///dir/readme.txt", "-b", "-o", "tmp/deleted")
        self.assertFalse(
            self.dp.file_exists("local:///dir/readme.txt", "cli_files"))
        backups = glob.glob(
            self.path("tmp", "deleted", "**", "readme.txt"), recursive=True)
        self.assertEqual(1, len(backups))


class TestAccountsCLI(CLITestCase):

    def test_user_and_group_commands(self):
        self.remove_leftovers(
            users=["cli_user", "cli_priv"], groups=["cli_group"])

        self.mast("accounts", "add-group", "-N", "cli_group", "-s")
        self.assertIn("cli_group", self.dp.groups)

        self.mast(
            "accounts", "add-user", "-u", "cli_user",
            "-p", GOOD_PASSWORD, "-g", "cli_group", "-s")
        self.mast(
            "accounts", "add-user", "-u", "cli_priv",
            "-p", GOOD_PASSWORD, "-P", "-s")
        out = self.mast("accounts", "list-users")
        self.assertIn("cli_user", out)
        self.assertIn("cli_priv", out)

        out = self.mast("accounts", "force-change-password", "-U", "cli_user")
        self.assertIn("OK", out)

        self.mast("accounts", "del-user", "-U", "cli_user", "-s")
        self.mast("accounts", "del-user", "-U", "cli_priv", "-s")
        self.assertNotIn("cli_user", self.dp.users)
        self.assertNotIn("cli_priv", self.dp.users)

        self.mast("accounts", "del-group", "-U", "cli_group", "-s")
        self.assertNotIn("cli_group", self.dp.groups)


class TestBackupsCLI(CLITestCase):

    def test_normal_backup_and_restore(self):
        self.fresh_domain("cli_backup")
        out = self.mast(
            "backups", "normal-backup", "-D", "cli_backup",
            "-C", "Daily Backup", "-o", "tmp")
        self.assertIn("Verified", out)
        backups = glob.glob(self.path(
            "tmp", ALIAS, "NormalBackup", "*", "*-cli_backup.zip"))
        self.assertEqual(1, len(backups))
        with zipfile.ZipFile(backups[0]) as archive:
            self.assertIsNone(archive.testzip())

        self.mast(
            "backups", "restore-normal-backup",
            "-f", backups[0], "-D", "cli_backup")
        self.assertIn("cli_backup", self.domain_names())

    def test_checkpoint_commands(self):
        self.fresh_domain("cli_chk")
        out = self.mast(
            "backups", "set-checkpoint", "-D", "cli_chk", "-C", "cli")
        self.assertIn("Successful", out)
        checkpoints = self.wait_for(
            lambda: self.dp.get_existing_checkpoints("cli_chk"))
        name = list(checkpoints)[0]
        self.assertTrue(name.startswith("cli-cli_chk-"))

        out = self.mast("backups", "list-checkpoints", "-D", "cli_chk")
        self.assertIn(name, out)

        self.mast(
            "backups", "remove-checkpoint", "-D", "cli_chk", "-C", name)
        self.wait_for(
            lambda: not self.dp.get_existing_checkpoints("cli_chk"))


class TestDeveloperCLI(CLITestCase):

    def test_export_and_import(self):
        self.fresh_domain("cli_dev")
        self.mast(
            "developer", "export", "-D", "cli_dev",
            "-o", "all-objects", "-O", "all-classes",
            "-C", "cli export", "--out-dir", "tmp/export")
        exports = glob.glob(
            self.path("tmp", "export", "**", "*.zip"), recursive=True)
        self.assertEqual(1, len(exports))
        with zipfile.ZipFile(exports[0]) as archive:
            self.assertIn(b"cli export", archive.read("export.xml"))

        self.mast(
            "developer", "import", "-D", "cli_dev",
            "-f", exports[0], "-o", "tmp/import")


class TestDeploymentCLI(CLITestCase):

    def test_get_file(self):
        self.fresh_domain("cli_deploy")
        local = self.local_file("deployed.txt", b"deployed\n")
        self.dp.set_file(local, "local:", "cli_deploy")
        self.mast(
            "deployment", "get-file", "-D", "cli_deploy",
            "-l", "local:///deployed.txt", "-o", "tmp/deploy")
        found = glob.glob(
            self.path("tmp", "deploy", "**", "*deployed.txt"), recursive=True)
        self.assertEqual(1, len(found))
        with open(found[0], "rb") as fin:
            self.assertEqual(b"deployed\n", fin.read())


class TestNetworkCLI(CLITestCase):

    def test_check_connectivity(self):
        out = self.mast("network", "check-connectivity")
        row = [line for line in out.splitlines() if line.startswith(ALIAS)]
        self.assertEqual(1, len(row))
        self.assertEqual([ALIAS, "True", "True", "True"], row[0].split())

    def test_host_alias_commands(self):
        self.addCleanup(self.dp.del_host_alias, "cli_alias")
        self.mast(
            "network", "add-host-alias", "-N", "cli_alias", "-i", "127.0.0.1")
        out = self.mast("network", "list-host-aliases")
        # One row for the appliance and one for what is common
        self.assertRegex(
            out, r"(?m)^{} +cli_alias +127\.0\.0\.1 +enabled$".format(ALIAS))
        self.assertRegex(out, r"(?m)^common +cli_alias +127\.0\.0\.1 +enabled$")

        self.mast("network", "del-host-alias", "-H", "cli_alias")
        names = [alias[0] for alias in self.dp.get_host_aliases()]
        self.assertNotIn("cli_alias", names)


if __name__ == "__main__":
    unittest.main()

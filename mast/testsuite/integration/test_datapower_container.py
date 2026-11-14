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
Integration tests for `mast.datapower.datapower.DataPower`.

The test classes share one DataPower container. Each class uses its
own domain, user and object names, and removes what it creates, so the
classes do not disturb each other and the container can be used again.
A class that sets `private_container` gets a container of its own. The
classes are skipped when a container cannot be started. See
`container.py` for the environment variables.

A container cannot test these parts of MAST, because DataPower locks
the related configuration when it runs in a container:

* Static hosts (`DNSNameService` is read-only)
* Secondary addresses and static routes (`EthernetInterface`)
* The RAID directory (there is no RAID volume)
* Firmware upgrades
"""
import io
import os
import shutil
import tempfile
import time
import unittest
import zipfile

from mast.datapower.datapower import DataPower
from mast.testsuite.integration.container import (
    ContainerUnavailable,
    DataPowerContainer,
    PASSWORD,
    SOMA_PORT,
    USERNAME,
    shared_container,
)

# A password that the default DataPower password policy accepts.
GOOD_PASSWORD = "Passw0rd!x9"


class ContainerTestCase(unittest.TestCase):
    """Give the test class a DataPower container."""

    # Set to True in a class that must not share its container.
    private_container = False

    @classmethod
    def setUpClass(cls):
        try:
            if cls.private_container:
                cls.container = DataPowerContainer()
                cls.container.start()
            else:
                cls.container = shared_container()
        except ContainerUnavailable as e:
            raise unittest.SkipTest(str(e))
        cls.addClassCleanup(cls._release_container)
        cls.dp = cls.container.datapower()
        cls.workdir = tempfile.mkdtemp(prefix="mast-test-")
        cls.addClassCleanup(shutil.rmtree, cls.workdir, ignore_errors=True)

    @classmethod
    def _release_container(cls):
        if cls.private_container:
            cls.container.stop()

    @classmethod
    def fresh_domain(cls, name):
        """Create the domain `name`. Remove it first if an earlier run
        left it behind, and remove it when the class ends."""
        status = cls.dp.get_status("DomainStatus")
        if name in [x.text for x in status.xml.findall(".//Domain")]:
            cls.dp.del_domain(name)
        assert cls.dp.add_domain(name)
        cls.addClassCleanup(cls.dp.del_domain, name)

    def remove_leftovers(self, users=(), groups=()):
        """Remove users and groups now, if an earlier run left them
        behind, and again when the test ends."""
        def remove():
            dp = self.container.datapower()
            fallback = dp.fallback_users
            for user in users:
                if user in fallback:
                    dp.del_rbm_fallback(user)
                if user in dp.users:
                    dp.remove_user(user)
            for group in groups:
                if group in dp.groups:
                    dp.del_group(group)
        remove()
        self.addCleanup(remove)

    def local_file(self, name, contents):
        path = os.path.join(self.workdir, name)
        with open(path, "wb") as fout:
            fout.write(contents)
        return path

    def wait_for(self, condition, timeout=60):
        """Call `condition` until it returns a true value."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            result = condition()
            if result:
                return result
            time.sleep(1)
        self.fail("Condition was not met in {} seconds".format(timeout))


class TestConnectivity(ContainerTestCase):

    def test_is_reachable(self):
        self.assertTrue(self.dp.is_reachable())

    def test_check_xml_mgmt(self):
        self.assertTrue(self.dp.check_xml_mgmt())

    def test_check_web_mgmt(self):
        self.assertTrue(self.dp.check_web_mgmt())

    def test_check_cli_mgmt(self):
        self.assertTrue(self.dp.check_cli_mgmt())

    def test_is_reachable_with_bad_credentials(self):
        """The appliance answers, so it is reachable. Only the
        credentials are wrong."""
        dp = DataPower(
            "127.0.0.1",
            "{}:not-the-password".format(USERNAME),
            port=self.container.ports[SOMA_PORT],
            check_hostname=False)
        self.assertTrue(dp.is_reachable())

    def test_is_reachable_when_nothing_listens(self):
        dp = DataPower(
            "127.0.0.1",
            "{}:{}".format(USERNAME, PASSWORD),
            port=1,
            check_hostname=False)
        self.assertFalse(dp.is_reachable())

    def test_history_is_text(self):
        self.dp.get_status("DateTimeStatus")
        history = self.dp.history
        self.assertIn("DateTimeStatus", history)
        self.assertNotIn("response: b'", history)


class TestDomains(ContainerTestCase):

    def test_domain_lifecycle(self):
        dp = self.dp
        self.assertIn("default", dp.domains)
        self.addCleanup(dp.del_domain, "lifecycle")

        self.assertTrue(dp.add_domain("lifecycle"))
        status = dp.get_status("DomainStatus")
        names = [x.text for x in status.xml.findall(".//Domain")]
        self.assertIn("lifecycle", names)

        self.assertTrue(dp.disable_domain("lifecycle"))
        self.assertTrue(dp.enable_domain("lifecycle"))
        self.assertTrue(dp.SaveConfig(domain="lifecycle"))

        self.assertTrue(dp.del_domain("lifecycle"))
        status = dp.get_status("DomainStatus")
        names = [x.text for x in status.xml.findall(".//Domain")]
        self.assertNotIn("lifecycle", names)

    def test_invalid_action_parameter(self):
        with self.assertRaises(ValueError):
            self.dp.SaveConfig(not_a_parameter="x")


class TestAccounts(ContainerTestCase):

    def test_group_and_user_lifecycle(self):
        dp = self.dp
        self.remove_leftovers(
            users=["group_user", "priv_user"], groups=["testers"])
        self.assertTrue(dp.add_group(
            "testers", access_policies=["*/*/*?Access=r"]))
        self.assertIn("testers", dp.groups)

        self.assertTrue(dp.add_user(
            "group_user", GOOD_PASSWORD,
            user_group="testers", supress_force_password_change=True))
        self.assertTrue(dp.add_user(
            "priv_user", GOOD_PASSWORD,
            privileged=True, supress_force_password_change=True))
        self.assertIn("group_user", dp.users)
        self.assertIn("priv_user", dp.users)

        self.assertTrue(dp.change_password("group_user", "N3w" + GOOD_PASSWORD))

        self.assertTrue(dp.add_rbm_fallback("priv_user"))
        self.assertIn("priv_user", dp.fallback_users)
        self.assertTrue(dp.del_rbm_fallback("priv_user"))
        self.assertNotIn("priv_user", dp.fallback_users)

        self.assertTrue(dp.remove_user("group_user"))
        self.assertTrue(dp.remove_user("priv_user"))
        self.assertNotIn("group_user", dp.users)
        self.assertTrue(dp.del_group("testers"))
        self.assertNotIn("testers", dp.groups)

    def test_add_user_needs_one_of_privileged_or_group(self):
        self.assertFalse(self.dp.add_user("nobody", GOOD_PASSWORD))
        self.assertFalse(self.dp.add_user(
            "nobody", GOOD_PASSWORD, privileged=True, user_group="x"))
        self.assertNotIn("nobody", self.dp.users)

    def test_add_rbm_fallback_needs_an_existing_user(self):
        with self.assertRaises(KeyError):
            self.dp.add_rbm_fallback("no_such_user")


class TestChangeOwnPassword(ContainerTestCase):

    # No other class can log in after this test.
    private_container = True

    def test_object_keeps_working_after_it_changes_its_own_password(self):
        dp = self.dp
        new_password = "Adm1n" + GOOD_PASSWORD
        self.assertTrue(dp.change_password(USERNAME, new_password))
        self.assertEqual(
            "{}:{}".format(USERNAME, new_password), dp.credentials)
        # A request with the new credentials must be accepted.
        self.assertIn(USERNAME, dp.users)


class TestFiles(ContainerTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.fresh_domain("files")

    def test_file_lifecycle(self):
        dp = self.dp
        local = self.local_file("hello.txt", b"hello mast\n")

        self.assertTrue(dp.CreateDir(domain="files", Dir="local:///one"))
        self.assertTrue(dp.directory_exists("local:///one", "files"))
        self.assertFalse(dp.directory_exists("local:///two", "files"))
        self.assertTrue(dp.location_exists("local:", "files"))

        self.assertTrue(dp.set_file(local, "local:///one", "files"))
        self.assertTrue(dp.file_exists("local:///one/hello.txt", "files"))
        self.assertFalse(dp.file_exists("local:///one/other.txt", "files"))
        self.assertEqual(["hello.txt"], dp.ls("local:/one", domain="files"))
        self.assertEqual(["local:/one/"], dp.ls("local:", domain="files"))
        self.assertEqual(
            b"hello mast\n", dp.getfile("files", "local:///one/hello.txt"))

        copy_dir = os.path.join(self.workdir, "copy")
        dp.copy_directory("local:///one", copy_dir, domain="files")
        copied = os.path.join(copy_dir, "local", "one", "hello.txt")
        with open(copied, "rb") as fin:
            self.assertEqual(b"hello mast\n", fin.read())

        backup_dir = os.path.join(self.workdir, "deleted")
        os.makedirs(backup_dir)
        self.assertTrue(dp.del_file(
            "files", "local:///one/hello.txt",
            backup=True, local_dir=backup_dir))
        with open(os.path.join(backup_dir, "hello.txt"), "rb") as fin:
            self.assertEqual(b"hello mast\n", fin.read())
        self.assertFalse(dp.file_exists("local:///one/hello.txt", "files"))

        self.assertTrue(dp.RemoveDir(domain="files", Dir="local:///one"))
        self.assertFalse(dp.directory_exists("local:///one", "files"))

    def test_set_file_does_not_overwrite_when_told_not_to(self):
        dp = self.dp
        first = self.local_file("keep.txt", b"first\n")
        self.assertTrue(dp.set_file(first, "local:", "files"))
        self.local_file("keep.txt", b"second\n")
        with self.assertRaises(ValueError):
            dp.set_file(first, "local:", "files", overwrite=False)
        with self.assertRaises(ValueError):
            dp.set_file(first, "local:///keep.txt", "files", overwrite=False)
        self.assertEqual(b"first\n", dp.getfile("files", "local:///keep.txt"))

    def test_set_file_rejects_a_path_without_a_location(self):
        local = self.local_file("bad.txt", b"x")
        with self.assertRaises(ValueError):
            self.dp.set_file(local, "no/location", "files")

    def test_get_all_logs(self):
        log_dir = os.path.join(self.workdir, "logs")
        self.dp.get_all_logs(log_dir=log_dir)
        found = [f for _, _, files in os.walk(log_dir) for f in files]
        self.assertIn("default-log", found)

    def test_filesystem_listings(self):
        temporary = self.dp.get_temporary_filesystem()
        self.assertIsNotNone(
            temporary.xml.find(".//location[@name='temporary:']"))
        encrypted = self.dp.get_encrypted_filesystem()
        self.assertIsNotNone(
            encrypted.xml.find(".//location[@name='local:']"))


class TestCheckpoints(ContainerTestCase):

    def test_checkpoint_lifecycle(self):
        dp = self.dp
        self.fresh_domain("chk")
        self.assertIsInstance(dp.max_checkpoints("chk"), int)
        self.assertEqual({}, dp.get_existing_checkpoints("chk"))

        self.assertTrue(dp.SaveCheckpoint(domain="chk", ChkName="first"))
        checkpoints = self.wait_for(
            lambda: dp.get_existing_checkpoints("chk"))
        self.assertEqual(["first"], list(checkpoints))
        self.assertEqual(3, len(checkpoints["first"]["date"]))
        self.assertEqual(3, len(checkpoints["first"]["time"]))

        self.assertTrue(dp.rollback_checkpoint("chk", "first"))
        # The domain restarts during a rollback.
        self.wait_for(lambda: dp.remove_oldest_checkpoint("chk"))
        self.assertEqual({}, dp.get_existing_checkpoints("chk"))


class TestBackupsAndExports(ContainerTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.fresh_domain("bk")
        cls.dp.add_host_alias("bk_alias", "127.0.0.1")
        cls.addClassCleanup(cls.dp.del_host_alias, "bk_alias")

    def test_normal_backup_and_restore(self):
        dp = self.dp
        backup = dp.get_normal_backup(domains="bk", comment="test backup")
        archive = zipfile.ZipFile(io.BytesIO(backup))
        self.assertIsNone(archive.testzip())
        self.assertIn("export.xml", archive.namelist())
        self.assertIn(b"test backup", archive.read("export.xml"))

        path = self.local_file("backup.zip", backup)
        resp = dp.restore_normal_backup(path, "bk")
        self.assertIsNotNone(resp.xml.find(".//imported-objects"))

    def test_export_and_import(self):
        dp = self.dp
        exported = dp.export(
            "default", "bk_alias", "HostAlias",
            comment="test export", persisted=False)
        archive = zipfile.ZipFile(io.BytesIO(exported))
        self.assertIsNone(archive.testzip())
        export_xml = archive.read("export.xml")
        self.assertIn(b"bk_alias", export_xml)
        self.assertIn(b"test export", export_xml)

        path = self.local_file("export.zip", exported)
        resp = dp.do_import("default", path, dry_run=True)
        self.assertIsNotNone(resp.xml.find(".//imported-objects"))

    def test_object_audit(self):
        audit = self.dp.object_audit("bk")
        self.assertIn(b'domain="bk"', audit)


class TestNetwork(ContainerTestCase):

    def test_host_alias_lifecycle(self):
        dp = self.dp
        self.addCleanup(dp.del_host_alias, "alias_one")
        self.assertTrue(dp.add_host_alias("alias_one", "127.0.0.1"))
        self.assertIn(
            ("alias_one", "127.0.0.1", "enabled"), dp.get_host_aliases())
        self.assertTrue(dp.del_host_alias("alias_one"))
        names = [alias[0] for alias in dp.get_host_aliases()]
        self.assertNotIn("alias_one", names)


class TestSSH(ContainerTestCase):

    def test_ssh_session(self):
        dp = self.dp
        self.assertFalse(dp.ssh_is_connected())
        dp.ssh_connect()
        try:
            self.assertTrue(dp.ssh_is_connected())
            output = dp.ssh_issue_command("show version")
            self.assertIn("Version", output)
        finally:
            self.assertTrue(dp.ssh_disconnect())
        self.assertFalse(dp.ssh_is_connected())

    def test_ssh_connect_uses_the_port_argument(self):
        dp = self.container.datapower()
        configured_port = dp.ssh_port
        dp.ssh_port = 1
        dp.ssh_connect(port=configured_port)
        self.assertTrue(dp.ssh_disconnect())

    def test_ssh_rbm_fallback(self):
        dp = self.dp
        self.remove_leftovers(users=["ssh_fallback"])
        dp.add_user(
            "ssh_fallback", GOOD_PASSWORD,
            privileged=True, supress_force_password_change=True)
        dp.ssh_add_rbm_fallback("ssh_fallback")
        self.assertIn("ssh_fallback", dp.fallback_users)
        dp.ssh_del_rbm_fallback("ssh_fallback")
        self.assertNotIn("ssh_fallback", dp.fallback_users)


if __name__ == "__main__":
    unittest.main()

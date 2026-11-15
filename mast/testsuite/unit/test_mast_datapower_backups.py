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
Unit tests for the commands of mast.datapower.backups, with the
appliances replaced by fakes. The integration tests run the same
commands against a real DataPower.
"""
import contextlib
import glob
import io
import os
import tempfile
import unittest
import zipfile
from xml.etree import ElementTree as etree

import mock

import mast.datapower.backups as backups
from mast.cli import captured
from mast.datapower.backups.backups import cli
from mast.plugin_utils.results import (
    ActionResults, ListResults, Report, SeeDownload, StatusResults)


def zip_bytes():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("export.xml", "<x/>")
    return buffer.getvalue()


class FakeResponse(object):
    def __init__(self, ok=True):
        self.xml = etree.fromstring("<result>OK</result>")
        self.pretty = b"<result>OK</result>"
        self.ok = ok

    def __bool__(self):
        return self.ok


class FakeAppliance(object):
    hostname = "dp1"
    domains = ["default", "other"]
    history = "request: x\nresponse: y"
    backup_is_good = True

    def __init__(self):
        self.calls = []
        self.checkpoints = {"old": {"date": ["2026", "01", "02"],
                                    "time": ["03", "04", "05"]}}

    def record(name):
        def method(self, *args, **kwargs):
            self.calls.append((name, args, kwargs))
            return FakeResponse()
        return method

    QuiesceDP = record("QuiesceDP")
    UnquiesceDP = record("UnquiesceDP")
    RemoveDir = record("RemoveDir")
    SaveCheckpoint = record("SaveCheckpoint")
    remove_oldest_checkpoint = record("remove_oldest_checkpoint")
    copy_directory = record("copy_directory")
    log_error = record("log_error")

    def file_exists(self, filename, domain):
        return True

    def verify_local_backup(self, directory):
        return self.backup_is_good

    def max_checkpoints(self, domain):
        return 1

    def get_existing_checkpoints(self, domain):
        return self.checkpoints


class FakeEnvironment(object):
    backup = zip_bytes()

    def __init__(self, hostnames, *args, **kwargs):
        self.created_with = (hostnames, args, kwargs)
        self.appliances = [FakeAppliance()]
        self.actions = []

    def perform_action(self, action, **kwargs):
        self.actions.append((action, kwargs))
        if action == "get_normal_backup":
            return {"dp1": self.backup}
        if action == "get_existing_checkpoints":
            return {"dp1": self.appliances[0].checkpoints}
        return {"dp1": FakeResponse()}

    perform_async_action = perform_action


class TestBackupsCommands(unittest.TestCase):

    def setUp(self):
        for patch in (
                mock.patch(
                    "mast.plugin_utils.params.Environment", FakeEnvironment),
                mock.patch("mast.datapower.backups.backups.sleep")):
            patch.start()
            self.addCleanup(patch.stop)
        self.out_dir = tempfile.mkdtemp(dir=os.path.join(
            os.environ["MAST_HOME"], "tmp"))

    def call(self, command, **kwargs):
        """Call command and return (result, progress messages)."""
        kwargs.setdefault("appliances", ["dp1"])
        with captured() as messages:
            result = command(**kwargs)
        return result, messages

    def test_normal_backup_of_several_domains_in_one_file(self):
        result, _ = self.call(
            backups.get_normal_backup, Domain=["a", "b", "a"],
            comment="c", out_dir=self.out_dir)
        self.assertIsInstance(result, StatusResults)
        ((action, kwargs),) = result.env.actions
        self.assertEqual("get_normal_backup", action)
        self.assertEqual(["a", "b"], sorted(kwargs["domains"]))
        self.assertEqual("c", kwargs["comment"])
        self.assertEqual(1, len(glob.glob(os.path.join(
            self.out_dir, "dp1", "NormalBackup", "*", "*.zip"))))
        (status,) = result.results.values()
        self.assertEqual("Verified", status)
        self.assertIn("Verified", result.text())
        self.assertIn("Verified", result.html())

    def test_normal_backup_of_each_domain_in_its_own_file(self):
        result, _ = self.call(
            backups.get_normal_backup, Domain=["a", "b"], individual=True,
            out_dir=self.out_dir)
        self.assertEqual(
            ["a", "b"],
            sorted(kwargs["domains"] for _, kwargs in result.env.actions))
        self.assertEqual(
            ["dp1-a-normalBackup", "dp1-b-normalBackup"],
            sorted(result.results))
        self.assertEqual(2, len(glob.glob(os.path.join(
            self.out_dir, "dp1", "NormalBackup", "*", "*.zip"))))

    def test_normal_backup_that_is_not_a_zip_file_is_corrupt(self):
        with mock.patch.object(FakeEnvironment, "backup", b"not a zip"):
            result, _ = self.call(
                backups.get_normal_backup, Domain=["a"], out_dir=self.out_dir)
        self.assertEqual(["Corrupt"], list(result.results.values()))

    def test_normal_backup_called_as_other_commands_call_it(self):
        # firmware-upgrade passes one hostname, one credential and one
        # domain, each as a string
        result, _ = self.call(
            backups.get_normal_backup, appliances="dp1",
            credentials="user:pass", Domain="all-domains",
            out_dir=self.out_dir)
        self.assertEqual(["dp1"], result.env.created_with[0])
        self.assertEqual(["user:pass"], result.env.created_with[1][0])
        self.assertEqual(["all-domains"], result.env.actions[0][1]["domains"])

    def test_normal_backup_needs_a_domain(self):
        with self.assertRaises(ValueError):
            self.call(backups.get_normal_backup, out_dir=self.out_dir)

    def test_restore_normal_backup(self):
        result, messages = self.call(
            backups.restore_normal_backup, file_in="backup.zip", Domain="d",
            out_dir=self.out_dir)
        self.assertIsInstance(result, SeeDownload)
        action, kwargs = result.env.actions[0]
        self.assertEqual("restore_normal_backup", action)
        self.assertEqual("backup.zip", kwargs["file_in"])
        self.assertTrue(kwargs["import_domain"])
        self.assertFalse(kwargs["reset_domain"])
        (filename,) = glob.glob(os.path.join(
            self.out_dir, "restore_normal_backup", "*", "*-dp1-d-results.xml"))
        self.assertIn(filename, messages[0])
        with self.assertRaises(ValueError):
            self.call(backups.restore_normal_backup, Domain="d")

    def test_secure_backup(self):
        result, messages = self.call(
            backups.get_secure_backup, CryptoCertificate="cert",
            destination="local:/raid0/", remove=True, quiesce_before=True,
            unquiesce_after=True, out_dir=self.out_dir)
        self.assertIsInstance(result, Report)
        self.assertEqual(1200, result.env.created_with[2]["timeout"])
        (create, backup) = result.env.actions
        self.assertEqual("CreateDir", create[0])
        self.assertRegex(create[1]["Dir"], r"^local:/raid0/\d")
        self.assertEqual("SecureBackup", backup[0])
        self.assertEqual("cert", backup[1]["cert"])
        self.assertEqual("off", backup[1]["include-raid"])
        self.assertEqual(
            ["QuiesceDP", "copy_directory", "RemoveDir", "UnquiesceDP"],
            [call[0] for call in result.env.appliances[0].calls])
        self.assertIn("\t dp1  -  Succeeded", messages)
        html = result.html()
        for step in ("Quiesce_appliance", "CreateDir", "SecureBackup",
                     "verify-SecureBackup", "RemoveDir", "Unquiesce_appliance"):
            self.assertEqual(1, html.count("dp1-" + step), step)

    def test_secure_backup_that_does_not_verify_is_not_removed(self):
        with mock.patch.object(FakeAppliance, "backup_is_good", False):
            result, messages = self.call(
                backups.get_secure_backup, remove=True, out_dir=self.out_dir)
        self.assertIn("\t dp1  -  Failed", messages)
        calls = [call[0] for call in result.env.appliances[0].calls]
        self.assertNotIn("RemoveDir", calls)
        self.assertIn("log_error", calls)
        self.assertNotIn("RemoveDir", result.html())

    def test_restore_secure_backup(self):
        result, _ = self.call(
            backups.restore_secure_backup, CryptoCertificate="cert",
            location="local:/raid0/x", validate_only=True)
        self.assertIsInstance(result, ActionResults)
        self.assertEqual(
            [("SecureRestore", {"cred": "cert", "source": "local:/raid0/x",
                                "validate": "on"})],
            result.env.actions)

    def test_list_checkpoints(self):
        result, _ = self.call(backups.list_checkpoints, Domain="d")
        self.assertIsInstance(result, ListResults)
        self.assertEqual({"dp1": ["old 2026-01-02 03:04:05"]}, result.results)
        self.assertIn("old 2026-01-02 03:04:05", result.text())
        self.assertIn("dp1-d", result.html())
        self.assertIn("Checkpoints", result.html())

    def test_remove_and_rollback_checkpoint(self):
        for command, action in (
                (backups.remove_checkpoint, "RemoveCheckpoint"),
                (backups.rollback_checkpoint, "RollbackCheckpoint")):
            result, _ = self.call(command, Domain="d", checkpoint_name="old")
            self.assertIsInstance(result, ActionResults)
            self.assertEqual(
                [(action, {"ChkName": "old", "domain": "d"})],
                result.env.actions)

    def test_set_checkpoint(self):
        result, messages = self.call(backups.set_checkpoint, comment="c")
        appliance = result.env.appliances[0]
        # The default domain, and the oldest checkpoint goes first
        # because the fake allows only one
        self.assertEqual(
            ["remove_oldest_checkpoint", "SaveCheckpoint"],
            [call[0] for call in appliance.calls])
        kwargs = appliance.calls[1][2]
        self.assertEqual("default", kwargs["domain"])
        self.assertTrue(kwargs["ChkName"].startswith("c-default-"))
        self.assertEqual(["dp1", "\t default", "\t\tSuccessful"], messages)
        self.assertIn("dp1-default-set_checkpoint", result.html())
        self.assertIn("Succeeded", result.html())

    def test_set_checkpoint_in_all_domains_keeping_the_oldest(self):
        result, _ = self.call(
            backups.set_checkpoint, Domain=["all-domains"],
            remove_oldest=False)
        calls = result.env.appliances[0].calls
        self.assertEqual(["SaveCheckpoint", "SaveCheckpoint"],
                         [call[0] for call in calls])
        self.assertEqual(["default", "other"],
                         [call[2]["domain"] for call in calls])

    def test_command_line(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.run(["normal-backup", "-a", "dp1", "-c", "user:pass", "-n",
                     "-D", "a", "-C", "Daily Backup", "-o", self.out_dir,
                     "-i"])
            cli.run(["set-checkpoint", "-a", "dp1", "-D", "a", "-C", "c",
                     "--no-remove-oldest"])
            cli.run(["list-checkpoints", "-a", "dp1", "-D", "a"])
            cli.run(["remove-checkpoint", "-a", "dp1", "-D", "a", "-C", "old"])
            cli.run(["secure-backup", "-a", "dp1", "-C", "cert", "-d", "x:/",
                     "-i", "-I", "-r", "-q", "-u", "-Q", "1", "-t", "5",
                     "-o", self.out_dir])
            cli.run(["restore-secure-backup", "-a", "dp1", "-C", "cert",
                     "-l", "x", "-v"])
            cli.run(["restore-normal-backup", "-a", "dp1", "-f", "b.zip",
                     "-D", "a", "-s", "XML", "-d", "policy", "-r",
                     "--no-overwrite-files", "--dry-run", "-o", self.out_dir])
        text = out.getvalue()
        self.assertIn("dp1-a-normalBackup\n==================\nVerified", text)
        self.assertIn("\t\tSuccessful", text)
        self.assertIn("old 2026-01-02 03:04:05", text)

    def test_no_command_takes_a_web_parameter(self):
        self.assertEqual(8, len(cli.commands))
        for command in cli.commands.values():
            self.assertIsNotNone(command.model, command.name)
            self.assertNotIn("web", command.model.model_fields)


if __name__ == "__main__":
    unittest.main()

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
Unit tests for the commands of mast.datapower.deployment that declare
their parameters with a model, with the appliances replaced by fakes.
"""
import contextlib
import glob
import io
import os
import tempfile
import unittest
from xml.etree import ElementTree as etree

import mock

import mast.datapower.deployment as deployment
from mast.cli import Text, captured
from mast.datapower.deployment.deployment import cli
from mast.plugin_utils.results import Report, SeeDownload, StatusResults

# These call commands of mast.datapower.system, which still take a web
# parameter. They move to models after the system commands do.
LEGACY = {"deploy", "postdeploy", "git-deploy"}


class FakeResponse(object):
    def __init__(self, ok=True):
        self.xml = etree.fromstring(
            '<r xmlns:dp="http://www.datapower.com/schemas/management">'
            '<dp:result>OK</dp:result></r>')
        self.ok = ok

    def __bool__(self):
        return self.ok


class FakeAppliance(object):
    hostname = "dp1"
    credentials = "user:pass"
    history = "request: x\nresponse: y"

    def __init__(self):
        self.calls = []

    def record(name):
        def method(self, *args, **kwargs):
            self.calls.append((name, args, kwargs))
            return FakeResponse()
        return method

    del_file = record("del_file")
    DeleteFile = record("DeleteFile")
    AddPasswordMap = record("AddPasswordMap")
    DeletePasswordMap = record("DeletePasswordMap")
    SaveConfig = record("SaveConfig")

    def ls(self, directory, domain, include_directories):
        return ["a.txt", "diag-log"]

    def getfile(self, domain, filename):
        return b"contents of " + filename.encode()


class FakeEnvironment(object):
    def __init__(self, hostnames, *args, **kwargs):
        self.appliances = [FakeAppliance()]
        self.actions = []

    def perform_action(self, action, **kwargs):
        self.actions.append((action, kwargs))
        if action == "getfile":
            return {"dp1": b"the file"}
        return {"dp1": FakeResponse()}

    perform_async_action = perform_action


class TestDeploymentCommands(unittest.TestCase):

    def setUp(self):
        patch = mock.patch(
            "mast.plugin_utils.params.Environment", FakeEnvironment)
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

    def test_set_file(self):
        result, _ = self.call(
            deployment.set_file, file_in="a.txt", destination="local:/a.txt",
            overwrite=False)
        self.assertEqual(
            [("set_file", {"file_in": "a.txt", "file_out": "local:/a.txt",
                           "domain": "default", "overwrite": False})],
            result.env.actions)
        self.assertEqual({"dp1": "Succeeded"}, result.results)
        self.assertIn("dp1-set_file", result.html())
        with self.assertRaises(ValueError):
            self.call(deployment.set_file, file_in="a.txt")

    def test_get_file(self):
        result, messages = self.call(
            deployment.get_file, location="local:///dir/a.txt", Domain="d",
            out_dir=os.path.join(self.out_dir, "new"))
        self.assertIsInstance(result, SeeDownload)
        self.assertEqual(
            [("getfile", {"domain": "d", "filename": "local:///dir/a.txt"})],
            result.env.actions)
        (filename,) = glob.glob(
            os.path.join(self.out_dir, "new", "dp1-*-a.txt"))
        with open(filename, "rb") as fin:
            self.assertEqual(b"the file", fin.read())
        self.assertIn(filename, messages[0])

    def test_delete_file(self):
        result, _ = self.call(
            deployment.delete_file, Domain="d", filename="local:/a.txt")
        self.assertEqual(
            [("del_file", {"filename": "local:/a.txt", "domain": "d"})],
            result.env.actions)
        self.assertIn("dp1\n===\nSuccess", result.text())

    def test_delete_file_with_a_backup(self):
        result, _ = self.call(
            deployment.delete_file, Domain="d", filename="local:/a.txt",
            backup=True, out_dir=self.out_dir)
        ((name, _, kwargs),) = result.env.appliances[0].calls
        self.assertEqual("del_file", name)
        self.assertTrue(kwargs["backup"])
        self.assertEqual(
            os.path.join(self.out_dir, "dp1"), kwargs["local_dir"])
        self.assertTrue(os.path.isdir(kwargs["local_dir"]))

    def test_clean_up(self):
        result, messages = self.call(
            deployment.clean_up, Domain="d", checkpoints=True, logtemp=True,
            error_reports=True, out_dir=self.out_dir)
        self.assertIsInstance(result, Report)
        deleted = [
            kwargs["File"]
            for _, _, kwargs in result.env.appliances[0].calls]
        # diag-log is a directory that DataPower does not let us remove
        self.assertEqual(["chkpoints://a.txt", "logtemp://a.txt"], deleted)
        self.assertEqual(2, len(glob.glob(
            os.path.join(self.out_dir, "dp1", "*", "d", "*", "a.txt"))))
        # The terminal gets the same table as the web GUI
        self.assertRegex(messages[0], r"chkpoints:/ +Cleaned")
        self.assertRegex(messages[0], r"ErrorReports +Cleaned")
        self.assertIn("ErrorReports", result.html())

    def test_clean_up_without_backups(self):
        result, _ = self.call(
            deployment.clean_up, export=True, backup_files=False,
            out_dir=self.out_dir)
        self.assertEqual([], os.listdir(self.out_dir))
        self.assertEqual(1, len(result.env.appliances[0].calls))

    def test_password_map_alias(self):
        for command, kwargs, method in (
                (deployment.add_password_map_alias,
                 {"password": "hunter2"}, "AddPasswordMap"),
                (deployment.del_password_map_alias, {}, "DeletePasswordMap")):
            result, messages = self.call(
                command, Domain="d", alias_name="alias", **kwargs)
            calls = result.env.appliances[0].calls
            self.assertEqual([method, "SaveConfig"], [c[0] for c in calls])
            self.assertEqual("alias", calls[0][2]["AliasName"])
            self.assertEqual("d", calls[1][2]["domain"])
            self.assertEqual(
                ["dp1", "\t\tOK", "\tSaving Configuration", "\t\tOK"],
                messages[:1] + messages[2:])
            self.assertIn("dp1-SaveConfig", result.html())
            self.assertEqual("", result.text())

            result, _ = self.call(
                command, Domain="d", alias_name="alias", save_config=False,
                **kwargs)
            self.assertEqual(
                [method], [c[0] for c in result.env.appliances[0].calls])

    def test_predeploy_calls_the_backups_commands(self):
        results = {
            name: Text(name + " text", history=name + " history;")
            for name in ("secure", "normal", "checkpoint")}
        with mock.patch(
                "mast.datapower.backups.get_secure_backup",
                return_value=results["secure"]) as secure, \
             mock.patch(
                "mast.datapower.backups.get_normal_backup",
                return_value=results["normal"]) as normal, \
             mock.patch(
                "mast.datapower.backups.set_checkpoint",
                return_value=results["checkpoint"]) as checkpoint, \
             mock.patch(
                "mast.datapower.deployment.deployment.system_call",
                return_value=(b"out", None)) as system_call:
            result, messages = self.call(
                deployment.predeploy, credentials=["user:pass"], Domain="d",
                comment="c", do_secure_backup=True, CryptoCertificate="cert",
                backup_all=False, predeploy_command="git pull",
                out_dir=self.out_dir)

        self.assertEqual("cert", secure.call_args[1]["CryptoCertificate"])
        self.assertTrue(secure.call_args[1]["remove"])
        self.assertEqual(["d", "default"], normal.call_args[1]["Domain"])
        self.assertEqual(["d", "default"], checkpoint.call_args[1]["Domain"])
        self.assertEqual("c", checkpoint.call_args[1]["comment"])
        for call in (secure, normal, checkpoint):
            self.assertNotIn("web", call.call_args[1])
        system_call.assert_called_once_with(command="git pull", shell=False)

        self.assertEqual(
            ["secure text", "normal text", "checkpoint text"], messages[:3])
        self.assertIn("Finished running pre-deploy command", messages[3])
        self.assertIn("<pre>normal text</pre>", result.html())
        self.assertIn("predeploy command", result.html())
        self.assertEqual(
            "secure history;normal history;checkpoint history;",
            result.history)

    def test_predeploy_secure_backup_needs_a_certificate(self):
        with self.assertRaises(ValueError) as caught:
            self.call(deployment.predeploy, do_secure_backup=True)
        self.assertIn("CryptoCertificate", str(caught.exception))
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
            cli.run(["predeploy", "-a", "dp1", "-d"])
        self.assertIn("CryptoCertificate must be specified", err.getvalue())

    def test_command_line(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.run(["get-file", "-a", "dp1", "-c", "user:pass", "-n",
                     "-D", "d", "-l", "local:///a.txt", "-o", self.out_dir])
            cli.run(["set-file", "-a", "dp1", "-f", "a.txt",
                     "-d", "local:/a.txt", "-D", "d", "--no-overwrite"])
            cli.run(["del-file", "-a", "dp1", "-D", "d", "-f", "local:/a.txt",
                     "-b", "-o", self.out_dir])
            cli.run(["clean-up", "-a", "dp1", "-D", "d", "-C", "-e", "-E",
                     "-r", "-l", "-L", "--no-backup-files",
                     "-o", self.out_dir])
            cli.run(["add-password-map-alias", "-a", "dp1", "-D", "d",
                     "-A", "alias", "-p", "secret", "--no-save-config"])
        text = out.getvalue()
        self.assertIn("Succeeded", text)
        self.assertRegex(text, r"logstore:/ +Cleaned")
        self.assertIn("Attempting to add Password Map Alias", text)

    def test_which_commands_take_a_web_parameter(self):
        self.assertEqual(10, len(cli.commands))
        legacy = {name for name, command in cli.commands.items()
                  if command.model is None}
        self.assertEqual(LEGACY, legacy)


if __name__ == "__main__":
    unittest.main()

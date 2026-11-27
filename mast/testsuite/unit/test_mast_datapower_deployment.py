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

from mast.testsuite.unit.registry_helpers import register
from xml.etree import ElementTree as etree

from unittest import mock

import mast.datapower.deployment as deployment
from mast.cli import Text, captured
from mast.datapower.deployment.deployment import cli
from mast.plugin_utils.results import Report, SeeDownload



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
            self.assertEqual([True, True], result.outcomes())

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
        register("dp1", "dp2")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.run(["get-file", "-a", "dp1", "-c", "user:pass", "-n",
                     "-D", "d", "-l", "local:///a.txt", "-o", self.out_dir])
            cli.run(["set-file", "-a", "dp1", "-f", "a.txt",
                     "-d", "local:/a.txt", "-D", "d", "--no-overwrite"])
            cli.run(["del-file", "-a", "dp1", "-D", "d", "-f", "local:/a.txt",
                     "-b", "-o", self.out_dir])
            cli.run(["add-password-map-alias", "-a", "dp1", "-D", "d",
                     "-A", "alias", "-p", "secret", "--no-save-config"])
        text = out.getvalue()
        self.assertIn("Succeeded", text)
        self.assertIn("Attempting to add Password Map Alias", text)

    def patch_steps(self, *names):
        """Replace the commands that deploy and postdeploy call. Return
        a list that gets (name, kwargs) for each call."""
        calls = []

        def fake(name):
            def command(**kwargs):
                calls.append((name, kwargs))
                return Text("did " + name)
            return command

        for target in names:
            patch = mock.patch(target, fake(target.rsplit(".", 1)[1]))
            patch.start()
            self.addCleanup(patch.stop)
        patch = mock.patch("mast.datapower.deployment.deployment.sleep")
        self.sleep = patch.start()
        self.addCleanup(patch.stop)
        FakeAppliance.log_info = lambda self, message: None
        self.addCleanup(delattr, FakeAppliance, "log_info")
        return calls

    SYSTEM = "mast.datapower.system."
    ONE = {"appliances": ["dp1"], "credentials": ["user:pass"],
           "no_check_hostname": False}

    def test_deploy(self):
        calls = self.patch_steps(
            self.SYSTEM + "quiesce_domain", self.SYSTEM + "quiesce_appliance",
            self.SYSTEM + "unquiesce_domain",
            self.SYSTEM + "unquiesce_appliance",
            self.SYSTEM + "objects_audit", "mast.datapower.developer._import")
        result, messages = self.call(
            deployment.deploy, Domain=["app"], file_in="export.zip",
            deployment_policy="policy", quiesce_appliance=True,
            quiesce_timeout=61, out_dir=self.out_dir)
        self.assertEqual(
            ["quiesce_domain", "quiesce_appliance", "_import",
             "unquiesce_domain", "unquiesce_appliance", "objects_audit"],
            [name for name, _ in calls])
        one = dict(self.ONE, timeout=180)
        self.assertEqual(
            dict(one, Domain=["app"], quiesce_timeout=61), calls[0][1])
        self.assertEqual(dict(one, quiesce_timeout=61), calls[1][1])
        self.assertEqual(
            dict(one, Domain=["app"], file_in="export.zip",
                 deployment_policy="policy", dry_run=False,
                 overwrite_files=True, overwrite_objects=True,
                 rewrite_local_ip=True, source_type="ZIP",
                 out_dir=self.out_dir), calls[2][1])
        self.assertEqual(dict(one, Domain=["app"]), calls[3][1])
        self.assertEqual(one, calls[4][1])
        self.assertEqual(dict(one, out_dir=self.out_dir), calls[5][1])
        self.assertEqual(
            [mock.call(61), mock.call(61)], self.sleep.call_args_list)
        self.assertIsInstance(result, Report)
        self.assertIn("did _import", result.html())
        self.assertEqual("did quiesce_domain", messages[0])

    def test_deploy_without_the_optional_steps(self):
        calls = self.patch_steps(
            self.SYSTEM + "quiesce_domain", self.SYSTEM + "quiesce_appliance",
            self.SYSTEM + "unquiesce_domain",
            self.SYSTEM + "unquiesce_appliance",
            self.SYSTEM + "objects_audit", "mast.datapower.developer._import")
        self.call(
            deployment.deploy, Domain="app", file_in="export.xml",
            format="XML", dry_run=True, quiesce_domain=False,
            object_audit=False)
        self.assertEqual(["_import"], [name for name, _ in calls])
        self.assertEqual("XML", calls[0][1]["source_type"])
        self.assertTrue(calls[0][1]["dry_run"])
        self.sleep.assert_not_called()

        with self.assertRaises(ValueError):
            deployment.deploy(appliances=["dp1"], Domain=["app"])
        with self.assertRaises(ValueError):
            deployment.deploy(
                appliances=["dp1"], file_in="export.tgz", format="TGZ")

    def test_postdeploy(self):
        calls = self.patch_steps(
            self.SYSTEM + "unquiesce_appliance",
            self.SYSTEM + "unquiesce_domain", self.SYSTEM + "save_config")
        with mock.patch(
                "mast.datapower.deployment.deployment.system_call",
                return_value=(b"done", None)) as system_call:
            result, messages = self.call(
                deployment.postdeploy, Domain="app",
                unquiesce_appliance=True, postdeploy_command="git push")
        one = dict(self.ONE, timeout=120)
        self.assertEqual(
            [("unquiesce_appliance", one),
             ("unquiesce_domain", dict(one, Domain="app")),
             ("save_config", dict(one, Domain="app"))], calls)
        system_call.assert_called_once_with(command="git push")
        html = result.html()
        self.assertIn("did save_config", html)
        self.assertIn("git push", html)
        self.assertIn("Finished saving the configuration", messages)

        del calls[:]
        with mock.patch(
                "mast.datapower.deployment.deployment.system_call") as system_call:
            self.call(deployment.postdeploy, Domain="app",
                      unquiesce_domain=False, save_config=False)
        self.assertEqual([], calls)
        system_call.assert_not_called()

    def git_deploy(self, **kwargs):
        """Call git_deploy with the repository and the plan replaced.
        Return (result, messages, config of the plan)."""
        # The package has a function with the same name as the module
        import sys
        module = sys.modules["mast.datapower.deployment.git_deploy"]
        seen = {}

        class FakePlan(object):
            def __init__(self, config):
                seen.update(config)
                self.environment = FakeEnvironment(["dp1"])

            def list_steps(self):
                module.print("App Domains")
                return {"App Domains": "dp1 -> app"}

            def execute(self):
                module.print(b"Step 0/0, import")
                return {"0-import": "Imported Files"}

        patch = mock.patch.object
        with patch(module, "parse_config",
                   return_value={"repo": "r"}) as parse_config, \
                patch(module, "_prepare_output_directories"), \
                patch(module, "_initialize_logging"), \
                patch(module, "_clone_pull_and_checkout"), \
                patch(module, "make_logger"), \
                patch(module, "Plan", FakePlan):
            result, messages = self.call(
                deployment.git_deploy, credentials=["user:pass"], **kwargs)
        seen["parse_config"] = parse_config.call_args[0]
        return result, messages, seen

    def test_git_deploy(self):
        result, messages, config = self.git_deploy(
            environment="prod", service="svc", commit="v1",
            quiesce_timeout=61, save_config=True)
        self.assertEqual(
            (["dp1"], ["user:pass"], "prod", "svc", True, 120),
            config["parse_config"])
        self.assertEqual("v1", config["commit"])
        self.assertEqual(61, config["quiesce_timeout"])
        self.assertTrue(config["save_config"])
        self.assertTrue(config["quiesce"])
        self.assertNotIn("web", config)
        # The steps are reported as they happen, bytes included
        self.assertEqual(["Step 0/0, import"], messages)
        self.assertIsInstance(result, Report)
        self.assertEqual("", result.text())
        self.assertIn("0-import", result.html())
        self.assertIn("Imported Files", result.html())
        self.assertIn("request: x", result.history)

    def test_git_deploy_dry_run(self):
        result, messages, config = self.git_deploy(
            environment="prod", service="svc", dry_run=True, quiesce=False)
        self.assertEqual(["App Domains"], messages)
        self.assertIn("dp1 -&gt; app", result.html())
        self.assertFalse(config["quiesce"])

    def test_no_command_takes_a_web_parameter(self):
        self.assertEqual(6, len(cli.commands))
        for command in cli.commands.values():
            self.assertIsNotNone(command.model, command.name)
            self.assertNotIn("web", command.model.model_fields)


if __name__ == "__main__":
    unittest.main()

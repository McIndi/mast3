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
Unit tests for the commands of mast.datapower.accounts, with the
appliances replaced by fakes. The integration tests run the same
commands against a real DataPower.
"""
import contextlib
import io
import unittest
from xml.etree import ElementTree as etree

from unittest import mock

import mast.datapower.accounts as accounts
from mast.cli import captured
from mast.datapower.accounts.accounts import cli
from mast.plugin_utils.results import ActionResults, CommonItems, Report


class FakeResponse(object):
    def __init__(self, ok=True):
        self.xml = etree.fromstring("<result>OK</result>")
        self.ok = ok

    def __bool__(self):
        return self.ok


class FakeAppliance(object):
    history = "request: x\nresponse: y"

    def __init__(self, hostname, names):
        self.hostname = hostname
        self.groups = self.users = self.fallback_users = names


class FakeEnvironment(object):
    def __init__(self, hostnames, *args, **kwargs):
        self.appliances = [
            FakeAppliance("dp1", ["admin", "only-dp1"]),
            FakeAppliance("dp2", ["admin", "only-dp2"]),
        ]
        self.actions = []

    def perform_action(self, action, **kwargs):
        self.actions.append((action, kwargs))
        return {"dp1": FakeResponse(), "dp2": FakeResponse(ok=False)}

    perform_async_action = perform_action


class TestAccountsCommands(unittest.TestCase):

    def setUp(self):
        patch = mock.patch(
            "mast.plugin_utils.params.Environment", FakeEnvironment)
        patch.start()
        self.addCleanup(patch.stop)

    def call(self, command, **kwargs):
        """Call command and return (result, progress messages)."""
        with captured() as messages:
            result = command(appliances=["dp1", "dp2"], **kwargs)
        return result, messages

    def test_lists_show_each_appliance_and_what_is_common(self):
        for command, title in (
                (accounts.list_groups, "Groups"),
                (accounts.list_users, "Users"),
                (accounts.list_rbm_fallback_users, "RBM Fallback Users")):
            result, _ = self.call(command)
            self.assertIsInstance(result, CommonItems)
            self.assertEqual(["admin"], result.common)
            self.assertEqual(
                "\ndp1\n===\n\tadmin\n\tonly-dp1\n"
                "\ndp2\n===\n\tadmin\n\tonly-dp2\n"
                "\nCommon\n======\n\tadmin",
                result.text())
            html = result.html()
            self.assertIn(title, html)
            self.assertIn("admin<br />only-dp1", html)
            self.assertIn("request: x", result.history)

    def test_actions(self):
        cases = (
            (accounts.add_group,
             {"name": "g", "access_policies": ["*/*/*?Access=r"]},
             "add_group", {"name": "g", "access_policies": ["*/*/*?Access=r"]}),
            (accounts.del_group, {"UserGroup": "g"},
             "del_group", {"group": "g"}),
            (accounts.add_user,
             {"username": "u", "password": "p", "group": "g"},
             "add_user",
             {"username": "u", "password": "p", "privileged": False,
              "user_group": "g", "supress_force_password_change": False}),
            (accounts.del_user, {"User": "u"},
             "remove_user", {"username": "u"}),
            (accounts.change_password, {"User": "u", "password": "p"},
             "change_password", {"username": "u", "password": "p"}),
            (accounts.force_change_password, {"User": "u"},
             "UserForcePasswordChange", {"User": "u"}),
            (accounts.add_rbm_fallback, {"User": "u"},
             "add_rbm_fallback", {"user": "u"}),
            (accounts.del_rbm_fallback, {"User": "u"},
             "del_rbm_fallback", {"username": "u"}),
        )
        for command, kwargs, action, expected in cases:
            with self.subTest(action=action, save_config=False):
                result, messages = self.call(command, **kwargs)
                self.assertIsInstance(result, Report)
                self.assertEqual([(action, expected)], result.env.actions)
                # One message, then the responses of the one step
                self.assertEqual(2, len(messages))
                self.assertIn("dp1\n===", messages[1])
                html = result.html()
                self.assertEqual(1, html.count("Succeeded"))
                self.assertEqual(1, html.count("Failed"))
                self.assertNotIn("save_config", html)

            with self.subTest(action=action, save_config=True):
                result, messages = self.call(
                    command, save_config=True, **kwargs)
                self.assertEqual(
                    [(action, expected), ("SaveConfig", {"domain": "default"})],
                    result.env.actions)
                # The terminal gets each step in the order it happens
                self.assertEqual(4, len(messages))
                self.assertIn("save config", messages[2])
                self.assertIn("dp1-save_config", result.html())
                self.assertEqual("", result.text())

    def test_a_response_can_be_none(self):
        # DataPower.add_static_route returns None without making a
        # request when the ethernet interface does not exist.
        result = ActionResults({"dp1": None})
        self.assertEqual("dp1\n===\nFailed", result.text())
        self.assertEqual(1, result.html().count("Failed"))

    def test_add_user_can_report_plain_boolean_responses(self):
        # DataPower.add_user returns False without making a request
        # when neither a group nor privileged access is supplied.
        with mock.patch.object(
                FakeEnvironment, "perform_async_action",
                return_value={"dp1": False, "dp2": True}):
            result, messages = self.call(accounts.add_user, username="u")
        self.assertIn("dp1\n===\nFailed", messages[1])
        self.assertIn("dp2\n===\nSucceeded", messages[1])
        html = result.html()
        self.assertEqual(1, html.count("Failed"))
        self.assertEqual(1, html.count("Succeeded"))

    def test_required_parameters(self):
        with self.assertRaises(ValueError):
            accounts.add_group(appliances=["dp1"])
        with self.assertRaises(ValueError):
            accounts.add_user(appliances=["dp1"], password="p")

    def test_passwords_are_not_in_repr(self):
        from mast.datapower.accounts.accounts import AddUser, ChangePassword
        for params in (
                AddUser(username="u", password="hunter2",
                        credentials=["admin:hunter2"]),
                ChangePassword(User="u", password="hunter2")):
            self.assertNotIn("hunter2", repr(params))

    def run_cli(self, *argv):
        """Run a command line and return its exit status."""
        try:
            cli.run(list(argv))
        except SystemExit as exc:
            return exc.code
        return 0

    def test_command_line(self):
        out = io.StringIO()
        status = {}
        with contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(io.StringIO()):
            status["add-user"] = self.run_cli(
                "add-user", "-a", "dp1", "-c", "user:pass", "-n",
                "-u", "new", "-p", "secret", "-P", "-S", "-s")
            status["add-group"] = self.run_cli(
                "add-group", "-a", "dp1", "-N", "g", "-A", "x", "-s")
            status["del-group"] = self.run_cli(
                "del-group", "-a", "dp1", "-U", "g")
            status["flush-aaa-cache"] = self.run_cli(
                "flush-aaa-cache", "-a", "dp1", "-D", "d", "-A", "p")
            status["list-users"] = self.run_cli(
                "list-users", "-a", "dp1")
        # The fake dp2 refuses every action, and that is the exit status
        self.assertEqual(status, {
            "add-user": 1, "add-group": 1, "del-group": 1,
            "flush-aaa-cache": 1, "list-users": 0})
        text = out.getvalue()
        # The message of a step comes before its responses
        self.assertLess(
            text.index("Attempting to add user new"), text.index("dp1\n==="))
        self.assertLess(
            text.index("dp1\n==="), text.index("Attempting to save config"))
        self.assertIn("OK", text)
        self.assertIn("\nCommon\n======\n\tadmin", text)

    def test_no_command_takes_a_web_parameter(self):
        self.assertEqual(11, len(cli.commands))
        for command in cli.commands.values():
            self.assertIsNotNone(command.model, command.name)
            self.assertNotIn("web", command.model.model_fields)


if __name__ == "__main__":
    unittest.main()

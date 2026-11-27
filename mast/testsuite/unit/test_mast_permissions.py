# -*- coding: utf-8 -*-
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
"""Unit tests for mast.permissions: paths, rules, permission sets,
groups, decisions, mast permissions and mast groups, and the API (step 5
of docs/design/mast-4.0.md)."""
import contextlib
import io
import json
import os
import unittest

from mast import permissions, registry
from mast.auth import users
from mast.jobs import db
from mast.permissions import InvalidRule, check_path, path_matches
from mast.testsuite.unit.test_mast_auth import fast_hashes
from mast.testsuite.unit.test_mast_jobs import PostgresMixin, SqliteCase
from mast.testsuite.unit.web_helpers import PASSWORD, WebCase

# The example of step 5 of the design
LAB_READER = [
    {"path": "mast/datapower/list-*", "allow": ["r", "x"],
     "scope": {"fleets": ["lab"]}},
    {"path": "mast/credentials/dp-reader", "allow": ["x"],
     "scope": {"fleets": ["lab"]}},
]


class TestPaths(unittest.TestCase):

    def test_good_and_bad_paths(self):
        for path in ("mast/datapower/get-status", "mast/datapower/list-*",
                     "mast/datapower/*", "mast/core/add-target",
                     "mast/credentials/dp-reader", "mast/targets/dp1",
                     "mast/fleets/*", "mast/linux/collect-crypto"):
            with self.subTest(path=path):
                self.assertEqual(check_path(path), path)
        for path in ("mast/*", "mast/datapower", "mast/datapower/*/x",
                     "mast/data*/get", "mast/data*power/get",
                     "mast/datapower/get*status", "other/datapower/get",
                     "mast/DataPower/get", "mast/datapower/", "mast//x",
                     "mast/datapower/**", "mast/datapower/a b", None):
            with self.subTest(path=path):
                with self.assertRaises(InvalidRule):
                    check_path(path)

    def test_a_wildcard_matches_within_the_last_segment(self):
        for pattern, path, matches in (
                ("mast/datapower/list-*", "mast/datapower/list-domains",
                 True),
                ("mast/datapower/list-*", "mast/datapower/get-status", False),
                ("mast/datapower/*", "mast/datapower/save", True),
                ("mast/datapower/*", "mast/linux/save", False),
                ("mast/datapower/get-*", "mast/datapower/get-file", True),
                ("mast/datapower/save", "mast/datapower/save", True),
                ("mast/datapower/save", "mast/datapower/save-all", False)):
            with self.subTest(pattern=pattern, path=path):
                self.assertEqual(path_matches(pattern, path), matches)


class PermissionsCase(SqliteCase):

    def setUp(self):
        super().setUp()
        fast_hashes(self)
        registry.add("dp1", "datapower")
        registry.add("dp2", "datapower")
        registry.add("dp3", "datapower")
        registry.fleet_add("lab", "datapower", members=["dp1", "dp2"])
        users.add("alice", PASSWORD, "viewer")

    def user_in(self, set_name, rules, user="alice"):
        permissions.add_set(set_name, rules)
        permissions.add_group(set_name + "-group", sets=[set_name])
        permissions.add_member(set_name + "-group", user)


class TestRules(PermissionsCase):

    def test_a_rule_is_checked_and_its_scope_has_ids(self):
        found = permissions.add_set("lab-reader", LAB_READER,
                                    "Read the lab")
        rule = found.rules[0]
        self.assertEqual((rule.allow, rule.deny), (("r", "x"), ()))
        lab = registry.fleet_get("lab")
        self.assertEqual(rule.scope, {"fleets": [lab.id]})
        # Shown with names
        self.assertEqual(found.json()["rules"][0]["scope"],
                         {"fleets": ["lab"]})
        for bad in ({"path": "mast/datapower/*"},
                    {"path": "mast/datapower/*", "allow": ["z"]},
                    {"path": "mast/datapower/*", "allow": ["r"],
                     "scope": {"fleets": ["nowhere"]}},
                    {"path": "mast/datapower/*", "allow": ["r"],
                     "scope": {"all": "yes"}},
                    {"path": "mast/datapower/*", "allow": ["r"],
                     "scope": {"targets": [], "fleets": ["lab"]}},
                    {"path": "mast/datapower/*", "allow": ["r"],
                     "colour": "red"},
                    "mast/datapower/*"):
            with self.subTest(rule=bad):
                with self.assertRaises(InvalidRule):
                    permissions.check_rule(bad)

    def test_sets_rules_and_groups(self):
        permissions.add_set("lab-reader", LAB_READER[:1])
        found = permissions.add_rule("lab-reader", LAB_READER[1])
        self.assertEqual(len(found.rules), 2)
        found = permissions.remove_rule("lab-reader", 1)
        self.assertEqual([r.path for r in found.rules],
                         ["mast/credentials/dp-reader"])
        with self.assertRaises(InvalidRule):
            permissions.remove_rule("lab-reader", 5)
        with self.assertRaises(permissions.NameTaken):
            permissions.add_set("lab-reader")
        group = permissions.add_group("lab-ops", "The lab", ["lab-reader"])
        group = permissions.add_member("lab-ops", "alice")
        self.assertEqual((group.sets, group.members),
                         (("lab-reader",), ("alice",)))
        self.assertEqual(permissions.get_set("lab-reader").groups,
                         ("lab-ops",))
        self.assertIn("lab-ops", permissions.groups_of("alice"))
        permissions.revoke("lab-ops", "lab-reader")
        permissions.remove_member("lab-ops", "alice")
        self.assertEqual(permissions.get_group("lab-ops").members, ())
        permissions.remove_set("lab-reader")
        permissions.remove_group("lab-ops")
        for change in (lambda: permissions.get_set("lab-reader"),
                       lambda: permissions.add_member("viewer", "nobody"),
                       lambda: permissions.grant("viewer", "nothing")):
            with self.assertRaises(permissions.UnknownName):
                change()

    def test_the_seeded_groups_follow_the_role(self):
        self.assertEqual([g.name for g in permissions.list_groups()],
                         ["admin", "datapower-operator", "datapower-reader",
                          "operator", "viewer"])
        self.assertEqual(permissions.groups_of("alice"), ["viewer"])
        users.set_role("alice", "admin")
        self.assertEqual(permissions.groups_of("alice"),
                         ["admin", "operator", "viewer"])
        users.set_role("alice", "operator")
        self.assertEqual(permissions.groups_of("alice"),
                         ["operator", "viewer"])
        # Installing a plugin grants nothing: no seeded set names one,
        # and the groups of the plugin have no members
        for name in ("viewer", "operator", "admin"):
            for rule in permissions.get_set(name).rules:
                self.assertNotIn("datapower", rule.path)
        for name in ("datapower-reader", "datapower-operator"):
            self.assertEqual(permissions.get_group(name).members, ())


class TestDecisions(PermissionsCase):

    def test_the_example_of_the_design(self):
        self.user_in("lab-reader", LAB_READER)
        cases = (
            ("datapower.list-domains", ["dp1"], True),
            ("datapower.list-domains", ["lab"], True),
            # Not in the fleet of the scope
            ("datapower.list-domains", ["dp3"], False),
            # A command that changes needs w too, and not list-*
            ("datapower.save", ["dp1"], False),
            ("datapower.get-status", ["dp1"], False))
        for command, targets, allowed in cases:
            with self.subTest(command=command, targets=targets):
                decisions = permissions.check_command("alice", command,
                                                      targets)
                self.assertEqual(all(d.allowed for d in decisions), allowed,
                                 [d.reason for d in decisions])
        decision = permissions.decide("alice", "x",
                                      "mast/credentials/dp-reader", "dp2")
        self.assertTrue(decision.allowed)
        self.assertEqual([(m.set, m.groups, m.index)
                          for m in decision.allowed_by],
                         [("lab-reader", ("lab-reader-group",), 1)])

    def test_a_change_needs_w_and_x(self):
        self.user_in("savers", [{"path": "mast/datapower/save",
                                 "allow": ["x"], "scope": {"all": True}}])
        decisions = permissions.check_command("alice", "datapower.save",
                                              ["dp1"])
        self.assertEqual([(d.capability, d.allowed) for d in decisions],
                         [("x", True), ("w", False)])
        permissions.add_rule("savers", {"path": "mast/datapower/save",
                                        "allow": ["w"],
                                        "scope": {"targets": ["dp1"]}})
        self.assertTrue(all(d.allowed for d in permissions.check_command(
            "alice", "datapower.save", ["dp1"])))
        self.assertFalse(all(d.allowed for d in permissions.check_command(
            "alice", "datapower.save", ["dp2"])))

    def test_a_denial_wins_and_the_union_of_the_sets(self):
        self.user_in("all-datapower", [{"path": "mast/datapower/*",
                                        "allow": ["r", "w", "x"],
                                        "scope": {"all": True}}])
        self.user_in("no-reboot", [{"path": "mast/datapower/reboot-*",
                                    "deny": ["x"],
                                    "scope": {"fleets": ["lab"]}}])
        self.assertTrue(permissions.decide(
            "alice", "x", "mast/datapower/reboot-appliance", "dp3").allowed)
        decision = permissions.decide(
            "alice", "x", "mast/datapower/reboot-appliance", "dp1")
        self.assertFalse(decision.allowed)
        self.assertEqual([m.set for m in decision.denied_by], ["no-reboot"])
        self.assertIn("denied", decision.reason)
        # It denies only what it names
        self.assertTrue(permissions.decide(
            "alice", "r", "mast/datapower/reboot-appliance", "dp1").allowed)

    def test_scopes(self):
        self.user_in("unscoped", [{"path": "mast/datapower/get-status",
                                   "allow": ["r", "x"]}])
        # Without a scope: the command, but no target
        self.assertTrue(permissions.decide(
            "alice", "x", "mast/datapower/get-status").allowed)
        self.assertFalse(permissions.decide(
            "alice", "x", "mast/datapower/get-status", "dp1").allowed)
        self.user_in("dp2-only", [{"path": "mast/datapower/get-status",
                                   "allow": ["x"],
                                   "scope": {"targets": ["dp2"]}}])
        self.assertTrue(permissions.decide(
            "alice", "x", "mast/datapower/get-status", "dp2").allowed)
        self.assertFalse(permissions.decide(
            "alice", "x", "mast/datapower/get-status", "dp1").allowed)
        # A renamed target stays in scope: the scope has its id
        registry.rename("dp2", "dp-two")
        self.assertTrue(permissions.decide(
            "alice", "x", "mast/datapower/get-status", "dp-two").allowed)
        self.assertEqual(permissions.get_set("dp2-only").json()["rules"][0]
                         ["scope"], {"targets": ["dp-two"]})
        # A denial without a scope applies to every target
        self.user_in("never", [{"path": "mast/datapower/get-status",
                                "deny": ["x"]}])
        self.assertFalse(permissions.decide(
            "alice", "x", "mast/datapower/get-status", "dp-two").allowed)

    def test_bad_questions(self):
        for args in (("q", "mast/datapower/save"),
                     ("x", "mast/datapower/*"),
                     ("x", "nothing")):
            with self.subTest(args=args):
                with self.assertRaises(InvalidRule):
                    permissions.decide("alice", *args)
        with self.assertRaises(permissions.UnknownName):
            permissions.decide("nobody", "x", "mast/datapower/save")
        with self.assertRaises(permissions.UnknownName):
            permissions.decide("alice", "x", "mast/datapower/save", "dp9")

    def test_the_admin_set(self):
        users.add("root", PASSWORD, "admin")
        for path, target in (("mast/core/add-target", None),
                             ("mast/credentials/anything", None),
                             ("mast/targets/dp1", "dp1")):
            with self.subTest(path=path):
                self.assertTrue(permissions.decide(
                    "root", "w", path, target).allowed)
        # A plugin command is not core
        self.assertFalse(permissions.decide(
            "root", "x", "mast/datapower/save", "dp1").allowed)


class TestPluginGroups(PermissionsCase):
    """A plugin defines groups with the rules of their sets; MAST makes
    them without members, and an admin puts users in them."""

    def test_the_groups_of_datapower(self):
        reader = permissions.get_group("datapower-reader")
        self.assertEqual((reader.sets, reader.members),
                         (("datapower-reader",), ()))
        permissions.add_member("datapower-reader", "alice")
        for command, allowed in (("datapower.list-domains", True),
                                 ("datapower.get-file", True),
                                 ("datapower.save", False),
                                 ("datapower.get-pmr-info", False)):
            with self.subTest(command=command):
                self.assertEqual(all(d.allowed for d in
                                     permissions.check_command(
                                         "alice", command, ["lab"])),
                                 allowed)
        permissions.add_member("datapower-operator", "alice")
        self.assertTrue(all(d.allowed for d in permissions.check_command(
            "alice", "datapower.save", ["dp3"])))

    def test_the_reader_has_exactly_the_commands_that_read(self):
        from mast.datapower.plugin import READ_COMMANDS
        from mast.jobs import registry as commands
        self.assertEqual(
            sorted(READ_COMMANDS),
            sorted(c.name for c in commands.commands("datapower")
                   if c.effect == "read"))

    def test_what_an_admin_does_stays(self):
        # Removed: not made again
        permissions.remove_group("datapower-reader")
        permissions.remove_set("datapower-reader")
        permissions._synced.clear()
        self.assertEqual(permissions.sync_plugin_groups(), [])
        with self.assertRaises(permissions.UnknownName):
            permissions.get_group("datapower-reader")
        # Changed: a newer version of the plugin does not undo it
        permissions.update_set("datapower-operator", [
            {"path": "mast/datapower/*", "allow": ["r", "x"],
             "scope": {"all": True}}], principal="alice")
        permissions._synced.clear()
        permissions.sync_plugin_groups()
        self.assertEqual(permissions.get_set("datapower-operator")
                         .rules[0].allow, ("r", "x"))

    def test_a_newer_version_of_the_plugin(self):
        from mast.plugins import Group
        from mast.testsuite.unit.test_mast_commands import (
            PluginsCase, fake_plugin)
        first, name = fake_plugin("acme", ("look", "reboot"))
        import sys
        sys.modules[name].manifest = permissions_manifest(
            sys.modules[name].manifest, Group(
                "acme-users", "Look",
                ({"path": "mast/acme/look", "allow": ["x"]},)))
        PluginsCase.use(self, first)
        self.assertIn("acme-users", [g.name for g in
                                     permissions.list_groups()])
        # The plugin grants more in its next version
        sys.modules[name].manifest = permissions_manifest(
            sys.modules[name].manifest, Group(
                "acme-users", "Look and reboot",
                ({"path": "mast/acme/*", "allow": ["x"]},)))
        from mast import plugins
        plugins.reset()
        found = permissions.get_set("acme-users")
        self.assertEqual((found.description, found.rules[0].path),
                         ("Look and reboot", "mast/acme/*"))

    keep_datapower = True

    def test_bad_groups_leave_the_plugin_out(self):
        from mast import plugins
        from mast.plugins import Group
        from mast.testsuite.unit.test_mast_commands import (
            PluginsCase, fake_plugin)
        import sys
        cases = {
            "acme": Group("other-users", "x",
                          ({"path": "mast/acme/look", "allow": ["x"]},)),
            "bolt": Group("bolt-users", "x",
                          ({"path": "mast/datapower/save", "allow": ["x"]},)),
            "coil": Group("coil-users", "x",
                          ({"path": "mast/coil/look", "allow": ["x"],
                            "scope": {"fleets": ["lab"]}},)),
            "dial": Group("dial-users", "x",
                          ({"path": "mast/dial/look", "allow": ["q"]},)),
        }
        entry_points = []
        for provider, group in cases.items():
            entry_point, name = fake_plugin(provider)
            sys.modules[name].manifest = permissions_manifest(
                sys.modules[name].manifest, group)
            entry_points.append(entry_point)
        PluginsCase.use(self, *entry_points)
        for provider in cases:
            with self.subTest(provider=provider):
                self.assertEqual(plugins.failure(provider).problem,
                                 plugins.INVALID)


def permissions_manifest(manifest, group):
    """The manifest with the groups (group,)."""
    import dataclasses
    return dataclasses.replace(manifest, groups=(group,))


class TestDecisionsPostgres(PostgresMixin, TestDecisions):
    pass


class TestCommandLine(PermissionsCase):

    def run_cli(self, *args):
        from mast.permissions.cli import groups_cli, permissions_cli
        cli = permissions_cli
        if args and args[0] == "groups":
            cli, args = groups_cli, args[1:]
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                cli.run(list(args))
            except SystemExit as exc:
                code = exc.code or 0
        return code, out.getvalue(), err.getvalue()

    def test_sets_groups_and_checks(self):
        path = os.path.join(self.tmp, "lab-reader.json")
        with open(path, "w") as fout:
            json.dump({"description": "Read the lab", "rules": LAB_READER},
                      fout)
        code, out, err = self.run_cli("add", "-n", "lab-reader", "-f", path)
        self.assertEqual(code, 0, err)
        self.assertIn("1. mast/datapower/list-*, allow rx, fleets: lab", out)
        code, out, err = self.run_cli(
            "add-rule", "-n", "lab-reader", "-p", "mast/datapower/save",
            "--deny", "x", "--all-targets")
        self.assertIn("3. mast/datapower/save, deny x, all targets", out)
        self.run_cli("groups", "add", "-n", "lab-ops", "-s", "lab-reader")
        code, out, _ = self.run_cli("groups", "add-member", "-n", "lab-ops",
                                    "-u", "alice")
        self.assertIn("members: alice", out)
        code, out, _ = self.run_cli("check", "-u", "alice", "--command",
                                    "datapower.list-domains", "-a", "lab")
        self.assertIn("allowed: alice is allowed x mast/datapower/"
                      "list-domains on dp1 by mast/datapower/list-* (rule 1, "
                      "lab-reader of lab-ops)", out)
        self.assertIn("alice may.", out)
        code, out, _ = self.run_cli("check", "-u", "alice", "--command",
                                    "datapower.save", "-a", "dp1")
        self.assertIn("denied: alice is denied x mast/datapower/save on dp1",
                      out)
        self.assertIn("alice may not.", out)
        code, out, _ = self.run_cli("check", "-u", "alice", "-p",
                                    "mast/core/list-jobs", "-c", "x")
        self.assertIn("alice may.", out)
        code, out, _ = self.run_cli("effective", "-u", "alice")
        self.assertRegex(out, r"lab-reader +lab-ops +1 +mast/datapower/list-")
        self.assertRegex(out, r"viewer +viewer +1 +mast/core/list-jobs")
        for args, message in (
                (("add-rule", "-n", "lab-reader", "-p",
                  "mast/datapower/x*y", "--allow", "r"),
                 "may only end the path"),
                (("check", "-u", "alice"), "Give --path or --command"),
                (("show", "-n", "nothing"), "no permission set nothing")):
            with self.subTest(args=args):
                code, _, err = self.run_cli(*args)
                self.assertEqual(code, 1)
                self.assertIn(message, err)
        code, out, _ = self.run_cli("list")
        self.assertRegex(out, r"lab-reader +3 +lab-ops +Read the lab")


class TestApi(WebCase):

    def call(self, method, path, body=None, client=None):
        from mast.web import security
        return (client or self.client).open(
            "/api/v1" + path, method=method, json=body,
            headers={security.CSRF_HEADER: self.token(client)})

    def test_an_admin_manages_and_checks(self):
        registry.add("dp1", "datapower")
        registry.fleet_add("lab", "datapower", members=["dp1"])
        self.login("admin")
        answer = self.call("POST", "/permission-sets", {
            "name": "lab-reader", "rules": LAB_READER})
        self.assertEqual(answer.status_code, 201, answer.get_json())
        self.assertEqual(answer.get_json()["rules"][0]["scope"],
                         {"fleets": ["lab"]})
        self.assertEqual(self.call("POST", "/permission-sets", {
            "name": "bad", "rules": [{"path": "mast/*"}]}).status_code, 422)
        self.assertEqual(self.call("POST", "/permission-sets", {
            "name": "lab-reader"}).status_code, 409)
        answer = self.call("POST", "/groups", {"name": "lab-ops",
                                               "sets": ["lab-reader"]})
        self.assertEqual(answer.status_code, 201)
        answer = self.call("PATCH", "/groups/lab-ops",
                           {"members": ["operator"]})
        self.assertEqual(answer.get_json()["members"], ["operator"])
        answer = self.call("POST", "/permissions/check", {
            "user": "operator", "command": "datapower.list-domains",
            "targets": ["lab"]})
        self.assertTrue(answer.get_json()["allowed"])
        self.assertEqual(answer.get_json()["decisions"][0]["allowed_by"][0]
                         ["set"], "lab-reader")
        answer = self.get_json = self.call(
            "GET", "/users/operator/permissions").get_json()
        self.assertIn("lab-ops", answer["groups"])
        self.assertEqual(self.call("GET", "/permission-sets/nothing")
                         .status_code, 404)
        self.assertEqual(self.call("DELETE", "/groups/lab-ops").status_code,
                         204)
        self.assertEqual(self.call("DELETE", "/permission-sets/lab-reader")
                         .status_code, 204)

    def test_only_an_admin(self):
        for role in ("operator", "viewer"):
            client = self.app.test_client()
            self.login(role, client=client)
            for method, path in (("GET", "/permission-sets"),
                                 ("POST", "/permission-sets"),
                                 ("GET", "/groups"),
                                 ("PATCH", "/groups/viewer"),
                                 ("POST", "/permissions/check"),
                                 ("GET", "/users/admin/permissions")):
                with self.subTest(role=role, path=path):
                    self.assertEqual(self.call(method, path, {},
                                               client=client).status_code,
                                     403)


class TestMigration(SqliteCase):
    """4004 makes the seeded sets and groups, with the users of each
    role in them, and goes down again."""

    def test_down_and_up(self):
        fast_hashes(self)
        users.add("vera", PASSWORD, "viewer")
        users.add("adam", PASSWORD, "admin")
        db.downgrade("4003")
        self.assertEqual(db.revision(), "4003")
        db.configure(self.url)
        # The tables are upgraded when they are used
        self.assertEqual(permissions.groups_of("vera"), ["viewer"])
        self.assertEqual(db.revision(), db.HEAD)
        self.assertEqual(permissions.groups_of("adam"),
                         ["admin", "operator", "viewer"])
        self.assertEqual(permissions.get_set("operator").rules[0].path,
                         "mast/core/cancel-own-job")


class TestMigrationPostgres(PostgresMixin, TestMigration):
    pass


if __name__ == "__main__":
    unittest.main()

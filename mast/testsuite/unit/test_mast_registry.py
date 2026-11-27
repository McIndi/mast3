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
"""
Unit tests for mast.registry: the appliances and environments in the
database, the import of the configuration files of MAST 3.15.2 and the
export back to them, mast targets, and the API of the registry.
"""
import configparser
import contextlib
import io
import os
import unittest
from unittest import mock

from mast import registry
from mast.registry import files
from mast.testsuite.unit.test_mast_jobs import PostgresMixin, SqliteCase
from mast.testsuite.unit.web_helpers import WebCase

HOSTS = """\
# The aliases
[hosts]
dp1: 10.0.0.1
dp2: 10.0.0.2
DP-Upper: 10.0.0.9
"""

ENVIRONMENTS = """\
[lab]
appliances: dp1 dp2

[prod]
appliances: dp2 dp3.example.com 10.0.0.4
"""

APPLIANCES = """\
[global]
soma_port: 5550
retry_interval: 0

[dp1]
soma_port: 5551
soma_spec_file: etc/v10.6.0.11-C.xml

[dp3.example.com]
ssh_port: 2222
soma_scheme: http

[lonely.example.com]
web_port: 9443
"""


class TestRegistry(SqliteCase):

    def test_add_get_and_defaults(self):
        found = registry.add("dp1", "10.0.0.1", soma_port="5551")
        self.assertTrue(found.registered)
        self.assertEqual(found.address, "10.0.0.1")
        self.assertEqual(found.soma_port, 5551)
        self.assertIsNone(found.web_port)
        self.assertEqual(found.tls_mode, "ca")
        self.assertFalse(found.poll)
        self.assertEqual(registry.get("dp1"), found)
        self.assertEqual(registry.add("dp2").address, "dp2")
        self.assertIsNone(registry.by_name("nothing"))
        with self.assertRaises(registry.UnknownName):
            registry.get("nothing")

    def test_refusals(self):
        registry.add("dp1")
        registry.env_add("lab")
        cases = (
            (registry.NameTaken, lambda: registry.add("dp1")),
            (registry.NameTaken, lambda: registry.add("lab")),
            (registry.NameTaken, lambda: registry.env_add("dp1")),
            (registry.InvalidValue, lambda: registry.add("a b")),
            (registry.InvalidValue, lambda: registry.add("x", "not an address!")),
            (registry.InvalidValue, lambda: registry.add("x", soma_port=70000)),
            (registry.InvalidValue, lambda: registry.add("x", soma_port="many")),
            (registry.InvalidValue, lambda: registry.add("x", tls_mode="maybe")),
            (registry.InvalidValue, lambda: registry.add("x", soma_scheme="ftp")),
            (registry.InvalidValue, lambda: registry.add("x", colour="red")),
            (registry.UnknownName, lambda: registry.update("nothing", poll=True)),
            (registry.UnknownName, lambda: registry.env_add("e", members=["nothing"])),
        )
        for error, change in cases:
            with self.subTest(error=error.__name__):
                with self.assertRaises(error):
                    change()
        self.assertEqual([a.name for a in registry.list_appliances()], ["dp1"])

    def test_update_rename_and_remove(self):
        registry.add("dp1", soma_port=5551, notes="old")
        registry.env_add("lab", members=["dp1"])
        found = registry.update("dp1", soma_port=None, web_port=9091,
                                poll=True, notes=None, tls_mode="none")
        self.assertIsNone(found.soma_port)
        self.assertEqual(found.web_port, 9091)
        self.assertTrue(found.poll)
        self.assertEqual(found.notes, "")
        self.assertEqual(found.tls_mode, "none")
        renamed = registry.rename("dp1", "dp-one")
        self.assertEqual(renamed.environments, ["lab"])
        self.assertEqual(registry.members("lab"), ["dp-one"])
        registry.remove("dp-one")
        self.assertEqual(registry.members("lab"), [])
        self.assertEqual(registry.list_appliances(), [])

    def test_pins_are_set_apart(self):
        registry.add("dp1")
        with self.assertRaises(registry.InvalidValue):
            registry.update("dp1", pin_sha256="ab" * 32)
        self.assertEqual(registry.set_pin("dp1", pin_sha256="ab" * 32)
                         .pin_sha256, "ab" * 32)

    def test_pin_mode_needs_a_pin(self):
        with self.assertRaises(registry.InvalidValue):
            registry.add("dp1", tls_mode="pin")
        registry.add("dp1")
        with self.assertRaises(registry.InvalidValue):
            registry.update("dp1", tls_mode="pin")
        self.assertEqual(registry.get("dp1").tls_mode, "ca")

    def test_pin_tls_and_ssh(self):
        registry.add("dp1")
        for bad in ("AB" * 32, "ab" * 31, "zz" * 32, None):
            with self.subTest(bad=bad), self.assertRaises(registry.InvalidValue):
                registry.pin_tls("dp1", bad, "os:me")
        registry.flag_mismatch("dp1", "tls", "cd" * 32)
        registry.flag_mismatch("dp1", "ssh", "SHA256:x")
        registry.flag_mismatch("no-such", "tls", "cd" * 32)
        flagged = registry.get("dp1")
        self.assertEqual(flagged.tls_mismatch, "cd" * 32)
        self.assertIsNotNone(flagged.tls_mismatch_at)
        found = registry.pin_tls("dp1", "ab" * 32, "os:me")
        self.assertEqual((found.tls_mode, found.pin_sha256, found.pinned_by),
                         ("pin", "ab" * 32, "os:me"))
        self.assertIsNone(found.tls_mismatch)
        self.assertEqual(found.ssh_mismatch, "SHA256:x")
        found = registry.pin_ssh("dp1", " ssh-ed25519 AAAA ", "user:admin")
        self.assertEqual((found.ssh_host_key, found.ssh_pinned_by),
                         ("ssh-ed25519 AAAA", "user:admin"))
        self.assertIsNone(found.ssh_mismatch)
        with self.assertRaises(registry.InvalidValue):
            registry.pin_ssh("dp1", "  ", "os:me")
        found = registry.unpin("dp1", tls=True)
        self.assertEqual((found.tls_mode, found.pin_sha256), ("ca", None))
        self.assertEqual(found.ssh_host_key, "ssh-ed25519 AAAA")
        found = registry.unpin("dp1", ssh=True)
        self.assertIsNone(found.ssh_host_key)
        with self.assertRaises(registry.UnknownName):
            registry.pin_tls("no-such", "ab" * 32, "os:me")

    def test_ca_bundle_must_be_pem(self):
        with self.assertRaises(registry.InvalidValue):
            registry.add("dp1", ca_bundle="not a certificate")
        with self.assertRaises(registry.InvalidValue):
            registry.add("dp1", ca_bundle="-----BEGIN CERTIFICATE-----\nAAAA\n"
                                          "-----END CERTIFICATE-----\n")

    def test_environments(self):
        for name in ("dp1", "dp2", "dp3"):
            registry.add(name)
        lab = registry.env_add("lab", "The lab", members=["dp3", "dp1", "dp3"])
        self.assertEqual(lab.members, ["dp3", "dp1"])
        self.assertFalse(lab.production)
        self.assertEqual(registry.env_update("lab", production=True)
                         .production, True)
        self.assertEqual(registry.env_set_members("lab", ["dp2"]).members,
                         ["dp2"])
        registry.env_add("prod", members=["dp2", "dp3"])
        self.assertEqual(registry.which_environments("dp2"), ["lab", "prod"])
        self.assertEqual(registry.get("dp2").environments, ["lab", "prod"])
        self.assertEqual([e.name for e in registry.env_list()],
                         ["lab", "prod"])
        self.assertTrue(registry.is_environment("lab"))
        self.assertFalse(registry.is_environment("dp1"))
        registry.env_remove("lab")
        self.assertIsNone(registry.members("lab"))
        self.assertIsNotNone(registry.by_name("dp2"))

    def test_resolve(self):
        registry.add("dp1", "10.0.0.1")
        registry.add("dp2", "10.0.0.2")
        registry.env_add("lab", members=["dp2", "dp1"])
        found = registry.resolve(
            ["lab", "dp1", "other.example.com", "10.1.2.3", "[::1]",
             "fe80::1", ""])
        self.assertEqual([a.name for a in found], [
            "dp2", "dp1", "other.example.com", "10.1.2.3", "[::1]",
            "fe80::1"])
        self.assertEqual([a.registered for a in found],
                         [True, True, False, False, False, False])
        self.assertEqual(found[4].address, "::1")
        self.assertEqual([a.name for a in registry.resolve("dp1")], ["dp1"])
        with self.assertRaises(registry.UnknownName):
            registry.resolve(["not a host!"])

    def test_by_address(self):
        registry.add("dp1", "Box.Example.com")
        self.assertEqual(registry.by_address("box.example.com").name, "dp1")
        self.assertIsNone(registry.by_address("other"))


class TestRegistryPostgres(PostgresMixin, TestRegistry):
    pass


class FilesCase(SqliteCase):

    def write(self, name, text):
        path = os.path.join(self.tmp, name)
        with open(path, "w") as fout:
            fout.write(text)
        return path

    def setUp(self):
        super().setUp()
        self.hosts = self.write("hosts.conf", HOSTS)
        self.environments = self.write("environments.conf", ENVIRONMENTS)
        self.appliances = self.write("appliances.conf", APPLIANCES)

    def import_(self, **kwargs):
        return files.import_files(self.hosts, self.environments,
                                  self.appliances, **kwargs)


class TestImport(FilesCase):

    def test_import(self):
        lines = self.import_()
        found = {a.name: a for a in registry.list_appliances()}
        # Every alias, every name of an environment, every section
        self.assertEqual(sorted(found), [
            "10.0.0.4", "dp-upper", "dp1", "dp2", "dp3.example.com",
            "lonely.example.com"])
        self.assertEqual(found["dp1"].address, "10.0.0.1")
        self.assertEqual(found["dp1"].soma_port, 5551)
        self.assertEqual(found["dp1"].soma_spec_file, "etc/v10.6.0.11-C.xml")
        self.assertEqual(found["dp3.example.com"].ssh_port, 2222)
        self.assertEqual(found["dp3.example.com"].soma_scheme, "http")
        self.assertEqual(found["lonely.example.com"].web_port, 9443)
        self.assertEqual(found["10.0.0.4"].address, "10.0.0.4")
        # A host in two environments is one appliance in both
        self.assertEqual(found["dp2"].environments, ["lab", "prod"])
        self.assertEqual(registry.members("prod"),
                         ["dp2", "dp3.example.com", "10.0.0.4"])
        self.assertIn("Added the appliance dp1 (10.0.0.1), soma_port 5551, "
                      "soma_spec_file etc/v10.6.0.11-C.xml", lines)
        # The files are renamed; appliances.conf keeps [global]
        for path in (self.hosts, self.environments, self.appliances):
            self.assertTrue(os.path.exists(path + ".imported"))
        self.assertFalse(os.path.exists(self.hosts))
        self.assertFalse(os.path.exists(self.environments))
        kept = configparser.ConfigParser()
        kept.read(self.appliances)
        self.assertEqual(kept.sections(), ["global"])
        self.assertEqual(kept.get("global", "soma_port"), "5550")

    def test_a_dry_run_changes_nothing(self):
        lines = self.import_(dry_run=True)
        self.assertIn("Would add the environment lab: dp1 dp2", lines)
        self.assertEqual(registry.list_appliances(), [])
        self.assertEqual(registry.env_list(), [])
        self.assertTrue(os.path.exists(self.hosts))

    def test_registered_names_are_kept(self):
        registry.add("dp1", "192.168.0.1")
        registry.env_add("lab", members=["dp1"])
        lines = self.import_()
        self.assertIn("Kept the appliance dp1: it is registered already.",
                      lines)
        self.assertIn("Kept the environment lab: it is registered already.",
                      lines)
        self.assertEqual(registry.get("dp1").address, "192.168.0.1")
        self.assertEqual(registry.members("lab"), ["dp1"])

    def test_appliances_conf_without_single_appliances_stays(self):
        self.write("appliances.conf", "[global]\nsoma_port: 5550\n")
        self.import_()
        self.assertTrue(os.path.exists(self.appliances))
        self.assertFalse(os.path.exists(self.appliances + ".imported"))

    def test_nothing_to_import(self):
        lines = files.import_files(*(os.path.join(self.tmp, n) for n in
                                     ("a.conf", "b.conf", "c.conf")))
        self.assertEqual(lines, ["There was nothing to import."])

    def test_import_then_export_gives_the_same_files(self):
        before = {name: self.parse(path) for name, path in (
            ("hosts.conf", self.hosts),
            ("environments.conf", self.environments))}
        settings_before = self.parse(self.appliances)
        with mock.patch.object(files, "_local",
                               lambda name: os.path.join(self.tmp, name)):
            self.import_()
            out = os.path.join(self.tmp, "out")
            files.export_files(out)
        after_hosts = self.parse(os.path.join(out, "hosts.conf"))
        # A name of an environment that is an address is not an alias
        self.assertEqual(after_hosts, before["hosts.conf"])
        self.assertEqual(self.parse(os.path.join(out, "environments.conf")),
                         before["environments.conf"])
        # [global] comes from etc/local/appliances.conf, which the import
        # rewrote with only that section
        self.assertEqual(self.parse(os.path.join(out, "appliances.conf")),
                         settings_before)

    def parse(self, path):
        config = configparser.ConfigParser()
        config.read(path)
        return {section: dict(config.items(section))
                for section in config.sections()}

    def test_export_does_not_replace_without_force(self):
        out = os.path.join(self.tmp, "out")
        os.makedirs(out)
        self.write(os.path.join("out", "hosts.conf"), "[hosts]\n")
        with self.assertRaises(FileExistsError):
            files.export_files(out)
        files.export_files(out, force=True)


class TestLegacyWarning(SqliteCase):

    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(files, "_warned", False)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(
            files, "_local", lambda name: os.path.join(self.tmp, name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_old_files_get_one_warning(self):
        with open(os.path.join(self.tmp, "hosts.conf"), "w") as fout:
            fout.write(HOSTS)
        err = io.StringIO()
        with contextlib.redirect_stderr(err), \
                self.assertLogs("mast.registry", "WARNING"):
            registry.resolve(["dp1"])
            registry.resolve(["dp1"])
        self.assertEqual(err.getvalue().count("mast targets import"), 1)

    def test_no_files_no_warning(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            registry.resolve(["dp1"])
        self.assertEqual(err.getvalue(), "")


class TestMastAppliances(FilesCase):

    def run_cli(self, *args):
        """mast targets ARGS, or mast fleets ARGS after "fleets"."""
        from mast.registry.cli import fleets, targets
        cli = targets
        if args and args[0] == "fleets":
            cli, args = fleets, args[1:]
        out, err = io.StringIO(), io.StringIO()
        status = 0
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                cli.run(list(args))
            except SystemExit as exc:
                status = exc.code or 0
        return status, out.getvalue(), err.getvalue()

    def test_appliances(self):
        status, out, err = self.run_cli(
            "add", "-n", "dp1", "-A", "10.0.0.1", "--soma-port", "5551",
            "--poll", "yes")
        self.assertEqual(status, 0, err)
        found = registry.get("dp1")
        self.assertEqual((found.address, found.soma_port, found.poll),
                         ("10.0.0.1", 5551, True))
        self.run_cli("update", "-n", "dp1", "--clear", "soma_port",
                     "--web-port", "9091", "--rename", "dp-one")
        found = registry.get("dp-one")
        self.assertIsNone(found.soma_port)
        self.assertEqual(found.web_port, 9091)
        status, out, _ = self.run_cli("list")
        self.assertIn("dp-one", out)
        self.assertIn("10.0.0.1", out)
        status, _, err = self.run_cli("add", "-n", "dp-one")
        self.assertEqual(status, 1)
        self.assertIn("taken", err)
        status, _, err = self.run_cli("update", "-n", "dp-one", "--clear",
                                      "address")
        self.assertEqual(status, 1)
        self.run_cli("remove", "-n", "dp-one")
        self.assertEqual(registry.list_appliances(), [])

    def test_environments(self):
        self.run_cli("add", "-n", "dp1")
        self.run_cli("add", "-n", "dp2")
        self.run_cli("fleets", "add", "-n", "lab", "-m", "dp1", "-m", "dp2", "-p")
        self.assertTrue(registry.env_get("lab").production)
        self.run_cli("fleets", "set-members", "-n", "lab", "-m", "dp2")
        self.assertEqual(registry.members("lab"), ["dp2"])
        self.run_cli("fleets", "update", "-n", "lab", "-p", "no", "-d", "Lab")
        self.assertFalse(registry.env_get("lab").production)
        status, out, _ = self.run_cli("fleets", "list")
        self.assertIn("lab", out)
        status, _, err = self.run_cli("fleets", "set-members", "-n", "lab",
                                      "-m", "nothing")
        self.assertEqual(status, 1)
        self.assertIn("There is no appliance nothing", err)
        self.run_cli("fleets", "remove", "-n", "lab")
        self.assertEqual(registry.env_list(), [])

    def test_import_and_export(self):
        status, out, _ = self.run_cli(
            "import", "--hosts", self.hosts, "--environments",
            self.environments, "--appliances", self.appliances, "-d")
        self.assertIn("Would add the appliance dp1", out)
        self.assertEqual(registry.list_appliances(), [])
        self.run_cli("import", "--hosts", self.hosts, "--environments",
                     self.environments, "--appliances", self.appliances)
        self.assertEqual(len(registry.list_appliances()), 6)
        out_dir = os.path.join(self.tmp, "back")
        status, out, _ = self.run_cli("export", "-o", out_dir)
        self.assertEqual(status, 0)
        self.assertTrue(os.path.exists(os.path.join(out_dir, "hosts.conf")))
        status, _, err = self.run_cli("export", "-o", out_dir)
        self.assertEqual(status, 1)
        self.assertIn("--force", err)


class TestRegistryApi(WebCase):

    def setUp(self):
        super().setUp()
        registry.add("dp1", "10.0.0.1")
        registry.env_add("lab", members=["dp1"])

    def call(self, method, path, body=None):
        return self.client.open("/api/v1" + path, method=method, json=body,
                                headers={"X-CSRF-Token": self.token()})

    def test_a_viewer_reads_and_cannot_change(self):
        self.login("viewer")
        found = self.client.get("/api/v1/appliances").get_json()["appliances"]
        self.assertEqual([a["name"] for a in found], ["dp1"])
        self.assertEqual(found[0]["environments"], ["lab"])
        envs = self.client.get("/api/v1/environments").get_json()
        self.assertEqual(envs["environments"][0]["members"], ["dp1"])
        for method, path in (("POST", "/appliances"),
                             ("PATCH", "/appliances/dp1"),
                             ("DELETE", "/appliances/dp1"),
                             ("POST", "/environments"),
                             ("PATCH", "/environments/lab"),
                             ("DELETE", "/environments/lab")):
            with self.subTest(method=method, path=path):
                self.assertEqual(self.call(method, path, {}).status_code, 403)

    def test_an_operator_cannot_change(self):
        self.login("operator")
        self.assertEqual(self.call("POST", "/appliances",
                                   {"name": "dp2"}).status_code, 403)

    def test_an_admin_changes_the_registry(self):
        self.login("admin")
        answer = self.call("POST", "/appliances", {
            "name": "dp2", "address": "10.0.0.2", "soma_port": 5551,
            "tls_mode": "none", "poll": True})
        self.assertEqual(answer.status_code, 201)
        # pin needs a pinned certificate first
        self.assertEqual(self.call("PATCH", "/appliances/dp2", {
            "tls_mode": "pin"}).status_code, 422)
        self.assertEqual(answer.get_json()["soma_port"], 5551)
        self.assertEqual(self.call("POST", "/appliances", {
            "name": "dp2"}).status_code, 409)
        self.assertEqual(self.call("POST", "/appliances", {
            "name": "dp3", "soma_port": 0}).status_code, 422)
        answer = self.call("PATCH", "/appliances/dp2", {
            "soma_port": None, "rename": "dp-two"})
        self.assertIsNone(answer.get_json()["soma_port"])
        self.assertEqual(answer.get_json()["name"], "dp-two")
        self.assertEqual(self.call("PATCH", "/appliances/nothing",
                                   {}).status_code, 404)
        answer = self.call("PATCH", "/environments/lab", {
            "members": ["dp-two", "dp1"], "production": True})
        self.assertEqual(answer.get_json()["members"], ["dp-two", "dp1"])
        self.assertTrue(answer.get_json()["production"])
        self.assertEqual(self.call("POST", "/environments", {
            "name": "prod", "members": ["dp1"]}).status_code, 201)
        self.assertEqual(self.call("DELETE", "/environments/prod")
                         .status_code, 204)
        self.assertEqual(self.call("DELETE", "/appliances/dp-two")
                         .status_code, 204)
        self.assertEqual(registry.members("lab"), ["dp1"])

    def test_the_admin_page(self):
        self.login("admin")
        page = self.client.get("/admin/appliances")
        self.assertEqual(page.status_code, 200)
        self.assertIn("admin-appliances.js", page.get_data(as_text=True))
        operator = self.app.test_client()
        self.login("operator", client=operator)
        self.assertEqual(operator.get("/admin/appliances").status_code, 403)

    def test_changes_are_logged(self):
        self.login("admin")
        with self.assertLogs("mast.registry", "INFO") as logs:
            self.call("POST", "/appliances", {"name": "dp2"})
        self.assertIn("Added the appliance dp2 (by admin", "\n".join(
            logs.output))


if __name__ == "__main__":
    unittest.main()

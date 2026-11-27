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
Unit tests for mast.registry: the targets and fleets in the database,
their kinds, extensions and trust, the import of the configuration
files of MAST 3.15.2 and the export back to them, mast targets and
mast fleets, and the API of the registry.
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
        found = registry.add("dp1", "datapower", "10.0.0.1",
                             attributes={"soma_port": "5551"})
        self.assertEqual((found.kind, found.address, found.revision),
                         ("datapower", "10.0.0.1", 1))
        # The values are of the types of the kind
        self.assertEqual(found.attributes, {"soma_port": 5551})
        self.assertEqual(found.attr("soma_port"), 5551)
        # One that is not set is the default of the kind, or None
        self.assertEqual(found.attr("ssh_port"), 22)
        self.assertIsNone(found.attr("soma_uri"))
        self.assertFalse(found.attr("poll"))
        self.assertEqual(found.schema_version, 1)
        # Each endpoint of the kind has its trust
        self.assertEqual([(t.endpoint, t.protocol, t.mode)
                          for t in found.trust],
                         [("soma", "tls", "ca"), ("ssh", "ssh", "none")])
        self.assertEqual(registry.get("dp1"), found)
        self.assertEqual(registry.by_id(found.id), found)
        self.assertEqual(registry.add("dp2", "datapower").address, "dp2")
        self.assertIsNone(registry.by_name("nothing"))
        with self.assertRaises(registry.UnknownName):
            registry.get("nothing")

    def test_refusals(self):
        registry.add("dp1", "datapower")
        registry.fleet_add("lab", "datapower")
        cases = (
            (registry.NameTaken, lambda: registry.add("dp1", "datapower")),
            (registry.NameTaken, lambda: registry.add("lab", "datapower")),
            (registry.NameTaken,
             lambda: registry.fleet_add("dp1", "datapower")),
            (registry.InvalidValue, lambda: registry.add("a b", "datapower")),
            (registry.InvalidValue,
             lambda: registry.add("x", "datapower", "not an address!")),
            (registry.InvalidValue, lambda: registry.add(
                "x", "datapower", attributes={"soma_port": 70000})),
            (registry.InvalidValue, lambda: registry.add(
                "x", "datapower", attributes={"soma_port": "many"})),
            (registry.InvalidValue, lambda: registry.add(
                "x", "datapower", attributes={"soma_scheme": "ftp"})),
            (registry.InvalidValue, lambda: registry.add(
                "x", "datapower", attributes={"colour": "red"})),
            (registry.InvalidValue, lambda: registry.add(
                "x", "datapower", attributes={"poll": "maybe"})),
            (registry.InvalidValue, lambda: registry.add(
                "x", "datapower", attributes={"soma_uri": "a\nb"})),
            # A kind that no plugin gives, and an endpoint without a port
            (registry.InvalidValue, lambda: registry.add("x", "linux")),
            (registry.InvalidValue, lambda: registry.add("x", "endpoint")),
            (registry.UnknownName,
             lambda: registry.update("nothing", notes="")),
            (registry.UnknownName, lambda: registry.fleet_add(
                "e", "datapower", members=["nothing"])),
            (registry.InvalidValue, lambda: registry.fleet_add("e", "linux")),
        )
        for error, change in cases:
            with self.subTest(error=error.__name__):
                with self.assertRaises(error):
                    change()
        self.assertEqual([a.name for a in registry.list_targets()], ["dp1"])

    def test_update_rename_and_remove(self):
        added = registry.add("dp1", "datapower",
                             attributes={"soma_port": 5551}, notes="old")
        registry.fleet_add("lab", "datapower", members=["dp1"])
        found = registry.update("dp1", attributes={
            "soma_port": None, "web_port": "9091", "poll": "yes"}, notes="")
        self.assertEqual(found.attributes, {"web_port": 9091, "poll": True})
        self.assertEqual(found.notes, "")
        self.assertEqual(found.revision, 2)
        renamed = registry.rename("dp1", "dp-one")
        # The id never changes
        self.assertEqual((renamed.id, renamed.revision), (added.id, 3))
        self.assertEqual(renamed.fleets, ["lab"])
        self.assertEqual(registry.members("lab"), ["dp-one"])
        registry.remove("dp-one")
        self.assertEqual(registry.members("lab"), [])
        self.assertEqual(registry.list_targets(), [])

    def test_trust(self):
        registry.add("dp1", "datapower")
        for bad in ("AB" * 32, "ab" * 31, "zz" * 32, None):
            with self.subTest(bad=bad), \
                    self.assertRaises(registry.InvalidValue):
                registry.pin("dp1", "soma", bad, "os:me")
        # pin needs a pin, and SSH has no CA
        for change in (lambda: registry.set_trust("dp1", "soma", "pin"),
                       lambda: registry.set_trust("dp1", "ssh", "ca"),
                       lambda: registry.set_trust("dp1", "ssh",
                                                  ca_bundle="x"),
                       lambda: registry.set_trust("dp1", "nothing", "ca"),
                       lambda: registry.set_trust(
                           "dp1", "soma", ca_bundle="not a certificate")):
            with self.assertRaises(registry.InvalidValue):
                change()
        registry.flag_mismatch("dp1", "soma", "cd" * 32)
        registry.flag_mismatch("dp1", "ssh", "SHA256:x")
        registry.flag_mismatch("no-such", "soma", "cd" * 32)
        registry.flag_mismatch("dp1", "nothing", "cd" * 32)
        soma = registry.get("dp1").trust_for("soma")
        self.assertEqual(soma.mismatch, "cd" * 32)
        self.assertIsNotNone(soma.mismatch_at)
        found = registry.pin("dp1", "soma", "ab" * 32, "os:me")
        soma, ssh = found.trust_for("soma"), found.trust_for("ssh")
        self.assertEqual((soma.mode, soma.pin, soma.pinned_by, soma.mismatch),
                         ("pin", "ab" * 32, "os:me", None))
        self.assertEqual(ssh.mismatch, "SHA256:x")
        found = registry.pin("dp1", "ssh", " ssh-ed25519 AAAA ", "user:admin")
        ssh = found.trust_for("ssh")
        self.assertEqual((ssh.mode, ssh.pin, ssh.pinned_by, ssh.mismatch),
                         ("pin", "ssh-ed25519 AAAA", "user:admin", None))
        with self.assertRaises(registry.InvalidValue):
            registry.pin("dp1", "ssh", "  ", "os:me")
        found = registry.unpin("dp1", "soma")
        self.assertEqual((found.trust_for("soma").mode,
                          found.trust_for("soma").pin), ("ca", None))
        self.assertEqual(found.trust_for("ssh").pin, "ssh-ed25519 AAAA")
        found = registry.unpin("dp1", "ssh")
        self.assertEqual(found.trust_for("ssh").mode, "none")
        with self.assertRaises(registry.UnknownName):
            registry.pin("no-such", "soma", "ab" * 32, "os:me")

    def test_a_change_marks_the_pins_for_review(self):
        registry.add("dp1", "datapower", "10.0.0.1")
        registry.pin("dp1", "soma", "ab" * 32, "os:me")
        # Not pinned: nothing to review
        found = registry.update("dp1", attributes={"ssh_port": 2222})
        self.assertIsNone(found.trust_for("ssh").review)
        self.assertIsNone(found.trust_for("soma").review)
        found = registry.update("dp1", attributes={"soma_port": 5551})
        self.assertEqual(found.trust_for("soma").review, "the port changed")
        with self.assertRaises(registry.InvalidValue):
            registry.review("dp1", "ssh", "os:me")
        found = registry.review("dp1", "soma", "os:admin")
        self.assertIsNone(found.trust_for("soma").review)
        self.assertEqual(found.trust_for("soma").pinned_by, "os:admin")
        registry.pin("dp1", "ssh", "SHA256:" + "A" * 43, "os:me")
        found = registry.update("dp1", address="10.0.0.2")
        for endpoint in ("soma", "ssh"):
            self.assertEqual(found.trust_for(endpoint).review,
                             "the address changed from 10.0.0.1 to 10.0.0.2")
        # A new pin is a review too
        found = registry.pin("dp1", "soma", "cd" * 32, "os:me")
        self.assertIsNone(found.trust_for("soma").review)
        # The same address is no change
        found = registry.update("dp1", address="10.0.0.2")
        self.assertIsNone(found.trust_for("soma").review)

    def test_fleets(self):
        for name in ("dp1", "dp2", "dp3"):
            registry.add(name, "datapower")
        lab = registry.fleet_add("lab", "datapower", "The lab",
                                 members=["dp3", "dp1", "dp3"])
        self.assertEqual((lab.kind, lab.members), ("datapower", ["dp3", "dp1"]))
        self.assertFalse(lab.production)
        self.assertEqual(registry.fleet_update("lab", production=True)
                         .production, True)
        self.assertEqual(registry.fleet_set_members("lab", ["dp2"]).members,
                         ["dp2"])
        registry.fleet_add("prod", "datapower", members=["dp2", "dp3"])
        self.assertEqual(registry.which_fleets("dp2"), ["lab", "prod"])
        self.assertEqual(registry.get("dp2").fleets, ["lab", "prod"])
        self.assertEqual([e.name for e in registry.fleet_list()],
                         ["lab", "prod"])
        self.assertTrue(registry.is_fleet("lab"))
        self.assertFalse(registry.is_fleet("dp1"))
        registry.fleet_remove("lab")
        self.assertIsNone(registry.members("lab"))
        self.assertIsNotNone(registry.by_name("dp2"))

    def test_a_fleet_holds_one_kind(self):
        registry.add("dp1", "datapower")
        registry.add("tls1", "endpoint", attributes={"port": 636})
        with self.assertRaises(registry.InvalidValue) as caught:
            registry.fleet_add("lab", "datapower", members=["dp1", "tls1"])
        self.assertIn("tls1 is not a datapower", str(caught.exception))
        registry.fleet_add("lab", "datapower", members=["dp1"])
        with self.assertRaises(registry.InvalidValue):
            registry.fleet_set_members("lab", ["tls1"])

    def test_resolve(self):
        registry.add("dp1", "datapower", "10.0.0.1")
        registry.add("dp2", "datapower", "10.0.0.2")
        registry.add("dp3", "datapower", "10.0.0.9")
        registry.add("dp4", "datapower", "10.0.0.9")
        registry.fleet_add("lab", "datapower", members=["dp2", "dp1"])
        found = registry.resolve(["lab", "dp1", "", "dp2"])
        self.assertEqual([a.name for a in found], ["dp2", "dp1"])
        self.assertEqual([a.name for a in registry.resolve("dp1")], ["dp1"])
        # Only registered targets
        for name, hint in (("other.example.com", "Register it first"),
                           ("10.0.0.1", "The target dp1 has this address"),
                           ("10.0.0.9", "The targets dp3 and dp4 have this "
                                        "address"),
                           ("not a host!", "")):
            with self.subTest(name=name):
                with self.assertRaises(registry.UnknownName) as caught:
                    registry.resolve([name])
                self.assertIn(hint, str(caught.exception))

    def test_by_address(self):
        registry.add("dp1", "datapower", "Box.Example.com")
        self.assertEqual(registry.by_address("box.example.com").name, "dp1")
        self.assertIsNone(registry.by_address("box.example.com",
                                              kind="endpoint"))
        self.assertIsNone(registry.by_address("other"))

    def test_a_kind_that_is_not_installed(self):
        # A target whose plugin is gone is kept and shown, not changed
        from mast.registry.models import TargetRecord
        from mast.jobs import db
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        with db.session() as session:
            session.add(TargetRecord(
                name="web1", kind="linux", address="10.0.0.7",
                attributes={"ssh_port": 22}, schema_version=1, revision=1,
                notes="", created_at=now, updated_at=now))
        found = registry.get("web1")
        self.assertEqual((found.kind, found.attributes),
                         ("linux", {"ssh_port": 22}))
        self.assertEqual(found.trust, [])
        self.assertFalse(registry.is_a(found, "datapower"))
        self.assertTrue(registry.is_a(found, "linux"))
        with self.assertRaises(registry.InvalidValue) as caught:
            registry.update("web1", attributes={"ssh_port": 2222})
        self.assertIn("install it", str(caught.exception))
        # Its address, notes and name can still change
        self.assertEqual(registry.update("web1", notes="kept").notes, "kept")

class TestRegistryPostgres(PostgresMixin, TestRegistry):
    pass


class TestExtensions(SqliteCase):
    """A target has one base kind and any number of extensions, which may
    be of other plugins (D18 to D21 of docs/design/mast-4.0.md)."""

    keep_datapower = True

    def setUp(self):
        super().setUp()
        from mast.plugins import Attribute, Endpoint, Kind
        from mast.testsuite.unit.test_mast_commands import (
            PluginsCase, fake_plugin)
        linux, _ = fake_plugin("linux", kinds=(Kind(
            "linux", "Linux host", capabilities=("collect-crypto",),
            attributes=(Attribute("ssh_port", "port", default=22),),
            endpoints=(Endpoint("ssh", "ssh", "ssh_port"),)),))
        django, _ = fake_plugin("django", kinds=(Kind(
            "django", "Django site", extends="linux",
            capabilities=("restart-app",),
            attributes=(Attribute("settings", required=True),
                        Attribute("admin_port", "port", default=8443)),
            endpoints=(Endpoint("admin", "tls", "admin_port"),)),))
        apic, _ = fake_plugin("apic", kinds=(Kind(
            "apic-gateway", "API Connect gateway", extends="datapower"),))
        PluginsCase.use(self, linux, django, apic)
        registry.add("web1", "linux", "10.0.0.7")

    def test_extensions_with_entries(self):
        found = registry.extend("web1", "django", "shop",
                                {"settings": "shop.settings"})
        found = registry.extend("web1", "django", "blog",
                                {"settings": "blog.settings",
                                 "admin_port": "9443"})
        self.assertEqual(registry.kinds_of(found), ["linux", "django"])
        self.assertEqual(
            [(e.kind, e.entry, e.attributes) for e in found.extensions], [
                ("django", "blog", {"settings": "blog.settings",
                                    "admin_port": 9443}),
                ("django", "shop", {"settings": "shop.settings"})])
        # Each entry has the trust of its own endpoints
        self.assertEqual([(t.scope, t.endpoint, t.mode) for t in found.trust],
                         [("", "ssh", "none"), ("django:blog", "admin", "ca"),
                          ("django:shop", "admin", "ca")])
        self.assertEqual(found.revision, 3)
        for change in (
                # A base kind, another base kind's extension, a twin
                lambda: registry.extend("web1", "linux"),
                lambda: registry.extend("web1", "apic-gateway"),
                lambda: registry.extend("web1", "django", "shop",
                                        {"settings": "x"}),
                # A required attribute
                lambda: registry.extend("web1", "django", "wiki")):
            with self.assertRaises(registry.RegistryError):
                change()

    def test_is_a_and_capabilities(self):
        target = registry.extend("web1", "django", "",
                                 {"settings": "s"})
        self.assertTrue(registry.is_a(target, "linux"))
        self.assertTrue(registry.is_a(target, "django"))
        self.assertFalse(registry.is_a(target, "datapower"))
        self.assertEqual(registry.capabilities_of(target),
                         {"collect-crypto", "restart-app"})
        self.assertTrue(registry.accepts(target, ("linux",)))
        self.assertTrue(registry.accepts(target, ("django",)))
        self.assertTrue(registry.accepts(target, (), ("restart-app",)))
        plain = registry.add("web2", "linux")
        self.assertFalse(registry.accepts(plain, ("django",)))
        self.assertFalse(registry.accepts(plain, (), ("restart-app",)))
        # An API Connect gateway is a DataPower appliance
        gateway = registry.add("gw1", "datapower")
        gateway = registry.extend("gw1", "apic-gateway")
        self.assertTrue(registry.accepts(gateway, ("datapower",)))
        self.assertTrue(registry.accepts(gateway, ("apic-gateway",)))
        self.assertEqual(
            [t.name for t in registry.list_targets("apic-gateway")], ["gw1"])

    def test_a_port_of_an_extension_marks_its_pin(self):
        registry.extend("web1", "django", "shop", {"settings": "s"})
        registry.pin("web1", "admin", "ab" * 32, "os:me", "django:shop")
        registry.pin("web1", "ssh", "SHA256:" + "A" * 43, "os:me")
        found = registry.update_extension("web1", "django", "shop",
                                          {"admin_port": 9000})
        self.assertEqual(found.trust_for("admin", "django:shop").review,
                         "the port changed")
        self.assertIsNone(found.trust_for("ssh").review)

    def test_fleets_of_an_extension(self):
        registry.extend("web1", "django", "", {"settings": "s"})
        registry.add("web2", "linux")
        with self.assertRaises(registry.InvalidValue):
            registry.fleet_add("sites", "django", members=["web2"])
        registry.fleet_add("sites", "django", members=["web1"])
        registry.fleet_add("hosts", "linux", members=["web1", "web2"])
        # Without its extension, it leaves the fleets of that kind
        found = registry.unextend("web1", "django")
        self.assertEqual(found.fleets, ["hosts"])
        self.assertEqual(found.trust_for("admin", "django"), None)
        self.assertEqual(registry.members("sites"), [])
        with self.assertRaises(registry.UnknownName):
            registry.unextend("web1", "django")

    def test_the_api_and_the_command_line(self):
        from mast.registry.cli import targets
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            targets.run(["extend", "-n", "web1", "-k", "django", "-e", "shop",
                         "-s", "settings=shop.settings"])
            targets.run(["set-extension", "-n", "web1", "-k", "django",
                         "-e", "shop", "-s", "admin_port=9000"])
            targets.run(["list", "-k", "django"])
        self.assertIn("extension django shop: settings=shop.settings", out.getvalue())
        self.assertRegex(out.getvalue(), r"web1 +linux \+ django +10\.0\.0\.7")
        self.assertEqual(registry.get("web1").extension("django", "shop")
                         .attributes["admin_port"], 9000)


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
        found = {a.name: a for a in registry.list_targets()}
        # Every alias, every name of an environment, every section
        self.assertEqual(sorted(found), [
            "10.0.0.4", "dp-upper", "dp1", "dp2", "dp3.example.com",
            "lonely.example.com"])
        self.assertEqual({a.kind for a in found.values()}, {"datapower"})
        self.assertEqual(found["dp1"].address, "10.0.0.1")
        self.assertEqual(found["dp1"].attributes, {
            "soma_port": 5551, "soma_spec_file": "etc/v10.6.0.11-C.xml"})
        self.assertEqual(found["dp3.example.com"].attributes,
                         {"ssh_port": 2222, "soma_scheme": "http"})
        self.assertEqual(found["lonely.example.com"].attributes,
                         {"web_port": 9443})
        self.assertEqual(found["10.0.0.4"].address, "10.0.0.4")
        # A host in two environments is one target in both fleets
        self.assertEqual(found["dp2"].fleets, ["lab", "prod"])
        self.assertEqual(registry.fleet_get("lab").kind, "datapower")
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
        self.assertEqual(registry.list_targets(), [])
        self.assertEqual(registry.fleet_list(), [])
        self.assertTrue(os.path.exists(self.hosts))

    def test_registered_names_are_kept(self):
        registry.add("dp1", "datapower", "192.168.0.1")
        registry.fleet_add("lab", "datapower", members=["dp1"])
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
        registry.add("dp1", "datapower")
        with open(os.path.join(self.tmp, "hosts.conf"), "w") as fout:
            fout.write(HOSTS)
        err = io.StringIO()
        with contextlib.redirect_stderr(err), \
                self.assertLogs("mast.registry", "WARNING"):
            registry.resolve(["dp1"])
            registry.resolve(["dp1"])
        self.assertEqual(err.getvalue().count("mast targets import"), 1)

    def test_no_files_no_warning(self):
        registry.add("dp1", "datapower")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            registry.resolve(["dp1"])
        self.assertEqual(err.getvalue(), "")


class TestMastTargets(FilesCase):

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

    def test_targets(self):
        status, out, err = self.run_cli(
            "add", "-n", "dp1", "-k", "datapower", "-A", "10.0.0.1",
            "-s", "soma_port=5551", "-s", "poll=yes")
        self.assertEqual(status, 0, err)
        found = registry.get("dp1")
        self.assertEqual((found.address, found.attributes),
                         ("10.0.0.1", {"soma_port": 5551, "poll": True}))
        self.run_cli("update", "-n", "dp1", "--clear", "soma_port",
                     "-s", "web_port=9091", "--rename", "dp-one")
        found = registry.get("dp-one")
        self.assertEqual(found.attributes, {"web_port": 9091, "poll": True})
        status, out, _ = self.run_cli("list")
        self.assertRegex(out, r"dp-one +datapower +10\.0\.0\.1 +soma:ca "
                              r"ssh:none")
        status, out, _ = self.run_cli("show", "-n", "dp-one")
        self.assertIn("web_port: 9091", out)
        self.assertIn("soma_port: 5550 (default)", out)
        self.assertIn("trust soma (tls): ca", out)
        for args, message in (
                (("add", "-n", "dp-one", "-k", "datapower"), "taken"),
                (("add", "-n", "x", "-k", "linux"), "No installed plugin"),
                (("add", "-n", "x", "-k", "datapower", "-s", "soma_port"),
                 "NAME=VALUE"),
                (("add", "-n", "x", "-k", "datapower", "-s", "colour=red"),
                 "has no attribute 'colour'"),
                (("update", "-n", "dp-one", "--clear", "address"),
                 "has no attribute 'address'")):
            with self.subTest(args=args):
                status, _, err = self.run_cli(*args)
                self.assertEqual(status, 1)
                self.assertIn(message, err)
        self.run_cli("remove", "-n", "dp-one")
        self.assertEqual(registry.list_targets(), [])

    def test_fleets(self):
        self.run_cli("add", "-n", "dp1", "-k", "datapower")
        self.run_cli("add", "-n", "dp2", "-k", "datapower")
        self.run_cli("fleets", "add", "-n", "lab", "-k", "datapower",
                     "-m", "dp1", "-m", "dp2", "-p")
        self.assertTrue(registry.fleet_get("lab").production)
        self.run_cli("fleets", "set-members", "-n", "lab", "-m", "dp2")
        self.assertEqual(registry.members("lab"), ["dp2"])
        self.run_cli("fleets", "update", "-n", "lab", "-p", "no", "-d", "Lab")
        self.assertFalse(registry.fleet_get("lab").production)
        status, out, _ = self.run_cli("fleets", "list")
        self.assertRegex(out, r"lab +datapower")
        status, _, err = self.run_cli("fleets", "set-members", "-n", "lab",
                                      "-m", "nothing")
        self.assertEqual(status, 1)
        self.assertIn("There is no target nothing", err)
        self.run_cli("fleets", "remove", "-n", "lab")
        self.assertEqual(registry.fleet_list(), [])

    def test_import_and_export(self):
        status, out, _ = self.run_cli(
            "import", "--hosts", self.hosts, "--environments",
            self.environments, "--appliances", self.appliances, "-d")
        self.assertIn("Would add the appliance dp1", out)
        self.assertEqual(registry.list_targets(), [])
        self.run_cli("import", "--hosts", self.hosts, "--environments",
                     self.environments, "--appliances", self.appliances)
        self.assertEqual(len(registry.list_targets()), 6)
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
        registry.add("dp1", "datapower", "10.0.0.1")
        registry.fleet_add("lab", "datapower", members=["dp1"])

    def call(self, method, path, body=None):
        return self.client.open("/api/v1" + path, method=method, json=body,
                                headers={"X-CSRF-Token": self.token()})

    def test_a_viewer_reads_and_cannot_change(self):
        self.login("viewer")
        found = self.client.get("/api/v1/targets").get_json()["targets"]
        self.assertEqual([a["name"] for a in found], ["dp1"])
        self.assertEqual((found[0]["fleets"], found[0]["kinds"]),
                         (["lab"], ["datapower"]))
        fleets = self.client.get("/api/v1/fleets").get_json()["fleets"]
        self.assertEqual((fleets[0]["members"], fleets[0]["kind"]),
                         (["dp1"], "datapower"))
        kinds = {k["name"]: k for k in
                 self.client.get("/api/v1/kinds").get_json()["kinds"]}
        self.assertEqual([e["name"] for e in kinds["datapower"]["endpoints"]],
                         ["soma", "ssh"])
        self.assertIn("soma_port", [a["name"] for a in
                                    kinds["datapower"]["attributes"]])
        for method, path in (("POST", "/targets"),
                             ("PATCH", "/targets/dp1"),
                             ("DELETE", "/targets/dp1"),
                             ("POST", "/targets/dp1/extensions"),
                             ("PATCH", "/targets/dp1/trust"),
                             ("POST", "/fleets"),
                             ("PATCH", "/fleets/lab"),
                             ("DELETE", "/fleets/lab")):
            with self.subTest(method=method, path=path):
                self.assertEqual(self.call(method, path, {}).status_code, 403)

    def test_an_operator_cannot_change(self):
        self.login("operator")
        self.assertEqual(self.call("POST", "/targets", {
            "name": "dp2", "kind": "datapower"}).status_code, 403)

    def test_an_admin_changes_the_registry(self):
        self.login("admin")
        answer = self.call("POST", "/targets", {
            "name": "dp2", "kind": "datapower", "address": "10.0.0.2",
            "attributes": {"soma_port": 5551, "poll": True}})
        self.assertEqual(answer.status_code, 201, answer.get_json())
        self.assertEqual(answer.get_json()["attributes"],
                         {"soma_port": 5551, "poll": True})
        # pin needs a pinned certificate first
        self.assertEqual(self.call("PATCH", "/targets/dp2/trust", {
            "endpoint": "soma", "mode": "pin"}).status_code, 422)
        self.assertEqual(self.call("PATCH", "/targets/dp2/trust", {
            "endpoint": "soma", "mode": "none"}).status_code, 200)
        self.assertEqual(self.call("POST", "/targets", {
            "name": "dp2", "kind": "datapower"}).status_code, 409)
        for body in ({"name": "dp3", "kind": "datapower",
                      "attributes": {"soma_port": 0}},
                     {"name": "dp3", "kind": "nothing"},
                     {"name": "dp3", "kind": "datapower", "attributes": [1]}):
            with self.subTest(body=body):
                self.assertEqual(self.call("POST", "/targets", body)
                                 .status_code, 422)
        answer = self.call("PATCH", "/targets/dp2", {
            "attributes": {"soma_port": None}, "rename": "dp-two"})
        self.assertEqual(answer.get_json()["attributes"], {"poll": True})
        self.assertEqual(answer.get_json()["name"], "dp-two")
        self.assertEqual(self.call("PATCH", "/targets/nothing",
                                   {}).status_code, 404)
        answer = self.call("PATCH", "/fleets/lab", {
            "members": ["dp-two", "dp1"], "production": True})
        self.assertEqual(answer.get_json()["members"], ["dp-two", "dp1"])
        self.assertTrue(answer.get_json()["production"])
        self.assertEqual(self.call("POST", "/fleets", {
            "name": "prod", "kind": "datapower",
            "members": ["dp1"]}).status_code, 201)
        self.assertEqual(self.call("POST", "/fleets", {
            "name": "bad", "kind": "nothing"}).status_code, 422)
        self.assertEqual(self.call("DELETE", "/fleets/prod")
                         .status_code, 204)
        self.assertEqual(self.call("DELETE", "/targets/dp-two")
                         .status_code, 204)
        self.assertEqual(registry.members("lab"), ["dp1"])

    def test_the_admin_page(self):
        self.login("admin")
        page = self.client.get("/admin/targets")
        self.assertEqual(page.status_code, 200)
        self.assertIn("admin-targets.js", page.get_data(as_text=True))
        operator = self.app.test_client()
        self.login("operator", client=operator)
        self.assertEqual(operator.get("/admin/targets").status_code, 403)

    def test_changes_are_logged(self):
        self.login("admin")
        with self.assertLogs("mast.registry", "INFO") as logs:
            self.call("POST", "/targets", {"name": "dp2",
                                           "kind": "datapower"})
        self.assertIn("Added the target dp2 (by admin", "\n".join(
            logs.output))


if __name__ == "__main__":
    unittest.main()

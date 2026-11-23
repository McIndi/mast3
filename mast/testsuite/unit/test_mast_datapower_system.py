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
Unit tests for the commands of mast.datapower.system, with the
appliances replaced by fakes.
"""
import base64
import contextlib
import glob
import gzip
import io
import os
import tempfile
import unittest
from xml.etree import ElementTree as etree

from unittest import mock

import mast.datapower.system as system
from mast.cli import Table, Text, captured
from mast.datapower.system.system import XMLResults, cli
from mast.plugin_utils.results import (
    ActionResults, CommonItems, Report, SeeDownload, StatusResults)
from mast.xor import xorencode

ENVELOPE = (
    '<env:Envelope xmlns:env="http://schemas.xmlsoap.org/soap/envelope/">'
    '<env:Body>'
    '<dp:response xmlns:dp="http://www.datapower.com/schemas/management">'
    '<dp:{kind}>{body}</dp:{kind}>'
    '</dp:response></env:Body></env:Envelope>')

STATUS = (
    "<Version><Version>10.5</Version>"
    "<Build><Date>2026</Date><Level>7</Level></Build></Version>")

CONFIG = (
    '<Domain name="app"><mAdminState>enabled</mAdminState>'
    '<ConfigDir><Path>config:</Path></ConfigDir></Domain>')


class FakeResponse(object):
    def __init__(self, ok=True, xml="<result>OK</result>"):
        self.xml = etree.fromstring(xml)
        self.ok = ok
        # As DPResponse: pretty is bytes and str() is the same as text
        self.pretty = ("pretty " + xml).encode()

    def __bool__(self):
        return self.ok

    def __str__(self):
        return self.pretty.decode()

    def __repr__(self):
        return "collapsed"


class FakeAppliance(object):
    credentials = "user:pass"
    history = "request: x\nresponse: y"
    reachable = True
    # Every call to every fake appliance, in order
    log = []

    def __init__(self, hostname):
        self.hostname = hostname
        self.domains = ["default", "app1", "app2"]

    def __repr__(self):
        return self.hostname

    def record(name, result=None):
        def method(self, *args, **kwargs):
            self.log.append((self.hostname, name, args, kwargs))
            return FakeResponse() if result is None else result
        return method

    SaveConfig = record("SaveConfig")
    DomainQuiesce = record("DomainQuiesce")
    DomainUnquiesce = record("DomainUnquiesce")
    RestartDomain = record("RestartDomain")
    disable_domain = record("disable_domain")
    enable_domain = record("enable_domain")
    Shutdown = record("Shutdown")
    ssh_connect = record("ssh_connect")
    ssh_issue_command = record("ssh_issue_command", "ssh> ")
    set_firmware = record("set_firmware")
    _set_file = record("_set_file")
    del_file = record("del_file")
    DeleteFile = record("DeleteFile")
    ErrorReport = record("ErrorReport")
    log_info = record("log_info")
    log_error = record("log_error")

    def is_reachable(self):
        return self.reachable

    def getfile(self, domain, filename):
        return b"contents of " + filename.encode()

    def copy_directory(self, location, out_dir, domain, recursive):
        self.log.append((self.hostname, "copy_directory",
                         (location, out_dir, domain), {"recursive": recursive}))

    def ls(self, directory, domain, include_directories):
        return ["a.txt", "diag-log"]


class FakeEnvironment(object):
    hostnames = ("dp1", "dp2")
    # The parameters of every environment that the commands made
    made = []

    def __init__(self, hostnames, credentials=None, timeout=120,
                 check_hostname=True):
        if isinstance(hostnames, str):
            hostnames = [hostnames]
        self.appliances = [
            FakeAppliance(name) for name in (hostnames or self.hostnames)]
        self.made.append((list(hostnames), credentials, timeout))
        self.actions = []

    def perform_action(self, action, **kwargs):
        self.actions.append((action, kwargs))
        FakeAppliance.log.append(
            (",".join(a.hostname for a in self.appliances), action, (), kwargs))
        hosts = [a.hostname for a in self.appliances]
        if action == "getfile":
            return {host: b"the file" for host in hosts}
        if action == "object_audit":
            return {host: "<diff/>" for host in hosts}
        if action == "get_status":
            xml = ENVELOPE.format(kind="status", body=STATUS)
            return {host: FakeResponse(xml=xml) for host in hosts}
        if action == "get_config":
            xml = ENVELOPE.format(kind="config", body=CONFIG)
            return {host: FakeResponse(xml=xml) for host in hosts}
        return {host: FakeResponse(ok=host != "dp2") for host in hosts}

    perform_async_action = perform_action


class SystemTestCase(unittest.TestCase):

    def setUp(self):
        FakeAppliance.log = []
        FakeEnvironment.made = []
        for target, new in (
                ("mast.plugin_utils.params.Environment", FakeEnvironment),
                ("mast.datapower.system.system.sleep", mock.Mock())):
            patch = mock.patch(target, new)
            self.sleep = patch.start()
            self.addCleanup(patch.stop)
        self.out_dir = tempfile.mkdtemp(dir=os.path.join(
            os.environ["MAST_HOME"], "tmp"))

    def call(self, command, **kwargs):
        """Call command and return (result, progress messages)."""
        kwargs.setdefault("appliances", ["dp1", "dp2"])
        with captured() as messages:
            result = command(**kwargs)
        return result, messages

    def calls(self, name=None):
        """The (hostname, name, kwargs) of the calls to the fakes."""
        return [(host, _name, kwargs)
                for host, _name, args, kwargs in FakeAppliance.log
                if name is None or _name == name]


class TestSystemCommands(SystemTestCase):

    def test_xor(self):
        result = system.xor(string="user:pass")
        self.assertIsInstance(result, Text)
        self.assertEqual(xorencode("user:pass"), result.text())

    def test_actions(self):
        cases = (
            (system.flush_aaa_cache, {"Domain": "d", "aaa_policy": "p"},
             "FlushAAACache", {"PolicyName": "p", "domain": "d"}),
            (system.flush_arp_cache, {}, "FlushArpCache", {}),
            (system.flush_dns_cache, {}, "FlushDNSCache", {}),
            (system.flush_document_cache, {"Domain": "d", "xml_manager": "m"},
             "FlushDocumentCache", {"XMLManager": "m", "domain": "d"}),
            (system.flush_ldap_pool_cache, {"Domain": "d", "xml_manager": "m"},
             "FlushLDAPPoolCache", {"XMLManager": "m", "domain": "d"}),
            (system.flush_nd_cache, {}, "FlushNDCache", {}),
            (system.flush_nss_cache, {"Domain": "d", "zos_nss_client": "z"},
             "FlushNSSCache", {"ZosNSSClient": "z", "domain": "d"}),
            (system.flush_pdp_cache, {"XACML_PDP": "x"},
             "FlushPDPCache", {"XACMLPDP": "x"}),
            (system.flush_rbm_cache, {"Domain": "d"},
             "FlushRBMCache", {"domain": "d"}),
            (system.flush_stylesheet_cache,
             {"Domain": "d", "xml_manager": "m"},
             "FlushStylesheetCache", {"XMLManager": "m", "domain": "d"}),
            (system.unquiesce_service,
             {"Domain": "d", "type": "t", "name": "n"},
             "ServiceUnquiesce", {"type": "t", "name": "n", "domain": "d"}),
            (system.unquiesce_appliance, {}, "UnquiesceDP", {}),
            (system.shutdown_appliance, {"delay": 3},
             "Shutdown", {"Mode": "halt", "Delay": "3"}),
            (system.create_dir, {"Domain": "d", "directory": "local:/x"},
             "CreateDir", {"domain": "d", "Dir": "local:/x"}),
        )
        for command, kwargs, action, expected in cases:
            with self.subTest(action=action):
                result, messages = self.call(command, **kwargs)
                self.assertIsInstance(result, ActionResults)
                self.assertEqual([(action, expected)], result.env.actions)
                self.assertEqual(1, len(messages))
                self.assertIn("dp1\n===", result.text())
                html = result.html()
                self.assertEqual(1, html.count("Succeeded"))
                self.assertEqual(1, html.count("Failed"))
                self.assertIn("request: x", result.history)
                self.sleep.assert_not_called()

    def test_quiesce_waits_for_the_quiesce_timeout(self):
        cases = (
            (system.quiesce_service,
             {"Domain": "d", "type": "t", "name": "n", "quiesce_timeout": 61},
             "ServiceQuiesce",
             {"type": "t", "name": "n", "timeout": 61, "domain": "d"}),
            (system.quiesce_appliance, {"quiesce_timeout": 61},
             "QuiesceDP", {"timeout": "61"}),
        )
        for command, kwargs, action, expected in cases:
            with self.subTest(action=action):
                self.sleep.reset_mock()
                result, _ = self.call(command, **kwargs)
                self.assertEqual([(action, expected)], result.env.actions)
                self.sleep.assert_called_once_with(61)

    def test_save_config(self):
        result, messages = self.call(system.save_config)
        self.assertEqual(
            [("dp1", "SaveConfig", {"domain": "default"}),
             ("dp2", "SaveConfig", {"domain": "default"})], self.calls())
        self.assertEqual(["dp1-default", "dp2-default"], list(result.responses))
        self.assertIn("dp1-default-save_config", result.html())
        self.assertEqual(2, len(messages))

        FakeAppliance.log = []
        self.call(system.save_config, appliances=["dp1"],
                  Domain=["all-domains"])
        self.assertEqual(
            ["default", "app1", "app2"],
            [kwargs["domain"] for _, _, kwargs in self.calls()])

        # One domain can be given as a string
        FakeAppliance.log = []
        self.call(system.save_config, appliances="dp1", Domain="app1")
        self.assertEqual([("dp1", "SaveConfig", {"domain": "app1"})],
                         self.calls())

    def test_list_domains(self):
        result, _ = self.call(system.list_domains)
        self.assertIsInstance(result, CommonItems)
        self.assertEqual(["app1", "app2", "default"], result.common)

    def test_list_objects(self):
        objects = {"dp1": [("Domain", "default"), ("Domain", "app1"),
                           ("XMLManager", "default")],
                   "dp2": [("Domain", "default"), ("Domain", "app2")]}

        def get_status(appliance, provider, domain="default"):
            appliance.log.append((appliance.hostname, "get_status", (),
                                  {"provider": provider, "domain": domain}))
            body = "".join(
                "<ObjectStatus><Class>{}</Class><Name>{}</Name></ObjectStatus>"
                .format(_class, name) for _class, name in objects[appliance.hostname])
            return FakeResponse(xml=ENVELOPE.format(kind="status", body=body))

        with mock.patch.object(FakeAppliance, "get_status", get_status, create=True):
            result, _ = self.call(system.list_objects, ObjectClass="Domain")
        self.assertIsInstance(result, Table)
        self.assertEqual(result.data(), {
            "kind": "table", "columns": list(system.OBJECTS_HEADER),
            "rows": [["app1", "no", "dp1"], ["app2", "no", "dp2"],
                     ["default", "yes", "dp1, dp2"]]})
        self.assertEqual({kwargs["domain"] for _, _, kwargs in self.calls("get_status")},
                         {"default"})
        # What the web UI offers: the objects that every appliance has
        from mast.plugin_utils import choices
        self.assertEqual(choices.from_result(result.data()), ["default"])
        self.assertEqual(choices.from_result(None), [])

    def test_add_and_del_domain(self):
        for command, kwargs, action in (
                (system.add_domain, {"domain_name": "new"}, "add_domain"),
                (system.del_domain, {"Domain": "new"}, "del_domain")):
            with self.subTest(action=action):
                result, messages = self.call(
                    command, save_config=True, **kwargs)
                self.assertIsInstance(result, Report)
                self.assertEqual(
                    [(action, {"name": "new"}),
                     ("SaveConfig", {"domain": "default"})],
                    result.env.actions)
                html = result.html()
                self.assertIn("dp1-" + action, html)
                self.assertIn("dp1-save_config", html)

                result, _ = self.call(command, **kwargs)
                self.assertEqual(1, len(result.env.actions))

    def test_domain_actions(self):
        cases = (
            (system.quiesce_domain, {"quiesce_timeout": 61}, "DomainQuiesce",
             lambda d: {"name": d, "timeout": "61", "domain": d}),
            (system.unquiesce_domain, {}, "DomainUnquiesce",
             lambda d: {"name": d}),
            (system.restart_domain, {}, "RestartDomain",
             lambda d: {"Domain": d}),
        )
        for command, kwargs, method, expected in cases:
            with self.subTest(method=method):
                FakeAppliance.log = []
                self.sleep.reset_mock()
                result, messages = self.call(
                    command, Domain=["app1", "app2"], **kwargs)
                self.assertEqual(
                    [(host, method, expected(domain))
                     for host in ("dp1", "dp2")
                     for domain in ("app1", "app2")], self.calls())
                self.assertEqual(
                    ["dp1-app1", "dp1-app2", "dp2-app1", "dp2-app2"],
                    list(result.responses))
                self.assertEqual(4, len(messages))
                self.assertEqual(4, result.html().count("Succeeded"))
                # Only a quiesce waits, once for each domain
                self.assertEqual(
                    4 if method == "DomainQuiesce" else 0,
                    self.sleep.call_count)

                FakeAppliance.log = []
                self.call(command, appliances=["dp1"],
                          Domain=["all-domains"], **kwargs)
                self.assertEqual(3, len(self.calls()))

    def test_enable_and_disable_domain(self):
        for command, method in ((system.disable_domain, "disable_domain"),
                                (system.enable_domain, "enable_domain")):
            with self.subTest(method=method):
                FakeAppliance.log = []
                result, _ = self.call(
                    command, appliances=["dp1"], Domain=["all-domains"],
                    save_config=True)
                # The default domain is never disabled, but it is saved
                self.assertEqual(
                    ["app1", "app2"],
                    [args[0] for _, name, args, _ in FakeAppliance.log
                     if name == method])
                self.assertEqual(
                    ["default", "app1", "app2"],
                    [kwargs["domain"]
                     for _, _, kwargs in self.calls("SaveConfig")])
                html = result.html()
                # One row for each domain, not one for each step so far
                self.assertEqual(5, html.count("Succeeded"))
                self.assertIn("dp1-app1-" + method, html)
                self.assertIn("dp1-default-save_config", html)

                FakeAppliance.log = []
                self.call(command, Domain="app1")
                self.assertEqual([], self.calls("SaveConfig"))
                self.assertEqual(2, len(FakeAppliance.log))


class TestRestarts(SystemTestCase):

    def test_reboot(self):
        result, messages = self.call(system.reboot_appliance, delay=3)
        self.assertEqual(
            [("dp1", "Shutdown", {"Mode": "reboot", "Delay": "3"}),
             ("dp2", "Shutdown", {"Mode": "reboot", "Delay": "3"})],
            self.calls())
        self.assertIsInstance(result, ActionResults)
        self.assertIn("dp1-reboot_appliance", result.html())

    def test_reboot_stops_when_an_appliance_does_not_come_back(self):
        clock = iter(range(0, 100000, 100))
        with mock.patch.object(FakeAppliance, "reachable", False), \
                mock.patch("mast.datapower.system.system.time",
                           lambda: next(clock)):
            with self.assertRaises(RuntimeError):
                self.call(system.reboot_appliance, wait=250)
        # The second appliance was not rebooted
        self.assertEqual(["dp1"], [host for host, _, _ in self.calls()])

    def test_reload(self):
        result, _ = self.call(system.reload_appliance, delay=3)
        self.assertEqual(
            [("dp1", "Shutdown", {"Mode": "reload", "Delay": "3"}),
             ("dp2", "Shutdown", {"Mode": "reload", "Delay": "3"})],
            self.calls())
        self.assertIn("pretty <result>", result.text())
        self.assertEqual(2, result.html().count("Succeeded"))

    def test_reload_reports_an_appliance_that_does_not_come_back(self):
        clock = iter(range(0, 100000, 100))
        with mock.patch.object(FakeAppliance, "reachable", False), \
                mock.patch("mast.datapower.system.system.time",
                           lambda: next(clock)):
            result, _ = self.call(system.reload_appliance, wait=250)
        # Both appliances were reloaded
        self.assertEqual(2, len(self.calls()))
        self.assertEqual(2, result.text().count("did not come back up"))
        self.assertEqual(2, result.html().count("Failed"))


class TestFirmwareUpgrade(SystemTestCase):

    def setUp(self):
        super(TestFirmwareUpgrade, self).setUp()
        patch = mock.patch(
            "mast.datapower.system.system.get_normal_backup",
            return_value=Text("backup taken"))
        self.backup = patch.start()
        self.addCleanup(patch.stop)

    def steps(self):
        """The names of the calls, with the repeats removed."""
        names = []
        for _, name, _ in self.calls():
            if name in ("ssh_issue_command", "DeleteFile"):
                continue
            if not names or names[-1] != name:
                names.append(name)
        return names

    def upgrade(self, **kwargs):
        return self.call(
            system.firmware_upgrade, appliances=["dp1"],
            credentials=["user:pass"], file_in="firmware.scrypt4",
            accept_license=True, out_dir=self.out_dir, **kwargs)

    def test_steps_happen_in_order(self):
        with mock.patch("mast.datapower.system.system._clean_error_reports"):
            result, messages = self.upgrade()
        self.assertEqual(
            ["ssh_connect",       # boot delete (the clean up deletes files)
             "SaveConfig",        # after the backup and the second clean up
             "QuiesceDP",
             "disable_domain",
             "SaveConfig",
             "Shutdown",
             "set_firmware",
             "enable_domain",
             "SaveConfig"],
            self.steps())
        self.assertEqual(
            [("dp1", "set_firmware", {})], self.calls("set_firmware"))
        self.assertEqual(
            ("firmware.scrypt4", True, 1200),
            [args for _, name, args, _ in FakeAppliance.log
             if name == "set_firmware"][0])
        self.assertEqual(
            ["co", "flash", "boot delete", "exit", "exit", "exit"],
            [args[0] for _, name, args, _ in FakeAppliance.log
             if name == "ssh_issue_command"])
        # Only the application domains are disabled and enabled
        for method in ("disable_domain", "enable_domain"):
            self.assertEqual(
                ["app1", "app2"],
                [args[0] for _, name, args, _ in FakeAppliance.log
                 if name == method])
        # The backup is one appliance at a time, with keyword arguments
        self.assertEqual((), self.backup.call_args[0])
        self.assertEqual("dp1", self.backup.call_args[1]["appliances"])
        self.assertEqual("all-domains", self.backup.call_args[1]["Domain"])
        self.assertEqual(self.out_dir, self.backup.call_args[1]["out_dir"])
        # Every step reaches the appliance with the same timeout
        self.assertEqual({1200}, {timeout for _, _, timeout
                                  in FakeEnvironment.made})

        html = result.html()
        self.assertIn("dp1-set_firmware", html)
        self.assertIn("backup taken", html)
        self.assertIn("dp1-app1-disable_domain", html)
        self.assertIn("request: x", result.history)
        text = "\n".join(messages)
        self.assertLess(text.index("dp1: Quiescing the appliance"),
                        text.index("dp1: Rebooting the appliance"))
        self.assertLess(text.index("dp1: Rebooting the appliance"),
                        text.index("dp1: Attempting to update the firmware"))

    def test_each_step_can_be_turned_off(self):
        result, _ = self.upgrade(
            boot_delete=False, no_cleanup=True, no_backup=True,
            no_save_config=True, no_quiesce_appliances=True,
            no_disable_domains=True, no_reboot=True, no_enable_domains=True)
        self.assertEqual(["set_firmware"], self.steps())
        self.backup.assert_not_called()

        FakeAppliance.log = []
        self.upgrade(
            boot_delete=False, no_cleanup=True, no_backup=True,
            no_quiesce_appliances=True, no_disable_domains=True,
            no_reboot=True, no_set_firmware=True, no_enable_domains=True)
        self.assertEqual(["SaveConfig"], self.steps())

    def test_stops_when_an_appliance_does_not_come_back(self):
        clock = iter(range(0, 1000000, 500))
        with mock.patch.object(FakeAppliance, "reachable", False), \
                mock.patch("mast.datapower.system.system.time",
                           lambda: next(clock)):
            with self.assertRaises(RuntimeError) as caught:
                self.call(
                    system.firmware_upgrade, file_in="f",
                    boot_delete=False, no_cleanup=True, no_backup=True,
                    no_save_config=True, no_quiesce_appliances=True,
                    no_disable_domains=True, no_reboot=True)
        self.assertIn("Aborting", str(caught.exception))
        self.assertEqual(
            ["dp1"], [host for host, _, _ in self.calls("set_firmware")])

    def test_the_firmware_file_is_required(self):
        with self.assertRaises(ValueError):
            system.firmware_upgrade(appliances=["dp1"])


class TestFilesAndAudits(SystemTestCase):

    def files(self):
        # The paths use "/" on every platform.
        return sorted(
            os.path.relpath(path, self.out_dir).replace(os.sep, "/")
            for path in glob.glob(
                os.path.join(self.out_dir, "**"), recursive=True)
            if os.path.isfile(path))

    def test_filesystem_listings(self):
        cases = (
            (system.get_encrypted_filesystem, {},
             "get_encrypted_filesystem", {}, "encrypted-filesystem"),
            (system.get_temporary_filesystem, {},
             "get_temporary_filesystem", {}, "temporary-filesystem"),
            (system.get_filestore, {"Domain": "d", "location": "cert:"},
             "get_filestore", {"domain": "d", "location": "cert:"},
             "get-filestore"),
        )
        for command, kwargs, action, expected, name in cases:
            with self.subTest(action=action):
                out_dir = tempfile.mkdtemp(dir=self.out_dir)
                result, messages = self.call(
                    command, out_dir=out_dir, **kwargs)
                self.assertIsInstance(result, SeeDownload)
                self.assertEqual([(action, expected)], result.env.actions)
                written = glob.glob(os.path.join(out_dir, "*", "*.xml"))
                self.assertEqual(
                    ["dp1-{}.xml".format(name), "dp2-{}.xml".format(name)],
                    sorted(os.path.basename(path) for path in written))
                with open(written[0]) as fin:
                    self.assertEqual("pretty <result>OK</result>", fin.read())
                self.assertEqual(2, len(messages))
                self.assertIn("dp1-" + action, result.html())

    def test_copy_file(self):
        result, _ = self.call(
            system.copy_file, Domain="d", src="local:/a", dst="local:/b",
            overwrite=False)
        self.assertEqual(
            (base64.encodebytes(b"contents of local:/a"),
             "local:/b", "d", False),
            FakeAppliance.log[0][2])
        self.assertIsInstance(result, StatusResults)
        self.assertEqual({"dp1": "Success", "dp2": "Success"}, result.results)

    def test_set_file(self):
        result, _ = self.call(
            system.set_file, file_in="a.txt", destination="local:/a.txt")
        self.assertEqual(
            [("set_file", {"file_in": "a.txt", "file_out": "local:/a.txt",
                           "domain": "default", "overwrite": True})],
            result.env.actions)
        self.assertEqual({"dp1": "Succeeded", "dp2": "Failed"}, result.results)

    def test_get_file(self):
        result, messages = self.call(
            system.get_file, location="local:/dir/a.txt", Domain="d",
            out_dir=os.path.join(self.out_dir, "new"))
        self.assertEqual(
            [("getfile", {"domain": "d", "filename": "local:/dir/a.txt"})],
            result.env.actions)
        files = self.files()
        self.assertEqual(2, len(files))
        self.assertRegex(files[0], r"^new/dp1-.*-a\.txt$")
        with open(os.path.join(self.out_dir, files[0]), "rb") as fin:
            self.assertEqual(b"the file", fin.read())
        self.assertEqual(2, len(messages))

    def test_delete_file(self):
        result, _ = self.call(
            system.delete_file, Domain="d", filename="local:/a")
        self.assertEqual(
            [("del_file", {"filename": "local:/a", "domain": "d"})],
            result.env.actions)
        self.assertEqual({"dp1": "Success", "dp2": "Error"}, result.results)

        FakeAppliance.log = []
        result, _ = self.call(
            system.delete_file, Domain="d", filename="local:/a",
            backup=True, out_dir=self.out_dir)
        self.assertEqual(
            [("dp1", "del_file",
              {"filename": "local:/a", "domain": "d", "backup": True,
               "local_dir": os.path.join(self.out_dir, "dp1")}),
             ("dp2", "del_file",
              {"filename": "local:/a", "domain": "d", "backup": True,
               "local_dir": os.path.join(self.out_dir, "dp2")})],
            self.calls())
        self.assertTrue(os.path.isdir(os.path.join(self.out_dir, "dp2")))

    def test_generate_error_report(self):
        result, messages = self.call(system.generate_error_report)
        self.assertEqual(2, len(self.calls("ErrorReport")))
        self.assertEqual(4, len(messages))
        self.assertIn("Successfully generated", messages[1])
        self.assertEqual(2, result.html().count("Succeeded"))

    def test_get_error_reports(self):
        def download(appliances, out_dir, ers, timestamp):
            for appliance in appliances:
                path = os.path.join(out_dir, appliance.hostname, "report.txt.gz")
                with gzip.open(path, "wb") as fout:
                    fout.write(b"the report")

        with mock.patch(
                "mast.datapower.system.system._pmr_get_error_report_settings"), \
                mock.patch(
                    "mast.datapower.system.system._pmr_download_error_reports",
                    download):
            result, _ = self.call(
                system.get_error_reports, out_dir=self.out_dir)
            self.assertEqual(
                ["dp1/report.txt.gz", "dp2/report.txt.gz"], self.files())

            result, messages = self.call(
                system.get_error_reports, out_dir=self.out_dir,
                decompress=True)
        self.assertEqual(["dp1/report.txt", "dp2/report.txt"], self.files())
        with open(os.path.join(self.out_dir, "dp1", "report.txt")) as fin:
            self.assertEqual("the report", fin.read())
        self.assertIsInstance(result, SeeDownload)
        self.assertIn("dp2-get_error_reports", result.html())

    def test_copy_directory(self):
        result, messages = self.call(
            system.copy_directory, location="local:/dir", Domain="d",
            recursive=True, out_dir=self.out_dir)
        calls = [entry for entry in FakeAppliance.log
                 if entry[1] == "copy_directory"]
        self.assertEqual(2, len(calls))
        location, out_dir, domain = calls[0][2]
        self.assertEqual(("local:/dir", "d"), (location, domain))
        self.assertTrue(os.path.isdir(out_dir))
        self.assertTrue(out_dir.endswith("dp1"))
        self.assertEqual({"recursive": True}, calls[0][3])
        self.assertIn("dp1-copy_directory", result.html())

    def test_fetch_logs(self):
        result, _ = self.call(system.fetch_logs, out_dir=self.out_dir)
        self.assertEqual(
            [("get_all_logs", {"log_dir": self.out_dir})], result.env.actions)
        self.assertIsInstance(result, SeeDownload)

    def test_get_pmr_info(self):
        names = [
            "_pmr_create_dirs", "_pmr_get_error_report_settings",
            "_pmr_conditionally_save_internal_state",
            "_pmr_generate_error_reports", "_pmr_backup_all_domains",
            "_pmr_query_status_providers", "_pmr_download_error_reports",
            "_pmr_cleanup"]
        order = []
        with contextlib.ExitStack() as stack:
            for name in names:
                stack.enter_context(mock.patch(
                    "mast.datapower.system.system." + name,
                    side_effect=lambda *args, name=name: order.append(name)))
            result, _ = self.call(system.get_pmr_info, out_dir=self.out_dir)
        self.assertEqual(names, order)
        self.assertIn("dp1-get_pmr_info", result.html())

    def test_object_audit(self):
        result, messages = self.call(
            system.objects_audit, out_dir=self.out_dir)
        files = self.files()
        self.assertEqual(2, len(files))
        self.assertRegex(
            files[0], r"^dp1/object_audit/.*/object-audit\.xml$")
        with open(os.path.join(self.out_dir, files[0])) as fin:
            self.assertEqual("<diff/>", fin.read())
        self.assertEqual(2, len(messages))

    def test_get_status(self):
        result, _ = self.call(
            system.get_status, appliances=["dp1"],
            StatusProvider=["Version", "CPUUsage"], Domain="d")
        self.assertIsInstance(result, XMLResults)
        self.assertEqual(
            [("get_status", {"provider": "Version", "domain": "d"}),
             ("get_status", {"provider": "CPUUsage", "domain": "d"})],
            result.env.actions)
        text = result.text()
        self.assertRegex(text, r"\n\ndp1 - Version - \S+\n\npretty <env")
        self.assertRegex(text, r"\n\ndp1 - CPUUsage - ")
        html = result.html()
        self.assertIn(">dp1-get_status<", html)
        for cell in ("Version", "10.5", "Build.Date", "2026", "Build.Level"):
            self.assertIn(">{}<".format(cell), html)
        # Each value appears once for each provider
        self.assertEqual(2, html.count(">Build.Level<"))

        result, _ = self.call(
            system.get_status, StatusProvider="Version", machine=True)
        self.assertIn("collapsed", result.text())

    def test_get_config(self):
        result, _ = self.call(
            system.get_config, appliances=["dp1"], ObjectClass="Domain",
            obj_name="app", recursive=True, persisted=True, Domain="d")
        self.assertEqual(
            [("get_config", {"_class": "Domain", "name": "app",
                             "recursive": True, "persisted": True,
                             "domain": "d"})],
            result.env.actions)
        self.assertRegex(result.text(), r"\n\ndp1 - Domain - \S+\n\npretty ")
        html = result.html()
        for cell in ("dp1-get_config", "Domain", "app", "mAdminState",
                     "enabled", "ConfigDir.Path", "config:"):
            self.assertIn(">{}<".format(cell), html)
        self.assertEqual(1, html.count(">ConfigDir.Path<"))

    def test_results_go_to_the_out_file(self):
        out_file = os.path.join(self.out_dir, "new", "status.txt")
        for command, kwargs in (
                (system.get_status, {"StatusProvider": ["Version"]}),
                (system.get_config, {"ObjectClass": "Domain"})):
            with self.subTest(command=command.__name__):
                result, messages = self.call(
                    command, out_file=out_file, **kwargs)
                with open(out_file) as fin:
                    self.assertIn("\n\ndp1 - ", fin.read())
                # The terminal gets the name of the file, not the XML
                self.assertEqual(
                    ["Wrote the results to " + out_file], messages)
                self.assertEqual("", result.text())
                # The web GUI still shows the values
                self.assertIn(">dp1-", result.html())

    def test_clean_up(self):
        with mock.patch(
                "mast.datapower.system.system._clean_error_reports") as clean:
            result, _ = self.call(
                system.clean_up, appliances=["dp1"], checkpoints=True,
                export=True, logtemp=True, logstore=True, error_reports=True,
                backup_files=True, out_dir=self.out_dir)
        self.assertIsInstance(result, Table)
        self.assertEqual(
            [["dp1", "chkpoints:/", "Cleaned"],
             ["dp1", "export:/", "Cleaned"],
             ["dp1", "logtemp:/", "Cleaned"],
             ["dp1", "logstore:/", "Cleaned"],
             ["dp1", "ErrorReports", "Cleaned"]], result.rows)
        self.assertEqual(1, clean.call_count)
        self.assertEqual(
            ["chkpoints://a.txt", "export://a.txt", "logtemp://a.txt",
             "logstore://a.txt"],
            [kwargs["File"] for _, _, kwargs in self.calls("DeleteFile")])
        files = self.files()
        self.assertEqual(4, len(files))
        self.assertRegex(files[0], r"^dp1/.*/default/chkpoints/a\.txt$")
        self.assertIn("request: x", result.history)

        # Without --backup-files nothing is saved
        self.out_dir = tempfile.mkdtemp(dir=self.out_dir)
        result, _ = self.call(
            system.clean_up, appliances=["dp1"], export=True,
            out_dir=self.out_dir)
        self.assertEqual([], self.files())
        self.assertEqual([["dp1", "export:/", "Cleaned"]], result.rows)


class TestSystemCommandLine(SystemTestCase):

    def test_command_line(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.run(["xor", "-s", "user:pass"])
            cli.run(["save", "-a", "dp1", "-c", "u:p", "-n",
                     "-D", "app1", "-D", "app2"])
            cli.run(["quiesce-domain", "-a", "dp1", "-D", "app1", "-q", "61"])
            cli.run(["disable-domain", "-a", "dp1", "-D", "app1", "-s"])
            cli.run(["list-domains", "-a", "dp1"])
            cli.run(["get-status", "-a", "dp1", "-S", "Version", "-m"])
            cli.run(["get-config", "-a", "dp1", "-O", "Domain", "-o", "app",
                     "-r", "-p"])
            cli.run(["clean-up", "-a", "dp1", "-e", "-l"])
            cli.run(["reboot-appliance", "-a", "dp1", "-d", "1", "-w", "9"])
        text = out.getvalue()
        self.assertIn(xorencode("user:pass"), text)
        self.assertLess(
            text.index("Attempting to save configuration of app1 on dp1"),
            text.index("Attempting to save configuration of app2 on dp1"))
        self.assertIn("dp1-app2\n========", text)
        self.assertIn("Attempting to quiesce domain app1 on dp1", text)
        self.assertIn("\nCommon\n======\n\tapp1", text)
        self.assertIn("dp1 - Version - ", text)
        self.assertIn("collapsed", text)
        self.assertIn("dp1 - Domain - ", text)
        self.assertRegex(text, r"dp1 +logtemp:/ +Cleaned")
        self.assertIn("Attempting to reboot dp1", text)

    def test_required_parameters(self):
        for command, kwargs in (
                (system.add_domain, {}),
                (system.set_file, {"file_in": "a"}),
                (system.set_file, {"destination": "local:/a"}),
                (system.get_file, {})):
            with self.subTest(command=command.__name__):
                with self.assertRaises(ValueError):
                    command(appliances=["dp1"], **kwargs)

        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
            cli.run(["firmware-upgrade", "-a", "dp1"])
        self.assertIn("-f/--file-in", err.getvalue())

    def test_credentials_are_not_in_repr(self):
        from mast.datapower.system.system import FirmwareUpgrade
        params = FirmwareUpgrade(file_in="f", credentials=["admin:hunter2"])
        self.assertNotIn("hunter2", repr(params))

    def test_no_command_takes_a_web_parameter(self):
        self.assertEqual(47, len(cli.commands))
        for command in cli.commands.values():
            self.assertIsNotNone(command.model, command.name)
            self.assertNotIn("web", command.model.model_fields)


if __name__ == "__main__":
    unittest.main()

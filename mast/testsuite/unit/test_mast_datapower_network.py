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
Unit tests for the commands of mast.datapower.network, with the
appliances replaced by fakes.
"""
import contextlib
import io
import unittest
import urllib.error
from xml.etree import ElementTree as etree

from unittest import mock

import mast.datapower.network as network
from mast.cli import Table, captured
from mast.datapower.network.network import (
    EthernetInterfaceResults, cli)
from mast.plugin_utils.results import ActionResults, Report

ENVELOPE = (
    '<env:Envelope xmlns:env="http://schemas.xmlsoap.org/soap/envelope/">'
    '<env:Body>'
    '<dp:response xmlns:dp="http://www.datapower.com/schemas/management">'
    '<dp:{kind}>{body}</dp:{kind}>'
    '</dp:response></env:Body></env:Envelope>')

ROUTES = (
    "<RoutingStatus3><Destination>0.0.0.0/0</Destination>"
    "<Device>eth0</Device><Metric>0</Metric></RoutingStatus3>"
    "<RoutingStatus3><Destination>10.0.0.0/8</Destination>"
    "<Device>eth1</Device><Metric/></RoutingStatus3>")

INTERFACE = (
    '<EthernetInterface name="eth0"><mAdminState>enabled</mAdminState>'
    '<IPAddress>10.0.0.5/24</IPAddress>'
    '<StaticRoutes><Destination>10.1.0.0/16</Destination>'
    '<Gateway>10.0.0.1</Gateway></StaticRoutes></EthernetInterface>')


class FakeResponse(object):
    def __init__(self, ok=True, xml="<result>OK</result>"):
        self.xml = etree.fromstring(xml)
        self.ok = ok

    def __bool__(self):
        return self.ok


class FakeAppliance(object):
    history = "request: x\nresponse: y"

    def __init__(self, hostname):
        self.hostname = hostname
        self.tests = []

    def check_xml_mgmt(self):
        return True

    def check_web_mgmt(self):
        return self.hostname == "dp1"

    def check_cli_mgmt(self):
        return False

    def TCPConnectionTest(self, RemoteHost, RemotePort):
        self.tests.append((RemoteHost, RemotePort))
        return FakeResponse(ok=RemotePort == "443")


class FakeEnvironment(object):
    # What perform_action returns for the actions that do not return
    # a response from each appliance.
    answers = {
        "get_host_aliases": {
            "dp1": [("alias1", "10.0.0.1", "enabled"),
                    ("both", "10.0.0.9", "enabled")],
            "dp2": [("both", "10.0.0.9", "enabled")]},
        "get_secondary_addresses": {
            "dp1": ["10.0.0.2/24", "10.0.0.3/24"], "dp2": []},
        "get_static_hosts": {
            "dp1": [("host1", "10.0.0.4")], "dp2": [("host2", "10.0.0.5")]},
        "get_static_routes": {
            "dp1": [("10.1.0.0/16", "10.0.0.1", "5")], "dp2": []},
    }
    routing_status_3 = True

    def __init__(self, hostnames, credentials=None, timeout=120,
                 check_hostname=True):
        self.appliances = [FakeAppliance("dp1"), FakeAppliance("dp2")]
        self.timeout = timeout
        self.actions = []

    def perform_action(self, action, **kwargs):
        self.actions.append((action, kwargs))
        if action in self.answers:
            return self.answers[action]
        if action == "get_status":
            if kwargs["provider"] == "RoutingStatus3" \
                    and not self.routing_status_3:
                raise urllib.error.HTTPError("url", 500, "no", {}, None)
            xml = ENVELOPE.format(
                kind="status",
                body=ROUTES.replace("RoutingStatus3", kwargs["provider"]))
            return {"dp1": FakeResponse(xml=xml)}
        if action == "get_config":
            xml = ENVELOPE.format(kind="config", body=INTERFACE)
            return {"dp1": FakeResponse(xml=xml)}
        return {"dp1": FakeResponse(), "dp2": FakeResponse(ok=False)}

    perform_async_action = perform_action


class TestNetworkCommands(unittest.TestCase):

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

    def test_check_connectivity(self):
        result, _ = self.call(network.check_connectivity)
        self.assertIsInstance(result, Table)
        self.assertEqual(["Appliance", "XML", "Web", "CLI"], result.header)
        self.assertEqual(
            [["dp1", "True", "True", "False"],
             ["dp2", "True", "False", "False"]], result.rows)
        self.assertIn("request: x", result.history)

    def test_tcp_connection_test(self):
        result, _ = self.call(
            network.tcp_connection_test,
            remote_hosts=["a", "b"], remote_ports=["80", "443"])
        self.assertEqual(8, len(result.rows))
        self.assertEqual(["dp1", "a", "80", "False"], result.rows[0])
        self.assertEqual(["dp2", "b", "443", "True"], result.rows[-1])

    def test_display_routing_table(self):
        result, _ = self.call(network.display_routing_table)
        self.assertEqual(
            ["Appliance", "Destination", "Device", "Metric"], result.header)
        self.assertEqual(
            [["dp1", "0.0.0.0/0", "eth0", "0"],
             ["dp1", "10.0.0.0/8", "eth1", ""]], result.rows)

    def test_display_routing_table_falls_back_to_routing_status_2(self):
        with mock.patch.object(FakeEnvironment, "routing_status_3", False):
            result, _ = self.call(network.display_routing_table)
        self.assertEqual(2, len(result.rows))
        self.assertEqual("eth0", result.rows[0][2])

    def test_display_ethernet_interface(self):
        result, _ = self.call(
            network.display_ethernet_interface, EthernetInterface="eth0")
        self.assertIsInstance(result, EthernetInterfaceResults)
        self.assertEqual(
            [("get_config", {"_class": "EthernetInterface", "name": "eth0",
                             "persisted": True})],
            result.env.actions)
        self.assertIn("dp1\n===", result.text())
        self.assertIn("10.0.0.5/24", result.text())
        html = result.html()
        self.assertIn(">eth0<", html)
        self.assertIn(">mAdminState<", html)
        self.assertIn(">StaticRoutes.Gateway<", html)
        self.assertIn(">10.0.0.1<", html)

        result, _ = self.call(
            network.display_ethernet_interface, persisted=False)
        self.assertFalse(result.env.actions[0][1]["persisted"])

    def test_display_ethernet_interface_uses_the_timeout(self):
        result, _ = self.call(network.display_ethernet_interface, timeout=7)
        self.assertEqual(7, result.env.timeout)

    def test_list_host_aliases(self):
        result, _ = self.call(network.list_host_aliases)
        self.assertEqual(
            [["dp1", "alias1", "10.0.0.1", "enabled"],
             ["dp1", "both", "10.0.0.9", "enabled"],
             ["dp2", "both", "10.0.0.9", "enabled"],
             ["common", "both", "10.0.0.9", "enabled"]], result.rows)

    def test_lists(self):
        cases = (
            (network.list_secondary_addresses, {"EthernetInterface": "eth0"},
             ("get_secondary_addresses", {"interface": "eth0"}),
             [["dp1", "10.0.0.2/24"], ["dp1", "10.0.0.3/24"]]),
            (network.list_static_hosts, {},
             ("get_static_hosts", {}),
             [["dp1", "host1", "10.0.0.4"], ["dp2", "host2", "10.0.0.5"]]),
            (network.list_static_routes, {"EthernetInterface": "eth0"},
             ("get_static_routes", {"interface": "eth0"}),
             [["dp1", "10.1.0.0/16", "10.0.0.1", "5"]]),
        )
        for command, kwargs, action, rows in cases:
            with self.subTest(action=action[0]):
                with mock.patch.object(
                        FakeEnvironment, "perform_action",
                        autospec=True,
                        side_effect=FakeEnvironment.perform_action) as perform:
                    with mock.patch.object(
                            FakeEnvironment, "perform_async_action", perform):
                        result, _ = self.call(command, **kwargs)
                self.assertEqual(action[0], perform.call_args[0][1])
                self.assertEqual(action[1], perform.call_args[1])
                self.assertEqual(rows, result.rows)

    def test_changes(self):
        cases = (
            (network.add_host_alias, {"name": "n", "ip": "10.0.0.1"},
             "add_host_alias",
             {"name": "n", "ip": "10.0.0.1", "admin_state": "enabled"}),
            (network.del_host_alias, {"HostAlias": "n"},
             "del_host_alias", {"name": "n"}),
            (network.add_secondary_address,
             {"EthernetInterface": "eth0", "secondary_address": "10.0.0.2"},
             "add_secondary_address",
             {"ethernet_interface": "eth0", "secondary_address": "10.0.0.2"}),
            (network.del_secondary_address,
             {"EthernetInterface": "eth0", "secondary_address": "10.0.0.2"},
             "del_secondary_address",
             {"ethernet_interface": "eth0", "secondary_address": "10.0.0.2"}),
            (network.add_static_host, {"hostname": "h", "ip": "10.0.0.3"},
             "add_static_host", {"hostname": "h", "ip": "10.0.0.3"}),
            (network.del_static_host, {"hostname": "h"},
             "del_static_host", {"hostname": "h"}),
            (network.add_static_route,
             {"EthernetInterface": "eth0", "destination": "10.1.0.0/16",
              "gateway": "10.0.0.1", "metric": 5},
             "add_static_route",
             {"ethernet_interface": "eth0", "destination": "10.1.0.0/16",
              "gateway": "10.0.0.1", "metric": "5"}),
            (network.del_static_route,
             {"EthernetInterface": "eth0", "destination": "10.1.0.0/16"},
             "del_static_route",
             {"ethernet_interface": "eth0", "destination": "10.1.0.0/16"}),
        )
        for command, kwargs, action, expected in cases:
            with self.subTest(action=action, save_config=False):
                result, messages = self.call(command, **kwargs)
                self.assertIsInstance(result, Report)
                self.assertEqual([(action, expected)], result.env.actions)
                self.assertEqual(2, len(messages))
                html = result.html()
                self.assertIn("dp1-" + action, html)
                self.assertEqual(1, html.count("Succeeded"))
                self.assertEqual(1, html.count("Failed"))

            with self.subTest(action=action, save_config=True):
                result, messages = self.call(
                    command, save_config=True, **kwargs)
                self.assertEqual(
                    [(action, expected), ("SaveConfig", {"domain": "default"})],
                    result.env.actions)
                self.assertEqual(4, len(messages))
                # The save has its own rows, with its own responses
                self.assertIn("dp1-save_config", result.html())
                self.assertEqual(2, result.html().count("Succeeded"))

    def test_flush_caches(self):
        cases = (
            (network.flush_arp_cache, {}, "FlushArpCache", {}),
            (network.flush_dns_cache, {}, "FlushDNSCache", {}),
            (network.flush_nd_cache, {}, "FlushNDCache", {}),
            (network.flush_pdp_cache, {"XACMLPDP": "p"},
             "FlushPDPCache", {"XACMLPDP": "p"}),
        )
        for command, kwargs, action, expected in cases:
            with self.subTest(action=action):
                result, messages = self.call(command, **kwargs)
                self.assertIsInstance(result, ActionResults)
                self.assertEqual([(action, expected)], result.env.actions)
                self.assertEqual(1, len(messages))
                self.assertEqual(1, result.html().count("Failed"))

    def test_required_parameters_and_choices(self):
        with self.assertRaises(ValueError):
            network.add_host_alias(appliances=["dp1"], name="n")
        with self.assertRaises(ValueError):
            network.add_host_alias(
                appliances=["dp1"], name="n", ip="i", admin_state="up")
        for metric in (-1, 256, "many"):
            with self.assertRaises(ValueError):
                network.add_static_route(
                    appliances=["dp1"], destination="d", gateway="g",
                    metric=metric)

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
            status["check-connectivity"] = self.run_cli(
                "check-connectivity", "-a", "dp1", "-c", "u:p", "-n")
            status["tcp-connection-test"] = self.run_cli(
                "tcp-connection-test", "-a", "dp1", "-r", "a", "-R", "443")
            status["add-static-route"] = self.run_cli(
                "add-static-route", "-a", "dp1", "-E", "eth0",
                "-d", "10.1.0.0/16", "-g", "10.0.0.1", "-m", "5", "-s")
            status["display-ethernet-interface"] = self.run_cli(
                "display-ethernet-interface", "-a", "dp1", "-E", "eth0",
                "--no-persisted")
            status["flush-pdp-cache"] = self.run_cli(
                "flush-pdp-cache", "-a", "dp1", "-X", "p")
        # The fake dp2 refuses every action, and that is the exit
        # status. What a test of the network found is not a failure of
        # the command.
        self.assertEqual(status, {
            "check-connectivity": 0, "tcp-connection-test": 0,
            "add-static-route": 1, "display-ethernet-interface": 0,
            "flush-pdp-cache": 1})
        text = out.getvalue()
        self.assertRegex(text, r"Appliance +XML +Web +CLI")
        self.assertRegex(text, r"dp1 +a +443 +True")
        self.assertLess(
            text.index("Attempting to add static route"),
            text.index("Attempting to save config"))
        self.assertIn("10.0.0.5/24", text)

        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
            cli.run(["add-static-route", "-a", "dp1", "-d", "d", "-g", "g",
                     "-m", "300"])
        self.assertIn("--metric", err.getvalue())
        self.assertIn("0 to 255", err.getvalue())

    def test_no_command_takes_a_web_parameter(self):
        self.assertEqual(20, len(cli.commands))
        for command in cli.commands.values():
            self.assertIsNotNone(command.model, command.name)
            self.assertNotIn("web", command.model.model_fields)

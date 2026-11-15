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
Unit tests for the commands of mast.datapower.developer, with the
appliances replaced by fakes. The integration tests run the same
commands against a real DataPower.
"""
import contextlib
import glob
import io
import os
import tempfile
import unittest
from xml.etree import ElementTree as etree

import mock

import mast.datapower.developer as developer
from mast.cli import captured
from mast.datapower.developer.developer import cli
from mast.plugin_utils.results import ActionResults, ListResults, SeeDownload


class FakeResponse(object):
    def __init__(self, text="<ok/>", ok=True):
        self.xml = etree.fromstring(text)
        self.pretty = text.encode()
        self.ok = ok

    def __bool__(self):
        return self.ok


class FakeAppliance(object):
    hostname = "dp1"
    domains = ["default", "other"]
    history = "request: x\nresponse: y"

    def __init__(self):
        self.imports = []

    def do_import(self, **kwargs):
        self.imports.append(kwargs)
        return FakeResponse()

    def get_config(self, **kwargs):
        return FakeResponse(
            '<env:Envelope'
            ' xmlns:env="http://schemas.xmlsoap.org/soap/envelope/"'
            ' xmlns:dp="http://www.datapower.com/schemas/management">'
            '<env:Body><dp:response><dp:config>'
            '<MultiProtocolGateway name="probed"><DebugMode>on</DebugMode>'
            '</MultiProtocolGateway>'
            '<MultiProtocolGateway name="quiet"><DebugMode>off</DebugMode>'
            '</MultiProtocolGateway>'
            '</dp:config></dp:response></env:Body></env:Envelope>')


class FakeEnvironment(object):
    def __init__(self, *args, **kwargs):
        self.created_with = (args, kwargs)
        self.appliances = [FakeAppliance()]
        self.actions = []

    def perform_action(self, action, **kwargs):
        self.actions.append((action, kwargs))
        if action == "export":
            return {"dp1": b"the export"}
        return {"dp1": FakeResponse()}


class TestDeveloperCommands(unittest.TestCase):

    def setUp(self):
        patch = mock.patch(
            "mast.plugin_utils.params.Environment", FakeEnvironment)
        patch.start()
        self.addCleanup(patch.stop)
        self.out_dir = tempfile.mkdtemp(dir=os.path.join(
            os.environ["MAST_HOME"], "tmp"))

    def test_environment_is_built_from_the_shared_parameters(self):
        result = developer.flush_document_cache(
            appliances=["dp1"], credentials=["user:pass"], timeout=5,
            no_check_hostname=True, Domain="default", xml_manager="default")
        self.assertEqual(
            ((["dp1"], ["user:pass"]),
             {"timeout": 5, "check_hostname": False}),
            result.env.created_with)

    def test_flush_caches(self):
        for command, action in (
                (developer.flush_document_cache, "FlushDocumentCache"),
                (developer.flush_stylesheet_cache, "FlushStylesheetCache")):
            with captured() as messages:
                result = command(
                    appliances=["dp1"], Domain="d", xml_manager="m")
            self.assertIsInstance(result, ActionResults)
            self.assertEqual(
                [(action, {"XMLManager": "m", "domain": "d"})],
                result.env.actions)
            self.assertEqual(1, len(messages))
            self.assertIn("Succeeded", result.html())
            self.assertIn("request: x", result.history)

    def test_export_writes_one_file_for_each_appliance(self):
        with captured() as messages:
            result = developer.export(
                appliances=["dp1"], Domain="d", object_name="all-objects",
                object_class="all-classes", format="XML",
                out_dir=self.out_dir)
        self.assertIsInstance(result, SeeDownload)
        (filename,) = glob.glob(
            os.path.join(self.out_dir, "dp1", "*", "*-all-objects.xcfg"))
        with open(filename, "rb") as fin:
            self.assertEqual(b"the export", fin.read())
        self.assertIn(filename, messages[0])
        action, kwargs = result.env.actions[0]
        self.assertEqual("export", action)
        self.assertEqual("XML", kwargs["format"])
        self.assertTrue(kwargs["persisted"])
        self.assertIn("See Download", result.html())

    def test_export_requires_a_name_and_a_class(self):
        with self.assertRaises(ValueError):
            developer.export(appliances=["dp1"], Domain="d")
        with self.assertRaises(ValueError):
            developer.export(
                appliances=["dp1"], object_name="a", object_class="b",
                format="TAR")

    def test_import_into_all_domains(self):
        with captured():
            result = developer._import(
                appliances=["dp1"], Domain=["all-domains"],
                file_in="export.zip", overwrite_files=False,
                out_dir=self.out_dir)
        imports = result.env.appliances[0].imports
        self.assertEqual(["default", "other"], [i["domain"] for i in imports])
        self.assertEqual("export.zip", imports[0]["zip_file"])
        self.assertFalse(imports[0]["overwrite_files"])
        self.assertTrue(imports[0]["overwrite_objects"])
        self.assertEqual("ZIP", imports[0]["source_type"])
        self.assertEqual(2, len(glob.glob(os.path.join(
            self.out_dir, "import_results", "*", "dp1-*-import_results.xml"))))

    def test_list_probes(self):
        result = developer.list_probes(appliances=["dp1"], Domain=["default"])
        self.assertIsInstance(result, ListResults)
        self.assertEqual(
            {"dp1-default": ["MultiProtocolGateway - probed"]},
            result.results)
        self.assertIn("MultiProtocolGateway - probed", result.text())
        self.assertIn("MultiProtocolGateway - probed", result.html())

    def test_command_line(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.run([
                "export", "-a", "dp1", "-c", "user:pass", "-n", "-D", "d",
                "-o", "all-objects", "-O", "all-classes", "-C", "a comment",
                "--out-dir", self.out_dir])
            cli.run(["flush-document-cache", "-a", "dp1", "-D", "d", "-x", "m"])
        self.assertIn("Writing export of all-objects from dp1", out.getvalue())
        # The response from each appliance is printed under its hostname
        self.assertIn("dp1\n===", out.getvalue())
        self.assertIn("ok", out.getvalue())

    def test_no_command_takes_a_web_parameter(self):
        for command in cli.commands.values():
            self.assertIsNotNone(command.model, command.name)
            self.assertNotIn("web", command.model.model_fields)


if __name__ == "__main__":
    unittest.main()

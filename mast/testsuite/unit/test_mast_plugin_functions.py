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
Unit tests for the web forms that mast.plugin_utils.plugin_functions
builds from the commands of a plugin.
"""
import os
import types
import unittest
from typing import Literal

import flask
import mock

import mast.plugin_utils.plugin_functions as pf
from mast.cli import Cli, Option, Table, say
from mast.plugin_utils.params import (
    ApplianceParams, OBJECT_CLASS, OUT_DIR, UPLOAD, configured)
from mast.xor import xorencode

COOKIE = "9x4h/mmek/j.ahba.ckhafn"
SESSION = "session-key"

cli = Cli()
calls = []


class Example(ApplianceParams):
    Domain: str = Option("", short="D", widget=configured("Domain"))
    names: list[str] = Option(default_factory=list, short="N")
    file_in: str | None = Option(None, short="f", widget=UPLOAD)
    kind: str = Option("", widget=OBJECT_CLASS)
    format: Literal["ZIP", "XML"] = Option("ZIP")
    new_password: str = Option("", secret=True)
    retries: int = Option(3, help="How many times to try")
    force: bool = Option(False)
    keep: bool = Option(True)
    out_dir: str = Option("tmp", widget=OUT_DIR)


@cli.command("example", category="examples")
def example(params: Example):
    """An example command."""
    calls.append(params)
    say("progress that the web GUI does not show")
    os.makedirs(params.out_dir)
    with open(os.path.join(params.out_dir, "result.txt"), "w") as fout:
        fout.write("result")
    return Table(("Appliance", "Result"), [("dp1", "<ok>")], history="the history")


@cli.command("legacy", category="examples")
def legacy(appliances=[], credentials=[], name="", web=False):
    """A legacy command."""
    calls.append((appliances, credentials, name, web))
    return "legacy output", "legacy history"


@cli.command("broken", category="examples")
def broken(params: ApplianceParams):
    raise RuntimeError("it broke")


class FakeEnvironment(object):
    def __init__(self, *args, **kwargs):
        pass

    def common_config(self, object_class):
        return ["{}-1".format(object_class), "{}-2".format(object_class)]


class WebFormTestCase(unittest.TestCase):

    def setUp(self):
        del calls[:]
        home = os.environ["MAST_HOME"]
        self.app = flask.Flask(
            __name__,
            template_folder=os.path.join(home, "var", "www", "templates"))
        os.makedirs(os.path.join(home, "var", "www", "static", "tmp"),
                    exist_ok=True)
        patches = [
            mock.patch.object(
                pf, "get_module",
                return_value=types.SimpleNamespace(cli=cli)),
            mock.patch.object(pf, "Environment", FakeEnvironment),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def post(self, **form):
        """Call a command the way the web GUI does and return the html."""
        form.setdefault("appliances[]", "dp1")
        form.setdefault("credentials[]", xorencode(
            "user:pass", key=xorencode(SESSION, key="_")))
        with self.app.test_request_context(
                "/example", method="POST", data=form,
                headers={"Cookie": "{}={}".format(COOKIE, SESSION)}):
            return pf.call_method("example", flask.request.form)

    def test_buttons(self):
        with self.app.test_request_context("/"):
            html = pf.html("mast.datapower.example")
        for name in ("example", "legacy", "broken"):
            self.assertIn(name, html)

    def test_form_for_a_model_command(self):
        with self.app.test_request_context("/"):
            form = pf.get_form("example", "example", ["dp1"], ["user:pass"])
        # The GUI supplies these, so the form does not ask for them
        for name in ("appliances", "credentials", "out_dir"):
            self.assertNotIn('name="{}"'.format(name), form)
        self.assertIn("<select name=Domain>", form)
        self.assertIn('<option value="Domain-1">', form)
        self.assertIn('<div id="names" class="multiTextOuter">', form)
        self.assertIn('id="fileUploadForm_file_in"', form)
        self.assertIn("<select name=kind>", form)
        self.assertIn('<option value="XML">', form)
        self.assertIn('<input type="password" name="new_password"', form)
        self.assertIn('name="retries" value="3"', form)
        self.assertIn('name="timeout" value="120"', form)
        self.assertRegex(form, r'type="checkbox" name="force"\s+/>')
        self.assertRegex(form, r'type="checkbox" name="keep" checked=checked')
        # The help comes from the docstring and the model
        self.assertIn("An example command.", form)
        self.assertIn("How many times to try", form)

    def test_call_a_model_command(self):
        html = self.post(**{
            "callable": "example", "Domain": "default", "names[]": "a",
            "retries": "5", "force": "true", "format": ""})
        self.assertIn("&lt;ok&gt;", html)
        self.assertIn("href", html)   # the link to the download of out_dir
        self.assertNotIn("progress", html)
        (params,) = calls
        self.assertEqual(["dp1"], params.appliances)
        self.assertEqual(["user:pass"], params.credentials)
        self.assertEqual("default", params.Domain)
        self.assertEqual(["a"], params.names)
        self.assertEqual(5, params.retries)
        self.assertTrue(params.force)
        # An unchecked checkbox is off, whatever the default
        self.assertFalse(params.keep)
        # An empty control leaves the default
        self.assertEqual("ZIP", params.format)
        self.assertEqual(120, params.timeout)
        self.assertTrue(params.out_dir.startswith(
            os.path.join("tmp", "web", "example")))

    def test_invalid_value_is_reported(self):
        html = self.post(callable="example", retries="many")
        self.assertIn("retries", html)
        self.assertIn("valid integer", html)
        self.assertEqual([], calls)

    def test_no_appliance_selected(self):
        html = self.post(**{"callable": "example", "appliances[]": ""})
        self.assertIn("Must select at least one appliance.", html)
        self.assertEqual([], calls)

    def test_exception_is_reported(self):
        html = self.post(callable="broken")
        self.assertIn("it broke", html)

    def test_legacy_command_still_works(self):
        with self.app.test_request_context("/"):
            form = pf.get_form("example", "legacy", ["dp1"], ["user:pass"])
        self.assertIn('name="name"', form)
        html = self.post(callable="legacy", name="x")
        self.assertIn("legacy output", html)
        # The legacy path passes the credentials on as bytes
        self.assertEqual([(["dp1"], [b"user:pass"], "x", True)], calls)


if __name__ == "__main__":
    unittest.main()

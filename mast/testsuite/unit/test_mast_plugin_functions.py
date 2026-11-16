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
import glob
import os
import re
import threading
import time
import types
import unittest
from typing import Literal

import flask
import mock

import mast.plugin_utils.plugin_functions as pf
from mast import jobs
from mast.cli import Cli, Option, Params, Table, say
from mast.plugin_utils.params import (
    ApplianceParams, OBJECT_CLASS, OUT_DIR, OUT_FILE, UPLOAD, configured)
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
    os.makedirs(params.out_dir, exist_ok=True)
    with open(os.path.join(params.out_dir, "result.txt"), "w") as fout:
        fout.write("result")
    return Table(("Appliance", "Result"), [("dp1", "<ok>")], history="the history")


@cli.command("broken", category="examples")
def broken(params: ApplianceParams):
    raise RuntimeError("it broke")


class FileExample(ApplianceParams):
    out_file: str = Option("tmp/result.txt", widget=OUT_FILE)
    fail: bool = Option(False)


@cli.command("file-example", category="examples")
def file_example(params: FileExample):
    calls.append(params)
    if params.fail:
        raise RuntimeError("it broke before writing the file")
    os.makedirs(os.path.dirname(params.out_file), exist_ok=True)
    with open(params.out_file, "w") as fout:
        fout.write("the result")
    return Table(("Appliance", "Result"), [("dp1", "Done")])


@cli.command("slow", category="examples")
def slow(params: ApplianceParams):
    """A command with many steps."""
    for step in range(400):
        say("step", step)
        time.sleep(0.02)
    return Table(("Appliance", "Result"), [("dp1", "all the steps")])


class DirExample(ApplianceParams):
    out_dir: str = Option("tmp", widget=OUT_DIR)


class Encode(Params):
    string: str = Option("")


@cli.command("encode", category="examples", queued=False)
def encode(params: Encode):
    """A utility that must not be stored."""
    return Table(("Result",), [("encoded " + params.string,)])


@cli.command("partial", category="examples")
def partial(params: DirExample):
    """A command that fails after it writes a file."""
    os.makedirs(params.out_dir, exist_ok=True)
    with open(os.path.join(params.out_dir, "part.txt"), "w") as fout:
        fout.write("part of the result")
    raise RuntimeError("it broke in the middle")


@cli.command("nothing", category="examples")
def nothing(params: DirExample):
    """A command that fails before it writes anything."""
    raise RuntimeError("it broke at the start")


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
        self.addCleanup(jobs.registry.unregister, "example")

    def submit(self, **form):
        """Submit a form the way the web GUI does and return the html
        of the answer."""
        form.setdefault("appliances[]", "dp1")
        form.setdefault("credentials[]", xorencode(
            "user:pass", key=xorencode(SESSION, key="_")))
        with self.app.test_request_context(
                "/example", method="POST", data=form,
                headers={"Cookie": "{}={}".format(COOKIE, SESSION)}):
            return pf.call_method("example", flask.request.form)

    def job_id(self, html):
        """Return the id of the job in the box that `submit` returned."""
        return re.search(r'class="[^"]*job_pending[^"]*" data-job="(\w+)"',
                         html).group(1)

    def state(self, job_id, after=0):
        with self.app.test_request_context("/jobs/" + job_id):
            return pf.job_state(job_id, after)

    def finished(self, job_id):
        """Wait for a job and return its last state."""
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            state = self.state(job_id)
            if state["finished"]:
                return state
            time.sleep(0.02)
        self.fail("the job {} did not finish".format(job_id))

    def post(self, **form):
        """Call a command the way the web GUI does and return the html
        that the browser shows at the end: the problem with the form,
        or the output of the job."""
        html = self.submit(**form)
        if "job_pending" not in html:
            return html
        return self.finished(self.job_id(html))["html"]

    def test_buttons(self):
        with self.app.test_request_context("/"):
            html = pf.html("mast.datapower.example")
        for name in ("example", "broken"):
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

    def test_a_command_is_a_job(self):
        html = self.submit(callable="example", Domain="default")
        self.assertIn("out - example -", html)
        self.assertIn("job_cancel", html)
        job_id = self.job_id(html)
        state = self.finished(job_id)
        self.assertEqual("succeeded", state["status"])
        self.assertEqual(
            ["progress that the web GUI does not show"], state["events"])
        self.assertIn("&lt;ok&gt;", state["html"])
        # The progress is not sent twice
        self.assertEqual([], self.state(job_id, state["last_event"])["events"])
        info = jobs.get(job_id)
        self.assertEqual("example.example", info.command)
        self.assertEqual(["dp1"], info.targets)
        self.assertEqual({"$secret": None}, info.params["credentials"])
        with self.assertRaises(jobs.JobNotFound):
            self.state("0" * 32)

    def test_the_output_of_a_job_is_made_once(self):
        job_id = self.job_id(self.submit(callable="example"))
        first = self.finished(job_id)["html"]
        history = re.search(r'id="([\w.-]+\.log)"', first).group(1)
        zips = glob.glob(os.path.join(
            "var", "www", "static", "tmp", "*-example-dp1.zip"))
        histories = glob.glob(os.path.join(
            "var", "www", "static", "tmp", "request_history", "*", "*"))
        self.assertIn(history, [os.path.basename(h) for h in histories])
        for _ in range(3):
            self.assertEqual(first, self.state(job_id)["html"])
        self.assertEqual(zips, glob.glob(os.path.join(
            "var", "www", "static", "tmp", "*-example-dp1.zip")))
        self.assertEqual(histories, glob.glob(os.path.join(
            "var", "www", "static", "tmp", "request_history", "*", "*")))
        with open(histories[[os.path.basename(h) for h in histories].index(
                history)]) as fin:
            self.assertEqual("the history", fin.read())

    def test_a_job_shows_its_progress_and_can_be_cancelled(self):
        html = self.submit(callable="slow")
        job_id = self.job_id(html)
        deadline = time.monotonic() + 20
        state = self.state(job_id)
        while len(state["events"]) < 2 and time.monotonic() < deadline:
            time.sleep(0.02)
            state = self.state(job_id)
        self.assertEqual("running", state["status"])
        self.assertNotIn("html", state)
        self.assertEqual("step 0", state["events"][0])
        with self.app.test_request_context(
                "/jobs/{}/cancel".format(job_id), method="POST"):
            self.assertTrue(pf.cancel_job(job_id)["cancel_requested"])
        state = self.finished(job_id)
        self.assertEqual("cancelled", state["status"])
        self.assertIn("was cancelled while it ran", state["html"])
        self.assertNotIn("all the steps", state["html"])

    def test_a_worker_does_the_job_if_one_is_alive(self):
        from mast.jobs.worker import Worker
        worker = Worker(poll_seconds=0.02)
        thread = threading.Thread(target=worker.run)
        thread.start()
        self.addCleanup(thread.join, 10)
        self.addCleanup(worker.stop)
        deadline = time.monotonic() + 10
        while not jobs.workers() and time.monotonic() < deadline:
            time.sleep(0.02)
        job_id = self.job_id(self.submit(callable="example"))
        state = self.finished(job_id)
        self.assertIn("&lt;ok&gt;", state["html"])
        self.assertTrue(jobs.get(job_id).worker_id.startswith(worker.id))

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

    def test_output_file_errors_are_reported_without_a_download(self):
        cases = (
            ({"appliances[]": ""}, "Must select at least one appliance."),
            ({"timeout": "many"}, "valid integer"),
            ({"fail": "true"}, "it broke before writing the file"),
        )
        for form, message in cases:
            with self.subTest(form=form):
                html = self.post(callable="file_example", **form)
                self.assertIn(message, html)
                self.assertNotIn("static/tmp/", html)

    def test_output_dir_errors_are_reported_without_a_download(self):
        cases = (
            ({"appliances[]": ""}, "Must select at least one appliance."),
            ({"timeout": "many"}, "valid integer"),
            ({}, "it broke at the start"),
        )
        for form, message in cases:
            with self.subTest(form=form):
                html = self.post(callable="nothing", **form)
                self.assertIn(message, html)
                self.assertNotIn("static/tmp/", html)

    def test_output_dir_is_downloaded_if_the_command_wrote_to_it(self):
        # What a command wrote before it failed is still offered
        html = self.post(callable="partial")
        self.assertIn("it broke in the middle", html)
        self.assertIn("static/tmp/", html)
        self.assertRegex(html, r"partial-dp1-\w{8}\.zip")

    def test_a_job_never_offers_the_download_of_another_job(self):
        # Two jobs of one command in the same second
        with mock.patch.object(
                pf.Timestamp, "timestamp", "20260101000000"):
            first = self.post(callable="file_example")
            second = self.post(callable="file_example", fail="true")
            third = self.post(callable="example")
            fourth = self.post(callable="example")
        self.assertIn("static/tmp/", first)
        self.assertIn("it broke before writing the file", second)
        self.assertNotIn("static/tmp/", second)
        self.addCleanup(os.remove, calls[0].out_file)
        self.assertNotEqual(calls[0].out_file, calls[1].out_file)
        self.assertNotEqual(calls[2].out_dir, calls[3].out_dir)
        links = [re.search(r"static/tmp/([\w.-]+\.zip)", html).group(1)
                 for html in (third, fourth)]
        self.assertNotEqual(links[0], links[1])
        logs = [re.search(r'id="([\w.-]+\.log)"', html).group(1)
                for html in (first, second, third, fourth)]
        self.assertEqual(len(set(logs)), 4)

    def test_a_command_that_is_not_queued_is_answered_at_once(self):
        html = self.submit(callable="encode", string="admin:hunter2")
        self.assertNotIn("job_pending", html)
        self.assertIn("encoded admin:hunter2", html)
        self.assertEqual([], [
            job for job in jobs.list_jobs() if job.command == "example.encode"])

    def test_output_file_is_downloaded_when_it_exists(self):
        html = self.post(callable="file_example")
        (params,) = calls
        self.addCleanup(os.remove, params.out_file)
        filename = os.path.basename(params.out_file)
        download = os.path.join("var", "www", "static", "tmp", filename)
        self.addCleanup(os.remove, download)
        self.assertIn(filename, html)
        self.assertIn("Done", html)
        with open(download) as fin:
            self.assertEqual("the result", fin.read())


if __name__ == "__main__":
    unittest.main()

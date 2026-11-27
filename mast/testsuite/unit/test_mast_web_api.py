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
Tests of the new web UI: its JSON API (mast.web.api), its pages
(mast.web.page and mast.web.pages), the structured results
(Result.data), and the parity guard between the commands and the
form builder.
"""
import base64
import hashlib
import io
import json
import os
import re
import shutil
import unittest
from unittest import mock

from mast import jobs
from mast.auth import users
from mast.cli import Cli, Option, Params, Table, Text
from mast.jobs import queue, registry
from mast.plugin_utils import results as dp_results
from mast.testsuite.unit.test_mast_jobs import PostgresMixin, submit
from mast.testsuite.unit.web_helpers import PASSWORD, WebCase
from mast.web import api, catalog, uploads, widgets
from mast.web import app as web_app
from mast.web.page import load_pages

SECRET = "hunter2-very-secret"
WEB = os.path.dirname(web_app.__file__)


class Files(Params):
    appliances: list[str] = Option(
        default_factory=list, short="a", widget="appliances")
    credentials: list[str] = Option(
        default_factory=list, short="c", secret=True, widget="credentials")
    file_in: str = Option(widget="upload", help="A file to read")
    out_file: str = Option("tmp/report.txt", widget="out_file")
    out_dir: str = Option("tmp", widget="out_dir")


def make_cli():
    cli = Cli(provider="webtest")

    @cli.command("copy", category="files", effect="change")
    def copy(params: Files):
        """Copy the uploaded file to out_file and out_dir."""
        with open(params.file_in) as fin:
            data = fin.read()
        os.makedirs(os.path.dirname(params.out_file), exist_ok=True)
        with open(params.out_file, "w") as fout:
            fout.write("copied " + data)
        os.makedirs(params.out_dir, exist_ok=True)
        with open(os.path.join(params.out_dir, "one.txt"), "w") as fout:
            fout.write(data)
        return Table(("File", "Data"), [("in", data)])

    return cli


def start_in_queue(command, params, principal, job_id):
    """Stands in for jobs.client.start: queue the job, and do not run
    it in a thread."""
    return queue.submit(command, params, principal=principal, job_id=job_id)


class ApiCase(WebCase):

    def setUp(self):
        super().setUp()
        registry.register("webtest", make_cli())
        self.addCleanup(registry.unregister, "webtest")
        patcher = mock.patch.object(api.jobs_client, "start",
                                    side_effect=start_in_queue)
        self.start = patcher.start()
        self.addCleanup(patcher.stop)
        for directory in (catalog.JOBS_DIR, uploads.UPLOADS_DIR):
            self.addCleanup(shutil.rmtree, directory, ignore_errors=True)

    def get(self, path, client=None):
        return (client or self.client).get("/api/v1" + path)

    def api_post(self, path, body=None, client=None, **kwargs):
        if body is not None:
            kwargs["json"] = body
        return self.post("/api/v1" + path, client=client, **kwargs)

    def run_job(self, command, params, user="operator"):
        """Submit through the API as `user`, run the job, return its id."""
        self.login(user)
        answer = self.api_post("/jobs", {"command": command, "params": params})
        self.assertEqual(answer.status_code, 201, answer.get_json())
        job_id = answer.get_json()["job_id"]
        jobs.run_next("w1")
        return job_id


class TestApiAccess(ApiCase):

    def test_without_a_session_the_api_answers_401(self):
        for path in ("/me", "/commands", "/jobs", "/workers"):
            with self.subTest(path=path):
                answer = self.get(path)
                self.assertEqual(answer.status_code, 401)
                self.assertEqual(answer.get_json(), {"error": "Log in first."})

    def test_a_post_without_the_token_gets_400_json(self):
        self.login("operator")
        answer = self.client.post("/api/v1/jobs", json={})
        self.assertEqual(answer.status_code, 400)
        self.assertIn("CSRF", answer.get_json()["error"])

    def test_a_user_who_must_change_the_password_gets_403(self):
        users.set_password("viewer", PASSWORD, temporary=True)
        self.login("viewer")
        answer = self.get("/me")
        self.assertEqual(answer.status_code, 403)
        self.assertIn("password", answer.get_json()["error"])

    def test_me(self):
        self.login("operator")
        me = self.get("/me").get_json()
        self.assertEqual(me["name"], "operator")
        self.assertEqual(me["role"], "operator")
        self.assertEqual(me["csrf_token"], self.token())
        self.assertEqual([page["name"] for page in me["pages"]],
                         ["appliances", "crypto", "jobs", "commands",
                          "terminal"])
        self.assertTrue(me["may"]["submit"])
        self.assertFalse(me["may"]["cancel-any"])

    def test_the_pages_of_each_role(self):
        expected = {"viewer": ["appliances", "crypto", "jobs"],
                    "operator": ["appliances", "crypto", "jobs", "commands",
                                 "terminal"],
                    "admin": ["appliances", "crypto", "jobs", "commands",
                              "terminal", "admin"]}
        for role, pages in expected.items():
            with self.subTest(role=role):
                client = self.app.test_client()
                self.login(role, client=client)
                me = self.get("/me", client=client).get_json()
                self.assertEqual([p["name"] for p in me["pages"]], pages)


class TestPages(ApiCase):

    def test_the_core_registers_its_pages(self):
        found = {page.name: page for page in load_pages()}
        self.assertEqual(found["jobs"].min_role, "viewer")
        self.assertEqual(found["commands"].min_role, "operator")
        self.assertEqual(found["admin"].min_role, "admin")
        self.assertLess(found["jobs"].order, found["admin"].order)

    def test_the_pages_of_the_plugins_come_from_their_manifest(self):
        names = [page.name for page in load_pages()]
        for name in ("appliances", "crypto", "terminal"):
            self.assertIn(name, names)

    def test_the_plugins_page(self):
        self.login("admin")
        page = self.client.get("/admin/plugins").get_data(as_text=True)
        self.assertIn("IBM DataPower", page)
        self.assertNotIn("data-left-out", page)
        answer = self.get("/plugins").get_json()
        (datapower,) = [p for p in answer["plugins"]
                        if p["provider"] == "datapower"]
        self.assertEqual(datapower["api"], 1)
        self.assertEqual(datapower["kinds"], ["datapower"])
        self.assertEqual(datapower["unavailable"], [])
        kinds = {k["name"]: k for k in answer["kinds"]}
        self.assertIsNone(kinds["datapower"]["extends"])
        self.assertIn("collect-crypto", kinds["datapower"]["capabilities"])
        self.assertEqual(kinds["endpoint"]["provider"], "core")
        self.assertIn("data-kind-rows", page)
        self.assertGreater(datapower["commands"], 90)
        self.assertEqual(answer["left_out"], [])
        client = self.app.test_client()
        self.login("operator", client=client)
        self.assertEqual(client.get("/admin/plugins").status_code, 403)
        self.assertEqual(client.get("/api/v1/plugins").status_code, 403)

    def test_a_page_refuses_a_lower_role(self):
        cases = (("viewer", "/jobs", 200), ("viewer", "/commands", 403),
                 ("viewer", "/admin/users", 403),
                 ("operator", "/commands", 200),
                 ("operator", "/admin/users", 403),
                 ("admin", "/admin/users", 200))
        for role, path, status in cases:
            with self.subTest(role=role, path=path):
                client = self.app.test_client()
                self.login(role, client=client)
                self.assertEqual(client.get(path).status_code, status)

    def test_home_goes_to_the_first_page(self):
        self.login("viewer")
        # The Appliances page of the DataPower plugin comes first
        self.assertEqual(self.client.get("/").location, "/appliances")

    def test_the_navigation_shows_only_allowed_pages(self):
        self.login("operator")
        page = self.client.get("/jobs").get_data(as_text=True)
        self.assertIn('href="/commands"', page)
        self.assertNotIn('href="/admin', page)

    def test_every_page_loads_its_scripts_as_modules(self):
        self.login("admin")
        for path, script in (("/jobs", "jobs.js"), ("/jobs/" + "0" * 32, "job.js"),
                             ("/commands", "commands.js"),
                             ("/admin/users", "admin-users.js")):
            with self.subTest(path=path):
                page = self.client.get(path).get_data(as_text=True)
                self.assertIn('type="module" src="/static/js/{}"'.format(
                    script), page)
                self.assertIn("bootstrap-5.3.3/bootstrap.min.css", page)
        for name in os.listdir(os.path.join(WEB, "static", "js")):
            with self.subTest(script=name):
                self.assertEqual(
                    self.client.get("/static/js/" + name).status_code, 200)

    def test_the_commands_page_has_the_target_bar(self):
        self.login("operator")
        page = self.client.get("/commands").get_data(as_text=True)
        self.assertIn('id="target-bar"', page)
        self.assertIn("data-target-password", page)


class TestBootstrap(unittest.TestCase):

    def test_the_vendored_files_are_the_published_ones(self):
        folder = os.path.join(WEB, "static", "vendor", "bootstrap-5.3.3")
        for name, digest in (
                ("bootstrap.min.css", "QWTKZyjpPEjISv5WaRU9OFeRpok6YctnYmDr"
                                      "5pNlyT2bRjXh0JMhjY6hW+ALEwIH"),
                ("bootstrap.bundle.min.js", "YvpcrYf0tY3lHB60NNkmXc5s9fDVZLES"
                                            "aAA55NDzOxhy9GkcIdslK1eN7N6jIeHz")):
            with self.subTest(name=name):
                with open(os.path.join(folder, name), "rb") as fin:
                    found = base64.b64encode(
                        hashlib.sha384(fin.read()).digest()).decode()
                self.assertEqual(found, digest)

    def test_no_page_loads_anything_from_another_site(self):
        folder = os.path.join(WEB, "templates")
        for name in os.listdir(folder):
            with open(os.path.join(folder, name)) as fin:
                text = fin.read()
            with self.subTest(template=name):
                self.assertIsNone(re.search(r'(src|href)="(https?:)?//', text))


class TestCommandsApi(ApiCase):

    def test_every_command_is_listed(self):
        self.login("viewer")
        found = {c["id"]: c for c in self.get("/commands").get_json()["commands"]}
        for command_id in ("test.work", "webtest.copy", "datapower.get-status",
                           "datapower.normal-backup", "datapower.list-users"):
            self.assertIn(command_id, found)
        work = found["test.work"]
        self.assertEqual(work["provider"], "test")
        self.assertEqual(work["invocation"], "mast test work")
        self.assertEqual(work["path"], "mast/test/work")
        self.assertTrue(work["queued"])
        # The management commands of core have pages of their own
        self.assertNotIn("core.add-target", found)
        self.assertIn("core.crypto-inventory", found)
        self.assertEqual(found["datapower.get-status"]["category"], "system")
        options = {o["name"]: o for o in work["options"]}
        self.assertEqual(options["seconds"]["type"], "float")
        self.assertEqual(options["fail"]["kind"], "flag")
        self.assertTrue(options["credentials"]["secret"])
        self.assertIsNone(options["credentials"]["default"])
        self.assertEqual(found["test.look"]["exclusive"], False)
        self.assertFalse(found["test.encode"]["queued"])
        # What the command declares (plugin API 1)
        status = found["datapower.get-status"]
        self.assertEqual((status["effect"], status["targets"]),
                         ("read", ["datapower"]))
        self.assertTrue({o["name"]: o for o in status["options"]}
                        ["appliances"]["target"])
        self.assertEqual(found["datapower.get-pmr-info"]["effect"], "change")
        self.assertEqual(status["kinds"], ["datapower"])
        inventory = found["core.crypto-inventory"]
        self.assertEqual((inventory["targets"], inventory["needs"],
                          inventory["kinds"]),
                         ([], ["collect-crypto"], ["datapower"]))

    def test_one_command(self):
        self.login("viewer")
        answer = self.get("/commands/webtest.copy").get_json()
        self.assertEqual(answer["category"], "files")
        self.assertIn("Copy the uploaded file", answer["doc"])
        self.assertEqual(self.get("/commands/nothing.here").status_code, 404)
        self.assertEqual(self.get("/commands/test.nothing").status_code, 404)

    def test_a_secret_default_is_never_sent(self):
        class Secretive(Params):
            password: str = Option("the default secret", secret=True)
        specs = catalog.cli_params.options(Secretive)
        self.assertIsNone(catalog.option_json(specs[0])["default"])


class TestParityGuard(unittest.TestCase):
    """Every option of every command can be sent as JSON, and every
    widget has a control in the form builder."""

    def test_every_option_serialises_and_every_widget_has_a_control(self):
        commands = catalog.all_commands()
        self.assertGreater(len(commands), 90)
        for command in commands:
            with self.subTest(command=command["id"]):
                json.dumps(command)
                for option in command["options"]:
                    if option["widget"]:
                        self.assertIsNotNone(
                            widgets.control(option["widget"]),
                            "{} has no control".format(option["widget"]))

    def test_the_javascript_map_is_the_python_map(self):
        with open(os.path.join(WEB, "static", "js", "forms.js")) as fin:
            text = fin.read()
        block = text.split("// WIDGETS-BEGIN")[1].split("// WIDGETS-END")[0]
        found = dict(re.findall(r'"([^"]+)":\s*"([^"]+)"', block))
        self.assertEqual(found, widgets.WIDGETS)

    def test_control(self):
        self.assertEqual(widgets.control("configured:Domain"), "choices")
        self.assertEqual(widgets.control("appliances"), "target-bar")
        self.assertIsNone(widgets.control("something-new"))
        self.assertIsNone(widgets.control(None))


class TestSubmit(ApiCase):

    def test_an_operator_submits_a_job(self):
        self.login("operator")
        answer = self.api_post("/jobs", {"command": "test.work", "params": {
            "appliances": ["dp1"], "seconds": 0.5}})
        self.assertEqual(answer.status_code, 201)
        info = jobs.get(answer.get_json()["job_id"])
        self.assertEqual(info.submitted_by, "operator")
        self.assertEqual(info.command, "test.work")
        self.assertEqual(info.params["seconds"], 0.5)
        self.assertEqual(info.targets, ["dp1"])

    def test_a_viewer_cannot_submit(self):
        self.login("viewer")
        answer = self.api_post("/jobs", {"command": "test.work", "params": {}})
        self.assertEqual(answer.status_code, 403)
        self.start.assert_not_called()

    def test_bad_parameters_get_422_with_a_message_for_each(self):
        self.login("operator")
        answer = self.api_post("/jobs", {"command": "test.work", "params": {
            "seconds": "soon", "nothing": 1}})
        self.assertEqual(answer.status_code, 422)
        body = answer.get_json()
        self.assertIn("seconds", body["fields"])
        self.assertIn("valid number", body["fields"]["seconds"])
        self.assertIn("", body["fields"])
        self.start.assert_not_called()

    def test_an_unknown_command(self):
        self.login("operator")
        answer = self.api_post("/jobs", {"command": "test.nothing"})
        self.assertEqual(answer.status_code, 404)

    def test_a_command_that_is_not_a_job_runs_now_without_secrets(self):
        self.login("operator")
        answer = self.api_post("/jobs", {"command": "test.encode", "params": {
            "credentials": ["admin:" + SECRET]}})
        self.assertEqual(answer.status_code, 200)
        result = answer.get_json()["result"]
        self.assertIn("encoded", result["text"])
        self.assertNotIn(SECRET, json.dumps(result))
        self.assertEqual(jobs.list_jobs(), [])


class TestJobsApi(ApiCase):

    def test_secret_params_are_never_sent(self):
        job_id = self.run_job("test.work", {
            "appliances": ["dp1"], "credentials": ["admin:" + SECRET]})
        for path in ("/jobs/" + job_id, "/jobs", "/jobs/{}/events".format(job_id),
                     "/jobs/{}/result".format(job_id)):
            with self.subTest(path=path):
                body = self.get(path).get_data(as_text=True)
                self.assertNotIn(SECRET, body)
        job = self.get("/jobs/" + job_id).get_json()
        self.assertEqual(job["params"]["credentials"], "********")

    def test_a_queued_job_does_not_show_its_sealed_secret(self):
        self.login("operator")
        job_id = self.api_post("/jobs", {"command": "test.work", "params": {
            "credentials": ["admin:" + SECRET]}}).get_json()["job_id"]
        job = self.get("/jobs/" + job_id).get_json()
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["params"]["credentials"], "********")
        self.assertNotIn("$secret", json.dumps(job))

    def test_a_job_its_events_and_its_result(self):
        job_id = self.run_job("test.page", {"credentials": ["admin:" + SECRET]})
        job = self.get("/jobs/" + job_id).get_json()
        self.assertEqual(job["status"], "succeeded")
        self.assertTrue(job["finished"])
        self.assertEqual(job["children"], [])
        events = self.get("/jobs/{}/events".format(job_id)).get_json()
        self.assertEqual([e["message"] for e in events["events"]][-1],
                         "succeeded")
        after = self.get("/jobs/{}/events?after={}".format(
            job_id, events["last_event"])).get_json()
        self.assertEqual(after["events"], [])
        result = self.get("/jobs/{}/result".format(job_id)).get_json()
        self.assertEqual(result["data"]["kind"], "table")
        self.assertIn("********", result["data"]["rows"][1])
        history = self.get("/jobs/{}/history".format(job_id))
        self.assertEqual(history.mimetype, "text/plain")

    def test_a_job_without_a_result(self):
        self.login("viewer")
        job_id = submit("dp1")
        self.assertEqual(self.get("/jobs/{}/result".format(job_id))
                         .status_code, 404)
        self.assertEqual(self.get("/jobs/" + "0" * 32).status_code, 404)

    def test_the_children_of_a_job(self):
        parent = submit("dp1")
        child = jobs.submit("test.work", {"appliances": ["dp2"]},
                            parent_id=parent)
        self.login("viewer")
        job = self.get("/jobs/" + parent).get_json()
        self.assertEqual([c["id"] for c in job["children"]], [child])
        self.assertEqual(job["children"][0]["targets"], ["dp2"])

    def test_filters(self):
        mine = self.run_job("test.work", {"appliances": ["dp1"]})
        theirs = submit("dp2")
        look = jobs.submit("test.look", {"appliances": ["dp3"]})

        def ids(query):
            return [job["id"] for job in self.get("/jobs" + query)
                    .get_json()["jobs"]]
        self.assertEqual(ids(""), [look, theirs, mine])
        self.assertEqual(ids("?mine=1"), [mine])
        self.assertEqual(ids("?command=test.look"), [look])
        self.assertEqual(ids("?appliance=dp2"), [theirs])
        self.assertEqual(ids("?status=succeeded"), [mine])
        self.assertEqual(ids("?status=succeeded&status=queued"),
                         [look, theirs, mine])
        self.assertEqual(ids("?limit=1"), [look])
        newest = self.get("/jobs/" + look).get_json()["created_at"]
        self.assertEqual(ids("?before=" + newest.replace("+", "%2B")),
                         [theirs, mine])
        self.assertEqual(self.get("/jobs?limit=x").status_code, 400)
        self.assertEqual(self.get("/jobs?before=yesterday").status_code, 400)

    def test_an_operator_cancels_only_own_jobs(self):
        self.login("operator")
        mine = self.api_post("/jobs", {"command": "test.work", "params": {
            "appliances": ["dp1"]}}).get_json()["job_id"]
        theirs = submit("dp2")
        self.assertEqual(self.api_post(
            "/jobs/{}/cancel".format(theirs)).status_code, 403)
        answer = self.api_post("/jobs/{}/cancel".format(mine))
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer.get_json()["status"], "cancelled")
        self.assertEqual(jobs.get(theirs).status, jobs.QUEUED)

    def test_a_viewer_cannot_cancel(self):
        job_id = submit("dp1")
        self.login("viewer")
        self.assertEqual(self.api_post(
            "/jobs/{}/cancel".format(job_id)).status_code, 403)

    def test_an_admin_cancels_any_job(self):
        job_id = submit("dp1")
        self.login("admin")
        self.assertEqual(self.api_post(
            "/jobs/{}/cancel".format(job_id)).get_json()["status"],
            "cancelled")

    def test_workers(self):
        self.login("viewer")
        jobs.worker_seen("w9", hostname="here", pid=1, concurrency=2)
        found = self.get("/workers").get_json()["workers"]
        self.assertEqual([w["id"] for w in found], ["w9"])


class TestUploadsAndFiles(ApiCase):

    def upload(self, data=b"the data", name="in.txt", client=None):
        return self.api_post("/uploads", client=client, data={
            "file": (io.BytesIO(data), name)},
            content_type="multipart/form-data")

    def test_an_upload_becomes_a_path_on_the_server(self):
        self.login("operator")
        answer = self.upload(name="../../etc/evil.txt")
        self.assertEqual(answer.status_code, 201)
        token = answer.get_json()["token"]
        self.assertEqual(answer.get_json()["name"], "etc_evil.txt")
        path = uploads.path_for(token, "operator")
        self.assertTrue(os.path.abspath(path).startswith(
            os.path.abspath(uploads.UPLOADS_DIR) + os.sep))
        with open(path, "rb") as fin:
            self.assertEqual(fin.read(), b"the data")

    def test_a_token_is_only_for_its_user(self):
        self.login("operator")
        token = self.upload().get_json()["token"]
        with self.assertRaises(uploads.UnknownUpload):
            uploads.path_for(token, "admin")
        for bad in ("0" * 32, "../" + token, token + "x", None, 7):
            with self.subTest(token=bad):
                with self.assertRaises(uploads.UnknownUpload):
                    uploads.path_for(bad, "operator")

    def test_a_viewer_cannot_upload(self):
        self.login("viewer")
        self.assertEqual(self.upload().status_code, 403)

    def test_an_unknown_token_is_refused(self):
        self.login("operator")
        answer = self.api_post("/jobs", {"command": "webtest.copy", "params": {
            "file_in": "f" * 32}})
        self.assertEqual(answer.status_code, 422)
        self.assertIn("Upload it again", answer.get_json()["fields"]["file_in"])
        other = self.app.test_client()
        self.login("admin", client=other)
        token = self.upload(client=other).get_json()["token"]
        answer = self.api_post("/jobs", {"command": "webtest.copy", "params": {
            "file_in": token}})
        self.assertEqual(answer.status_code, 422)

    def test_out_paths_are_chosen_by_the_server_and_downloaded(self):
        self.login("operator")
        token = self.upload(b"hello").get_json()["token"]
        answer = self.api_post("/jobs", {"command": "webtest.copy", "params": {
            "file_in": token, "out_file": "/etc/passwd", "out_dir": "/tmp"}})
        self.assertEqual(answer.status_code, 201)
        job_id = answer.get_json()["job_id"]
        info = jobs.get(job_id)
        self.assertTrue(info.params["out_file"].startswith(
            catalog.job_dir(job_id)))
        self.assertTrue(info.params["out_dir"].startswith(
            catalog.job_dir(job_id)))
        self.assertTrue(info.params["file_in"].startswith(uploads.UPLOADS_DIR))
        jobs.run_next("w1")
        self.assertEqual(jobs.get(job_id).status, "succeeded")
        job = self.get("/jobs/" + job_id).get_json()
        names = sorted(f["name"] for f in job["files"])
        self.assertEqual(names, ["copy-{}.txt".format(job_id[:8]), "out.zip"])
        download = self.get("/jobs/{}/files/copy-{}.txt".format(
            job_id, job_id[:8]))
        self.assertEqual(download.get_data(), b"copied hello")
        archive = self.get("/jobs/{}/files/out.zip".format(job_id))
        self.assertEqual(archive.mimetype, "application/zip")
        import zipfile
        with zipfile.ZipFile(io.BytesIO(archive.get_data())) as opened:
            self.assertEqual(opened.read("one.txt"), b"hello")
        self.assertEqual(self.get("/jobs/{}/files/passwd".format(job_id))
                         .status_code, 404)

    def test_files_outside_the_job_are_not_offered(self):
        # A job that the command line queued, with its own out_file
        source = os.path.join(self.tmp, "in.txt")
        with open(source, "w") as fout:
            fout.write("data")
        job_id = jobs.submit("webtest.copy", {
            "file_in": source, "out_file": os.path.join(self.tmp, "out.txt"),
            "out_dir": os.path.join(self.tmp, "out")})
        self.login("viewer")
        jobs.run_next("w1")
        self.assertEqual(jobs.get(job_id).status, "succeeded")
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "out.txt")))
        self.assertEqual(self.get("/jobs/" + job_id).get_json()["files"], [])


class TestChoices(ApiCase):

    def test_status_providers_and_object_classes(self):
        self.login("viewer")
        for widget in ("status_provider", "object_class"):
            with self.subTest(widget=widget):
                found = self.api_post("/choices/" + widget).get_json()["choices"]
                self.assertGreater(len(found), 50)
        self.assertIn("CPUUsage", self.api_post(
            "/choices/status_provider").get_json()["choices"])

    def test_configured_needs_appliances_and_a_role_that_runs_commands(self):
        self.login("viewer")
        self.assertEqual(self.api_post(
            "/choices/configured:Domain").status_code, 403)
        operator = self.app.test_client()
        self.login("operator", client=operator)
        answer = self.api_post("/choices/configured:Domain", {},
                               client=operator)
        self.assertEqual(answer.get_json(), {"choices": []})

    def test_configured_asks_the_appliances(self):
        self.login("operator")
        from mast.plugin_utils import choices
        with mock.patch("mast.datapower.datapower.Environment") as env, \
                mock.patch.object(choices, "choices",
                                  return_value=["default", "dev"]) as asked:
            answer = self.api_post("/choices/configured:Domain", {
                "appliances": ["dp1"], "credentials": ["admin:" + SECRET],
                "no_check_hostname": True})
        self.assertEqual(answer.get_json(), {"choices": ["default", "dev"]})
        env.assert_called_once_with(
            ["dp1"], ["admin:" + SECRET], timeout=30, check_hostname=False)
        self.assertEqual(asked.call_args.args[0], "configured:Domain")

    def test_an_unknown_widget(self):
        self.login("viewer")
        self.assertEqual(self.api_post("/choices/upload").status_code, 404)


class TestAdminUsers(ApiCase):

    def test_an_admin_manages_users(self):
        self.login("admin")
        answer = self.api_post("/users", {"name": "Dora", "role": "viewer",
                                          "password": "a temporary password"})
        self.assertEqual(answer.status_code, 201)
        self.assertEqual(answer.get_json()["name"], "dora")
        self.assertTrue(users.get("dora").must_change)
        listed = self.get("/users").get_json()["users"]
        self.assertIn("dora", [u["name"] for u in listed])
        self.assertNotIn("scrypt", json.dumps(listed))
        self.assertNotIn("password_hash", json.dumps(listed))

        def patch(body):
            return self.client.patch(
                "/api/v1/users/dora", json=body,
                headers={"X-CSRF-Token": self.token()})
        self.assertEqual(patch({"role": "operator"}).get_json()["role"],
                         "operator")
        self.assertTrue(patch({"disabled": True}).get_json()["disabled"])
        self.assertFalse(patch({"disabled": False}).get_json()["disabled"])
        answer = patch({"password": "another temp password"})
        self.assertNotIn("another temp password", answer.get_data(as_text=True))
        users.authenticate("dora", "another temp password")
        self.assertEqual(patch({"role": "root"}).status_code, 422)
        self.assertEqual(patch({"password": "short"}).status_code, 422)
        answer = self.client.delete("/api/v1/users/dora",
                                    headers={"X-CSRF-Token": self.token()})
        self.assertEqual(answer.status_code, 204)
        with self.assertRaises(users.UserNotFound):
            users.get("dora")

    def test_add_refusals(self):
        self.login("admin")
        for body, field in (
                ({"name": "admin", "role": "viewer",
                  "password": "a temporary password"}, "name"),
                ({"name": "os:x", "role": "viewer",
                  "password": "a temporary password"}, "name"),
                ({"name": "eve", "role": "root",
                  "password": "a temporary password"}, "role"),
                ({"name": "eve", "role": "viewer", "password": "short"},
                 "password")):
            with self.subTest(body=body):
                answer = self.api_post("/users", body)
                self.assertIn(answer.status_code, (409, 422))
                self.assertIn(field, answer.get_json()["fields"])

    def test_an_admin_cannot_lock_themselves_out(self):
        self.login("admin")
        headers = {"X-CSRF-Token": self.token()}
        for body in ({"role": "viewer"}, {"disabled": True}):
            with self.subTest(body=body):
                self.assertEqual(self.client.patch(
                    "/api/v1/users/admin", json=body,
                    headers=headers).status_code, 400)
        self.assertEqual(self.client.delete(
            "/api/v1/users/admin", headers=headers).status_code, 400)
        self.assertEqual(users.get("admin").role, "admin")

    def test_only_an_admin(self):
        self.login("operator")
        self.assertEqual(self.get("/users").status_code, 403)
        self.assertEqual(self.api_post("/users", {}).status_code, 403)

    def test_changes_are_logged_without_passwords(self):
        self.login("admin")
        with self.assertLogs("mast.web.auth", "INFO") as logs:
            self.api_post("/users", {"name": "dora", "role": "viewer",
                                     "password": "a temporary password"})
        text = "\n".join(logs.output)
        self.assertIn("Added the user dora with the role viewer (by admin", text)
        self.assertNotIn("a temporary password", text)


class TestApiPostgres(PostgresMixin, TestJobsApi):
    pass


class TestResultData(unittest.TestCase):
    """Result.data() of each result class."""

    def test_text_has_none(self):
        self.assertIsNone(Text("x").data())

    def test_table(self):
        self.assertEqual(Table(("a", "b"), [(1, "x\ny")]).data(), {
            "kind": "table", "columns": ["a", "b"], "rows": [["1", "x\ny"]]})

    def test_the_results_of_the_plugins(self):
        class Response(object):
            def __init__(self, ok):
                self.ok = ok
                self.xml = None

            def __bool__(self):
                return self.ok
        cases = (
            (dp_results.ActionResults({"dp1": Response(True),
                                       "dp2": Response(False)}),
             ["Appliance", "Result"],
             [["dp1", "Succeeded"], ["dp2", "Failed"]]),
            (dp_results.ListResults({"dp1": ["a", "b"]}, title="Domains"),
             ["Appliance", "Domains"], [["dp1", "a\nb"]]),
            (dp_results.StatusResults({"dp1": "Verified"}, suffix="x"),
             ["Appliance", "Result"], [["dp1-x", "Verified"]]),
            (dp_results.CommonItems("Users", {"dp1": ["a", "b"],
                                              "dp2": ["b"]}),
             ["Appliance", "Users"],
             [["dp1", "a\nb"], ["dp2", "b"], ["All", "b"]]),
            (dp_results.SeeDownload(["dp1"]),
             ["Appliance", "Result"], [["dp1", "See Download"]]),
        )
        for result, columns, rows in cases:
            with self.subTest(result=type(result).__name__):
                data = result.data()
                self.assertEqual(data["kind"], "table")
                self.assertEqual(data["columns"], columns)
                self.assertEqual(data["rows"], rows)
                json.dumps(data)

    def test_a_report_is_its_parts(self):
        report = dp_results.Report()
        with mock.patch.object(dp_results, "say"):
            report.add(dp_results.ListResults({"dp1": ["a"]}))
            report.add(Text("a step"))
            report.add(Text(""))
        data = report.data()
        self.assertEqual(data["kind"], "items")
        self.assertEqual([item["kind"] for item in data["items"]],
                         ["table", "text"])
        self.assertEqual(data["items"][1]["text"], "a step")

    def test_xml_and_ethernet_results(self):
        from mast.datapower.network.network import EthernetInterfaceResults
        from mast.datapower.system.system import XMLResults
        for result in (XMLResults("status"), EthernetInterfaceResults({})):
            with self.subTest(result=type(result).__name__):
                data = result.data()
                self.assertEqual(data["kind"], "table")
                self.assertEqual(data["rows"], [])

    def test_html_escapes_appliance_values(self):
        evil = "<script>alert(1)</script>"
        for result in (Table(("a",), [(evil,)]),
                       dp_results.ListResults({evil: [evil]}),
                       dp_results.StatusResults({evil: evil}),
                       dp_results.CommonItems(evil, {evil: [evil]}),
                       Text(evil)):
            with self.subTest(result=type(result).__name__):
                self.assertNotIn("<script>", result.html())


if __name__ == "__main__":
    unittest.main()


class TestConnectivity(ApiCase):
    """The Test button of the target bar: POST /api/v1/connectivity
    runs a passive job of datapower.test-connectivity."""

    def setUp(self):
        super().setUp()

        def start_and_run(command, params, principal, passive=False,
                          job_id=None):
            job_id = queue.submit(command, params, principal=principal,
                                  job_id=job_id, passive=passive)
            jobs.run_next("w-test")
            return job_id
        self.start.side_effect = start_and_run
        from mast.datapower.system import system as dp_system

        def tested(appliance, timeout):
            ok = appliance.hostname != "dp2"
            return [appliance.hostname, "yes", "IDG.11.0.0.3",
                    "yes" if ok else "no", "" if ok else "SSH: refused"]
        patcher = mock.patch.object(dp_system, "_test_connectivity",
                                    side_effect=tested)
        patcher.start()
        self.addCleanup(patcher.stop)

        class Env(object):
            def __init__(self, names, credentials=None, **kwargs):
                self.appliances = [mock.Mock(hostname=n) for n in names]
        patcher = mock.patch("mast.plugin_utils.params.Environment", Env)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_viewer_may_not(self):
        self.login("viewer")
        self.assertEqual(self.api_post("/connectivity", {
            "appliances": ["dp1"], "credentials": ["a:b"]}).status_code, 403)

    def test_the_rows_and_the_job(self):
        self.login("operator")
        answer = self.api_post("/connectivity", {
            "appliances": ["dp1", "dp2"],
            "credentials": ["admin:" + SECRET], "no_check_hostname": True})
        self.assertEqual(answer.status_code, 200, answer.get_json())
        found = answer.get_json()
        self.assertEqual(found["rows"], [
            {"appliance": "dp1", "soma": "yes", "firmware": "IDG.11.0.0.3",
             "ssh": "yes", "problem": ""},
            {"appliance": "dp2", "soma": "yes", "firmware": "IDG.11.0.0.3",
             "ssh": "no", "problem": "SSH: refused"}])
        job = jobs.get(found["job_id"])
        self.assertEqual((job.command, job.submitted_by),
                         ("datapower.test-connectivity", "operator"))
        self.assertTrue(job.passive)
        self.assertNotIn(SECRET, answer.get_data(as_text=True))

    def test_it_needs_appliances_and_credentials(self):
        self.login("operator")
        self.assertEqual(self.api_post("/connectivity", {
            "appliances": [], "credentials": ["a:b"]}).status_code, 422)
        self.assertEqual(self.api_post("/connectivity", {
            "appliances": ["dp1"]}).status_code, 422)

    def test_the_target_bar_has_the_button(self):
        self.login("operator")
        for path in ("/commands", "/terminal"):
            with self.subTest(path=path):
                page = self.client.get(path).get_data(as_text=True)
                self.assertIn("data-target-test", page)

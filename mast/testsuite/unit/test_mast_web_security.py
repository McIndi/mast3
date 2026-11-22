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
Tests of how the web UI defends itself: the login and the session, the
roles on the job routes, the CSRF token, the Content Security Policy
and HSTS. The classic GUI is tested where the web UI mounts it, under
/classic/.
"""
import html.parser
import os
import time
import unittest
from unittest import mock

from mast import jobs
from mast.auth import users
from mast.datapower.web import gui
from mast.jobs import queue
from mast.testsuite.unit.test_mast_jobs import submit
from mast.testsuite.unit.web_helpers import (
    PASSWORD, TOKEN, WebCase, server_conf)
from mast.web import app as web_app
from mast.web import auth as web_auth
from mast.web import security, server

C = "/classic"


class _InlineCode(html.parser.HTMLParser):
    """Collects what a strict Content Security Policy blocks in a page:
    inline scripts, style elements, style attributes and event handler
    attributes."""
    def __init__(self):
        super().__init__()
        self.found = []

    def handle_starttag(self, tag, attrs):
        names = {name for name, _ in attrs}
        if tag == "script" and "src" not in names:
            self.found.append("<script> without src")
        if tag == "style":
            self.found.append("<style>")
        for name in names:
            if name == "style" or name.startswith("on"):
                self.found.append("{} attribute on <{}>".format(name, tag))


class TestLogin(WebCase):

    def test_without_a_session_every_page_goes_to_the_login(self):
        for path in ("/", "/account/password", C + "/", C + "/plugins.js",
                     C + "/jobs/abc", C + "/config/ssh"):
            with self.subTest(path=path):
                answer = self.client.get(path)
                self.assertEqual(answer.status_code, 302)
                self.assertTrue(answer.location.startswith("/login?next="))
        answer = self.client.post(C + "/jobs/abc/cancel")
        self.assertEqual(answer.status_code, 302)
        self.assertEqual(answer.location, "/login?next=/")

    def test_the_login_page_and_static_files_need_no_session(self):
        self.assertEqual(self.client.get("/login").status_code, 200)
        self.assertEqual(
            self.client.get("/static/css/mast-web.css").status_code, 200)

    def test_login_and_logout(self):
        answer = self.login("admin")
        self.assertEqual(answer.status_code, 302)
        self.assertEqual(answer.location, "/")
        page = self.client.get("/jobs").get_data(as_text=True)
        self.assertIn("admin", page)
        self.assertEqual(self.client.get(C + "/").status_code, 200)
        answer = self.post("/logout")
        self.assertEqual(answer.location, "/login")
        self.assertEqual(self.client.get("/jobs").status_code, 302)

    def test_a_wrong_password_gets_one_generic_message(self):
        for name, password in (("admin", "not the password"),
                               ("nobody", PASSWORD)):
            with self.subTest(name=name):
                answer = self.login(name, password)
                self.assertEqual(answer.status_code, 401)
                self.assertIn(users.LoginFailed.MESSAGE.replace("'", "&#39;"),
                              answer.get_data(as_text=True))
        self.assertEqual(self.client.get("/jobs").status_code, 302)

    def test_the_login_goes_to_the_page_asked_for(self):
        self.client.get("/login")
        token = self.token()
        answer = self.client.post("/login", data={
            "username": "admin", "password": PASSWORD, "csrf_token": token,
            "next": C + "/"})
        self.assertEqual(answer.location, C + "/")

    def test_the_login_does_not_go_to_another_site(self):
        for target in ("https://evil.example/", "//evil.example/",
                       "/\\evil.example", "javascript:alert(1)"):
            with self.subTest(target=target):
                client = self.app.test_client()
                client.get("/login")
                with client.session_transaction() as session:
                    token = session["csrf"]
                answer = client.post("/login", data={
                    "username": "admin", "password": PASSWORD,
                    "csrf_token": token, "next": target})
                self.assertEqual(answer.location, "/")

    def test_a_login_without_the_token_is_refused(self):
        self.client.get("/login")
        answer = self.client.post("/login", data={
            "username": "admin", "password": PASSWORD})
        self.assertEqual(answer.status_code, 400)
        self.assertEqual(self.client.get("/jobs").status_code, 302)

    def test_the_login_makes_a_new_session(self):
        # Session fixation: what was in the session before the login is
        # gone after it, and the CSRF token is new
        self.client.get("/login")
        with self.client.session_transaction() as session:
            session["planted"] = "by someone else"
        before = self.token()
        self.login("admin")
        with self.client.session_transaction() as session:
            self.assertNotIn("planted", session)
            self.assertNotEqual(session["csrf"], before)
            self.assertEqual(set(session), {
                "user_id", "epoch", "login_at", "seen_at", "csrf"})

    def test_a_user_who_must_change_the_password_does_that_first(self):
        users.set_password("viewer", PASSWORD, temporary=True)
        answer = self.login("viewer")
        self.assertEqual(answer.location, "/account/password")
        for path in ("/", C + "/"):
            self.assertEqual(self.client.get(path).location,
                             "/account/password")
        self.assertEqual(self.client.get("/account/password").status_code,
                         200)
        answer = self.post("/account/password", data={
            "current": PASSWORD, "new": "a brand new password",
            "confirm": "a brand new password"})
        self.assertEqual(answer.location, "/")
        self.assertEqual(self.client.get("/jobs").status_code, 200)
        self.assertFalse(users.get("viewer").must_change)

    def test_change_password_errors(self):
        self.login("viewer")
        for data, message in (
                ({"current": PASSWORD, "new": "a brand new password",
                  "confirm": "another new password"}, "not the same"),
                ({"current": "wrong password!", "new": "a brand new password",
                  "confirm": "a brand new password"}, "not correct"),
                ({"current": PASSWORD, "new": "short", "confirm": "short"},
                 "at least 12")):
            with self.subTest(message=message):
                answer = self.post("/account/password", data=data)
                self.assertEqual(answer.status_code, 400)
                self.assertIn(message, answer.get_data(as_text=True))
        users.authenticate("viewer", PASSWORD)

    def test_a_password_change_logs_out_the_other_sessions(self):
        other = self.app.test_client()
        self.login("viewer", client=other)
        self.login("viewer")
        self.post("/account/password", data={
            "current": PASSWORD, "new": "a brand new password",
            "confirm": "a brand new password"})
        self.assertEqual(self.client.get("/jobs").status_code, 200)
        self.assertEqual(other.get("/jobs").status_code, 302)

    def test_no_users(self):
        for user in users.list_users():
            users.delete(user.name)
        answer = self.client.get("/", follow_redirects=True)
        self.assertEqual(answer.status_code, 503)
        self.assertIn("mast-users add", answer.get_data(as_text=True))
        self.assertEqual(self.client.get(C + "/").status_code, 302)

    def test_logins_are_logged_without_the_password(self):
        with self.assertLogs("mast.web.auth", "INFO") as logs:
            self.login("admin", "the wrong password")
            self.login("admin")
            self.post("/logout")
        text = "\n".join(logs.output)
        self.assertIn("Failed login for 'admin'", text)
        self.assertIn("Login of admin (admin)", text)
        self.assertIn("Logout of admin", text)
        self.assertNotIn(PASSWORD, text)
        self.assertNotIn("the wrong password", text)

    def test_a_lockout_is_logged(self):
        with self.assertLogs("mast.web.auth", "WARNING") as logs:
            for _ in range(users.MAX_FAILURES):
                self.login("viewer", "the wrong password")
        self.assertIn("Locked the user 'viewer'", "\n".join(logs.output))
        self.assertEqual(self.login("viewer").status_code, 401)


class TestSessionEnds(WebCase):

    def copy_cookie(self):
        """A second client with a copy of the session cookie, as a thief
        would have."""
        thief = self.app.test_client()
        cookie = self.client.get_cookie("session")
        thief.set_cookie("session", cookie.value)
        return thief

    def test_a_copied_cookie_works_until_the_logout(self):
        self.login("operator")
        thief = self.copy_cookie()
        self.assertEqual(thief.get("/jobs").status_code, 200)
        self.post("/logout")
        self.assertEqual(thief.get("/jobs").status_code, 302)
        self.assertEqual(thief.get(C + "/").status_code, 302)

    def test_a_new_epoch_ends_the_session(self):
        changes = {
            "set_password": lambda: users.set_password("operator", PASSWORD),
            "set_role": lambda: users.set_role("operator", "operator"),
            "disable": lambda: users.disable("operator"),
            "delete": lambda: users.delete("operator"),
        }
        for name, change in changes.items():
            with self.subTest(change=name):
                client = self.app.test_client()
                self.login("operator", client=client)
                self.assertEqual(client.get("/jobs").status_code, 200)
                change()
                self.assertEqual(client.get("/jobs").status_code, 302)
                self.assertEqual(client.get(C + "/").status_code, 302)
                if name == "disable":
                    users.enable("operator")

    def test_idle_expiry(self):
        self.login("viewer")
        now = time.time()
        idle = self.app.config["MAST_IDLE_SECONDS"]
        self.assertEqual(idle, 30 * 60)
        with mock.patch.object(web_auth.time, "time",
                               return_value=now + idle - 5):
            self.assertEqual(self.client.get("/jobs").status_code, 200)
        # That request was a use: the idle time starts again
        with mock.patch.object(web_auth.time, "time",
                               return_value=now + 2 * idle - 10):
            self.assertEqual(self.client.get("/jobs").status_code, 200)
        with mock.patch.object(web_auth.time, "time",
                               return_value=now + 3 * idle):
            self.assertEqual(self.client.get("/jobs").status_code, 302)

    def test_absolute_expiry(self):
        self.login("viewer")
        start = time.time()
        longest = self.app.config["MAST_MAX_SECONDS"]
        self.assertEqual(longest, 12 * 3600)
        step = self.app.config["MAST_IDLE_SECONDS"] - 60
        at = start
        while at + step < start + longest:
            at += step
            with mock.patch.object(web_auth.time, "time", return_value=at):
                self.assertEqual(self.client.get("/jobs").status_code, 200)
        with mock.patch.object(web_auth.time, "time",
                               return_value=start + longest + 1):
            self.assertEqual(self.client.get("/jobs").status_code, 302)

    def test_the_limits_come_from_server_conf(self):
        conf = server_conf()
        conf["auth"] = {"idle_minutes": 5, "max_hours": 1}
        app = web_app.create_app(conf, classic=False)
        self.assertEqual(app.config["MAST_IDLE_SECONDS"], 300)
        self.assertEqual(app.config["MAST_MAX_SECONDS"], 3600)


class TestCookie(WebCase):

    def test_the_cookie_flags(self):
        self.login("viewer")
        cookie = self.client.get_cookie("session")
        self.assertTrue(cookie.http_only)
        self.assertEqual(cookie.same_site, "Lax")
        self.assertEqual(cookie.path, "/")
        self.assertFalse(cookie.secure)
        app = web_app.create_app(server_conf(secure=True), classic=False)
        self.assertTrue(app.config["SESSION_COOKIE_SECURE"])
        # The classic GUI has the same key and cookie
        self.assertEqual(gui.app.secret_key, self.app.secret_key)
        self.assertEqual(gui.app.config["SESSION_COOKIE_PATH"], "/")


class TestStart(unittest.TestCase):

    def test_no_tls_only_on_a_loopback_address(self):
        server.check_transport(False, "127.0.0.1")
        server.check_transport(False, "::1")
        server.check_transport(True, "0.0.0.0")
        for host in ("0.0.0.0", "10.1.2.3", "::", "mast.example.com"):
            with self.subTest(host=host):
                with self.assertRaises(server.InsecureServer) as caught:
                    server.check_transport(False, host)
                self.assertIn("secure = true", str(caught.exception))

    def test_main_refuses_to_start(self):
        values = web_app.settings(server_conf(host="0.0.0.0"))
        with mock.patch.object(web_app, "settings", return_value=values), \
                mock.patch.object(server.cherrypy.engine, "start") as start:
            with self.assertRaises(server.InsecureServer):
                server.main()
        start.assert_not_called()


class TestJobRoles(WebCase):
    """The job routes of the classic GUI use the principal of the
    session."""

    def test_a_viewer_reads_a_job_and_cannot_cancel_it(self):
        job_id = submit("dp1")
        self.login("viewer")
        self.assertEqual(self.client.get(C + "/jobs/" + job_id).status_code,
                         200)
        answer = self.post(C + "/jobs/{}/cancel".format(job_id))
        self.assertEqual(answer.status_code, 403)
        self.assertEqual(jobs.get(job_id).status, jobs.QUEUED)

    def test_an_operator_cancels_only_own_jobs(self):
        operator = jobs.Principal("operator", "operator")
        mine = queue.submit("test.work", {"appliances": ["dp1"]},
                            principal=operator)
        theirs = submit("dp2")
        self.login("operator")
        self.assertEqual(
            self.post(C + "/jobs/{}/cancel".format(theirs)).status_code, 403)
        self.assertEqual(
            self.post(C + "/jobs/{}/cancel".format(mine)).status_code, 200)
        self.assertEqual(jobs.get(mine).status, jobs.CANCELLED)
        self.assertEqual(jobs.get(theirs).status, jobs.QUEUED)

    def test_an_admin_cancels_any_job(self):
        theirs = submit("dp2")
        self.login("admin")
        self.assertEqual(
            self.post(C + "/jobs/{}/cancel".format(theirs)).status_code, 200)
        self.assertEqual(jobs.get(theirs).status, jobs.CANCELLED)

    def test_a_viewer_cannot_post_to_a_plugin(self):
        self.login("viewer")
        from mast.plugin_utils import plugin_functions
        with mock.patch.object(plugin_functions, "call_method") as call:
            for plugin in ("system", "ssh", "backups"):
                with self.subTest(plugin=plugin):
                    answer = self.post(C + "/" + plugin,
                                       data={"callable": "xor"})
                    self.assertEqual(answer.status_code, 403)
        call.assert_not_called()
        # A GET (the forms) is reading
        self.assertEqual(self.client.get(C + "/system?callable=xor")
                         .status_code, 200)

    def test_an_operator_posts_to_a_plugin(self):
        self.login("operator")
        from mast.plugin_utils import plugin_functions
        with mock.patch.object(plugin_functions, "call_method",
                               return_value="done") as call:
            answer = self.post(C + "/system", data={"callable": "xor"})
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(call.call_args.kwargs["principal"],
                         jobs.Principal("operator", "operator"))

    def test_the_history_download_serves_only_histories(self):
        self.login("viewer")
        for name in ("../../../../etc/local/server.key",
                     "20261122071926-../../../../../etc/local/jobs.key",
                     "20261122071926-{}.log/../../x".format("0" * 32), ""):
            with self.subTest(name=name):
                answer = self.post(C + "/download_history",
                                   data={"id": name})
                self.assertEqual(answer.status_code, 404)
        history = os.path.join("var", "www", "static", "tmp",
                               "request_history", "20261122071926")
        os.makedirs(history, exist_ok=True)
        name = "20261122071926-{}.log".format("a" * 32)
        with open(os.path.join(history, name), "w") as fout:
            fout.write("the history")
        self.addCleanup(os.remove, os.path.join(history, name))
        answer = self.post(C + "/download_history", data={"id": name})
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer.get_data(as_text=True), "the history")

    def upload(self, name):
        import io
        return self.post(C + "/upload", data={
            "_file_in": (io.BytesIO(b"uploaded"), name)},
            content_type="multipart/form-data")

    def test_an_upload_stays_in_the_upload_directory(self):
        self.login("operator")
        for name in ("../../etc/local/evil.conf", "/tmp/evil", "ok.xml"):
            with self.subTest(name=name):
                answer = self.upload(name)
                self.assertEqual(answer.status_code, 200)
                path = answer.get_json()["filename"]
                self.addCleanup(os.remove, path)
                upload_dir = os.path.abspath(gui.upload_dir)
                self.assertTrue(os.path.abspath(path).startswith(
                    upload_dir + os.sep), path)
                self.assertNotIn("..", path)
                with open(path, "rb") as fin:
                    self.assertEqual(fin.read(), b"uploaded")

    def test_a_viewer_cannot_upload(self):
        self.login("viewer")
        self.assertEqual(self.upload("ok.xml").status_code, 403)

    def test_the_config_route_keeps_secrets(self):
        self.login("viewer")
        for name in ("server", "jobs", "xor", "server.conf"):
            with self.subTest(name=name):
                self.assertEqual(
                    self.client.get(C + "/config/" + name).get_json(), {})


class ClassicCase(WebCase):
    """Logged in as admin, on the classic GUI."""

    def setUp(self):
        super().setUp()
        self.login("admin")

    def page(self):
        return self.client.get(C + "/").get_data(as_text=True)

    def page_token(self):
        return TOKEN.search(self.page()).group(1)


class TestCsrf(ClassicCase):

    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(gui.plugin_functions, "cancel_job",
                                    return_value={"events": []})
        self.cancel_job = patcher.start()
        self.addCleanup(patcher.stop)

    def cancel(self, **kwargs):
        return self.client.post(C + "/jobs/abc/cancel", data={"after": "0"},
                                **kwargs)

    def test_the_page_has_the_long_random_token_of_the_login(self):
        token = self.page_token()
        self.assertGreaterEqual(len(token), 32)
        self.assertEqual(token, self.token())
        other = self.app.test_client()
        self.login("admin", client=other)
        self.assertNotEqual(self.token(other), token)

    def test_the_token_stays_the_same_in_a_session(self):
        # Every tab of the web GUI must be able to use it.
        self.assertEqual(self.page_token(), self.page_token())

    def test_a_post_without_the_token_is_refused(self):
        answer = self.cancel()
        self.assertEqual(answer.status_code, 403)
        self.assertIn("Reload", answer.get_data(as_text=True))
        self.cancel_job.assert_not_called()

    def test_a_post_with_another_token_is_refused(self):
        answer = self.cancel(headers={gui.CSRF_HEADER: "x" * 43})
        self.assertEqual(answer.status_code, 403)
        self.cancel_job.assert_not_called()

    def test_the_token_is_accepted_as_a_header(self):
        answer = self.cancel(headers={gui.CSRF_HEADER: self.page_token()})
        self.assertEqual(answer.status_code, 200)
        self.cancel_job.assert_called_once()
        self.assertEqual(self.cancel_job.call_args.kwargs["principal"],
                         jobs.Principal("admin", "admin"))

    def test_the_token_is_accepted_as_a_form_field(self):
        token = self.page_token()
        answer = self.client.post(C + "/jobs/abc/cancel",
                                  data={"after": "0", gui.CSRF_FIELD: token})
        self.assertEqual(answer.status_code, 200)

    def test_every_method_that_changes_something_needs_the_token(self):
        for method in ("POST", "PUT", "DELETE", "PATCH"):
            with self.subTest(method=method):
                answer = self.client.open(C + "/system", method=method)
                self.assertEqual(answer.status_code, 403)

    def test_a_get_needs_no_token(self):
        self.assertEqual(self.client.get(C + "/plugins.js").status_code, 200)

    def test_the_upload_form_carries_the_token(self):
        token = self.page_token()
        with gui.app.test_request_context("/", base_url="http://localhost" + C):
            gui.flask.session["csrf"] = token
            form = gui.flask.render_template(
                "fileupload.html", name="file_in", label="File", plugin="system")
        self.assertIn('name="csrf_token" value="{}"'.format(token), form)
        self.assertIn('action="/classic/upload"', form)

    def test_the_scripts_send_the_token(self):
        static = gui.app.static_folder
        with open(static + "/js/mast.js") as fin:
            self.assertIn('"X-CSRF-Token": MAST_CSRF_TOKEN', fin.read())
        with open(static + "/js/jquery.download.js") as fin:
            self.assertIn('name="csrf_token"', fin.read())


class TestNewAppCsrf(WebCase):

    def test_a_post_without_the_token_gets_400(self):
        self.login("admin")
        answer = self.client.post("/logout")
        self.assertEqual(answer.status_code, 400)
        self.assertEqual(self.client.get("/jobs").status_code, 200)
        answer = self.client.post("/logout", headers={
            security.CSRF_HEADER: "x" * 43})
        self.assertEqual(answer.status_code, 400)

    def test_the_form_field_works_too(self):
        self.login("admin")
        answer = self.client.post("/logout", data={
            security.CSRF_FIELD: self.token()})
        self.assertEqual(answer.status_code, 302)


class TestClassicMount(ClassicCase):

    def test_the_page_points_into_the_mount(self):
        page = self.page()
        self.assertIn('<meta name="mast-root" content="/classic" />', page)
        self.assertIn('<script src="/classic/plugins.js"></script>', page)
        self.assertIn('href="/classic/plugins.css"', page)
        self.assertNotIn('src="/static', page)
        self.assertNotIn("src='/static", page)
        self.assertEqual(
            self.client.get(C + "/static/js/mast.js").status_code, 200)

    def test_the_scripts_prefix_their_requests(self):
        static = gui.app.static_folder
        with open(static + "/js/mast.js") as fin:
            self.assertIn("$.ajaxPrefilter", fin.read())
        with open(static + "/js/jquery.download.js") as fin:
            self.assertIn("mastUrl(url)", fin.read())


PAGES = ("/", "/login", "/account/password", "/static/css/mast-web.css",
         "/jobs", "/jobs/" + "0" * 32, "/commands", "/admin/users",
         "/api/v1/me", "/api/v1/jobs", "/static/js/forms.js",
         C + "/", C + "/plugins.js", C + "/static/js/mast.js")


class TestContentSecurityPolicy(ClassicCase):

    def test_every_answer_has_a_strict_policy(self):
        for path in PAGES + ("/nothing/here",):
            with self.subTest(path=path):
                policy = self.client.get(path).headers["Content-Security-Policy"]
                directives = dict(d.strip().split(" ", 1) for d in policy.split(";"))
                self.assertEqual(directives["script-src"], "'self'")
                self.assertEqual(directives["style-src"], "'self'")
                self.assertEqual(directives["default-src"], "'self'")
                self.assertEqual(directives["object-src"], "'none'")
                self.assertEqual(directives["frame-ancestors"], "'self'")
                self.assertNotIn("unsafe", policy)

    def test_the_policy_is_on_the_login_page_without_a_session(self):
        client = self.app.test_client()
        for path in ("/login", "/", C + "/"):
            with self.subTest(path=path):
                self.assertIn("Content-Security-Policy",
                              client.get(path).headers)

    def test_the_pages_have_no_inline_script_or_style(self):
        for path in ("/jobs", "/jobs/" + "0" * 32, "/commands",
                     "/admin/users", "/account/password", C + "/"):
            with self.subTest(path=path):
                parser = _InlineCode()
                parser.feed(self.client.get(path).get_data(as_text=True))
                self.assertEqual(parser.found, [])
        parser = _InlineCode()
        parser.feed(self.app.test_client().get("/login").get_data(as_text=True))
        self.assertEqual(parser.found, [])

    def test_the_templates_have_no_inline_script(self):
        folder = os.path.join(os.path.dirname(web_app.__file__), "templates")
        for name in os.listdir(folder):
            with self.subTest(template=name):
                with open(os.path.join(folder, name)) as fin:
                    parser = _InlineCode()
                    parser.feed(fin.read())
                self.assertEqual(parser.found, [])

    def test_the_page_loads_the_code_of_the_plugins_as_files(self):
        page = self.page()
        self.assertIn('<script src="/classic/plugins.js"></script>', page)
        self.assertIn('href="/classic/plugins.css"', page)

    def test_the_code_of_the_plugins_is_served(self):
        js = self.client.get(C + "/plugins.js")
        self.assertEqual(js.mimetype, "text/javascript")
        body = js.get_data(as_text=True)
        self.assertTrue(body.startswith("$(function(){"))
        self.assertIn(str(gui.app.PLUGINS["js"]).strip()[:200], body)
        css = self.client.get(C + "/plugins.css")
        self.assertEqual(css.mimetype, "text/css")
        self.assertEqual(css.get_data(as_text=True), str(gui.app.PLUGINS["css"]))

    def test_other_headers(self):
        for path in ("/", C + "/"):
            headers = self.client.get(path).headers
            self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
            self.assertEqual(headers["Referrer-Policy"], "same-origin")


class TestHsts(ClassicCase):

    def test_no_hsts_without_tls(self):
        for path in PAGES:
            with self.subTest(path=path):
                self.assertNotIn("Strict-Transport-Security",
                                 self.client.get(path).headers)

    def test_hsts_with_tls(self):
        app = web_app.create_app(server_conf(secure=True, hsts_max_age=600))
        client = app.test_client()
        for path in ("/login", "/", C + "/", "/static/css/mast-web.css"):
            with self.subTest(path=path):
                answer = client.get(path, base_url="https://localhost")
                self.assertEqual(
                    answer.headers["Strict-Transport-Security"], "max-age=600")


if __name__ == "__main__":
    unittest.main()

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
CSRF token, the Content Security Policy and HSTS, and that the classic
GUI is gone. The roles of the job routes are tested in
test_mast_web_api.
"""
import html.parser
import os
import time
import unittest
from unittest import mock

from mast.auth import users
from mast.testsuite.unit.web_helpers import PASSWORD, WebCase, server_conf
from mast.web import app as web_app
from mast.web import auth as web_auth
from mast.web import security, server

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
        for path in ("/", "/account/password", "/jobs", "/commands",
                     "/terminal", "/crypto", "/appliances", "/admin/users"):
            with self.subTest(path=path):
                answer = self.client.get(path)
                self.assertEqual(answer.status_code, 302)
                self.assertTrue(answer.location.startswith("/login?next="))

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
        self.assertEqual(self.client.get("/crypto").status_code, 200)
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
            "next": "/crypto"})
        self.assertEqual(answer.location, "/crypto")

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
        for path in ("/", "/crypto"):
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
        self.assertIn("mast users add", answer.get_data(as_text=True))
        self.assertEqual(self.client.get("/crypto").status_code, 302)

    def test_logins_are_logged_without_the_password(self):
        with self.assertLogs("mast.web.auth", "INFO") as logs:
            self.login("admin", "the wrong password")
            self.login("admin")
            self.post("/logout")
        text = "\n".join(logs.output)
        self.assertIn("Failed login for 'admin'", text)
        self.assertIn("Login of admin from", text)
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
        self.assertEqual(thief.get("/crypto").status_code, 302)

    def test_a_new_epoch_ends_the_session(self):
        changes = {
            "set_password": lambda: users.set_password("operator", PASSWORD),
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
                self.assertEqual(client.get("/crypto").status_code, 302)
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
        app = web_app.create_app(conf)
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
        app = web_app.create_app(server_conf(secure=True))
        self.assertTrue(app.config["SESSION_COOKIE_SECURE"])


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


PAGES = ("/", "/login", "/account/password", "/static/css/mast-web.css",
         "/jobs", "/jobs/" + "0" * 32, "/commands", "/admin/users",
         "/appliances", "/crypto", "/terminal",
         "/api/v1/me", "/api/v1/jobs", "/static/js/forms.js",
         "/static/terminal/terminal.js", "/static/crypto/crypto.js")


class AdminCase(WebCase):
    """Logged in as admin."""

    def setUp(self):
        super().setUp()
        self.login("admin")


class TestClassicIsGone(AdminCase):
    """The classic GUI was removed in R-1: its routes, its scripts with
    the xor-encoded passwords, and its link."""

    def test_its_routes_are_not_found(self):
        for path in ("/classic/", "/classic/plugins.js", "/classic/system",
                     "/classic/test/connectivity/dp1", "/classic/jobs/abc",
                     "/classic/static/js/mast.js", "/classic/upload",
                     "/test/connectivity/dp1", "/plugins.js"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.post("/classic/system").status_code, 404)

    def test_no_page_links_to_it(self):
        for path in ("/jobs", "/commands", "/terminal", "/crypto"):
            with self.subTest(path=path):
                page = self.client.get(path).get_data(as_text=True)
                self.assertNotIn("/classic", page)
                self.assertNotIn("Classic GUI", page)


class TestContentSecurityPolicy(AdminCase):

    def test_every_answer_has_a_strict_policy(self):
        for path in PAGES + ("/nothing/here",):
            with self.subTest(path=path):
                policy = self.client.get(path).headers["Content-Security-Policy"]
                directives = dict(d.strip().split(" ", 1) for d in policy.split(";"))
                self.assertEqual(directives["script-src"], "'self'")
                self.assertEqual(directives["style-src"], "'self'")
                self.assertEqual(directives["default-src"], "'self'")
                self.assertEqual(directives["object-src"], "'none'")
                self.assertEqual(directives["frame-ancestors"], "'none'")
                self.assertNotIn("unsafe", policy)

    def test_the_policy_is_on_the_login_page_without_a_session(self):
        client = self.app.test_client()
        for path in ("/login", "/"):
            with self.subTest(path=path):
                self.assertIn("Content-Security-Policy",
                              client.get(path).headers)

    def test_the_pages_have_no_inline_script_or_style(self):
        for path in ("/jobs", "/jobs/" + "0" * 32, "/commands",
                     "/admin/users", "/account/password", "/appliances",
                     "/crypto", "/terminal"):
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

    def test_other_headers(self):
        for path in ("/", "/terminal"):
            headers = self.client.get(path).headers
            self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
            self.assertEqual(headers["Referrer-Policy"], "same-origin")


class TestHsts(AdminCase):

    def test_no_hsts_without_tls(self):
        for path in PAGES:
            with self.subTest(path=path):
                self.assertNotIn("Strict-Transport-Security",
                                 self.client.get(path).headers)

    def test_hsts_with_tls(self):
        app = web_app.create_app(server_conf(secure=True, hsts_max_age=600))
        client = app.test_client()
        for path in ("/login", "/", "/static/css/mast-web.css"):
            with self.subTest(path=path):
                answer = client.get(path, base_url="https://localhost")
                self.assertEqual(
                    answer.headers["Strict-Transport-Security"], "max-age=600")


if __name__ == "__main__":
    unittest.main()

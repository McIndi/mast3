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
Tests of how the web GUI defends itself in the browser: the CSRF token,
the Content Security Policy and HSTS.
"""
import html.parser
import re
import unittest
from unittest import mock

from mast.datapower.web import gui

TOKEN = re.compile(r'name="csrf-token" content="([^"]+)"')


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


class WebTestCase(unittest.TestCase):
    def setUp(self):
        self.client = gui.app.test_client()

    def page(self):
        return self.client.get("/").get_data(as_text=True)

    def token(self):
        return TOKEN.search(self.page()).group(1)


class TestCsrf(WebTestCase):

    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(gui.plugin_functions, "cancel_job",
                                    return_value={"events": []})
        self.cancel_job = patcher.start()
        self.addCleanup(patcher.stop)

    def cancel(self, **kwargs):
        return self.client.post("/jobs/abc/cancel", data={"after": "0"}, **kwargs)

    def test_the_page_has_a_long_random_token(self):
        token = self.token()
        self.assertGreaterEqual(len(token), 32)
        other = gui.app.test_client().get("/").get_data(as_text=True)
        self.assertNotEqual(TOKEN.search(other).group(1), token)

    def test_the_token_stays_the_same_in_a_session(self):
        # Every tab of the web GUI must be able to use it.
        self.assertEqual(self.token(), self.token())

    def test_a_post_without_the_token_is_refused(self):
        self.page()
        answer = self.cancel()
        self.assertEqual(answer.status_code, 403)
        self.assertIn("Reload", answer.get_data(as_text=True))
        self.cancel_job.assert_not_called()

    def test_a_post_with_another_token_is_refused(self):
        self.page()
        answer = self.cancel(headers={gui.CSRF_HEADER: "x" * 43})
        self.assertEqual(answer.status_code, 403)
        self.cancel_job.assert_not_called()

    def test_a_post_without_a_session_is_refused(self):
        token = self.token()
        stranger = gui.app.test_client()
        answer = stranger.post("/jobs/abc/cancel", data={"after": "0"},
                               headers={gui.CSRF_HEADER: token})
        self.assertEqual(answer.status_code, 403)

    def test_the_token_is_accepted_as_a_header(self):
        answer = self.cancel(headers={gui.CSRF_HEADER: self.token()})
        self.assertEqual(answer.status_code, 200)
        self.cancel_job.assert_called_once()

    def test_the_token_is_accepted_as_a_form_field(self):
        token = self.token()
        answer = self.client.post("/jobs/abc/cancel",
                                  data={"after": "0", gui.CSRF_FIELD: token})
        self.assertEqual(answer.status_code, 200)

    def test_every_method_that_changes_something_needs_the_token(self):
        self.page()
        for method in ("POST", "PUT", "DELETE", "PATCH"):
            with self.subTest(method=method):
                answer = self.client.open("/system", method=method)
                self.assertEqual(answer.status_code, 403)

    def test_a_get_needs_no_token(self):
        self.assertEqual(self.client.get("/plugins.js").status_code, 200)

    def test_the_upload_form_carries_the_token(self):
        token = self.token()
        with gui.app.test_request_context("/"):
            gui.flask.session["csrf"] = token
            form = gui.flask.render_template(
                "fileupload.html", name="file_in", label="File", plugin="system")
        self.assertIn('name="csrf_token" value="{}"'.format(token), form)

    def test_the_scripts_send_the_token(self):
        static = gui.app.static_folder
        with open(static + "/js/mast.js") as fin:
            self.assertIn('"X-CSRF-Token": MAST_CSRF_TOKEN', fin.read())
        with open(static + "/js/jquery.download.js") as fin:
            self.assertIn('name="csrf_token"', fin.read())


class TestContentSecurityPolicy(WebTestCase):

    def test_every_answer_has_a_strict_policy(self):
        for path in ("/", "/plugins.js", "/static/js/mast.js"):
            with self.subTest(path=path):
                policy = self.client.get(path).headers["Content-Security-Policy"]
                directives = dict(d.strip().split(" ", 1) for d in policy.split(";"))
                self.assertEqual(directives["script-src"], "'self'")
                self.assertEqual(directives["style-src"], "'self'")
                self.assertEqual(directives["default-src"], "'self'")
                self.assertEqual(directives["object-src"], "'none'")
                self.assertEqual(directives["frame-ancestors"], "'self'")
                self.assertNotIn("unsafe", policy)

    def test_the_page_has_no_inline_script_or_style(self):
        parser = _InlineCode()
        parser.feed(self.page())
        self.assertEqual(parser.found, [])

    def test_the_page_loads_the_code_of_the_plugins_as_files(self):
        page = self.page()
        self.assertIn('<script src="/plugins.js"></script>', page)
        self.assertIn('href="/plugins.css"', page)

    def test_the_code_of_the_plugins_is_served(self):
        js = self.client.get("/plugins.js")
        self.assertEqual(js.mimetype, "text/javascript")
        body = js.get_data(as_text=True)
        self.assertTrue(body.startswith("$(function(){"))
        self.assertIn(str(gui.app.PLUGINS["js"]).strip()[:200], body)
        css = self.client.get("/plugins.css")
        self.assertEqual(css.mimetype, "text/css")
        self.assertEqual(css.get_data(as_text=True), str(gui.app.PLUGINS["css"]))

    def test_other_headers(self):
        headers = self.client.get("/").headers
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["Referrer-Policy"], "same-origin")


class TestHsts(WebTestCase):

    def test_no_hsts_without_tls(self):
        with mock.patch.object(gui, "secure", False):
            self.assertNotIn("Strict-Transport-Security",
                             self.client.get("/").headers)

    def test_hsts_with_tls(self):
        with mock.patch.object(gui, "secure", True), \
                mock.patch.object(gui, "hsts_max_age", 31536000):
            self.assertEqual(
                self.client.get("/").headers["Strict-Transport-Security"],
                "max-age=31536000")


if __name__ == "__main__":
    unittest.main()

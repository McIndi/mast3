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
Helpers for the tests of the web UI: an app with an empty job database,
users with cheap password hashes, and a client that logs in.
"""
import copy
import re

from mast.auth import users
from mast.config import get_configs_dict
from mast.testsuite.unit.test_mast_auth import as_role, fast_hashes
from mast.testsuite.unit.test_mast_jobs import SqliteCase
from mast.web import security
from mast.web.app import create_app

PASSWORD = "a long test password"
TOKEN = re.compile(r'name="csrf-token" content="([^"]+)"')


def server_conf(**server):
    """The server.conf of the test home, with `server` changed in its
    [server] stanza. The tests use HTTP, so `secure` is False unless
    given."""
    conf = copy.deepcopy(get_configs_dict()["server.conf"])
    conf["server"]["secure"] = False
    conf["server"].update(server)
    return conf


class WebCase(SqliteCase):
    """Each test has an empty job database, the users `admin`,
    `operator` and `viewer` (whose passwords need no change), and
    `self.client`, which is not logged in."""

    def setUp(self):
        super().setUp()
        fast_hashes(self)
        for role in ("admin", "operator", "viewer"):
            users.add(role, PASSWORD, as_role(role))
            users.set_password(role, PASSWORD)
        self.app = create_app(server_conf())
        self.client = self.app.test_client()

    def login(self, name="admin", password=PASSWORD, client=None):
        client = client or self.client
        client.get("/login")
        with client.session_transaction() as session:
            token = session["csrf"]
        return client.post("/login", data={
            "username": name, "password": password, "csrf_token": token,
            "next": "/"})

    def token(self, client=None):
        with (client or self.client).session_transaction() as session:
            return session.get("csrf")

    def page_token(self, path="/jobs", client=None):
        page = (client or self.client).get(path).get_data(as_text=True)
        return TOKEN.search(page).group(1)

    def post(self, path, client=None, **kwargs):
        """POST with the CSRF token of the session."""
        headers = kwargs.pop("headers", {})
        headers.setdefault(security.CSRF_HEADER, self.token(client))
        return (client or self.client).post(path, headers=headers, **kwargs)

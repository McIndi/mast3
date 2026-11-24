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
No secret leaks: a stored credential whose password is a unique marker
is used in every way that MAST offers (a job of the web UI, a list of
the objects on the appliances for a form, a poll of the status of the
appliances, the crypto inventory and its export, a job of a worker, a job that
fails, a command that repeats the password, a test
of the credential, a schedule, a new password, a new key). Then the
marker, in any of its encodings, must be nowhere:

* in no column of any table of the database (a new table is searched
without a change here);
* in no log file of this process, with every logger at DEBUG;
* in no file of the jobs;
* in no answer of any GET route of the web UI (each route of the URL
map, so that a new route is searched too; a route with an argument that
this test cannot fill fails the test, and gets a value in `_values`).

The password is encrypted in `credentials.token`, so it is not there as
text either.
"""
import base64
import html
import json
import logging
import os
import re
import secrets
import ssl
import time
import urllib.parse
from unittest import mock

from sqlalchemy import inspect, select, table, column

from mast import jobs, registry
from mast import logging as mast_logging
from mast.config import MAST_HOME
from mast.credentials import store
from mast.datapower.datapower import tls
from mast.jobs import db
from mast.jobs import registry as commands
from mast.jobs.auth import Principal
from mast.jobs.models import FINISHED
from mast.testsuite.integration.fake_soma import FakeSoma
from mast.testsuite.unit.test_mast_credentials import (
    ADMIN, OPERATOR, make_cli)
from mast.testsuite.unit.test_mast_jobs import PostgresMixin
from mast.testsuite.unit.web_helpers import WebCase
from mast.web.catalog import job_dir

USERNAME = "reader"


def forms(secret):
    """The encodings in which `secret` could leak."""
    raw = secret.encode()
    found = {
        secret,
        html.escape(secret),
        urllib.parse.quote(secret),
        urllib.parse.quote_plus(secret),
        json.dumps(secret)[1:-1],
        base64.b64encode("{}:{}".format(USERNAME, secret).encode()).decode(),
    }
    # In base64, at each of the three alignments inside a longer text
    for pad in range(3):
        encoded = base64.b64encode(b"\0" * pad + raw).decode()
        found.add(encoded[4 if pad else 0:-4])
    return sorted(found)


def wait_finished(job_id, seconds=30):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        info = jobs.get(job_id)
        if info.status in FINISHED:
            return info
        time.sleep(0.05)
    raise AssertionError("the job {} did not finish".format(job_id))


class TestNoSecretLeaks(WebCase):

    def setUp(self):
        super().setUp()
        self.markers = [
            "Zq9-leak-marker-{}&+".format(secrets.token_hex(8)),
            "Zq9-leak-marker-{}&+".format(secrets.token_hex(8))]
        self.key_file = os.path.join(self.tmp, "keys", "credentials.key")
        patcher = mock.patch("mast.jobs.config.credentials_key_file",
                             return_value=self.key_file)
        patcher.start()
        self.addCleanup(patcher.stop)
        commands.register("credtest", make_cli())
        self.addCleanup(commands.unregister, "credtest")
        self.debug_logs()
        self.soma = FakeSoma().__enter__()
        self.addCleanup(self.soma.__exit__, None, None, None)

    def debug_logs(self):
        """Every logger of MAST, and every one that is made later, at
        DEBUG for this test."""
        # make_logger takes its level as the default of an argument
        defaults = mast_logging.make_logger.__defaults__
        patcher = mock.patch.object(mast_logging.make_logger, "__defaults__",
                                    (logging.DEBUG,) + defaults[1:])
        patcher.start()
        self.addCleanup(patcher.stop)
        for logger in list(logging.Logger.manager.loggerDict.values()):
            if not isinstance(logger, logging.Logger):
                continue
            for handler in logger.handlers:
                if isinstance(handler, mast_logging.DelayedDirCreatingRotatingFileHandler):
                    self.addCleanup(handler.setLevel, handler.level)
                    handler.setLevel(logging.DEBUG)
                    self.addCleanup(logger.setLevel, logger.level)
                    logger.setLevel(logging.DEBUG)

    def call(self, method, path, body=None, client=None, status=None):
        client = client or self.client
        answer = client.open("/api/v1" + path, method=method, json=body,
                             headers={"X-CSRF-Token": self.token(client)})
        self.answers.append(("{} {}".format(method, path), answer))
        if status is not None:
            self.assertEqual(answer.status_code, status, answer.get_data(as_text=True))
        return answer

    def fake_certificate_sha256(self):
        pem = ssl.get_server_certificate(("127.0.0.1", self.soma.port))
        return tls.fingerprint(ssl.PEM_cert_to_DER_cert(pem))

    def use_everything(self):
        """Use the credential in every way, as an admin and an
        operator. Return the ids of the jobs."""
        self.answers = []
        registry.add("dp-fake", "127.0.0.1", soma_port=self.soma.port)
        registry.pin_tls("dp-fake", self.fake_certificate_sha256(), "admin")
        # The same appliance with a wrong pin: its job fails
        registry.add("dp-changed", "127.0.0.1", soma_port=self.soma.port)
        registry.pin_tls("dp-changed", "0" * 64, "admin")

        admin = self.client
        self.login("admin", client=admin)
        operator = self.app.test_client()
        self.login("operator", client=operator)

        self.call("POST", "/credentials", {
            "name": "leaky", "username": USERNAME, "password": self.markers[0],
            "purpose": "collect", "bindings": ["dp-fake", "dp-changed"],
            "description": "for the leak test"}, client=admin, status=201)
        job_ids = []

        # A job of the web UI, done by the web server
        answer = self.call("POST", "/jobs", {
            "command": "system.list-domains",
            "params": {"appliances": ["dp-fake"], "credential": "leaky"}},
            client=operator)
        self.assertLess(answer.status_code, 300, answer.get_data(as_text=True))
        job_ids.append(answer.get_json()["job_id"])
        self.assertEqual(wait_finished(job_ids[-1]).status, jobs.SUCCEEDED,
                         jobs.get(job_ids[-1]).error)
        self.assertGreater(self.soma.requests, 0)

        # A list of the objects on the appliances, for a form
        answer = self.call("POST", "/choices/configured:Domain", {
            "appliances": ["dp-fake"], "credential": "leaky"}, client=operator)
        self.assertEqual(answer.status_code, 200, answer.get_data(as_text=True))
        job_ids.append(answer.get_json()["job_id"])

        # The status of the appliance, kept for the Appliances page
        job_ids.append(jobs.submit("status.collect", {
            "appliances": ["dp-fake"], "credential": "leaky",
            "request_delay": 0}, principal=OPERATOR))
        jobs.run_next("w-leak")
        self.assertEqual(jobs.get(job_ids[-1]).status, jobs.SUCCEEDED,
                         jobs.get(job_ids[-1]).error)

        # The crypto inventory (a parent and a child job) and its export
        # (as the user operator of the web UI: the parent checks its
        # children as the user who submitted it, who must exist)
        job_ids.append(jobs.submit("crypto.inventory", {
            "appliances": ["dp-fake"], "credential": "leaky",
            "request_delay": 0}, principal=Principal("operator", "operator")))
        jobs.run_next("w-leak")
        self.assertEqual(jobs.get(job_ids[-1]).status, jobs.SUCCEEDED,
                         jobs.get(job_ids[-1]).error)
        job_ids += [c.id for c in jobs.list_jobs(parent_id=job_ids[-1])]
        export = jobs.submit("crypto.export", {"format": "cbom", "out_file": os.path.join(
            MAST_HOME, job_dir(job_ids[-1]), "inventory")}, principal=OPERATOR)
        jobs.run_next("w-leak")
        self.assertEqual(jobs.get(export).status, jobs.SUCCEEDED,
                         jobs.get(export).error)
        job_ids.append(export)

        # A job of a worker that fails before it sends anything
        job_ids.append(jobs.submit("system.list-domains", {
            "appliances": ["dp-changed"], "credential": "leaky"},
            principal=OPERATOR))
        jobs.run_next("w-leak")
        self.assertEqual(jobs.get(job_ids[-1]).status, jobs.FAILED)

        # A command that repeats the password in its output, its result
        # and its history: MAST must take it out of each
        job_ids.append(jobs.submit("credtest.look", {
            "appliances": ["dp-fake"], "credential": "leaky"},
            principal=OPERATOR))
        jobs.run_next("w-leak")
        self.assertEqual(jobs.get(job_ids[-1]).status, jobs.SUCCEEDED)

        # A test of the credential
        answer = self.call("POST", "/credentials/leaky/tests",
                           {"appliance": "dp-fake"}, client=admin)
        self.assertLess(answer.status_code, 300, answer.get_data(as_text=True))
        test_job = answer.get_json()["job_id"]
        job_ids.append(test_job)
        if jobs.get(test_job).status not in FINISHED:
            jobs.run_next("w-leak")
        wait_finished(test_job)
        self.call("POST", "/credentials/leaky/tests/" + test_job, client=admin)

        # A schedule, run now
        self.call("POST", "/schedules", {
            "name": "leaky-look", "command": "system.list-domains",
            "params": {"appliances": ["dp-fake"], "credential": "leaky"},
            "cron": "0 2 * * *"}, client=admin, status=201)
        answer = self.call("POST", "/schedules/leaky-look/run", client=admin)
        self.assertLess(answer.status_code, 300, answer.get_data(as_text=True))
        job_ids.append(answer.get_json()["job_id"])
        jobs.run_next("w-leak")
        self.assertEqual(wait_finished(job_ids[-1]).status, jobs.SUCCEEDED)

        # A new password, a new key, and a job with both
        self.call("PATCH", "/credentials/leaky", {"password": self.markers[1]},
                  client=admin, status=200)
        store.rotate_key(principal=ADMIN)
        job_ids.append(jobs.submit("system.list-domains", {
            "appliances": ["dp-fake"], "credential": "leaky"},
            principal=OPERATOR))
        jobs.run_next("w-leak")
        self.assertEqual(jobs.get(job_ids[-1]).status, jobs.SUCCEEDED)

        # A pin preview talks to the appliance too
        self.call("POST", "/appliances/dp-fake/pin/preview", {}, client=admin)

        # The Test button of the target bar, with the stored credential
        answer = self.call("POST", "/connectivity", {
            "appliances": ["dp-fake"], "credential": "leaky"},
            client=operator, status=200)
        found = answer.get_json()
        # It asked the appliance; SSH is refused before the login, as
        # the host key of dp-fake is not pinned
        self.assertEqual(found["rows"][0]["appliance"], "dp-fake", found)
        self.assertIn("NotPinned", found["rows"][0]["problem"])
        job_ids.append(found["job_id"])

        # A terminal: a typed password for the login, and another one
        # typed at a password prompt (a fake shell, which echoes both)
        job_ids.append(self.use_a_terminal(operator))
        return job_ids

    def use_a_terminal(self, operator):
        from mast.datapower.ssh import page as terminal_page
        from mast.datapower.ssh import sessions
        from mast.testsuite.unit.test_mast_datapower_ssh_terminal import (
            FakeShell)
        manager = sessions.SessionManager(
            shell_factory=FakeShell,
            settings_=sessions.Settings(connect_timeout=2),
            sweep_seconds=3600)
        patcher = mock.patch.object(terminal_page, "MANAGER", manager)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(manager.close_all)
        answer = self.call("POST", "/ssh/sessions", {
            "appliances": ["dp-fake"],
            "credentials": [USERNAME + ":" + self.markers[1]]},
            client=operator, status=201)
        session = answer.get_json()
        base = "/ssh/sessions/" + session["id"]
        for line in ("show clock", "ask", self.markers[0]):
            self.call("POST", base + "/lines", {"line": line},
                      client=operator, status=200)
        self.call("GET", base, client=operator, status=200)
        self.call("GET", "/ssh/sessions", client=operator, status=200)
        self.call("GET", base + "/lines/3", client=operator, status=200)
        for query in ("", "?appliance=dp-fake"):
            self.call("GET", base + "/transcript" + query, client=operator,
                      status=200)
        self.terminal_id = session["id"]
        self.call("DELETE", base, client=operator, status=200)
        self.assertEqual(jobs.get(session["job_id"]).status, jobs.SUCCEEDED)
        # The appliance got both, and echoed both: they were there to leak
        shell = FakeShell.made[-1]
        self.assertEqual((shell.password, shell.lines[-1]),
                         (self.markers[1], self.markers[0]))
        result = jobs.result(session["job_id"]).text
        self.assertIn("Password: ********", result)
        self.assertIn("You typed ********", result)
        return session["job_id"]

    def assert_clean(self, where, text):
        if isinstance(text, bytes):
            text = text.decode("utf-8", "replace")
        for marker in self.markers:
            for form in forms(marker):
                self.assertNotIn(form, text, "A password is in {} (as {!r})".format(
                    where, form))

    def check_database(self):
        engine = db.engine()
        with engine.connect() as connection:
            names = inspect(connection).get_table_names()
            self.assertIn("credentials", names)
            for name in names:
                columns = [c["name"] for c in inspect(connection).get_columns(name)]
                query = select(*[column(c) for c in columns]).select_from(table(name))
                for row in connection.execute(query):
                    for key, value in zip(columns, row):
                        if value is not None:
                            self.assert_clean("{}.{}".format(name, key),
                                              value if isinstance(value, bytes)
                                              else str(value))

    def check_files(self, directories):
        searched = 0
        for directory in directories:
            for root, _, names in os.walk(directory):
                for name in names:
                    path = os.path.join(root, name)
                    with open(path, "rb") as fin:
                        self.assert_clean(path, fin.read())
                    searched += 1
        return searched

    def log_directory(self):
        import getpass
        return os.path.join(
            mast_logging.mast_home, "var", "log", getpass.getuser(),
            "{}-{}".format(mast_logging.t.timestamp, os.getpid()))

    def _values(self, rule, job_ids):
        """The values to try for each argument of a GET route."""
        values = {
            "job_id": job_ids,
            # The Terminal page: the session is closed by now, and its
            # answers while it was open are in self.answers
            "session_id": [getattr(self, "terminal_id", "none")],
            "number": [1, 3],
            "command_id": ["system.list-domains", "credtest.look"],
            "filename": None,  # static files: not searched
        }
        if rule.startswith("/api/v1/credentials/"):
            values["name"] = ["leaky"]
        elif rule.startswith("/api/v1/appliances-status/"):
            values["name"] = ["dp-fake", "dp-changed"]
        elif rule.startswith("/api/v1/crypto/objects/"):
            from mast.datapower.crypto.models import CryptoObject
            with db.session(write=False) as session:
                values["object_id"] = list(session.scalars(
                    select(CryptoObject.id).order_by(CryptoObject.id))) or [1]
        elif rule.startswith("/api/v1/jobs/"):
            values["name"] = [f["name"] for job_id in job_ids
                              for f in self.client.get(
                                  "/api/v1/jobs/{}/files".format(job_id)
                              ).get_json().get("files", [])] or ["nothing"]
        return values

    def check_routes(self, job_ids):
        """Every GET route, as the admin, with each value of its
        arguments."""
        checked = []
        for rule in self.app.url_map.iter_rules():
            # The static files (of the app and of a page's blueprint)
            # hold no data
            if "GET" not in rule.methods or rule.endpoint == "static" \
                    or rule.endpoint.endswith(".static"):
                continue
            values = self._values(rule.rule, job_ids)
            missing = [a for a in rule.arguments if not values.get(a)]
            self.assertEqual(missing, [], "The leak test does not know what "
                             "to give {} for {}: add it to _values".format(
                                 rule.rule, missing))
            combos = [{}]
            for argument in sorted(rule.arguments):
                combos = [dict(c, **{argument: v}) for c in combos
                          for v in values[argument]]
            for combo in combos:
                path = rule.rule
                for argument, value in combo.items():
                    # <name>, or with a converter: <int:name>
                    path = re.sub(r"<(?:\w+:)?{}>".format(argument),
                                  str(value), path)
                answer = self.client.get(path)
                self.assert_clean("GET " + path, answer.get_data())
                checked.append(path)
        # The routes whose arguments are in the query
        from mast.datapower.crypto.models import CryptoSnapshot
        with db.session(write=False) as session:
            snapshots = list(session.scalars(
                select(CryptoSnapshot.id).order_by(CryptoSnapshot.id)))
        for path in ["/api/v1/crypto/snapshots?appliance=dp-fake"] + [
                "/api/v1/crypto/diff?from={}&to={}".format(a, b)
                for a in snapshots for b in snapshots]:
            answer = self.client.get(path)
            self.assertLess(answer.status_code, 500, path)
            self.assert_clean("GET " + path, answer.get_data())
            checked.append(path)
        for where, answer in self.answers:
            self.assert_clean(where, answer.get_data())
        return checked

    def test_a_stored_password_is_nowhere(self):
        job_ids = self.use_everything()
        self.check_database()
        logs = self.log_directory()
        self.assertTrue(os.path.isdir(logs), logs)
        self.assertGreater(self.check_files([logs]), 0)
        self.check_files([os.path.join(MAST_HOME, job_dir(j)) for j in job_ids])
        checked = self.check_routes(job_ids)
        # The routes with data were searched
        for path in ("/api/v1/jobs", "/api/v1/credentials",
                     "/api/v1/schedules", "/api/v1/appliances",
                     "/api/v1/jobs/{}/result".format(job_ids[0]),
                     "/api/v1/jobs/{}/events".format(job_ids[7]),
                     "/api/v1/jobs/{}/history".format(job_ids[7]),
                     "/api/v1/appliances-status",
                     "/api/v1/appliances-status/dp-fake",
                     "/api/v1/credentials/leaky/uses",
                     "/api/v1/credentials/leaky/audit",
                     "/api/v1/crypto/overview", "/api/v1/crypto/inventory",
                     "/api/v1/crypto/findings", "/api/v1/crypto/exceptions",
                     "/api/v1/crypto/snapshots?appliance=dp-fake"):
            self.assertIn(path, checked)
        # The terminal job: its events and its result were searched
        self.assertIn("/api/v1/jobs/{}/events".format(job_ids[-1]), checked)
        self.assertIn("/api/v1/jobs/{}/result".format(job_ids[-1]), checked)
        self.assertTrue([p for p in checked
                         if p.startswith("/api/v1/crypto/objects/")])
        # And the credential was really used, with the second password
        self.assertGreaterEqual(len(store.uses("leaky")), 7)


class TestNoSecretLeaksPostgres(PostgresMixin, TestNoSecretLeaks):
    pass

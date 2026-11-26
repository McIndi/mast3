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
Unit tests for the Terminal page (`mast.datapower.ssh.sessions` and
`mast.datapower.ssh.page`), with a fake shell in place of SSH.
"""
import os
import tempfile
import threading
import time
from unittest import mock

from mast import jobs, registry
from mast.auth import users
from mast.credentials import store as credentials
from mast.datapower.ssh import page as terminal_page
from mast.datapower.ssh import sessions
from mast.jobs.auth import Principal
from mast.testsuite.unit.test_mast_credentials import ADMIN
from mast.testsuite.unit.test_mast_jobs import PostgresMixin
from mast.testsuite.unit.web_helpers import WebCase

PASSWORD = "Pw-of-the-login-7"
TYPED = "Typed-at-the-prompt-9"


class FakeShell(object):
    """The DataPower CLI of one appliance, as `DataPowerShell` gives it.
    Its banner shows the password of the login (a careless appliance),
    so that the redaction is tested. `delays` makes an appliance slow;
    `ask` asks for a password, and the answer to it is echoed."""
    delays = {}
    made = []

    def __init__(self, name, username, password):
        from mast.cli.results import using_stored_credential
        self.name = name
        self.password = password
        self.stored = using_stored_credential()
        self.closed = False
        self.asking = False
        self.stop = threading.Event()
        self.warnings = []
        self.lines = []
        FakeShell.made.append(self)

    def prompt(self):
        return "{}# ".format(self.name)

    def connect(self, domain, timeout):
        if self.name.startswith("down"):
            raise ConnectionRefusedError("refused")
        return "Welcome to {}\r\nlogin: admin\r\nPassword: {}\r\n{}".format(
            self.name, self.password, self.prompt())

    def run(self, line, timeout):
        self.lines.append(line)
        delay = FakeShell.delays.get(self.name, 0)
        if delay and self.stop.wait(delay):
            self.stop.clear()
            return "{}\n^C\n{}".format(line, self.prompt())
        if self.asking:
            self.asking = False
            return "\nYou typed {}\n{}".format(line, self.prompt())
        if line == "ask":
            self.asking = True
            return "ask\nEnter password: "
        if line == "exit":
            self.closed = True
            return "exit\nGoodbye"
        return "{}\n\x1b[1;32mout of {}\x1b[0m\r\n{}".format(
            line, self.name, self.prompt())

    def interrupt(self):
        self.stop.set()

    def connected(self):
        return not self.closed

    def close(self):
        self.closed = True


class Clock(object):
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class TerminalCase(WebCase):

    def setUp(self):
        super().setUp()
        FakeShell.delays = {}
        FakeShell.made = []
        self.clock = Clock()
        self.manager = sessions.SessionManager(
            shell_factory=FakeShell, clock=self.clock,
            settings_=sessions.Settings(connect_timeout=2),
            sweep_seconds=3600)
        self.addCleanup(self.manager.close_all)
        self.operator = users.get("operator")
        self.as_operator = Principal("operator", "operator")

    def open(self, names=("dp1", "dp2"), user=None, principal=None, **kw):
        kw.setdefault("credentials", ["admin:" + PASSWORD])
        return self.manager.open(user or self.operator,
                                 principal or self.as_operator, list(names),
                                 **kw)

    def everything(self, session):
        """Every place a session leaves text: its events, its result,
        its transcript."""
        found = [e.message for e in jobs.events(session.job_id)]
        result = jobs.result(session.job_id)
        if result is not None:
            found.append(result.text)
        found.append(session.transcript())
        return "\n".join(found)


class TestSessions(TerminalCase):

    def test_open_send_and_close(self):
        session = self.open()
        self.assertEqual(len(session.id), 43)
        self.assertEqual([p.connected for p in session.panes.values()],
                         [True, True])
        job = jobs.get(session.job_id)
        self.assertEqual((job.command, job.status, job.submitted_by),
                         ("ssh.terminal", jobs.RUNNING, "operator"))
        self.assertTrue(job.passive)
        line = self.manager.send(session.id, self.operator, "show clock")
        self.assertTrue(line.finished())
        self.assertEqual(line.results["dp1"]["output"],
                         "show clock\nout of dp1\ndp1# ")
        # Only to dp2
        line = self.manager.send(session.id, self.operator, "show mem",
                                 ["dp2"])
        self.assertEqual(list(line.results), ["dp2"])
        self.assertEqual(FakeShell.made[0].lines, ["show clock"])
        events = [e.message for e in jobs.events(session.job_id)]
        self.assertIn("> show clock", events)
        self.assertIn("[dp2] show mem\nout of dp2\ndp2# ", events)
        self.manager.close(session.id, self.operator)
        job = jobs.get(session.job_id)
        self.assertEqual(job.status, jobs.SUCCEEDED)
        text = jobs.result(session.job_id).text
        self.assertIn("===== dp1 =====", text)
        self.assertIn("out of dp2", text)
        self.assertTrue(all(s.closed for s in FakeShell.made))
        with self.assertRaises(sessions.NoSuchSession):
            self.manager.get(session.id, self.operator)

    def test_the_password_of_the_login_is_nowhere(self):
        session = self.open()
        self.manager.send(session.id, self.operator, "show version")
        self.manager.close(session.id, self.operator)
        everything = self.everything(session)
        self.assertNotIn(PASSWORD, everything)
        self.assertIn("Password: ********", session.transcript())

    def test_a_line_typed_at_a_password_prompt_is_nowhere(self):
        session = self.open(["dp1"])
        line = self.manager.send(session.id, self.operator, "ask")
        self.assertTrue(line.results["dp1"]["password_prompt"])
        self.assertTrue(session.panes["dp1"].password_prompt)
        line = self.manager.send(session.id, self.operator, TYPED)
        self.assertEqual(line.text, "********")
        # The appliance echoed it; the answer has stars
        self.assertIn("You typed ********", line.results["dp1"]["output"])
        self.assertFalse(session.panes["dp1"].password_prompt)
        self.manager.close(session.id, self.operator)
        self.assertNotIn(TYPED, self.everything(session))
        self.assertIn("> ********", self.everything(session))

    def test_one_appliance_that_fails_does_not_stop_the_others(self):
        session = self.open(["down1", "dp1"])
        self.assertEqual(
            [(p.name, p.connected) for p in session.panes.values()],
            [("down1", False), ("dp1", True)])
        self.assertIn("refused", session.panes["down1"].error)
        line = self.manager.send(session.id, self.operator, "show clock")
        self.assertIn("refused", line.results["down1"]["error"])
        self.assertIn("out of dp1", line.results["dp1"]["output"])

    def test_a_slow_appliance_does_not_hold_up_a_fast_one(self):
        FakeShell.delays = {"slow": 30}
        session = self.open(["slow", "fast"])
        started = time.monotonic()
        line = self.manager.send(session.id, self.operator, "show clock",
                                 wait=0.5)
        self.assertLess(time.monotonic() - started, 5)
        self.assertFalse(line.finished())
        self.assertTrue(line.results["fast"]["done"])
        self.assertFalse(line.results["slow"]["done"])
        # A line to the busy appliance is refused there, not queued
        second = self.manager.send(session.id, self.operator, "show mem",
                                   wait=0.5)
        self.assertIn("still running", second.results["slow"]["error"])
        # Ctrl-C ends it
        self.assertEqual(self.manager.interrupt(session.id, self.operator),
                         ["slow"])
        with line.done:
            line.done.wait_for(line.finished, timeout=5)
        self.assertIn("^C", line.results["slow"]["output"])
        self.assertIs(self.manager.line(session.id, self.operator, 1), line)

    def test_only_the_owner(self):
        session = self.open()
        admin = users.get("admin")
        for call in (lambda: self.manager.get(session.id, admin),
                     lambda: self.manager.send(session.id, admin, "show clock"),
                     lambda: self.manager.get("made-up", self.operator),
                     lambda: self.manager.close(
                         session.id, users.get("viewer"))):
            with self.assertRaises(sessions.NoSuchSession):
                call()
        self.assertEqual(self.manager.list(admin), [])
        self.assertEqual(self.manager.list(self.operator), [session])
        # An admin may close it
        self.manager.close(session.id, admin)
        self.assertEqual(self.manager.list(self.operator), [])

    def test_the_ids_are_made_here_and_differ(self):
        first, second = self.open(), self.open()
        self.assertNotEqual(first.id, second.id)

    def test_at_most_three_for_a_user(self):
        for _ in range(3):
            self.open()
        with self.assertRaises(sessions.TooManySessions):
            self.open()
        # Another user has their own three
        self.open(user=users.get("admin"), principal=ADMIN)

    def test_idle_and_too_old(self):
        idle = self.open()
        self.clock.now += 14 * 60
        self.manager.sweep()
        self.manager.get(idle.id, self.operator)    # used now
        self.clock.now += 15 * 60 + 1
        self.manager.sweep()
        self.assertTrue(idle.closed)
        self.assertIn("Closed: idle for 15 minutes",
                      [e.message for e in jobs.events(idle.job_id)])
        old = self.open()
        for _ in range(9):
            self.clock.now += 3600
            self.manager.get(old.id, self.operator)
            self.manager.sweep()
        self.assertTrue(old.closed)
        self.assertEqual(jobs.get(old.job_id).status, jobs.SUCCEEDED)

    def test_a_running_line_is_not_idle(self):
        FakeShell.delays = {"slow": 30}
        session = self.open(["slow"])
        self.manager.send(session.id, self.operator, "show clock", wait=0.1)
        self.clock.now += 20 * 60
        self.manager.sweep()
        self.assertFalse(session.closed)

    def test_logout_and_a_new_password_close_it(self):
        session = self.open()
        users.new_epoch(self.operator.id)
        self.manager.sweep()
        self.assertTrue(session.closed)
        operator = users.get("operator")
        second = self.open(user=operator)
        users.disable("operator")
        self.manager.sweep()
        self.assertTrue(second.closed)

    def test_cancel_on_the_jobs_page_closes_it(self):
        session = self.open()
        jobs.cancel(session.job_id, ADMIN)
        self.manager.sweep()
        self.assertTrue(session.closed)
        self.assertEqual(jobs.get(session.job_id).status, jobs.CANCELLED)

    def test_a_lost_web_server_leaves_the_job_lost(self):
        session = self.open()
        # No heartbeat for longer than the lease: the queue declares it lost
        jobs.heartbeat(session.job_id, self.manager.worker_id,
                       lease_seconds=-1)
        jobs.reap()
        self.assertEqual(jobs.get(session.job_id).status, jobs.LOST)
        self.manager.sweep()
        self.assertTrue(session.closed)
        self.assertEqual(jobs.get(session.job_id).status, jobs.LOST)

    def test_close_all_when_the_web_server_stops(self):
        first, second = self.open(), self.open(["dp3"])
        self.manager.close_all()
        for session in (first, second):
            self.assertEqual(jobs.get(session.job_id).status, jobs.SUCCEEDED)

    def test_reconnect(self):
        session = self.open(["dp1"])
        self.manager.send(session.id, self.operator, "exit")
        self.assertFalse(session.panes["dp1"].connected)
        pane = self.manager.reconnect(session.id, self.operator, "dp1",
                                      credentials=["admin:" + PASSWORD])
        self.assertTrue(pane.connected)
        self.assertEqual(len(FakeShell.made), 2)


class TestStoredCredential(TerminalCase):

    def setUp(self):
        super().setUp()
        key_file = os.path.join(tempfile.mkdtemp(dir=self.tmp), "k")
        patcher = mock.patch("mast.jobs.config.credentials_key_file",
                             return_value=key_file)
        patcher.start()
        self.addCleanup(patcher.stop)
        registry.add("dp1", "10.0.0.1")
        credentials.create("changer", "admin", PASSWORD, "change", ["dp1"],
                           principal=ADMIN)

    def test_each_use_is_recorded_and_the_host_key_is_checked(self):
        session = self.open(["dp1"], user=users.get("admin"), principal=ADMIN,
                            credentials=None, credential="changer")
        self.assertTrue(session.panes["dp1"].connected)
        # The shell was made inside stored_credential(): an unpinned host
        # key is refused there (mast.datapower.datapower.tls)
        self.assertTrue(FakeShell.made[0].stored)
        self.assertEqual(FakeShell.made[0].password, PASSWORD)
        uses = credentials.uses("changer")
        self.assertEqual([(u.job_id, u.appliance) for u in uses],
                         [(session.job_id, "dp1")])
        self.assertEqual(jobs.get(session.job_id).params["credential"],
                         "changer")
        self.manager.close(session.id)
        self.assertNotIn(PASSWORD, self.everything(session))


class TestTerminalPage(WebCase):

    def setUp(self):
        super().setUp()
        FakeShell.delays = {}
        FakeShell.made = []
        key_file = os.path.join(tempfile.mkdtemp(dir=self.tmp), "k")
        patcher = mock.patch("mast.jobs.config.credentials_key_file",
                             return_value=key_file)
        patcher.start()
        self.addCleanup(patcher.stop)
        manager = sessions.SessionManager(
            shell_factory=FakeShell,
            settings_=sessions.Settings(connect_timeout=2),
            sweep_seconds=3600)
        patcher = mock.patch.object(terminal_page, "MANAGER", manager)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(manager.close_all)
        self.manager = manager
        registry.add("dp1", "10.0.0.1")
        registry.add("dp2", "10.0.0.2")
        credentials.create("collector", "reader", PASSWORD, "collect",
                           ["dp1", "dp2"], principal=ADMIN)
        credentials.create("changer", "admin", PASSWORD, "change",
                           ["dp1", "dp2"], principal=ADMIN)

    def client_as(self, role):
        client = self.app.test_client()
        self.login(role, client=client)
        return client

    def call(self, client, method, path, body=None):
        return client.open("/api/v1" + path, method=method, json=body,
                           headers={"X-CSRF-Token": self.token(client)})

    def open(self, client, **body):
        body.setdefault("appliances", ["dp1", "dp2"])
        if "credential" not in body:
            body.setdefault("credentials", ["admin:" + PASSWORD])
        return self.call(client, "POST", "/ssh/sessions", body)

    def test_a_viewer_has_no_terminal(self):
        viewer = self.client_as("viewer")
        self.assertEqual(viewer.get("/terminal").status_code, 403)
        self.assertEqual(self.call(viewer, "GET", "/ssh/sessions")
                         .status_code, 403)
        self.assertEqual(self.open(viewer).status_code, 403)
        page = viewer.get("/crypto").get_data(as_text=True)
        self.assertNotIn('href="/terminal"', page)

    def test_the_page(self):
        operator = self.client_as("operator")
        page = operator.get("/terminal").get_data(as_text=True)
        self.assertIn('href="/terminal"', page)
        self.assertIn("data-open-form", page)
        self.assertIn("target-bar", page)
        self.assertNotIn("stored credential of purpose change", page)
        for name in ("terminal.js", "terminal.css"):
            self.assertEqual(operator.get(
                "/static/terminal/" + name).status_code, 200)
        self.assertIn("stored credential of purpose change",
                      self.client_as("admin").get("/terminal")
                      .get_data(as_text=True))

    def test_an_operator_types_the_credentials(self):
        operator = self.client_as("operator")
        answer = self.open(operator)
        self.assertEqual(answer.status_code, 201, answer.get_json())
        found = answer.get_json()
        self.assertEqual([a["connected"] for a in found["appliances"]],
                         [True, True])
        self.assertNotIn(PASSWORD, answer.get_data(as_text=True))
        # A stored credential is for admins
        answer = self.open(operator, credential="changer")
        self.assertEqual(answer.status_code, 403)
        self.assertIn("Only an admin", answer.get_json()["error"])
        # Without credentials
        self.assertEqual(self.open(operator, credentials=[]).status_code, 422)
        self.assertEqual(self.open(operator, appliances=[]).status_code, 422)

    def test_an_admin_with_a_stored_credential(self):
        admin = self.client_as("admin")
        answer = self.open(admin, credential="collector")
        self.assertEqual(answer.status_code, 403)
        self.assertIn("collecting", answer.get_json()["error"])
        # A change credential, but the SSH host keys are not pinned
        answer = self.open(admin, credential="changer")
        self.assertEqual(answer.status_code, 403)
        self.assertIn("host key is not pinned: dp1, dp2",
                      answer.get_json()["error"])
        for name in ("dp1", "dp2"):
            registry.pin_ssh(name, "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI"
                             "Kq0nOD5u6bKLmP8nkdLqyKKUvG9SkGKDYS2Hh6S1hHi",
                             "admin")
        answer = self.open(admin, credential="changer")
        self.assertEqual(answer.status_code, 201, answer.get_json())
        self.assertEqual(len(credentials.uses("changer")), 2)
        # Not bound to dp3
        registry.add("dp3", "10.0.0.3")
        answer = self.open(admin, credential="changer",
                           appliances=["dp1", "dp3"])
        self.assertEqual(answer.status_code, 403)

    def test_lines_transcripts_and_closing(self):
        operator = self.client_as("operator")
        session = self.open(operator).get_json()
        base = "/ssh/sessions/" + session["id"]
        answer = self.call(operator, "POST", base + "/lines",
                           {"line": "show clock"})
        self.assertEqual(answer.status_code, 200)
        line = answer.get_json()
        self.assertEqual(line["number"], 1)
        self.assertTrue(line["finished"])
        self.assertEqual(line["results"]["dp2"]["output"],
                         "show clock\nout of dp2\ndp2# ")
        self.assertEqual(self.call(operator, "GET", base + "/lines/1?appliance=dp1")
                         .get_json()["results"].keys(), {"dp1"})
        self.assertEqual(self.call(operator, "GET", base + "/lines/9")
                         .status_code, 404)
        self.assertEqual(self.call(operator, "POST", base + "/lines",
                                   {"line": "a\nb"}).status_code, 422)
        self.assertEqual(self.call(operator, "POST", base + "/lines",
                                   {"line": "x", "appliances": ["dp9"]})
                         .status_code, 422)
        # A reload finds it, with what it showed
        listed = self.call(operator, "GET", "/ssh/sessions").get_json()
        self.assertEqual([s["id"] for s in listed["sessions"]],
                         [session["id"]])
        again = self.call(operator, "GET", base).get_json()
        self.assertIn("out of dp1", again["appliances"][0]["transcript"])
        # The transcript as a file, without the password
        answer = operator.get("/api/v1" + base + "/transcript?appliance=dp1")
        self.assertEqual(answer.status_code, 200)
        self.assertIn("attachment", answer.headers["Content-Disposition"])
        self.assertIn("-dp1.txt", answer.headers["Content-Disposition"])
        text = answer.get_data(as_text=True)
        self.assertIn("show clock", text)
        self.assertNotIn(PASSWORD, text)
        self.assertNotIn("===== dp2", text)
        self.assertIn("===== dp2", operator.get(
            "/api/v1" + base + "/transcript").get_data(as_text=True))
        # Another user: as if it did not exist
        admin = self.client_as("admin")
        for method, path in (("GET", base), ("POST", base + "/lines"),
                             ("GET", base + "/transcript"),
                             ("POST", base + "/interrupt")):
            self.assertEqual(self.call(admin, method, path,
                                       {"line": "x"} if method == "POST"
                                       else None).status_code, 404, path)
        answer = self.call(operator, "DELETE", base)
        self.assertEqual(answer.get_json()["job_id"], session["job_id"])
        self.assertEqual(jobs.get(session["job_id"]).status, jobs.SUCCEEDED)
        self.assertEqual(self.call(operator, "GET", base).status_code, 404)

    def test_a_slow_answer_is_a_202(self):
        FakeShell.delays = {"dp2": 30}
        operator = self.client_as("operator")
        session = self.open(operator).get_json()
        base = "/ssh/sessions/" + session["id"]
        with mock.patch.object(sessions, "WAIT_SECONDS", 0.3):
            answer = self.call(operator, "POST", base + "/lines",
                               {"line": "show clock"})
        self.assertEqual(answer.status_code, 202)
        self.assertTrue(answer.get_json()["results"]["dp1"]["done"])
        self.assertEqual(self.call(operator, "POST", base + "/interrupt", {})
                         .get_json(), {"interrupted": ["dp2"]})
        for _ in range(50):
            answer = self.call(operator, "GET", base + "/lines/1")
            if answer.status_code == 200:
                break
            time.sleep(0.1)
        self.assertEqual(answer.status_code, 200)
        self.assertIn("^C", answer.get_json()["results"]["dp2"]["output"])

    def test_the_most_appliances_in_a_session(self):
        self.manager._settings = sessions.Settings(
            connect_timeout=2, max_appliances_per_session=2)
        operator = self.client_as("operator")
        answer = self.open(operator, appliances=["dp1", "dp2", "10.0.0.9"])
        self.assertEqual(answer.status_code, 422)
        self.assertIn("at most 2 appliances", answer.get_json()["error"])
        self.assertEqual(self.open(operator).status_code, 201)

    def test_four_sessions_are_too_many(self):
        operator = self.client_as("operator")
        for _ in range(3):
            self.assertEqual(self.open(operator).status_code, 201)
        self.assertEqual(self.open(operator).status_code, 429)

    def test_the_command_cannot_be_run_or_scheduled(self):
        admin = self.client_as("admin")
        ids = [c["id"] for c in self.call(admin, "GET", "/commands")
               .get_json()["commands"]]
        self.assertIn("crypto.probe", ids)
        self.assertNotIn("ssh.terminal", ids)
        self.assertEqual(self.call(admin, "GET", "/commands/ssh.terminal")
                         .status_code, 404)
        self.assertEqual(self.call(admin, "POST", "/jobs", {
            "command": "ssh.terminal",
            "params": {"appliances": ["dp1"]}}).status_code, 404)
        from mast.jobs import schedules
        with self.assertRaises(schedules.ScheduleError):
            schedules.add("t", "ssh.terminal", {"appliances": ["dp1"],
                                                "credential": "changer"},
                          "0 3 * * *", principal=ADMIN)
        # A worker that took it anyway would refuse to run it
        from mast.datapower.ssh.terminal import terminal
        with self.assertRaises(RuntimeError):
            terminal(appliances=["dp1"])


class TestHelpers(TerminalCase):

    def test_clean_and_prompts(self):
        self.assertEqual(sessions.clean("\x1b[1;31mred\x1b[0m\r\nx"), "red\nx")
        for prompt in ("Password: ", "Enter password", "login\nPassword:",
                       "Please enter new password: ",
                       "Please re-enter new password to confirm: "):
            self.assertTrue(sessions.is_password_prompt(prompt), prompt)
        for prompt in ("dp1# ", "show password-map\ndp1# ", ""):
            self.assertFalse(sessions.is_password_prompt(prompt), prompt)

    def test_settings_from_ssh_conf(self):
        found = sessions.settings()
        self.assertEqual((found.idle_minutes, found.max_hours,
                          found.max_sessions_per_user,
                          found.max_appliances_per_session,
                          found.command_timeout),
                         (15, 8, 3, 20, 120))


class TestSessionsPostgres(PostgresMixin, TestSessions):
    pass


class TestMastSsh(TerminalCase):
    """mast-ssh on the command line: --credential, and a line typed at
    a password prompt."""

    def setUp(self):
        super().setUp()
        key_file = os.path.join(tempfile.mkdtemp(dir=self.tmp), "k")
        patcher = mock.patch("mast.jobs.config.credentials_key_file",
                             return_value=key_file)
        patcher.start()
        self.addCleanup(patcher.stop)
        registry.add("dp1", "10.0.0.1")
        credentials.create("changer", "admin", PASSWORD, "change", ["dp1"],
                           principal=ADMIN)
        credentials.create("collector", "reader", PASSWORD, "collect",
                           ["dp1"], principal=ADMIN)

    def recorder(self, name):
        from mast.datapower.ssh.__main__ import _Recorder
        return _Recorder(name, ["dp1"])

    def test_a_stored_credential_needs_change_and_a_pinned_host_key(self):
        with self.assertRaises(jobs.Forbidden) as caught:
            self.recorder("changer")
        self.assertIn("not pinned: dp1", str(caught.exception))
        registry.pin_ssh("dp1", "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKq0nO"
                         "D5u6bKLmP8nkdLqyKKUvG9SkGKDYS2Hh6S1hHi", "admin")
        with self.assertRaises(jobs.Forbidden):
            self.recorder("collector")
        recorder = self.recorder("changer")
        self.assertEqual(recorder.credentials, ["admin:" + PASSWORD])
        self.assertEqual([u.job_id for u in credentials.uses("changer")],
                         [recorder.job_id])
        recorder.line("show clock\n")
        recorder.line("Typed-secret\n", secret=True)
        recorder.line("echo {}\n".format(PASSWORD))
        recorder.finish()
        job = jobs.get(recorder.job_id)
        self.assertEqual((job.command, job.status),
                         ("ssh.terminal", jobs.SUCCEEDED))
        events = [e.message for e in jobs.events(recorder.job_id)]
        self.assertIn("> show clock", events)
        self.assertIn("> ********", events)
        self.assertIn("> echo ********", events)
        self.assertNotIn("Typed-secret", "\n".join(events))

    def test_a_secret_line_is_not_logged(self):
        import logging
        from mast.datapower.datapower import DataPower

        class Channel(object):
            def __init__(self):
                self.sent = []
                self.answer = []

            def sendall(self, text):
                self.sent.append(text)
                self.answer = [b"\r\nxi52# "]

            def recv_ready(self):
                return bool(self.answer)

            def recv(self, size):
                return self.answer.pop(0)

        appliance = DataPower("dp1", "admin:" + PASSWORD)
        appliance._ssh_conn = Channel()
        records = []
        handler = logging.Handler()
        handler.emit = records.append
        loggers = [logging.getLogger(n) for n in
                   ("audit", "DataPower.dp1", "DataPower.10.0.0.1")]
        for logger in loggers:
            logger.addHandler(handler)
            self.addCleanup(logger.removeHandler, handler)
        with mock.patch.object(DataPower, "ssh_is_connected",
                               return_value=True):
            appliance.ssh_issue_command("Typed-secret", secret=True)
        self.assertEqual(appliance._ssh_conn.sent, ["Typed-secret\n"])
        logged = "\n".join(r.getMessage() for r in records)
        self.assertIn("********", logged)
        self.assertNotIn("Typed-secret", logged)

    def test_a_login_prompt_that_comes_late(self):
        # The banner comes first and "login:" 0.3 s later. The username
        # is sent only after the prompt, so each answer of the login is
        # read with its own line, and the welcome is not in the answer
        # of the first command.
        import sys
        from mast.datapower.datapower import DataPower
        module = sys.modules["mast.datapower.datapower.DataPower"]

        class Channel(object):
            def __init__(self):
                self.sent = []
                now = time.monotonic()
                self.chunks = [(now, b"Unauthorized access prohibited.\r\n"),
                               (now + 0.3, b"login: ")]

            def _due(self):
                return bool(self.chunks) and \
                    self.chunks[0][0] <= time.monotonic()

            def recv_ready(self):
                return self._due()

            def recv(self, size):
                if not self.chunks:
                    raise AssertionError("read with nothing to come")
                while not self._due():
                    time.sleep(0.01)
                return self.chunks.pop(0)[1]

            def sendall(self, text):
                self.sent.append(text)
                answers = {
                    "admin\n": [b"admin\r\n", b"Password: "],
                    PASSWORD + "\n": [
                        b"\r\n", b"Welcome to IBM DataPower Gateway "
                        b"console configuration.\r\n\r\nidg# "],
                }.get(text, [text.encode() + b"\r\n10:00:00\r\nidg# "])
                # It answers after what it still has to send, as the
                # appliance reads a line only at its prompt, and slower
                # than ssh_issue_command looks (every 0.25 s)
                start = max([time.monotonic()] +
                            [due for due, _ in self.chunks])
                self.chunks += [(start + 0.3 * (i + 1), answer)
                                for i, answer in enumerate(answers)]

        class Client(object):
            def set_missing_host_key_policy(self, policy):
                pass

            def connect(self, *args, **kwargs):
                pass

            def get_transport(self):
                return mock.Mock()

            def invoke_shell(self):
                # The clock of the banner starts here, not before the
                # configuration is read
                return Channel()

        appliance = DataPower("dp1", "admin:" + PASSWORD)
        with mock.patch.object(module.paramiko, "SSHClient", Client), \
                mock.patch.object(DataPower, "ssh_is_connected",
                                  return_value=True):
            banner = appliance.ssh_connect(timeout=10)
            clock = appliance.ssh_issue_command("show clock")
        self.assertEqual(appliance._ssh_conn.sent,
                         ["admin\n", PASSWORD + "\n", "show clock\n"])
        self.assertIn("Welcome", banner)
        self.assertTrue(banner.strip().endswith("idg#"), banner)
        self.assertFalse(sessions.is_password_prompt(banner))
        self.assertNotIn("Welcome", clock)
        self.assertIn("10:00:00", clock)

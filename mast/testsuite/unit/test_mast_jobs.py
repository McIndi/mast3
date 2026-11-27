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
Unit tests for mast.jobs: the job queue.

The tests use a SQLite database in a temporary directory. To run them
against PostgreSQL as well, set MAST_TEST_JOBS_DB_URL to the URL of an
empty database that the tests may erase, for example
`postgresql+psycopg://mast:mast@localhost/mast_test`.
"""
import contextlib
import io
import multiprocessing
import os
import shutil
import base64
import html
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from pydantic import ValidationError
from sqlalchemy import func, select, text

from mast import jobs
from mast.cli import Cli, Option, Params, Table, Text, checkpoint, say
from mast.jobs import client, config, db, queue, registry, runner, secrets
from mast.jobs.cli import jobs_cli, schedules_cli
from mast.jobs.models import Base, Job, JobTarget
# The users table is in the same database (and in Base.metadata)
import mast.auth.models  # noqa: F401
import mast.registry.models  # noqa: F401
import mast.credentials.models  # noqa: F401
import mast.datapower.status.models  # noqa: F401
import mast.datapower.crypto.models  # noqa: F401
from mast.jobs.worker import Plugin, Worker

POSTGRES_URL = os.environ.get("MAST_TEST_JOBS_DB_URL")

# What datapower.list-users prints when it is given no appliance
NO_USERS = "\nCommon\n======"


class Work(Params):
    appliances: list[str] = Option(
        default_factory=list, short="a", widget="appliances", target=True)
    credentials: list[str] = Option(
        default_factory=list, short="c", secret=True, widget="credentials")
    seconds: float = Option(0.0, help="How long to work")
    fail: bool = Option(False, help="Raise an exception")
    exit_status: int | None = Option(None, help="Call sys.exit")
    steps: int = Option(0, help="How many steps to report")


class Judged(Text):
    """A result that says on which appliances the command failed."""
    def __init__(self, appliances):
        super().__init__(
            "\n".join(appliances), history="the requests")
        self.appliances = appliances

    def outcomes(self):
        return [not name.startswith("bad") for name in self.appliances]


# The appliances that the "requests" command sent a request to, and an
# Event for each appliance whose request does not return until it is set
sent = []
blocked = {}

# The credentials that the last "work" command got
seen = []


def make_cli():
    cli = Cli(provider="test", targets=("endpoint", "datapower"))

    @cli.command("work", effect="change")
    def work(params: Work):
        """Pretend to work on the appliances."""
        seen[:] = params.credentials
        say("working on", ", ".join(params.appliances))
        time.sleep(params.seconds)
        if params.fail:
            raise RuntimeError("the appliance said no")
        if params.exit_status is not None:
            raise SystemExit(params.exit_status)
        return Text("worked with " + ",".join(params.credentials),
                    history="the history")

    meeting = threading.Barrier(2, timeout=10)

    @cli.command("meet", effect="change")
    def meet(params: Work):
        """Wait for another job that runs at the same time."""
        meeting.wait()
        return Text("met")

    @cli.command("steps", effect="change")
    def steps(params: Work):
        """Report progress again and again, as a long command does."""
        for step in range(params.steps):
            try:
                say("step", step)
            except Exception:
                # A command that catches every Exception is still cancelled
                pass
            time.sleep(0.05)
        return Text("all steps done")

    @cli.command("look", exclusive=False, effect="read")
    def look(params: Work):
        """Only read from the appliances."""
        return Text("looked")

    @cli.command("quiet", effect="change")
    def quiet(params: Work):
        """Return nothing."""

    @cli.command("leak", effect="change")
    def leak(params: Work):
        """Repeat the credentials everywhere, as the request that sets
        a password does."""
        secret = params.credentials[0]
        forms = "{} {} {}".format(
            secret, html.escape(secret),
            base64.b64encode(secret.encode()).decode())
        say("said", forms)
        if params.fail:
            raise RuntimeError("failed with " + forms)
        return Text("text " + forms, history="<Password>{}</Password>".format(
            html.escape(secret, quote=False)))

    @cli.command("checkout", locks=["the-checkout"], effect="change")
    def checkout(params: Work):
        """Use a directory that every job of this command shares."""
        return Text("checked out")

    @cli.command("per-appliance", effect="change",
                 locks=lambda params: sorted(
                     "dir-" + name for name in params.appliances))
    def per_appliance(params: Work):
        """Use one directory for each appliance."""

    @cli.command("requests", effect="change")
    def requests(params: Work):
        """Send one request to each appliance and report nothing
        between them, as Environment.perform_action does."""
        for name in params.appliances:
            checkpoint()
            sent.append(name)
            if name in blocked:
                blocked[name].wait(10)
        return Text("sent")

    @cli.command("page", effect="change")
    def page(params: Work):
        """Return a table that has the credentials in it."""
        secret = params.credentials[0]
        return Table(("Appliance", "class"),
                     [("dp1", "a table for " + secret), ("a&b", secret)])

    @cli.command("judge", effect="change")
    def judge(params: Work):
        """Fail on each appliance whose name starts with "bad"."""
        return Judged(params.appliances)

    @cli.command("basic", effect="change")
    def basic(params: Work):
        """Decode xor encoded credentials, as Environment does, and
        repeat them in a header."""
        from mast.xor import xordecode
        decoded = xordecode(params.credentials[0]).decode()
        say("Authorization: Basic " + base64.b64encode(
            decoded.encode()).decode())
        return Text("sent as " + decoded, history="<p>{}</p>".format(decoded))

    @cli.command("colors", effect="change")
    def colors(params: Work):
        """Say and return text with the escape codes of a terminal, as
        a SOMA response highlighted for a terminal has."""
        say("\x1b[34m<dp:response>\x1b[39;49;00m")
        return Text("\x1b[1;32mgreen\x1b[0m & plain")

    @cli.command("encode", queued=False, effect="change")
    def encode(params: Work):
        """A utility that must not be stored."""
        say("encoding")
        return Text("encoded " + ",".join(params.credentials))

    return cli


def known(*names, kind="endpoint"):
    """Register each name as a target of `kind` (the endpoint of core by
    default): jobs run only on registered targets."""
    from mast import registry as targets
    for name in names:
        if targets.by_name(name) is None and not targets.is_fleet(name):
            targets.add(name, kind, "127.0.0.1",
                        attributes={"port": 1} if kind == "endpoint" else {})


def submit(*appliances, **params):
    exclusive = params.pop("exclusive", None)
    known(*appliances)
    return jobs.submit(
        "test.work", dict(appliances=list(appliances), **params),
        exclusive=exclusive)


class Clock(object):
    """Stands in for the clock of the queue."""
    def __init__(self):
        self.now = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def __call__(self):
        # No two calls return the same time, as with the real clock
        self.now += timedelta(microseconds=1)
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


def _erase(url):
    db.configure(url)
    with db.engine().begin() as connection:
        Base.metadata.drop_all(connection)
        connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
    db.configure(url)


class SqliteCase(unittest.TestCase):
    """The base of the tests. Each test gets an empty database, with the
    targets `known_targets` (of the kind endpoint)."""

    known_targets = ()
    known_kind = "endpoint"

    def make_url(self):
        return "sqlite:///" + os.path.join(
            tempfile.mkdtemp(dir=self.tmp), "jobs.db")

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mast-jobs-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.url = self.make_url()
        db.configure(self.url)
        self.addCleanup(db.configure, None)
        registry.register("test", make_cli())
        self.addCleanup(registry.unregister, "test")
        # Jobs run only on registered targets
        known(*self.known_targets, kind=self.known_kind)

    def use_clock(self):
        clock = Clock()
        patcher = mock.patch.object(queue, "_now", clock)
        patcher.start()
        self.addCleanup(patcher.stop)
        return clock

    def raw_params(self, job_id):
        with db.session() as session:
            return session.get(Job, job_id).params


class JobsCase(SqliteCase):
    """The tests of the queue: the names that they give are targets."""

    known_targets = ("dp1", "dp2", "dp3", "dp4", "bad", "bad1", "bad2")


class PostgresMixin(object):
    def make_url(self):
        if not POSTGRES_URL:
            self.skipTest("MAST_TEST_JOBS_DB_URL is not set")
        _erase(POSTGRES_URL)
        return POSTGRES_URL


class TestSchema(JobsCase):
    def test_the_migrations_make_the_tables_of_the_models(self):
        with db.engine().connect() as connection:
            context = MigrationContext.configure(connection)
            self.assertEqual(compare_metadata(context, Base.metadata), [])

    def test_head_is_the_newest_migration(self):
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        cfg = Config()
        cfg.set_main_option("script_location", "mast.jobs:migrations")
        self.assertEqual(
            db.HEAD, ScriptDirectory.from_config(cfg).get_current_head())

    def test_alembic_is_not_imported_for_a_current_database(self):
        submit("dp1")
        code = ("import sys; from mast import jobs; "
                "assert len(jobs.list_jobs()) == 1; "
                "assert 'alembic' not in sys.modules")
        subprocess.run(
            [sys.executable, "-c", code], check=True,
            env=dict(os.environ, MAST_JOBS_DB_URL=self.url))

    def test_upgrade_twice_changes_nothing(self):
        job_id = submit("dp1")
        db.upgrade()
        self.assertEqual(jobs.get(job_id).status, jobs.QUEUED)

    def test_a_reader_does_not_wait_for_a_writer(self):
        if db.engine().dialect.name != "sqlite":
            self.skipTest("only SQLite locks the database for a write")
        job_id = submit("dp1")
        holding, release = threading.Event(), threading.Event()

        def write(started=None):
            with db.session() as session:
                session.get(Job, job_id).error = "written"
                if started is not None:
                    started.set()
                release.wait(10)

        writer = threading.Thread(target=write, args=(holding,))
        writer.start()
        self.addCleanup(writer.join, 10)
        self.addCleanup(release.set)
        self.assertTrue(holding.wait(10))
        began = time.monotonic()
        self.assertEqual(jobs.get(job_id).status, jobs.QUEUED)
        self.assertEqual(len(jobs.events(job_id)), 1)
        self.assertLess(time.monotonic() - began, 1)
        # Another write waits
        second = threading.Thread(target=write)
        second.start()
        self.addCleanup(second.join, 10)
        time.sleep(0.3)
        self.assertTrue(second.is_alive())
        release.set()
        writer.join(10)
        second.join(10)
        self.assertFalse(second.is_alive())
        self.assertEqual(jobs.get(job_id).error, "written")

    def test_another_database_is_refused(self):
        db.configure("mysql://localhost/mast")
        with self.assertRaises(ValueError):
            db.engine()


class TestSubmit(JobsCase):
    def test_a_submitted_job_is_queued(self):
        job_id = submit("dp1", seconds=1.5)
        info = jobs.get(job_id)
        self.assertEqual(info.status, jobs.QUEUED)
        self.assertEqual(info.command, "test.work")
        self.assertEqual(info.submitted_by, jobs.LOCAL.name)
        self.assertTrue(info.submitted_by.startswith("os:"))
        self.assertEqual(info.params["seconds"], 1.5)
        self.assertEqual(info.targets, ["dp1"])
        self.assertTrue(info.exclusive)
        self.assertFalse(info.finished)
        self.assertEqual(
            [event.message for event in jobs.events(job_id)], ["queued"])

    def test_a_params_model_is_accepted(self):
        job_id = jobs.submit("test.work", Work(appliances=["dp1"]))
        self.assertEqual(jobs.get(job_id).targets, ["dp1"])

    def test_bad_parameters_are_refused(self):
        with self.assertRaises(ValidationError):
            jobs.submit("test.work", {"seconds": "soon"})
        with self.assertRaises(ValidationError):
            jobs.submit("test.work", {"no_such_parameter": 1})
        self.assertEqual(jobs.list_jobs(), [])

    def test_an_unknown_command_is_refused(self):
        for command in ("test.nothing", "nothing.work", "nothing",
                        "os.path.join", "../x.work"):
            with self.assertRaises(jobs.UnknownCommand):
                jobs.submit(command, {})

    def test_a_command_of_a_mast_plugin_is_found(self):
        command = jobs.resolve("datapower.xor")
        self.assertEqual(command.name, "xor")

    def test_secret_parameters_are_stored_encrypted(self):
        job_id = submit("dp1", credentials=["admin:hunter2"])
        stored = self.raw_params(job_id)
        self.assertNotIn("hunter2", repr(stored))
        self.assertEqual(
            secrets.unseal(stored)["credentials"], ["admin:hunter2"])
        # The token does not leave the queue
        self.assertEqual(
            jobs.get(job_id).params["credentials"], {"$secret": None})

    def test_a_secret_from_another_key_is_an_error(self):
        job_id = submit("dp1", credentials=["admin:hunter2"])
        stored = self.raw_params(job_id)
        other = os.path.join(self.tmp, "other.key")
        with mock.patch("mast.jobs.config.key_file", return_value=other):
            with self.assertRaises(secrets.SecretsError):
                secrets.unseal(stored)

    def test_targets_are_resolved(self):
        from mast import registry
        registry.fleet_add("lab", "endpoint", members=["dp1", "dp2"])
        # A fleet becomes its targets, in order and without repeats
        job_id = jobs.submit("test.work",
                             {"appliances": ["lab", "dp3", "dp2"]})
        self.assertEqual(jobs.get(job_id).targets, ["dp1", "dp2", "dp3"])

    def test_a_name_that_is_not_registered_is_refused(self):
        from mast import registry
        registry.add("box", "endpoint", "10.0.0.5", attributes={"port": 1})
        for name, message in (
                ("10.0.0.9", "Register it first: mast targets add"),
                ("10.0.0.5", "The target box has this address"),
                ("not a host!", "is not a registered target or fleet")):
            with self.subTest(name=name):
                with self.assertRaises(jobs.TargetRefused) as caught:
                    jobs.submit("test.work", {"appliances": [name]})
                self.assertIn(message, str(caught.exception))
        self.assertEqual(jobs.list_jobs(), [])

    def test_a_target_of_another_kind_is_refused(self):
        with self.assertRaises(jobs.TargetRefused) as caught:
            jobs.submit("datapower.list-domains", {
                "appliances": ["dp1"], "credentials": ["u:p"]})
        self.assertIn("dp1 is of the kind endpoint, and "
                      "datapower.list-domains accepts datapower",
                      str(caught.exception))
        self.assertEqual(jobs.list_jobs(), [])

    def test_the_caller_can_choose_the_id(self):
        job_id = jobs.new_job_id()
        self.assertEqual(
            jobs.submit("test.work", {"appliances": ["dp1"]}, job_id=job_id),
            job_id)
        self.assertEqual(jobs.get(job_id).status, jobs.QUEUED)
        self.assertNotEqual(jobs.new_job_id(), job_id)

    def test_a_command_names_what_else_it_locks(self):
        job_id = jobs.submit("test.checkout", {"appliances": ["dp1"]})
        self.assertEqual(
            jobs.get(job_id).targets, ["dp1", "resource:the-checkout"])
        job_id = jobs.submit(
            "test.per-appliance", {"appliances": ["dp2", "dp1"]})
        self.assertEqual(
            jobs.get(job_id).targets,
            ["dp2", "dp1", "resource:dir-dp1", "resource:dir-dp2"])

    def test_list_jobs(self):
        first, second = submit("dp1"), submit("dp2")
        jobs.cancel(first)
        self.assertEqual(
            [info.id for info in jobs.list_jobs()], [second, first])
        self.assertEqual(
            [info.id for info in jobs.list_jobs(status=jobs.QUEUED)],
            [second])
        self.assertEqual(len(jobs.list_jobs(limit=1)), 1)

    def test_a_missing_job(self):
        for call in (jobs.get, jobs.events, jobs.result, jobs.cancel):
            with self.assertRaises(jobs.JobNotFound):
                call("0" * 32)


class TestClaim(JobsCase):
    def test_jobs_are_claimed_oldest_first_and_only_once(self):
        first, second = submit("dp1"), submit("dp2")
        self.assertEqual(jobs.claim("w1"), first)
        self.assertEqual(jobs.claim("w2"), second)
        self.assertIsNone(jobs.claim("w3"))
        info = jobs.get(first)
        self.assertEqual(info.status, jobs.RUNNING)
        self.assertEqual(info.worker_id, "w1")
        self.assertIsNotNone(info.started_at)

    def test_a_job_waits_for_its_appliance(self):
        first, second = submit("dp1", "dp2"), submit("dp2", "dp3")
        other = submit("dp4")
        self.assertEqual(jobs.claim("w1"), first)
        # dp2 is in use, so the second job is passed over
        self.assertEqual(jobs.claim("w2"), other)
        self.assertIsNone(jobs.claim("w3"))
        self.assertTrue(jobs.finish(first, "w1", jobs.SUCCEEDED))
        self.assertEqual(jobs.claim("w3"), second)

    def test_jobs_that_are_not_exclusive_share_an_appliance(self):
        # Detached jobs keep their order only while a worker is alive
        jobs.worker_seen("w", "host", 1, 1)
        first = submit("dp1", exclusive=False)
        second = submit("dp1", exclusive=False)
        third = submit("dp1")
        fourth = submit("dp1", exclusive=False)
        self.assertEqual(jobs.claim("w1"), first)
        self.assertEqual(jobs.claim("w2"), second)
        # The exclusive job waits for both, and the job behind it does
        # not overtake it
        self.assertIsNone(jobs.claim("w3"))
        jobs.finish(first, "w1", jobs.SUCCEEDED)
        self.assertIsNone(jobs.claim("w3"))
        jobs.finish(second, "w2", jobs.SUCCEEDED)
        self.assertEqual(jobs.claim("w3"), third)
        self.assertIsNone(jobs.claim("w4"))
        jobs.finish(third, "w3", jobs.FAILED, error="no")
        self.assertEqual(jobs.claim("w4"), fourth)

    def test_jobs_for_one_appliance_start_in_order(self):
        jobs.worker_seen("w", "host", 1, 1)
        submit("dp1")
        blocked = submit("dp1", "dp2")
        behind = submit("dp2")
        self.assertIsNotNone(jobs.claim("w1"))
        # dp2 is free, but an older job waits for it
        self.assertIsNone(jobs.claim("w2"))
        self.assertEqual(jobs.get(blocked).status, jobs.QUEUED)
        self.assertEqual(jobs.get(behind).status, jobs.QUEUED)

    def test_a_detached_job_does_not_hold_up_while_no_worker_is_alive(self):
        detached = submit("dp1")
        attached = jobs.submit(
            "test.work", {"appliances": ["dp1"]}, attached=True,
            reserved_for="w1")
        self.assertEqual(jobs.claim("w1", job_id=attached), attached)
        self.assertEqual(jobs.get(detached).status, jobs.QUEUED)
        jobs.finish(attached, "w1", jobs.SUCCEEDED)
        # While a worker is alive, the jobs start in order
        jobs.worker_seen("w2", "host", 1, 1)
        behind = jobs.submit(
            "test.work", {"appliances": ["dp1"]}, attached=True,
            reserved_for="w1")
        self.assertIsNone(jobs.claim("w1", job_id=behind))
        self.assertEqual(jobs.claim("w2"), detached)

    def test_an_attached_job_still_holds_up_the_jobs_behind_it(self):
        first = jobs.submit(
            "test.work", {"appliances": ["dp1"]}, attached=True)
        second = jobs.submit(
            "test.work", {"appliances": ["dp1"]}, attached=True)
        self.assertIsNone(jobs.claim("w1", job_id=second))
        self.assertEqual(jobs.claim("w1"), first)

    def test_a_job_waits_for_what_else_it_locks(self):
        first = jobs.submit("test.checkout", {"appliances": ["dp1"]})
        second = jobs.submit("test.checkout", {"appliances": ["dp2"]})
        other = submit("dp3")
        self.assertEqual(jobs.claim("w1"), first)
        # Another appliance, but the same checkout
        self.assertEqual(jobs.claim("w2"), other)
        self.assertIsNone(jobs.claim("w3"))
        jobs.finish(first, "w1", jobs.SUCCEEDED)
        self.assertEqual(jobs.claim("w3"), second)

    def test_progress_is_only_stored_for_the_owner(self):
        clock = self.use_clock()
        job_id = submit("dp1")
        jobs.claim("w1", lease_seconds=60)
        self.assertFalse(jobs.add_event(job_id, "one", worker_id="w1"))
        with self.assertRaises(jobs.NotOwner):
            jobs.add_event(job_id, "not mine", worker_id="w2")
        clock.advance(61)
        jobs.reap()
        with self.assertRaises(jobs.NotOwner):
            jobs.add_event(job_id, "too late", worker_id="w1")
        self.assertEqual(
            [e.message for e in jobs.events(job_id) if e.kind == "say"],
            ["one"])

    def test_jobs_without_appliances_do_not_wait(self):
        first, second = submit(), submit()
        self.assertEqual(jobs.claim("w1"), first)
        self.assertEqual(jobs.claim("w2"), second)

    def test_load_gives_the_owner_the_secrets(self):
        job_id = submit("dp1", credentials=["admin:hunter2"])
        with self.assertRaises(jobs.NotOwner):
            jobs.load(job_id, "w1")
        jobs.claim("w1")
        with self.assertRaises(jobs.NotOwner):
            jobs.load(job_id, "w2")
        command, values = jobs.load(job_id, "w1")
        self.assertEqual(command.name, "work")
        self.assertEqual(values["credentials"], ["admin:hunter2"])

    def test_finish_stores_the_result_and_removes_the_secrets(self):
        job_id = submit("dp1", credentials=["admin:hunter2"])
        jobs.claim("w1")
        self.assertIsNone(jobs.result(job_id))
        self.assertFalse(jobs.finish(job_id, "w2", jobs.SUCCEEDED))
        self.assertTrue(jobs.finish(
            job_id, "w1", jobs.SUCCEEDED, text="t", html="<p>t</p>",
            history="h"))
        info = jobs.get(job_id)
        self.assertEqual(info.status, jobs.SUCCEEDED)
        self.assertTrue(info.finished)
        self.assertIsNotNone(info.finished_at)
        result = jobs.result(job_id)
        self.assertEqual(
            (result.text, result.html, result.history), ("t", "<p>t</p>", "h"))
        self.assertEqual(
            self.raw_params(job_id)["credentials"], {"$secret": None})
        # A job is finished once
        self.assertFalse(jobs.finish(job_id, "w1", jobs.FAILED))
        self.assertEqual(jobs.get(job_id).status, jobs.SUCCEEDED)

    def test_finish_needs_a_final_status(self):
        job_id = submit("dp1")
        jobs.claim("w1")
        for status in (jobs.QUEUED, jobs.RUNNING, jobs.LOST):
            with self.assertRaises(ValueError):
                jobs.finish(job_id, "w1", status)


class TestCancel(JobsCase):
    def test_a_queued_job_is_cancelled_now(self):
        job_id = submit("dp1", credentials=["admin:hunter2"])
        behind = submit("dp1")
        info = jobs.cancel(job_id)
        self.assertEqual(info.status, jobs.CANCELLED)
        self.assertTrue(info.finished)
        self.assertEqual(
            self.raw_params(job_id)["credentials"], {"$secret": None})
        # It is not claimed, and it does not block the job behind it
        self.assertEqual(jobs.claim("w1"), behind)

    def test_a_running_job_learns_of_it_with_a_heartbeat(self):
        job_id = submit("dp1")
        jobs.claim("w1")
        self.assertFalse(jobs.heartbeat(job_id, "w1").cancel_requested)
        info = jobs.cancel(job_id)
        self.assertEqual(info.status, jobs.RUNNING)
        self.assertTrue(info.cancel_requested)
        self.assertTrue(jobs.heartbeat(job_id, "w1").cancel_requested)
        jobs.cancel(job_id)
        self.assertEqual(
            [e.message for e in jobs.events(job_id)].count(
                "cancel requested"), 1)
        self.assertTrue(jobs.finish(job_id, "w1", jobs.CANCELLED))
        self.assertEqual(jobs.get(job_id).status, jobs.CANCELLED)

    def test_a_finished_job_does_not_change(self):
        job_id = submit("dp1")
        jobs.claim("w1")
        jobs.finish(job_id, "w1", jobs.SUCCEEDED)
        info = jobs.cancel(job_id)
        self.assertEqual(info.status, jobs.SUCCEEDED)
        self.assertFalse(info.cancel_requested)


class TestLease(JobsCase):
    def test_a_silent_worker_loses_its_job(self):
        clock = self.use_clock()
        job_id = submit("dp1", credentials=["admin:hunter2"])
        behind = submit("dp1")
        jobs.claim("w1", lease_seconds=60)
        clock.advance(59)
        self.assertEqual(jobs.reap(), [])
        self.assertIsNone(jobs.claim("w2"))
        clock.advance(2)
        # claim finds the dead job, and its appliance is free again
        self.assertEqual(jobs.claim("w2"), behind)
        info = jobs.get(job_id)
        self.assertEqual(info.status, jobs.LOST)
        self.assertIn("w1", info.error)
        self.assertEqual(
            self.raw_params(job_id)["credentials"], {"$secret": None})
        # The worker comes back too late
        self.assertFalse(jobs.heartbeat(job_id, "w1").owned)
        self.assertFalse(jobs.finish(job_id, "w1", jobs.SUCCEEDED))
        self.assertEqual(jobs.get(job_id).status, jobs.LOST)
        self.assertIsNone(jobs.result(job_id))

    def test_a_follower_finds_the_job_of_a_dead_worker(self):
        job_id = submit("dp1")
        jobs.claim("dead", lease_seconds=1)
        info = jobs.get(job_id)
        self.assertEqual(info.lease_expires_at.tzinfo, timezone.utc)
        self.assertIs(jobs.reap_if_expired(info), info)
        time.sleep(1.2)
        out, err = io.StringIO(), io.StringIO()
        began = time.monotonic()
        # Nobody claims, so only the follower can see that it is dead
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = client.follow(job_id)
        self.assertLess(time.monotonic() - began, 3)
        self.assertEqual(status, 1)
        self.assertIn("lost", err.getvalue())
        info = jobs.get(job_id)
        self.assertEqual(info.status, jobs.LOST)
        self.assertIn("dead", info.error)

    def test_reap_if_expired(self):
        clock = self.use_clock()
        job_id = submit("dp1")
        queued = jobs.get(job_id)
        self.assertIs(jobs.reap_if_expired(queued), queued)
        jobs.claim("w1", lease_seconds=60)
        clock.advance(59)
        running = jobs.get(job_id)
        self.assertIs(jobs.reap_if_expired(running), running)
        clock.advance(2)
        self.assertEqual(jobs.reap_if_expired(running).status, jobs.LOST)

    def test_a_heartbeat_keeps_the_job(self):
        clock = self.use_clock()
        job_id = submit("dp1")
        jobs.claim("w1", lease_seconds=60)
        for _ in range(5):
            clock.advance(50)
            self.assertTrue(jobs.heartbeat(job_id, "w1", 60).owned)
        self.assertEqual(jobs.reap(), [])
        self.assertFalse(jobs.heartbeat(job_id, "w2", 60).owned)
        clock.advance(61)
        self.assertEqual(jobs.reap(), [job_id])


class TestJobTargets(JobsCase):
    """A job records the ids and the revisions of its targets, and runs
    on those targets only if they did not change (step 4c of
    docs/design/mast-4.0.md)."""

    def records(self, job_id):
        from mast.jobs.models import JobTarget
        with db.session(write=False) as session:
            return [(row.appliance, row.target_id, row.revision)
                    for row in session.scalars(
                        select(JobTarget).where(JobTarget.job_id == job_id)
                        .order_by(JobTarget.id))]

    def test_a_job_records_its_targets(self):
        from mast import registry as targets
        registry_ids = {name: targets.get(name).id for name in ("dp1", "dp2")}
        job_id = jobs.submit("test.checkout", {"appliances": ["dp2", "dp1"]})
        self.assertEqual(self.records(job_id), [
            ("dp2", registry_ids["dp2"], 1), ("dp1", registry_ids["dp1"], 1),
            ("resource:the-checkout", None, None)])

    def test_a_fleet_is_its_targets_when_the_job_is_queued(self):
        from mast import registry as targets
        targets.fleet_add("lab", "endpoint", members=["dp1", "dp2"])
        job_id = jobs.submit("test.work", {"appliances": ["lab"]})
        self.assertEqual(jobs.get(job_id).params["appliances"],
                         ["dp1", "dp2"])
        # A target added to the fleet later is not in the job
        targets.fleet_set_members("lab", ["dp1", "dp2", "dp3"])
        jobs.run_next("w1")
        found = jobs.get(job_id)
        self.assertEqual(found.status, jobs.SUCCEEDED, found.error)
        self.assertEqual(found.targets, ["dp1", "dp2"])
        self.assertIn("working on dp1, dp2",
                      [e.message for e in jobs.events(job_id)])

    def test_a_changed_target_stops_the_job_before_it_starts(self):
        from mast import registry as targets
        job_id = jobs.submit("test.work", {"appliances": ["dp1", "dp2"]})
        targets.update("dp2", attributes={"port": 2})
        jobs.run_next("w1")
        found = jobs.get(job_id)
        self.assertEqual(found.status, jobs.FAILED)
        self.assertIn("dp2 changed (revision 1, now 2)", found.error)
        self.assertNotIn("working on", " ".join(
            e.message for e in jobs.events(job_id)))

    def test_a_removed_target_stops_the_job(self):
        from mast import registry as targets
        job_id = jobs.submit("test.work", {"appliances": ["dp1"]})
        targets.remove("dp1")
        jobs.run_next("w1")
        found = jobs.get(job_id)
        self.assertEqual(found.status, jobs.FAILED)
        self.assertIn("dp1 was removed", found.error)

    def test_notes_are_not_a_change(self):
        from mast import registry as targets
        job_id = jobs.submit("test.work", {"appliances": ["dp1"]})
        found = targets.update("dp1", notes="the lab one")
        self.assertEqual(found.revision, 1)
        jobs.run_next("w1")
        self.assertEqual(jobs.get(job_id).status, jobs.SUCCEEDED)


class TestJobTargetsPostgres(PostgresMixin, TestJobTargets):
    pass


class TestRunner(JobsCase):
    def test_a_job_runs(self):
        job_id = submit("dp1", credentials=["admin:hunter2"])
        worker = jobs.new_worker_id()
        self.assertEqual(jobs.run_next(worker), job_id)
        self.assertIsNone(jobs.run_next(worker))
        info = jobs.get(job_id)
        self.assertEqual(info.status, jobs.SUCCEEDED)
        self.assertIsNone(info.error)
        self.assertEqual(seen, ["admin:hunter2"])
        result = jobs.result(job_id)
        self.assertEqual(result.text, "worked with ********")
        self.assertEqual(result.html, "<pre>worked with ********</pre>")
        self.assertEqual(result.history, "the history")
        events = jobs.events(job_id)
        self.assertEqual(
            [(e.kind, e.message) for e in events],
            [("state", "queued"), ("state", "running on " + worker),
             ("say", "working on dp1"), ("state", "succeeded")])
        self.assertEqual(jobs.events(job_id, after=events[1].id), events[2:])

    def test_a_job_keeps_no_terminal_colors(self):
        job_id = jobs.submit("test.colors", {})
        jobs.run_next("w1")
        result = jobs.result(job_id)
        self.assertEqual(result.text, "green & plain")
        self.assertEqual(result.html, "<pre>green &amp; plain</pre>")
        self.assertIn(("say", "<dp:response>"),
                      [(e.kind, e.message) for e in jobs.events(job_id)])

    def test_the_status_comes_from_the_result(self):
        cases = (
            (["dp1", "dp2"], jobs.SUCCEEDED, None),
            (["dp1", "bad1", "bad2"], jobs.PARTIAL,
             "The command reported that 2 of its 3 results failed."),
            (["bad1", "bad2"], jobs.FAILED,
             "The command reported that 2 of its 2 results failed."),
            ([], jobs.SUCCEEDED, None),
        )
        for appliances, status, error in cases:
            with self.subTest(appliances=appliances):
                job_id = jobs.submit("test.judge", {"appliances": appliances})
                jobs.run_next("w1")
                info = jobs.get(job_id)
                self.assertEqual((info.status, info.error), (status, error))
                self.assertTrue(info.finished)
                # The result is kept, whatever the status
                result = jobs.result(job_id)
                self.assertEqual(result.text, "\n".join(appliances))
                self.assertEqual(result.history, "the requests")
        self.assertEqual(len(jobs.list_jobs(status=jobs.PARTIAL)), 1)
        # The appliances of a partial job are free again
        self.assertIsNotNone(jobs.claim("w1", job_id=submit("bad1")))

    def test_a_command_with_no_result(self):
        job_id = jobs.submit("test.quiet", {})
        jobs.run_next("w1")
        self.assertEqual(jobs.get(job_id).status, jobs.SUCCEEDED)
        self.assertEqual(jobs.result(job_id).text, "")

    def test_an_exception_fails_the_job(self):
        job_id = submit("dp1", fail=True)
        self.assertEqual(jobs.run_next("w1"), job_id)
        info = jobs.get(job_id)
        self.assertEqual(info.status, jobs.FAILED)
        self.assertEqual(info.error, "RuntimeError: the appliance said no")
        self.assertIn("Traceback", jobs.result(job_id).history)

    def test_sys_exit_does_not_stop_the_worker(self):
        failed = submit("dp1", exit_status=2)
        fine = submit("dp2", exit_status=0)
        jobs.run_next("w1")
        jobs.run_next("w1")
        self.assertEqual(jobs.get(failed).status, jobs.FAILED)
        self.assertIn("status 2", jobs.get(failed).error)
        self.assertEqual(jobs.get(fine).status, jobs.SUCCEEDED)

    def test_an_interrupt_is_recorded_and_continues(self):
        job_id = submit("dp1")
        with mock.patch("mast.jobs.runner._execute",
                        side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                jobs.run_next("w1")
        info = jobs.get(job_id)
        self.assertEqual(info.status, jobs.FAILED)
        self.assertIn("KeyboardInterrupt", info.error)

    def test_heartbeats_keep_a_long_job(self):
        # The job runs for twice its lease. A heartbeat comes every half
        # second, so a stall of the machine of up to a second is fine.
        job_id = submit("dp1", seconds=3.0)
        jobs.claim("w1", lease_seconds=1.5)
        reaped = []

        def reap_soon():
            time.sleep(2.2)
            reaped.extend(jobs.reap())

        thread = threading.Thread(target=reap_soon)
        thread.start()
        self.assertEqual(
            jobs.run_job(job_id, "w1", lease_seconds=1.5), jobs.SUCCEEDED)
        thread.join()
        self.assertEqual(reaped, [])

    def test_a_job_that_was_lost_is_not_overwritten(self):
        clock = self.use_clock()
        job_id = submit("dp1")
        jobs.claim("w1", lease_seconds=60)
        clock.advance(61)
        jobs.reap()
        self.assertIsNone(jobs.run_job(job_id, "w1", lease_seconds=60))
        self.assertEqual(jobs.get(job_id).status, jobs.LOST)


    def run_steps(self, **kwargs):
        """Start a job with many steps in a thread. Return the id of
        the job, the thread and the list that gets what `run_job`
        returned."""
        job_id = jobs.submit(
            "test.steps", {"appliances": ["dp1"], "steps": 200})
        jobs.claim("w1", **kwargs)
        outcome = []
        thread = threading.Thread(
            target=lambda: outcome.append(
                jobs.run_job(job_id, "w1", **kwargs)))
        thread.start()
        self.addCleanup(thread.join, 15)
        return job_id, thread, outcome

    def said(self, job_id):
        return [e for e in jobs.events(job_id) if e.kind == "say"]

    def test_a_job_that_is_lost_while_it_runs_stops_at_its_next_step(self):
        job_id, thread, outcome = self.run_steps()
        self.assertTrue(wait_for(lambda: len(self.said(job_id)) >= 3))
        # What the queue does when the worker is silent for too long
        self.assertTrue(queue.abandon(job_id, "w1", "the lease ended"))
        # The appliance now belongs to the next job
        after = submit("dp1")
        self.assertEqual(jobs.claim("w2"), after)
        steps = len(self.said(job_id))
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(outcome, [None])
        self.assertEqual(jobs.get(job_id).status, jobs.LOST)
        self.assertEqual(jobs.get(job_id).error, "the lease ended")
        # Nothing of the old job was stored after it was lost
        self.assertEqual(len(self.said(job_id)), steps)
        self.assertIsNone(jobs.result(job_id))

    def run_requests(self, run_kwargs=None, **kwargs):
        """Start a job that sends a request to dp1, which does not
        return, and then to dp2. Return the id of the job, the thread
        and the list that gets what `run_job` returned. `kwargs` go to
        `claim` and `run_job`, `run_kwargs` only to `run_job`."""
        del sent[:]
        blocked["dp1"] = threading.Event()
        self.addCleanup(blocked.clear)
        job_id = jobs.submit(
            "test.requests", {"appliances": ["dp1", "dp2"]})
        jobs.claim("w1", **kwargs)
        outcome = []
        thread = threading.Thread(
            target=lambda: outcome.append(
                jobs.run_job(job_id, "w1", **kwargs, **(run_kwargs or {}))))
        thread.start()
        self.addCleanup(thread.join, 15)
        self.addCleanup(blocked["dp1"].set)
        self.assertTrue(wait_for(lambda: sent == ["dp1"]))
        return job_id, thread, outcome

    def test_a_lost_job_sends_no_request_to_the_next_appliance(self):
        # The command reports nothing between the appliances
        job_id, thread, outcome = self.run_requests(lease_seconds=0.6)
        self.assertTrue(queue.abandon(job_id, "w1", "the lease ended"))
        # Another job now has the second appliance
        other = submit("dp2")
        self.assertEqual(jobs.claim("w2", job_id=other), other)
        # The heartbeat learns that the job is lost
        time.sleep(0.5)
        blocked["dp1"].set()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(sent, ["dp1"])
        self.assertEqual(outcome, [None])
        self.assertEqual(jobs.get(job_id).status, jobs.LOST)

    def test_a_job_without_heartbeats_sends_no_more_requests(self):
        with mock.patch.object(
                queue, "heartbeat", side_effect=OSError("database away")):
            job_id, thread, outcome = self.run_requests(lease_seconds=0.4)
            time.sleep(0.6)
            blocked["dp1"].set()
            thread.join(5)
        self.assertEqual(sent, ["dp1"])
        self.assertEqual(outcome, [None])
        self.assertEqual(jobs.get(job_id).status, jobs.LOST)

    def test_a_cancelled_job_sends_no_request_to_the_next_appliance(self):
        job_id, thread, outcome = self.run_requests(lease_seconds=0.6)
        jobs.cancel(job_id)
        # The heartbeat brings the request to cancel
        time.sleep(0.5)
        blocked["dp1"].set()
        thread.join(5)
        self.assertEqual(sent, ["dp1"])
        self.assertEqual(outcome, [jobs.CANCELLED])
        self.assertEqual(jobs.get(job_id).status, jobs.CANCELLED)

    def test_a_cancel_is_seen_long_before_the_next_heartbeat(self):
        # A lease of a minute: the first heartbeat comes after 20 seconds
        with mock.patch.object(queue, "heartbeat", wraps=queue.heartbeat) as beat:
            job_id, thread, outcome = self.run_requests(
                run_kwargs={"cancel_poll_seconds": 0.05}, lease_seconds=60)
            jobs.cancel(job_id)
            time.sleep(0.3)
            blocked["dp1"].set()
            thread.join(5)
        self.assertEqual(beat.call_count, 0)
        self.assertEqual(sent, ["dp1"])
        self.assertEqual(outcome, [jobs.CANCELLED])

    def test_the_program_that_runs_the_job_can_cancel_it_at_once(self):
        # Ctrl-C: no database, no heartbeat, no polling
        cancel = threading.Event()
        with mock.patch.object(queue, "cancel_requested",
                               side_effect=AssertionError("asked the queue")):
            job_id, thread, outcome = self.run_requests(
                run_kwargs={"cancel": cancel, "cancel_poll_seconds": 60},
                lease_seconds=60)
            cancel.set()
            blocked["dp1"].set()
            thread.join(5)
        self.assertEqual(sent, ["dp1"])
        self.assertEqual(outcome, [jobs.CANCELLED])

    def test_a_failed_look_for_a_cancel_does_not_stop_the_job(self):
        with mock.patch.object(queue, "cancel_requested",
                               side_effect=OSError("database away")) as look:
            job_id, thread, outcome = self.run_requests(
                run_kwargs={"cancel_poll_seconds": 0.05}, lease_seconds=60)
            self.assertTrue(wait_for(lambda: look.call_count >= 2))
            blocked["dp1"].set()
            thread.join(5)
        self.assertEqual(sent, ["dp1", "dp2"])
        self.assertEqual(outcome, [jobs.SUCCEEDED])

    def test_the_lease_of_the_worker_starts_when_it_claims(self):
        del sent[:]
        job_id = jobs.submit("test.requests", {"appliances": ["dp1"]})
        # The worker asked for the job 3 seconds ago, and the lease is
        # 2 seconds: the queue can give the appliance to another job
        claimed_at = time.monotonic() - 3
        self.assertEqual(jobs.claim("w1", lease_seconds=2), job_id)
        self.assertIsNone(jobs.run_job(
            job_id, "w1", lease_seconds=2, claimed_at=claimed_at))
        self.assertEqual(sent, [])
        self.assertEqual(jobs.get(job_id).status, jobs.LOST)

    def test_a_job_that_keeps_its_lease_sends_every_request(self):
        # The request takes longer than the lease; the heartbeats (every
        # half second) keep it
        job_id, thread, outcome = self.run_requests(lease_seconds=1.5)
        time.sleep(2.0)
        blocked["dp1"].set()
        thread.join(5)
        self.assertEqual(sent, ["dp1", "dp2"])
        self.assertEqual(outcome, [jobs.SUCCEEDED])

    def test_a_request_to_an_appliance_is_a_checkpoint(self):
        # A real command: the request is stopped before it is sent
        from mast.cli import guarded
        from mast.datapower.datapower import WSClientLib

        class Stop(BaseException):
            pass

        def stop():
            raise Stop()

        command = jobs.resolve("datapower.add-user")
        params = command.model(
            appliances=["dp.example.test"], credentials=["user:pass"],
            username="new", password="hunter2", privileged=True)
        with mock.patch.object(
                WSClientLib.Request, "send",
                side_effect=AssertionError("a request was sent")) as send:
            with guarded(stop), self.assertRaises(Stop):
                command.fn(params)
        send.assert_not_called()

    def test_a_secret_does_not_break_the_html_of_the_result(self):
        tags = lambda text: re.findall(r"<[^>]*>", text)
        markup = tags(Table(
            ("Appliance", "class"), [("", ""), ("", "")]).html())
        cell = '<td class="result_table_cell">{}</td>'
        cases = (
            # The secret is in the names of the tags and of the entities,
            # and in a word of the text, which does not change
            ("a", "******** table for ********"),
            ("amp", "a table for ********"),
            ("class", "a table for ********"),
            ("x<y>&\"z\"", "a table for ********"),
        )
        for secret, shown in cases:
            with self.subTest(secret=secret):
                job_id = jobs.submit("test.page", {"credentials": [secret]})
                jobs.run_next("w1")
                html = jobs.result(job_id).html
                self.assertEqual(tags(html), markup)
                self.assertIn(cell.format(shown), html)
                self.assertIn(cell.format("********"), html)
                if secret not in ("a", "amp"):
                    self.assertIn(cell.format("a&amp;b"), html)

    def test_the_data_of_a_result_is_stored_and_redacted(self):
        secret = "hunter2-secret"
        job_id = jobs.submit("test.page", {"credentials": [secret]})
        jobs.run_next("w1")
        data = jobs.result(job_id).data
        self.assertEqual(data["kind"], "table")
        self.assertEqual(data["columns"], ["Appliance", "class"])
        self.assertEqual(data["rows"], [
            ["dp1", "a table for ********"], ["a&b", "********"]])
        self.assertNotIn(secret, repr(data))
        # A result without data has None
        job_id = submit("dp1")
        jobs.run_next("w1")
        self.assertIsNone(jobs.result(job_id).data)

    def test_redact_data_reaches_every_string(self):
        redact = secrets.Redactor({"pw"})
        self.assertEqual(
            secrets.redact_data(redact, {"pw": ["pw", {"a": "x pw"}, 1, None]}),
            {"********": ["********", {"a": "x ********"}, 1, None]})

    def test_only_the_text_between_tags_is_redacted(self):
        redact = secrets.Redactor({"hunter2", "class"})
        self.assertEqual(
            redact.html('<a class="x" id=one>hunter2 &amp; class</a>'),
            '<a class="x" id=one>******** &amp; ********</a>')
        # What has no secret in it is not changed at all
        same = "<b class='x'>nothing</b> &amp; &lt; &quot;"
        self.assertEqual(redact.html(same), same)
        self.assertEqual(secrets.Redactor(()).html(same), same)

    def test_a_secret_is_only_redacted_where_it_stands_alone(self):
        redact = secrets.Redactor({"admin"})
        self.assertEqual(
            redact("user admin in domain administrators"),
            "user ******** in domain administrators")
        self.assertEqual(redact("sysadmin admin_x xadmin admin1"),
                         "sysadmin admin_x xadmin admin1")
        self.assertEqual(redact("admin"), "********")
        self.assertEqual(redact("(admin), admin:admin"),
                         "(********), ********:********")

    def test_a_credential_in_a_header_is_redacted(self):
        found = secrets.redactor(Work, {"credentials": ["user:pass"]})
        encoded = base64.b64encode(b"user:pass").decode()
        self.assertEqual(encoded, "dXNlcjpwYXNz")
        self.assertEqual(
            found("Authorization: Basic {}\r\n".format(encoded)),
            "Authorization: Basic ********\r\n")
        # A form that ends with = is found too
        padded = base64.b64encode(b"user:passw").decode()
        self.assertTrue(padded.endswith("="))
        found = secrets.redactor(Work, {"credentials": ["user:passw"]})
        self.assertEqual(found("Basic {}\n".format(padded)),
                         "Basic ********\n")

    def test_xor_encoded_credentials_are_decoded(self):
        from mast.xor import xorencode
        encoded = xorencode("user:s3cret")
        self.assertNotIn(":", encoded)
        found = secrets.redactor(Work, {"credentials": [encoded]})
        basic = base64.b64encode(b"user:s3cret").decode()
        self.assertEqual(
            found("{} | {} | {}".format(encoded, "user:s3cret", basic)),
            "******** | ******** | ********")
        # Only the credentials are decoded
        class Other(Params):
            token: str = Option("", secret=True)
        found = secrets.redactor(Other, {"token": encoded})
        self.assertEqual(found("user:s3cret " + encoded),
                         "user:s3cret ********")
        # A value that is not xor encoded is no problem
        found = secrets.redactor(Work, {"credentials": ["not base64!"]})
        self.assertEqual(found("not base64!"), "********")

    def test_xor_encoded_credentials_are_not_stored(self):
        from mast.xor import xorencode
        encoded = xorencode("user:s3cret")
        basic = base64.b64encode(b"user:s3cret").decode()
        job_id = jobs.submit("test.basic", {"credentials": [encoded]})
        jobs.run_next("w1")
        info, result = jobs.get(job_id), jobs.result(job_id)
        self.assertEqual(info.status, jobs.SUCCEEDED)
        self.assertIn("Authorization: Basic ********",
                      [e.message for e in jobs.events(job_id)])
        self.assertEqual(result.text, "sent as ********")
        self.assertEqual(result.history, "<p>********</p>")
        for found in [e.message for e in jobs.events(job_id)] + [
                result.text, result.html, result.history]:
            for secret in ("s3cret", basic, encoded):
                self.assertNotIn(secret, found)

    def test_a_secret_with_the_characters_of_a_pattern(self):
        redact = secrets.Redactor({"p@ss.w*rd(1)"})
        self.assertEqual(redact("pw p@ss.w*rd(1) p@ssXwwrd1"),
                         "pw ******** p@ssXwwrd1")
        self.assertEqual(secrets.Redactor({"", "x"})("a x b"), "a ******** b")
        self.assertEqual(secrets.Redactor(())("text"), "text")

    def test_a_job_stops_when_its_heartbeats_do_not_arrive(self):
        with mock.patch.object(
                queue, "heartbeat", side_effect=OSError("database away")):
            job_id, thread, outcome = self.run_steps(lease_seconds=0.5)
            thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(outcome, [None])
        info = jobs.get(job_id)
        self.assertEqual(info.status, jobs.LOST)
        self.assertIn("heartbeats", info.error)
        self.assertLess(len(self.said(job_id)), 200)

    def test_secret_parameters_are_not_stored_with_the_result(self):
        secret = "admin:hunter<2>&\"x\""
        encoded = base64.b64encode(secret.encode()).decode()
        for fail in (False, True):
            with self.subTest(fail=fail):
                job_id = jobs.submit("test.leak", {
                    "appliances": ["dp1"], "credentials": [secret],
                    "fail": fail})
                jobs.run_next("w1")
                info, result = jobs.get(job_id), jobs.result(job_id)
                self.assertEqual(
                    info.status, jobs.FAILED if fail else jobs.SUCCEEDED)
                stored = [e.message for e in jobs.events(job_id)] + [
                    info.error or "", result.text, result.html,
                    result.history, str(self.raw_params(job_id))]
                for found in stored:
                    self.assertNotIn("hunter", found)
                    self.assertNotIn(encoded, found)
                self.assertIn(
                    "said ******** ******** ********", stored)
                if fail:
                    self.assertEqual(
                        info.error,
                        "RuntimeError: failed with ******** ******** "
                        "********")
                else:
                    self.assertEqual(
                        result.history, "<Password>********</Password>")

    def test_a_running_job_is_cancelled_at_its_next_step(self):
        job_id = jobs.submit("test.steps", {"steps": 200})
        jobs.claim("w1")
        outcome = []
        thread = threading.Thread(
            target=lambda: outcome.append(jobs.run_job(job_id, "w1")))
        thread.start()
        while len(jobs.events(job_id)) < 5:
            time.sleep(0.01)
        jobs.cancel(job_id)
        thread.join(10)
        self.assertEqual(outcome, [jobs.CANCELLED])
        info = jobs.get(job_id)
        self.assertEqual(info.status, jobs.CANCELLED)
        self.assertIn("cancelled", info.error)
        steps = [e for e in jobs.events(job_id) if e.kind == "say"]
        self.assertLess(len(steps), 200)


class TestCancelPollSeconds(unittest.TestCase):

    def value(self, text):
        with mock.patch.object(config, "_get", return_value=text):
            return config.cancel_poll_seconds()

    def test_the_default_is_one_second(self):
        self.assertEqual(config.cancel_poll_seconds(), 1.0)

    def test_a_value_that_is_set(self):
        self.assertEqual(self.value("0.25"), 0.25)

    def test_a_value_that_cannot_be_used_is_the_default(self):
        for text in ("0", "-1", "soon"):
            with self.subTest(text=text):
                self.assertEqual(self.value(text), 1.0)


def wait_for(condition, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


class TestWorker(JobsCase):
    def start(self, **kwargs):
        kwargs.setdefault("poll_seconds", 0.02)
        worker = Worker(**kwargs)
        thread = threading.Thread(target=worker.run)
        thread.start()
        self.addCleanup(thread.join, 10)
        self.addCleanup(worker.abort)
        return worker, thread

    def test_drain_runs_every_job_and_returns(self):
        submitted = [submit("dp1"), submit("dp1"), submit("dp2", fail=True)]
        lines = []
        Worker(drain=True, poll_seconds=0.02, report=lines.append).run()
        self.assertEqual(
            [jobs.get(job_id).status for job_id in submitted],
            [jobs.SUCCEEDED, jobs.SUCCEEDED, jobs.FAILED])
        self.assertIn("job {} started".format(submitted[0]), lines)
        self.assertIn("job {} failed".format(submitted[2]), lines)
        self.assertEqual(lines[-1], "stopped")

    def test_concurrency_runs_jobs_at_the_same_time(self):
        # Each of these jobs waits until the other one runs too
        first = jobs.submit("test.meet", {"appliances": ["dp1"]})
        second = jobs.submit("test.meet", {"appliances": ["dp2"]})
        Worker(concurrency=2, drain=True, poll_seconds=0.02).run()
        self.assertEqual(jobs.get(first).status, jobs.SUCCEEDED)
        self.assertEqual(jobs.get(second).status, jobs.SUCCEEDED)
        self.assertNotEqual(
            jobs.get(first).worker_id, jobs.get(second).worker_id)

    def test_concurrency_must_be_positive(self):
        with self.assertRaises(ValueError):
            Worker(concurrency=0)

    def test_a_worker_is_listed_while_it_runs(self):
        self.assertEqual(jobs.workers(), [])
        worker, thread = self.start(concurrency=3)
        self.assertTrue(wait_for(lambda: jobs.workers()))
        listed = jobs.workers()[0]
        self.assertEqual(listed.id, worker.id)
        self.assertEqual(listed.concurrency, 3)
        self.assertEqual(listed.pid, os.getpid())
        worker.stop()
        thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(jobs.workers(alive=False), [])

    def test_a_worker_that_died_is_not_alive(self):
        clock = self.use_clock()
        jobs.worker_seen("w1", "host", 1, 1)
        clock.advance(59)
        self.assertEqual([w.id for w in jobs.workers()], ["w1"])
        jobs.worker_seen("w1", "host", 1, 1)
        clock.advance(59)
        self.assertEqual([w.id for w in jobs.workers()], ["w1"])
        clock.advance(2)
        self.assertEqual(jobs.workers(), [])
        self.assertEqual([w.id for w in jobs.workers(alive=False)], ["w1"])
        # A day later, another worker's sign of life removes the row
        clock.advance(24 * 60 * 60)
        jobs.worker_seen("w2", "host", 2, 1)
        self.assertEqual([w.id for w in jobs.workers(alive=False)], ["w2"])

    def test_stop_waits_for_the_running_job(self):
        running = jobs.submit("test.steps", {"steps": 10})
        waiting = jobs.submit("test.steps", {"steps": 10})
        worker, thread = self.start()
        self.assertTrue(wait_for(lambda: worker.running() == [running]))
        worker.stop()
        thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(jobs.get(running).status, jobs.SUCCEEDED)
        self.assertEqual(jobs.get(waiting).status, jobs.QUEUED)

    def test_abort_loses_the_running_job(self):
        job_id = jobs.submit(
            "test.steps", {"steps": 40, "appliances": ["dp1"]})
        behind = submit("dp1")
        worker, thread = self.start()
        self.assertTrue(wait_for(lambda: worker.running() == [job_id]))
        worker.abort()
        thread.join(10)
        self.assertFalse(thread.is_alive())
        info = jobs.get(job_id)
        self.assertEqual(info.status, jobs.LOST)
        self.assertIn("was stopped", info.error)
        # The command is still in its thread. Its result is not stored.
        self.assertTrue(wait_for(
            lambda: not any(t.name == "mast-worker-1" and t.is_alive()
                            for t in threading.enumerate())))
        self.assertEqual(jobs.get(job_id).status, jobs.LOST)
        self.assertIsNone(jobs.result(job_id))
        self.assertEqual(jobs.claim("w2"), behind)

    def test_the_worker_survives_a_database_error(self):
        job_id = submit("dp1")
        real, calls = queue.claim, []

        def flaky(*args, **kwargs):
            calls.append(1)
            if len(calls) < 3:
                raise RuntimeError("the database is away")
            return real(*args, **kwargs)

        with mock.patch.object(queue, "claim", flaky):
            Worker(drain=True, poll_seconds=0.02).run()
        self.assertEqual(jobs.get(job_id).status, jobs.SUCCEEDED)

    def test_the_mastd_plugin(self):
        plugin = Plugin()
        plugin.start()
        self.assertTrue(wait_for(lambda: jobs.workers()))
        plugin.stop()
        plugin.join(10)
        self.assertFalse(plugin.is_alive())

    def test_the_mastd_plugin_can_be_turned_off(self):
        with mock.patch("mast.jobs.config.mastd_worker", return_value=False):
            plugin = Plugin()
        plugin.start()
        time.sleep(0.2)
        self.assertTrue(plugin.is_alive())
        self.assertEqual(jobs.workers(), [])
        plugin.stop()
        plugin.join(10)
        self.assertFalse(plugin.is_alive())


@unittest.skipIf(sys.platform == "win32", "sends POSIX signals")
class TestWorkerProcess(JobsCase):
    def spawn(self, *args):
        env = dict(os.environ, MAST_JOBS_DB_URL=self.url)
        process = subprocess.Popen(
            [sys.executable, "-m", "mast", "worker", "-p", "0.05"]
            + list(args),
            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True)
        self.addCleanup(process.stdout.close)
        self.addCleanup(process.kill)
        return process

    def test_sigterm_stops_an_idle_worker(self):
        process = self.spawn("-c", "2")
        self.assertTrue(wait_for(lambda: jobs.workers(), 30))
        self.assertEqual(jobs.workers()[0].pid, process.pid)
        process.send_signal(signal.SIGTERM)
        self.assertEqual(process.wait(30), 0)
        output = process.stdout.read()
        self.assertIn("runs up to 2 job(s)", output)
        self.assertIn("stopped", output)
        self.assertEqual(jobs.workers(alive=False), [])

    def test_a_worker_runs_a_command_of_a_plugin(self):
        # With no appliance, the command talks to nothing
        job_id = jobs.submit("datapower.list-users", {})
        process = self.spawn("--drain")
        self.assertEqual(process.wait(60), 0, process.stdout.read())
        self.assertEqual(jobs.get(job_id).status, jobs.SUCCEEDED)
        self.assertEqual(jobs.result(job_id).text, NO_USERS)

    def test_bad_options_are_refused(self):
        process = self.spawn("-c", "0")
        self.assertEqual(process.wait(30), 2)
        self.assertIn("--concurrency", process.stdout.read())


class CliCase(JobsCase):
    """Runs command lines in this process and collects what they print."""

    def setUp(self):
        super().setUp()
        self.cli = make_cli()
        registry.register("test", self.cli)

    def run_cli(self, *argv, cli=None):
        """Return (exit status, standard output, standard error)."""
        out, err = io.StringIO(), io.StringIO()
        status = 0
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                (cli or self.cli).run(list(argv))
            except SystemExit as exc:
                status = exc.code
        return status, out.getvalue(), err.getvalue()

    def start_worker(self, **kwargs):
        kwargs.setdefault("poll_seconds", 0.02)
        worker = Worker(**kwargs)
        thread = threading.Thread(target=worker.run)
        thread.start()
        self.addCleanup(thread.join, 10)
        self.addCleanup(worker.stop)
        self.assertTrue(wait_for(lambda: jobs.workers()))
        return worker

    def only_job(self):
        found = jobs.list_jobs()
        self.assertEqual(len(found), 1)
        return found[0]


class TestCommandLine(CliCase):
    def test_a_command_does_its_own_job_when_there_is_no_worker(self):
        status, out, err = self.run_cli(
            "work", "-a", "dp1", "-c", "admin:hunter2")
        self.assertEqual(
            (status, out, err),
            (0, "working on dp1\nworked with ********\n", ""))
        job = self.only_job()
        self.assertEqual(job.command, "test.work")
        self.assertEqual(job.status, jobs.SUCCEEDED)
        self.assertEqual(job.targets, ["dp1"])
        self.assertEqual(job.params["credentials"], {"$secret": None})
        self.assertEqual(
            [e.message for e in jobs.events(job.id) if e.kind == "say"],
            ["working on dp1"])

    def test_a_worker_does_the_job_when_one_is_alive(self):
        worker = self.start_worker()
        status, out, err = self.run_cli("work", "-a", "dp1", "-c", "u:p")
        self.assertEqual(
            (status, out, err), (0, "working on dp1\nworked with ********\n", ""))
        self.assertTrue(self.only_job().worker_id.startswith(worker.id))

    def test_local_does_the_job_here(self):
        worker = self.start_worker()
        for _ in range(10):
            status, out, _ = self.run_cli("work", "-a", "dp1", "--local")
            self.assertEqual(
                (status, out), (0, "working on dp1\nworked with \n"))
        for job in jobs.list_jobs():
            self.assertEqual(job.status, jobs.SUCCEEDED)
            self.assertFalse(job.worker_id.startswith(worker.id))

    def test_the_progress_of_a_job_done_here_is_shown_once(self):
        expected = "".join("step {}\n".format(n) for n in range(8)) \
            + "all steps done\n"
        for argv in ((), ("--local",)):
            with self.subTest(argv=argv):
                status, out, err = self.run_cli("steps", "--steps", "8", *argv)
                self.assertEqual((status, out, err), (0, expected, ""))

    def test_a_reserved_job_is_only_for_its_worker(self):
        reserved = jobs.submit(
            "test.work", {"appliances": ["dp1"]}, reserved_for="me")
        other = submit("dp2")
        self.assertEqual(jobs.claim("w1"), other)
        self.assertIsNone(jobs.claim("w1"))
        self.assertEqual(jobs.claim("me"), reserved)

    def test_a_job_whose_submitter_went_away_is_cancelled(self):
        clock = self.use_clock()
        jobs.submit("test.work", {"appliances": ["dp1"]}, reserved_for="x")
        waiting = jobs.submit(
            "test.work", {"appliances": ["dp1"]}, attached=True)
        detached = submit("dp1")
        clock.advance(50)
        jobs.attach(waiting)
        jobs.attach(detached)
        clock.advance(50)
        jobs.reap()
        self.assertEqual(jobs.get(waiting).status, jobs.QUEUED)
        clock.advance(61)
        jobs.worker_seen("w1", "host", 1, 1)
        # Its submitter is silent. Any look at the queue removes it.
        self.assertIsNone(jobs.claim("w1"))
        info = jobs.get(waiting)
        self.assertEqual(info.status, jobs.CANCELLED)
        self.assertIn("stopped before the job started", info.error)
        # A job that nobody waits for stays
        self.assertEqual(jobs.get(detached).status, jobs.QUEUED)

    def test_a_command_keeps_its_job_while_it_waits(self):
        clock = self.use_clock()
        ahead = submit("dp1")
        jobs.claim("somebody", lease_seconds=100000)
        result = []
        thread = threading.Thread(
            target=lambda: result.append(self.run_cli("work", "-a", "dp1")))
        with mock.patch.object(client, "_note"):
            # A short lease while the job waits: the command must say
            # again and again that it still waits, or the job is
            # cancelled. The queue measures this with the test's clock.
            with mock.patch("mast.jobs.config.lease_seconds",
                            return_value=0.3):
                thread.start()
                self.assertTrue(wait_for(lambda: len(jobs.list_jobs()) == 2))
                mine = jobs.list_jobs()[0].id
                for _ in range(10):
                    time.sleep(0.1)
                    clock.advance(0.1)
                self.assertEqual(jobs.get(mine).status, jobs.QUEUED)
            # The run that follows has the normal lease. The worker
            # measures its lease with the real clock, and a run with a
            # lease of 0.3 seconds is lost if the machine stalls for that
            # long, which is not what this test is about.
            jobs.finish(ahead, "somebody", jobs.SUCCEEDED)
            thread.join(10)
        self.assertEqual(jobs.get(mine).status, jobs.SUCCEEDED)

    def test_a_command_that_failed_on_an_appliance(self):
        for local in (True, False):
            with self.subTest(local=local):
                if not local:
                    self.start_worker()
                status, out, err = self.run_cli(
                    "judge", "-a", "dp1", "-a", "bad1")
                self.assertEqual((status, out), (1, "dp1\nbad1\n"))
                self.assertRegex(
                    err, r"^The job \w{8} partial: The command reported "
                         r"that 1 of its 2 results failed\.\n$")
        status, out, err = self.run_cli("judge", "-a", "bad1")
        self.assertEqual((status, out), (1, "bad1\n"))
        # The history of such a job is not a traceback
        self.assertNotIn("the requests", err)
        self.assertIn("failed: The command reported", err)
        status, out, err = self.run_cli("judge", "-a", "dp1")
        self.assertEqual((status, out, err), (0, "dp1\n", ""))

    def test_a_cli_with_no_namespace_exits_with_the_outcome(self):
        cli = Cli()

        @cli.command("judge", effect="change")
        def judge(params: Work):
            return Judged(params.appliances)

        self.assertEqual(
            self.run_cli("judge", "-a", "dp1", cli=cli), (0, "dp1\n", ""))
        self.assertEqual(
            self.run_cli("judge", "-a", "dp1", "-a", "bad1", cli=cli),
            (1, "dp1\nbad1\n", ""))

    def test_a_failed_command(self):
        status, out, err = self.run_cli("work", "-a", "dp1", "--fail")
        self.assertEqual(status, 1)
        self.assertEqual(out, "working on dp1\n")
        self.assertIn("Traceback", err)
        self.assertIn("failed: RuntimeError: the appliance said no", err)
        self.assertEqual(self.only_job().status, jobs.FAILED)

    def test_bad_options_do_not_make_a_job(self):
        status, out, err = self.run_cli("work", "--seconds", "soon")
        self.assertEqual(status, 2)
        self.assertIn("--seconds", err)
        self.assertEqual(jobs.list_jobs(), [])

    def test_detach_prints_the_id_and_returns(self):
        status, out, err = self.run_cli("work", "-a", "dp1", "--detach")
        job = self.only_job()
        self.assertEqual((status, out), (0, job.id + "\n"))
        self.assertEqual(job.status, jobs.QUEUED)
        self.assertIn("No worker is running", err)
        self.assertIn("mast jobs show -j " + job.id[:8], err)

    def test_a_detached_job_does_not_hold_up_a_command_with_no_worker(self):
        self.run_cli("work", "-a", "dp1", "--detach")
        detached = self.only_job()
        result = []
        thread = threading.Thread(
            target=lambda: result.append(self.run_cli("work", "-a", "dp1")))
        thread.start()
        thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result, [(0, "working on dp1\nworked with \n", "")])
        self.assertEqual(jobs.get(detached.id).status, jobs.QUEUED)

    def test_the_queue_options_are_in_the_help(self):
        status, out, _ = self.run_cli("work", "-h")
        self.assertIn("--detach", out)
        self.assertIn("--local", out)

    def test_a_cli_with_no_namespace_calls_the_command(self):
        cli = Cli()

        @cli.command("work", effect="change")
        def work(params: Work):
            return Text("called")

        status, out, err = self.run_cli("work", cli=cli)
        self.assertEqual((status, out), (0, "called\n"))
        self.assertEqual(jobs.list_jobs(), [])
        status, _, err = self.run_cli("work", "--detach", cli=cli)
        self.assertEqual(status, 2)
        with self.assertRaises(ValueError):
            Cli(main=work, provider="test")

    def test_a_job_waits_for_its_appliance(self):
        ahead = submit("dp1")
        jobs.claim("somebody")
        result = []
        thread = threading.Thread(
            target=lambda: result.append(self.run_cli("work", "-a", "dp1")))
        with mock.patch.object(client, "_note") as note:
            thread.start()
            self.assertTrue(wait_for(lambda: len(jobs.list_jobs()) == 2))
            time.sleep(0.6)
            mine = jobs.list_jobs()[0]
            self.assertEqual(jobs.get(mine.id).status, jobs.QUEUED)
            jobs.finish(ahead, "somebody", jobs.SUCCEEDED)
            thread.join(10)
        self.assertEqual(jobs.get(mine.id).status, jobs.SUCCEEDED)
        self.assertEqual(note.call_count, 0)

    def test_ctrl_c_cancels_a_job_that_waits(self):
        submit("dp1")
        jobs.claim("somebody")
        with mock.patch.object(
                client.time, "sleep", side_effect=KeyboardInterrupt):
            status, out, err = self.run_cli("work", "-a", "dp1")
        self.assertEqual((status, out), (client.INTERRUPTED, ""))
        self.assertIn("cancelled", err)
        self.assertEqual(jobs.list_jobs()[0].status, jobs.CANCELLED)

    def test_ctrl_c_cancels_a_job_on_a_worker(self):
        self.start_worker()
        real, calls = time.sleep, []

        def sleep(seconds):
            # time.sleep is the same function for the command, which
            # runs in the thread of the worker
            if threading.current_thread() is threading.main_thread():
                calls.append(seconds)
                if len(calls) == 3:
                    raise KeyboardInterrupt
            real(seconds)

        with mock.patch.object(client.time, "sleep", sleep):
            status, out, err = self.run_cli("steps", "--steps", "400")
        self.assertEqual(status, client.INTERRUPTED)
        self.assertIn("Asked to cancel", err)
        self.assertIn("cancelled", err)
        self.assertNotIn("all steps done", out)
        self.assertEqual(self.only_job().status, jobs.CANCELLED)

    def interrupt_at(self, *numbers):
        """Patch time.sleep so that the main thread gets a Ctrl-C at
        its calls with these numbers."""
        real, calls = time.sleep, []

        def sleep(seconds):
            # time.sleep is the same function for the command, which
            # runs in another thread
            if threading.current_thread() is threading.main_thread():
                calls.append(seconds)
                if len(calls) in numbers:
                    raise KeyboardInterrupt
            real(seconds)

        return mock.patch.object(client.time, "sleep", sleep)

    def test_ctrl_c_cancels_a_job_done_here_at_its_next_step(self):
        with self.interrupt_at(3):
            status, out, err = self.run_cli(
                "steps", "--steps", "400", "--local")
        self.assertEqual(status, client.INTERRUPTED)
        self.assertIn("Asked to cancel", err)
        self.assertNotIn("all steps done", out)
        info = self.only_job()
        self.assertEqual(info.status, jobs.CANCELLED)
        self.assertIn("cancelled while it ran", info.error)

    def test_ctrl_c_twice_stops_a_job_done_here(self):
        with self.interrupt_at(3, 4), mock.patch.object(
                jobs.runner, "_call", side_effect=self.endless_call):
            status, out, err = self.run_cli("steps", "--local")
        self.assertEqual(status, client.INTERRUPTED)
        self.assertIn("was stopped before it ended", err)
        self.assertNotIn("continues", err)
        info = self.only_job()
        self.assertEqual(info.status, jobs.LOST)
        self.assertEqual(info.error, client._STOPPED)

    def endless_call(self, job_id, worker_id, store, guard, redact):
        """A command that reports progress and cannot be cancelled, so
        that the second Ctrl-C comes while it runs."""
        release = threading.Event()
        self.addCleanup(release.set)
        store("working")
        release.wait(10)
        raise jobs.JobLost()

    def test_ctrl_c_twice_leaves_the_job(self):
        ahead = submit("dp1")
        jobs.claim("somebody")
        # The job that runs is followed, and does not end
        with mock.patch.object(
                client.time, "sleep", side_effect=KeyboardInterrupt):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(err):
                status = client.follow(ahead)
        self.assertEqual(status, client.INTERRUPTED)
        self.assertIn("continues", err.getvalue())
        info = jobs.get(ahead)
        self.assertEqual(info.status, jobs.RUNNING)
        self.assertTrue(info.cancel_requested)

    def test_a_lost_job(self):
        clock = self.use_clock()
        job_id = submit("dp1")
        jobs.claim("somebody", lease_seconds=60)
        clock.advance(61)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            # follow has no worker, and its own look at the queue
            # finds the dead job
            jobs.reap()
            status = client.follow(job_id)
        self.assertEqual(status, 1)
        self.assertIn("lost", err.getvalue())

    def test_a_command_that_is_not_queued_is_called_directly(self):
        self.start_worker()
        status, out, err = self.run_cli("encode", "-c", "admin:hunter2")
        self.assertEqual(
            (status, out, err), (0, "encoding\nencoded admin:hunter2\n", ""))
        self.assertEqual(jobs.list_jobs(), [])
        with self.assertRaises(jobs.NotQueued):
            jobs.submit("test.encode", {})
        # It has no options for the queue
        status, _, err = self.run_cli("encode", "--detach")
        self.assertEqual(status, 2)
        self.assertIn("--detach", err)

    def test_xor_is_not_queued(self):
        from mast.datapower.system import cli as system
        self.assertFalse(system.commands["xor"].queued)
        status, out, _ = self.run_cli("xor", "-s", "admin:hunter2", cli=system)
        self.assertEqual(status, 0)
        self.assertTrue(out.strip())
        self.assertEqual(jobs.list_jobs(), [])

    def test_only_one_git_deploy_runs_at_a_time(self):
        self.assertEqual(
            jobs.resolve("datapower.git-deploy").locks, ["git-deploy"])

    def test_commands_that_only_read_are_not_exclusive(self):
        self.run_cli("look", "-a", "dp1", "--detach")
        self.assertFalse(self.only_job().exclusive)
        self.assertFalse(jobs.resolve("datapower.get-status").exclusive)
        self.assertFalse(jobs.resolve("datapower.list-users").exclusive)
        self.assertTrue(jobs.resolve("datapower.save").exclusive)
        self.assertTrue(jobs.resolve("datapower.normal-backup").exclusive)

    def test_every_plugin_command_can_be_a_job(self):
        from mast.datapower.commands import cli
        self.assertEqual(cli.provider, "datapower")
        for name in cli.commands:
            self.assertIs(jobs.resolve("datapower." + name),
                          cli.commands[name])


class TestMastJobs(CliCase):
    def mast_jobs(self, *argv):
        return self.run_cli(*argv, cli=jobs_cli)

    def test_list(self):
        first = submit("dp1")
        second = submit("dp2", "dp3")
        jobs.cancel(first)
        status, out, _ = self.mast_jobs("list")
        self.assertEqual(status, 0)
        lines = out.splitlines()
        self.assertTrue(lines[0].startswith("Job"))
        self.assertIn(second, lines[2])
        self.assertIn("queued", lines[2])
        self.assertIn("dp2 dp3", lines[2])
        self.assertIn(first, lines[3])
        status, out, _ = self.mast_jobs("list", "-s", "cancelled", "-n", "5")
        self.assertIn(first, out)
        self.assertNotIn(second, out)
        self.assertEqual(self.mast_jobs("list", "-s", "nothing")[0], 2)

    def test_show(self):
        job_id = submit("dp1", credentials=["admin:hunter2"])
        jobs.run_next("w1")
        status, out, _ = self.mast_jobs("show", "-j", job_id[:6])
        self.assertEqual(status, 0)
        for expected in (job_id, "test.work", "succeeded", "by " + jobs.LOCAL.name,
                         "Worker:     w1", "credentials: (secret)",
                         "working on dp1", "[running on w1]",
                         "worked with ********"):
            self.assertIn(expected, out)
        self.assertNotIn("the history", out)
        status, out, _ = self.mast_jobs("show", "-j", job_id, "-H")
        self.assertIn("the history", out)

    def test_a_job_that_does_not_exist(self):
        for command in ("show", "cancel", "follow"):
            for prefix in ("0000", "not-an-id", "%"):
                status, out, err = self.mast_jobs(command, "-j", prefix)
                self.assertEqual(status, 1)
                self.assertIn("There is no job", err)

    def test_a_prefix_must_name_one_job(self):
        with db.session() as session:
            for job_id in ("ab" + "0" * 30, "ab" + "1" * 30):
                session.add(Job(
                    id=job_id, command="test.work", params={},
                    status=jobs.QUEUED, exclusive=True, submitted_by="local",
                    created_at=queue._now(), cancel_requested=False))
        self.assertEqual(jobs.find("AB0"), "ab" + "0" * 30)
        status, _, err = self.mast_jobs("show", "-j", "ab")
        self.assertEqual(status, 1)
        self.assertIn("more than one job", err)

    def test_cancel(self):
        job_id = submit("dp1")
        status, out, _ = self.mast_jobs("cancel", "-j", job_id)
        self.assertEqual(status, 0)
        self.assertIn("is cancelled", out)
        running = submit("dp2")
        jobs.claim("w1")
        status, out, _ = self.mast_jobs("cancel", "-j", running)
        self.assertIn("Asked to cancel", out)
        self.assertTrue(jobs.get(running).cancel_requested)
        status, out, _ = self.mast_jobs("show", "-j", running)
        self.assertIn("running (cancel requested)", out)

    def test_follow(self):
        status, out, _ = self.run_cli("work", "-a", "dp1", "--detach")
        job_id = out.strip()
        self.start_worker()
        status, out, err = self.mast_jobs("follow", "-j", job_id)
        self.assertEqual(
            (status, out, err), (0, "working on dp1\nworked with \n", ""))
        # A job that ended can be followed again
        self.assertEqual(self.mast_jobs("follow", "-j", job_id)[1], out)

    def test_workers(self):
        status, out, _ = self.mast_jobs("workers")
        self.assertIn("No worker is running", out)
        worker = self.start_worker(concurrency=2)
        status, out, _ = self.mast_jobs("workers")
        self.assertIn(worker.id, out)
        self.assertIn(str(os.getpid()), out)


class TestCommandLineProcess(JobsCase):
    def mast(self, *argv):
        """Run `mast ARGV` (python -m mast)."""
        return subprocess.run(
            [sys.executable, "-m", "mast"] + list(argv), text=True,
            capture_output=True, timeout=120,
            env=dict(os.environ, MAST_JOBS_DB_URL=self.url))

    def test_a_plugin_command_is_a_job(self):
        done = self.mast("datapower", "list-users")
        self.assertEqual(
            (done.returncode, done.stdout, done.stderr),
            (0, NO_USERS + "\n", ""))
        job = jobs.list_jobs()[0]
        self.assertEqual((job.command, job.status),
                         ("datapower.list-users", jobs.SUCCEEDED))
        listed = self.mast("jobs", "list")
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertIn(job.id, listed.stdout)
        self.assertIn("datapower.list-users", listed.stdout)

    def test_a_command_that_is_not_queued_makes_no_job(self):
        from mast.xor import xorencode
        done = self.mast("datapower", "xor", "-s", "user:pass")
        self.assertEqual(
            (done.returncode, done.stdout, done.stderr),
            (0, xorencode("user:pass") + "\n", ""))
        self.assertEqual(jobs.list_jobs(), [])

    def test_a_detached_job_is_done_by_a_worker(self):
        done = self.mast("datapower", "list-users", "--detach")
        job_id = done.stdout.strip()
        self.assertEqual(jobs.get(job_id).status, jobs.QUEUED)
        worker = self.mast("worker", "--drain", "-p", "0.05")
        self.assertEqual(worker.returncode, 0, worker.stdout + worker.stderr)
        shown = self.mast("jobs", "follow", "-j", job_id[:8])
        self.assertEqual(
            (shown.returncode, shown.stdout),
            (0, NO_USERS + "\n"))


def _compete(url, worker_id, start, claimed, overlaps, ready=None,
             hold_seconds=0.0):
    """Run in another process: claim jobs until there are none. Put
    `worker_id` in `ready` once the process can claim, then wait for
    `start`. Keep each job `hold_seconds` before finishing it, so that
    the other workers claim while it runs."""
    db.configure(url)
    if ready is not None:
        ready.put(worker_id)
    start.wait()
    while True:
        job_id = jobs.claim(worker_id)
        if job_id is None:
            if not jobs.list_jobs(status=jobs.QUEUED, limit=1):
                return
            time.sleep(0.005)
            continue
        claimed.put((worker_id, job_id))
        with db.session() as session:
            mine = select(JobTarget.appliance).where(
                JobTarget.job_id == job_id)
            running = session.scalar(
                select(func.count()).select_from(Job).join(
                    JobTarget, JobTarget.job_id == Job.id).where(
                    Job.status == jobs.RUNNING,
                    JobTarget.appliance.in_(mine)))
        if running != 1:
            overlaps.put((worker_id, job_id, running))
        time.sleep(hold_seconds)
        jobs.finish(job_id, worker_id, jobs.SUCCEEDED)


class TestWorkersTogether(JobsCase):
    def test_processes_do_not_get_in_each_others_way(self):
        appliances = ["dp1", "dp2", "dp3"]
        submitted = [submit(appliances[n % 3]) for n in range(45)]
        context = multiprocessing.get_context("spawn")
        start, claimed, overlaps, ready = (
            context.Event(), context.Queue(), context.Queue(), context.Queue())
        workers = [
            context.Process(
                target=_compete,
                args=(self.url, "w{}".format(n), start, claimed, overlaps,
                      ready, 0.01))
            for n in range(6)]
        for worker in workers:
            worker.start()
        # A spawned process takes a while to import MAST. Start them
        # together, or the first one can take every job alone.
        for _ in workers:
            ready.get(timeout=120)
        start.set()
        got = [claimed.get(timeout=120) for _ in submitted]
        for worker in workers:
            worker.join(60)
            self.assertEqual(worker.exitcode, 0)
        self.assertTrue(claimed.empty())
        self.assertTrue(overlaps.empty())
        # Every job went to exactly one worker
        self.assertEqual(
            sorted(job_id for _, job_id in got), sorted(submitted))
        self.assertGreater(len({worker for worker, _ in got}), 1)
        self.assertEqual(
            {info.status for info in jobs.list_jobs(limit=100)},
            {jobs.SUCCEEDED})

    def test_processes_can_make_the_tables_at_the_same_time(self):
        url = self.make_url()
        db.configure(url)
        context = multiprocessing.get_context("spawn")
        start, claimed, overlaps = (
            context.Event(), context.Queue(), context.Queue())
        workers = [
            context.Process(
                target=_compete,
                args=(url, "w{}".format(n), start, claimed, overlaps))
            for n in range(4)]
        for worker in workers:
            worker.start()
        start.set()
        for worker in workers:
            worker.join(60)
            self.assertEqual(worker.exitcode, 0)


class TestStart(JobsCase):
    """
    client.start, which the web GUI uses: a thread of the web server
    watches each job until it leaves the queue, and does the job when
    no worker is alive. The tests take the place of that thread and
    call its step() themselves, so that they decide what happens
    between two steps.
    """
    def setUp(self):
        super().setUp()
        del sent[:]

    def start(self):
        """Start a job as the web GUI does, without the thread. Return
        the id of the job and its monitor."""
        # Only the threading of client.py: patching threading.Thread
        # itself would break the modules that subclass it on import
        with mock.patch.object(client, "threading") as threading_:
            # Only Thread is replaced; the job needs a real Event
            threading_.Event = threading.Event
            job_id = client.start("test.requests", {"appliances": ["dp1"]})
        thread = threading_.Thread
        monitor = thread.call_args.kwargs["target"].__self__
        self.assertIsInstance(monitor, client._Monitor)
        thread.return_value.start.assert_called_once_with()
        return job_id, monitor

    def worker_alive(self, worker_id="ghost"):
        """Record a worker as alive. No such worker takes jobs."""
        jobs.worker_seen(worker_id, "host", 1, 1)

    def test_a_job_waits_while_a_worker_is_alive(self):
        self.worker_alive()
        job_id, monitor = self.start()
        for _ in range(3):
            self.assertFalse(monitor.step())
        self.assertEqual(jobs.get(job_id).status, "queued")
        self.assertIsNotNone(self.attached_until(job_id))
        self.assertEqual(sent, [])

    def test_the_job_runs_here_once_when_the_last_worker_stops_first(self):
        self.worker_alive()
        job_id, monitor = self.start()
        self.assertFalse(monitor.step())
        jobs.worker_gone("ghost")
        self.assertTrue(monitor.step())
        self.assertEqual(sent, ["dp1"])
        info = jobs.get(job_id)
        self.assertEqual(info.status, "succeeded")
        self.assertEqual(info.worker_id, monitor.local.worker_id)
        self.assertEqual(jobs.result(job_id).text, "sent")
        # Nothing more happens
        self.assertTrue(monitor.step())
        self.assertEqual(sent, ["dp1"])

    def test_a_worker_that_comes_back_first_does_the_job(self):
        job_id, monitor = self.start()
        self.worker_alive("back")
        # While a worker is alive, the monitor leaves the job to it
        self.assertFalse(monitor.step())
        self.assertEqual(jobs.claim("back", job_id=job_id), job_id)
        self.assertTrue(monitor.step())
        self.assertFalse(monitor.local.running)
        runner.run_job(job_id, "back")
        self.assertEqual(sent, ["dp1"])
        self.assertEqual(jobs.get(job_id).worker_id, "back")

    def test_a_worker_that_comes_back_late_does_not_get_the_job(self):
        job_id, monitor = self.start()
        self.assertTrue(monitor.step())
        self.worker_alive("back")
        self.assertIsNone(jobs.claim("back", job_id=job_id))
        self.assertEqual(sent, ["dp1"])
        self.assertEqual(jobs.get(job_id).worker_id, monitor.local.worker_id)

    def test_a_job_cancelled_while_it_waits_never_runs(self):
        self.worker_alive()
        job_id, monitor = self.start()
        self.assertFalse(monitor.step())
        jobs.cancel(job_id)
        self.assertTrue(monitor.step())
        jobs.worker_gone("ghost")
        self.assertTrue(monitor.step())
        self.assertEqual(sent, [])
        self.assertEqual(jobs.get(job_id).status, "cancelled")

    def test_the_job_is_cancelled_when_this_process_stops_before_it_starts(self):
        clock = self.use_clock()
        self.worker_alive()
        job_id, monitor = self.start()
        # The process stops: the monitor does no more steps
        clock.advance(config.lease_seconds() + 1)
        jobs.reap()
        info = jobs.get(job_id)
        self.assertEqual(info.status, "cancelled")
        self.assertIn("stopped before the job started", info.error)
        self.assertEqual(sent, [])

    def test_the_thread_does_the_job_when_no_worker_is_alive(self):
        job_id = client.start("test.requests", {"appliances": ["dp1"]})
        self.assertTrue(wait_for(lambda: jobs.get(job_id).finished))
        self.assertEqual(jobs.get(job_id).status, "succeeded")
        self.assertEqual(sent, ["dp1"])

    def attached_until(self, job_id):
        with db.session() as session:
            return session.get(Job, job_id).attached_until


class TestSchemaPostgres(PostgresMixin, TestSchema):
    pass


class TestStartPostgres(PostgresMixin, TestStart):
    pass


class TestSubmitPostgres(PostgresMixin, TestSubmit):
    pass


class TestClaimPostgres(PostgresMixin, TestClaim):
    pass


class TestCancelPostgres(PostgresMixin, TestCancel):
    pass


class TestLeasePostgres(PostgresMixin, TestLease):
    pass


class TestRunnerPostgres(PostgresMixin, TestRunner):
    pass


class TestWorkersTogetherPostgres(PostgresMixin, TestWorkersTogether):
    pass


class TestWorkerPostgres(PostgresMixin, TestWorker):
    pass


class TestWorkerProcessPostgres(PostgresMixin, TestWorkerProcess):
    pass


class TestCommandLinePostgres(PostgresMixin, TestCommandLine):
    pass


class TestMastJobsPostgres(PostgresMixin, TestMastJobs):
    pass


class TestCommandLineProcessPostgres(PostgresMixin, TestCommandLineProcess):
    pass


if __name__ == "__main__":
    unittest.main()

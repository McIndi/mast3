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
from mast.cli import Cli, Option, Params, Text, say
from mast.jobs import client, db, queue, registry, secrets
from mast.jobs.cli import cli as jobs_cli
from mast.jobs.models import Base, Job, JobTarget
from mast.jobs.worker import Plugin, Worker

POSTGRES_URL = os.environ.get("MAST_TEST_JOBS_DB_URL")


class Work(Params):
    appliances: list[str] = Option(
        default_factory=list, short="a", widget="appliances")
    credentials: list[str] = Option(
        default_factory=list, short="c", secret=True)
    seconds: float = Option(0.0, help="How long to work")
    fail: bool = Option(False, help="Raise an exception")
    exit_status: int | None = Option(None, help="Call sys.exit")
    steps: int = Option(0, help="How many steps to report")


def make_cli():
    cli = Cli(namespace="test")

    @cli.command("work")
    def work(params: Work):
        """Pretend to work on the appliances."""
        say("working on", ", ".join(params.appliances))
        time.sleep(params.seconds)
        if params.fail:
            raise RuntimeError("the appliance said no")
        if params.exit_status is not None:
            raise SystemExit(params.exit_status)
        return Text("worked with " + ",".join(params.credentials),
                    history="the history")

    meeting = threading.Barrier(2, timeout=10)

    @cli.command("meet")
    def meet(params: Work):
        """Wait for another job that runs at the same time."""
        meeting.wait()
        return Text("met")

    @cli.command("steps")
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

    @cli.command("look", exclusive=False)
    def look(params: Work):
        """Only read from the appliances."""
        return Text("looked")

    @cli.command("quiet")
    def quiet(params: Work):
        """Return nothing."""

    return cli


def submit(*appliances, **params):
    exclusive = params.pop("exclusive", None)
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
    """The base of the tests. Each test gets an empty database."""

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

    def use_clock(self):
        clock = Clock()
        patcher = mock.patch.object(queue, "_now", clock)
        patcher.start()
        self.addCleanup(patcher.stop)
        return clock

    def raw_params(self, job_id):
        with db.session() as session:
            return session.get(Job, job_id).params


class PostgresMixin(object):
    def make_url(self):
        if not POSTGRES_URL:
            self.skipTest("MAST_TEST_JOBS_DB_URL is not set")
        _erase(POSTGRES_URL)
        return POSTGRES_URL


class TestSchema(SqliteCase):
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

    def test_another_database_is_refused(self):
        db.configure("mysql://localhost/mast")
        with self.assertRaises(ValueError):
            db.engine()


class TestSubmit(SqliteCase):
    def test_a_submitted_job_is_queued(self):
        job_id = submit("dp1", seconds=1.5)
        info = jobs.get(job_id)
        self.assertEqual(info.status, jobs.QUEUED)
        self.assertEqual(info.command, "test.work")
        self.assertEqual(info.submitted_by, "local")
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
        command = jobs.resolve("system.xor")
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
        from mast.datapower.datapower import environment
        hosts = os.path.join(
            os.environ["MAST_HOME"], "etc", "local", "hosts.conf")
        with open(hosts, "w") as fout:
            fout.write("[hosts]\ndp1: 10.0.0.1\ndp2: 10.0.0.2\n")
        self.addCleanup(os.remove, hosts)
        with mock.patch.dict(
                environment.environments, {"lab": ["dp1", "dp2"]}):
            job_id = submit("lab", "10.0.0.1", "DP3.example.com", "dp2")
        self.assertEqual(
            jobs.get(job_id).targets,
            ["10.0.0.1", "10.0.0.2", "dp3.example.com"])

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


class TestClaim(SqliteCase):
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
        submit("dp1")
        blocked = submit("dp1", "dp2")
        behind = submit("dp2")
        self.assertIsNotNone(jobs.claim("w1"))
        # dp2 is free, but an older job waits for it
        self.assertIsNone(jobs.claim("w2"))
        self.assertEqual(jobs.get(blocked).status, jobs.QUEUED)
        self.assertEqual(jobs.get(behind).status, jobs.QUEUED)

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


class TestCancel(SqliteCase):
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


class TestLease(SqliteCase):
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


class TestRunner(SqliteCase):
    def test_a_job_runs(self):
        job_id = submit("dp1", credentials=["admin:hunter2"])
        worker = jobs.new_worker_id()
        self.assertEqual(jobs.run_next(worker), job_id)
        self.assertIsNone(jobs.run_next(worker))
        info = jobs.get(job_id)
        self.assertEqual(info.status, jobs.SUCCEEDED)
        self.assertIsNone(info.error)
        result = jobs.result(job_id)
        self.assertEqual(result.text, "worked with admin:hunter2")
        self.assertEqual(result.html, "<pre>worked with admin:hunter2</pre>")
        self.assertEqual(result.history, "the history")
        events = jobs.events(job_id)
        self.assertEqual(
            [(e.kind, e.message) for e in events],
            [("state", "queued"), ("state", "running on " + worker),
             ("say", "working on dp1"), ("state", "succeeded")])
        self.assertEqual(jobs.events(job_id, after=events[1].id), events[2:])

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
        job_id = submit("dp1", seconds=1.5)
        jobs.claim("w1", lease_seconds=0.6)
        reaped = []

        def reap_soon():
            time.sleep(1.0)
            reaped.extend(jobs.reap())

        thread = threading.Thread(target=reap_soon)
        thread.start()
        self.assertEqual(
            jobs.run_job(job_id, "w1", lease_seconds=0.6), jobs.SUCCEEDED)
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


def wait_for(condition, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


class TestWorker(SqliteCase):
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
class TestWorkerProcess(SqliteCase):
    def spawn(self, *args):
        env = dict(os.environ, MAST_JOBS_DB_URL=self.url)
        process = subprocess.Popen(
            [sys.executable, "-m", "mast.jobs.worker", "-p", "0.05"]
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
        # system.xor needs no appliance
        job_id = jobs.submit("system.xor", {"string": "user:password"})
        process = self.spawn("--drain")
        self.assertEqual(process.wait(60), 0, process.stdout.read())
        self.assertEqual(jobs.get(job_id).status, jobs.SUCCEEDED)
        from mast.xor import xorencode
        self.assertIn(xorencode("user:password"), jobs.result(job_id).text)

    def test_bad_options_are_refused(self):
        process = self.spawn("-c", "0")
        self.assertEqual(process.wait(30), 2)
        self.assertIn("--concurrency", process.stdout.read())


class CliCase(SqliteCase):
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
            (0, "working on dp1\nworked with admin:hunter2\n", ""))
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
            (status, out, err), (0, "working on dp1\nworked with u:p\n", ""))
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
        with mock.patch.object(client, "_note"), mock.patch(
                "mast.jobs.config.lease_seconds", return_value=0.3):
            thread.start()
            self.assertTrue(wait_for(lambda: len(jobs.list_jobs()) == 2))
            mine = jobs.list_jobs()[0].id
            for _ in range(10):
                time.sleep(0.1)
                clock.advance(0.1)
            self.assertEqual(jobs.get(mine).status, jobs.QUEUED)
            jobs.finish(ahead, "somebody", jobs.SUCCEEDED)
            thread.join(10)
        self.assertEqual(jobs.get(mine).status, jobs.SUCCEEDED)

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
        self.assertIn("mast-jobs show -j " + job.id[:8], err)

    def test_the_queue_options_are_in_the_help(self):
        status, out, _ = self.run_cli("work", "-h")
        self.assertIn("--detach", out)
        self.assertIn("--local", out)

    def test_a_cli_with_no_namespace_calls_the_command(self):
        cli = Cli()

        @cli.command("work")
        def work(params: Work):
            return Text("called")

        status, out, err = self.run_cli("work", cli=cli)
        self.assertEqual((status, out), (0, "called\n"))
        self.assertEqual(jobs.list_jobs(), [])
        status, _, err = self.run_cli("work", "--detach", cli=cli)
        self.assertEqual(status, 2)
        with self.assertRaises(ValueError):
            Cli(main=work, namespace="test")

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

    def test_commands_that_only_read_are_not_exclusive(self):
        self.run_cli("look", "-a", "dp1", "--detach")
        self.assertFalse(self.only_job().exclusive)
        self.assertFalse(jobs.resolve("system.get-status").exclusive)
        self.assertFalse(jobs.resolve("accounts.list-users").exclusive)
        self.assertTrue(jobs.resolve("system.save").exclusive)
        self.assertTrue(jobs.resolve("backups.normal-backup").exclusive)

    def test_every_plugin_command_can_be_a_job(self):
        import importlib
        for plugin in ("accounts", "backups", "crypto", "deployment",
                       "developer", "network", "system"):
            module = importlib.import_module("mast.datapower." + plugin)
            self.assertEqual(module.cli.namespace, plugin)
            for name in module.cli.commands:
                self.assertIs(
                    jobs.resolve("{}.{}".format(plugin, name)),
                    module.cli.commands[name])


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
        for expected in (job_id, "test.work", "succeeded", "by local",
                         "Worker:     w1", "credentials: (secret)",
                         "working on dp1", "[running on w1]",
                         "worked with admin:hunter2"):
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


class TestCommandLineProcess(SqliteCase):
    def mast(self, *argv):
        return subprocess.run(
            [sys.executable, "-m"] + list(argv), text=True,
            capture_output=True, timeout=120,
            env=dict(os.environ, MAST_JOBS_DB_URL=self.url))

    def test_a_plugin_command_is_a_job(self):
        from mast.xor import xorencode
        done = self.mast("mast.datapower.system", "xor", "-s", "user:pass")
        self.assertEqual(
            (done.returncode, done.stdout, done.stderr),
            (0, xorencode("user:pass") + "\n", ""))
        job = jobs.list_jobs()[0]
        self.assertEqual((job.command, job.status),
                         ("system.xor", jobs.SUCCEEDED))
        listed = self.mast("mast.jobs", "list")
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertIn(job.id, listed.stdout)
        self.assertIn("system.xor", listed.stdout)

    def test_a_detached_job_is_done_by_a_worker(self):
        from mast.xor import xorencode
        done = self.mast(
            "mast.datapower.system", "xor", "-s", "user:pass", "--detach")
        job_id = done.stdout.strip()
        self.assertEqual(jobs.get(job_id).status, jobs.QUEUED)
        worker = self.mast("mast.jobs.worker", "--drain", "-p", "0.05")
        self.assertEqual(worker.returncode, 0, worker.stdout + worker.stderr)
        shown = self.mast("mast.jobs", "follow", "-j", job_id[:8])
        self.assertEqual(
            (shown.returncode, shown.stdout),
            (0, xorencode("user:pass") + "\n"))


def _compete(url, worker_id, start, claimed, overlaps):
    """Run in another process: claim jobs until there are none."""
    db.configure(url)
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
        jobs.finish(job_id, worker_id, jobs.SUCCEEDED)


class TestWorkersTogether(SqliteCase):
    def test_processes_do_not_get_in_each_others_way(self):
        appliances = ["dp1", "dp2", "dp3"]
        submitted = [submit(appliances[n % 3]) for n in range(45)]
        context = multiprocessing.get_context("spawn")
        start, claimed, overlaps = (
            context.Event(), context.Queue(), context.Queue())
        workers = [
            context.Process(
                target=_compete,
                args=(self.url, "w{}".format(n), start, claimed, overlaps))
            for n in range(6)]
        for worker in workers:
            worker.start()
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


class TestSchemaPostgres(PostgresMixin, TestSchema):
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

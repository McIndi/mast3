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
import multiprocessing
import os
import shutil
import tempfile
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
from mast.jobs import db, queue, registry, secrets
from mast.jobs.models import Base, Job, JobTarget

POSTGRES_URL = os.environ.get("MAST_TEST_JOBS_DB_URL")


class Work(Params):
    appliances: list[str] = Option(
        default_factory=list, short="a", widget="appliances")
    credentials: list[str] = Option(
        default_factory=list, short="c", secret=True)
    seconds: float = Option(0.0, help="How long to work")
    fail: bool = Option(False, help="Raise an exception")
    exit_status: int | None = Option(None, help="Call sys.exit")


def make_cli():
    cli = Cli()

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

        import threading
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


if __name__ == "__main__":
    unittest.main()

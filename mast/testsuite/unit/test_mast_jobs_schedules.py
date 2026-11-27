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
Unit tests for the scheduled jobs (mast.jobs.schedules, the cron
helpers of mast.cron and the scheduler of the workers), passive jobs, retention (mast.jobs.retention),
migration 0007, mast schedules, mast jobs prune, and the schedule API.
"""
import contextlib
import io
import json
import os
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from sqlalchemy import select, text

from mast import cron, jobs, registry
from mast.auth import users
from mast.credentials import store
from mast.credentials.models import AuditEvent, CredentialUse
from mast.jobs import db, queue, retention, schedules
from mast.jobs.auth import LOCAL, Forbidden, Principal
from mast.jobs.cli import jobs_cli, schedules_cli
from mast.jobs.models import Job, JobResult, Schedule
from mast.jobs.worker import Plugin as WorkerPlugin, Worker
from mast.testsuite.unit.test_mast_auth import fast_hashes
from mast.testsuite.unit.test_mast_credentials import (
    ADMIN, PASSWORD, CredentialsCase)
from mast.testsuite.unit.test_mast_jobs import PostgresMixin, SqliteCase, wait_for
from mast.testsuite.unit.web_helpers import WebCase

UTC = timezone.utc
T0 = datetime(2026, 11, 23, 2, 0, tzinfo=UTC)


def minute(when):
    return when.replace(second=0, microsecond=0)


class TestCron(unittest.TestCase):

    def test_parse(self):
        for good in ("0 2 * * *", "*/15 * * * *", "@daily", "0 9 * * mon-fri",
                     "0 3 L * *", "  0   2 * *  * "):
            with self.subTest(good=good):
                cron.parse_schedule(good)
        for bad in ("", "* * *", "61 * * * *", "* * * * * extra", "abc",
                    "* 25 * * *"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                cron.parse_schedule(bad)

    def test_next_runs(self):
        after = datetime(2026, 11, 22, 23, 30, tzinfo=UTC)
        self.assertEqual(cron.next_runs(cron.parse_schedule("0 2 * * *"), after, 2),
                         [datetime(2026, 11, 23, 2, 0, tzinfo=UTC),
                          datetime(2026, 11, 24, 2, 0, tzinfo=UTC)])
        self.assertEqual(cron.next_runs(cron.parse_schedule("*/15 * * * *"), after, 1),
                         [datetime(2026, 11, 22, 23, 45, tzinfo=UTC)])
        # A rare one is quick, and the horizon limits it
        started = time.monotonic()
        self.assertEqual(cron.next_runs(cron.parse_schedule("0 0 29 2 *"), after, 5),
                         [datetime(2028, 2, 29, tzinfo=UTC)])
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(cron.next_runs(cron.parse_schedule("0 0 L * *"), after, 1),
                         [datetime(2026, 11, 30, tzinfo=UTC)])

    def test_is_due(self):
        expression = cron.parse_schedule("0 2 * * *")
        self.assertTrue(cron.is_due(expression, T0))
        self.assertTrue(cron.is_due(expression, T0 + timedelta(seconds=59)))
        self.assertFalse(cron.is_due(expression, T0 + timedelta(minutes=1)))


class ScheduleCase(CredentialsCase):
    """The appliances and credential store of CredentialsCase, a
    stored credential "ro" for collecting, bound to lab, and admins
    and an operator in the users table."""

    def setUp(self):
        super().setUp()
        fast_hashes(self)
        users.add("alice", PASSWORD, "admin")
        users.add("olga", PASSWORD, "operator")
        self.add()

    def schedule(self, name="look", cron_text="0 2 * * *", principal=ADMIN,
                 command="credtest.look", **params):
        params.setdefault("appliances", ["lab"])
        params.setdefault("credential", "ro")
        return schedules.add(name, command, params, cron_text, principal=principal)

    def job_count(self):
        return len(jobs.list_jobs(limit=500))


class TestSchedules(ScheduleCase):

    def test_add_list_get_and_remove(self):
        info = self.schedule()
        self.assertEqual((info.name, info.command, info.cron, info.enabled,
                          info.created_by), ("look", "credtest.look",
                                             "0 2 * * *", True, "alice"))
        self.assertEqual(info.params["credential"], "ro")
        self.assertEqual([s.name for s in schedules.list_schedules()], ["look"])
        self.assertEqual(schedules.get("look").id, info.id)
        with self.assertRaises(schedules.ScheduleTaken):
            self.schedule()
        schedules.remove("look", ADMIN)
        with self.assertRaises(schedules.UnknownSchedule):
            schedules.get("look")

    def test_refusals(self):
        cases = [
            ("A schedule cannot hold a password",
             dict(credential=None, credentials=["u:p"])),
            ("needs a stored credential", dict(credential=None)),
            ("not a cron expression", dict(cron_text="61 * * * *")),
            ("There is no command", dict(command="credtest.nothing")),
            ("for collecting", dict(command="credtest.change")),
            ("not bound", dict(appliances=["dp3"])),
            ("A schedule name", dict(name="no spaces")),
        ]
        for message, kwargs in cases:
            with self.subTest(message=message):
                with self.assertRaises(schedules.ScheduleError) as found:
                    self.schedule(**kwargs)
                self.assertIn(message, str(found.exception))
        self.assertEqual(schedules.list_schedules(), [])

    def test_bad_parameters_are_refused(self):
        with self.assertRaises(schedules.ScheduleError) as found:
            self.schedule(timeout="soon")
        self.assertIn("parameters of credtest.look are not valid",
                      str(found.exception))

    def test_only_an_admin_manages(self):
        operator = Principal("olga", "operator")
        with self.assertRaises(Forbidden):
            self.schedule(principal=operator)
        self.schedule()
        for call in (lambda: schedules.update("look", operator, enabled=False),
                     lambda: schedules.remove("look", operator),
                     lambda: schedules.run_now("look", operator)):
            with self.assertRaises(Forbidden):
                call()
        # Everyone who can log in may read them
        self.assertEqual(len(schedules.list_schedules(Principal("v", "viewer"))), 1)

    def test_update(self):
        self.schedule()
        info = schedules.update("look", ADMIN, cron_text="*/5 * * * *",
                                description="every five minutes")
        self.assertEqual((info.cron, info.description),
                         ("*/5 * * * *", "every five minutes"))
        info = schedules.update("look", ADMIN, params={
            "appliances": ["dp1"], "credential": "ro"})
        self.assertEqual(info.params["appliances"], ["dp1"])
        with self.assertRaises(schedules.ScheduleError):
            schedules.update("look", ADMIN, params={"appliances": ["dp1"]})
        with self.assertRaises(schedules.ScheduleError):
            schedules.update("look", ADMIN, cron_text="nope")
        self.assertEqual(schedules.update("look", ADMIN, enabled=False).enabled, False)

    def test_next_runs(self):
        runs = schedules.next_runs("0 2 * * *", count=3, now=T0)
        self.assertEqual(runs[0], T0 + timedelta(days=1))
        with self.assertRaises(schedules.ScheduleError):
            schedules.next_runs("bad")


class TestFiring(ScheduleCase):

    def test_due_and_not_due(self):
        self.schedule()
        self.assertEqual(schedules.fire_due([T0 - timedelta(minutes=1)]), {})
        fired = schedules.fire_due([T0])
        job_id = fired["look"]
        job = jobs.get(job_id)
        self.assertEqual(job.submitted_by, "schedule:look")
        self.assertEqual(job.schedule_id, schedules.get("look").id)
        self.assertEqual(job.params["credential"], "ro")
        info = schedules.get("look")
        self.assertEqual((info.last_fired_at, info.last_job_id, info.last_skip_reason),
                         (T0, job_id, None))
        # The same minute again (another check, or another mastd): nothing
        self.assertEqual(schedules.fire_due([T0]), {})
        self.assertEqual(self.job_count(), 1)

    def test_several_missed_minutes_fire_once(self):
        self.schedule(cron_text="* * * * *")
        fired = schedules.fire_due([T0 + timedelta(minutes=n) for n in range(5)])
        self.assertEqual(list(fired), ["look"])
        self.assertEqual(self.job_count(), 1)
        self.assertEqual(schedules.get("look").last_fired_at, T0 + timedelta(minutes=4))

    def test_coalescing(self):
        self.schedule(cron_text="* * * * *")
        first = schedules.fire_due([T0])["look"]
        reason = schedules.fire_due([T0 + timedelta(minutes=1)])["look"]
        self.assertIn("still queued; this run is skipped", reason)
        self.assertEqual(schedules.get("look").last_skip_reason, reason)
        self.assertEqual(schedules.get("look").last_job_id, first)
        self.assertEqual(self.job_count(), 1)
        # Once the job ended, the next run queues a job again
        jobs.run_next("w1")
        second = schedules.fire_due([T0 + timedelta(minutes=2)])["look"]
        self.assertNotEqual(second, first)
        self.assertIsNone(schedules.get("look").last_skip_reason)

    def test_a_disabled_schedule_does_not_fire(self):
        self.schedule()
        schedules.update("look", ADMIN, enabled=False)
        self.assertEqual(schedules.fire_due([T0]), {})

    def test_paused_when_the_creator_may_no_longer_submit(self):
        for change, message in (
                (lambda: users.disable("alice"), "is disabled"),
                (lambda: users.set_role("alice", "viewer"), "may not submit jobs"),
                (lambda: users.delete("alice"), "no longer exists")):
            with self.subTest(message=message):
                with contextlib.suppress(users.UserNotFound):
                    users.delete("alice")
                users.add("alice", PASSWORD, "admin")
                with contextlib.suppress(schedules.UnknownSchedule):
                    schedules.remove("look", LOCAL)
                self.schedule()
                change()
                reason = schedules.fire_due([T0])["look"]
                self.assertIn(message, reason)
                info = schedules.get("look")
                self.assertFalse(info.enabled)
                self.assertTrue(info.last_skip_reason.startswith("Paused:"))
                self.assertEqual(self.job_count(), 0)
                # It cannot be enabled again while the creator cannot run it
                with self.assertRaises(schedules.ScheduleError):
                    schedules.update("look", LOCAL, enabled=True)

    def test_the_command_line_is_an_admin(self):
        self.schedule(principal=LOCAL)
        self.assertEqual(schedules.get("look").created_by, LOCAL.name)
        self.assertIn("look", schedules.fire_due([T0]))
        self.assertEqual(self.job_count(), 1)

    def test_a_job_that_is_refused_is_recorded(self):
        self.schedule()
        store.rebind("ro", ["dp3"], principal=ADMIN)
        reason = schedules.fire_due([T0])["look"]
        self.assertIn("The job was not queued", reason)
        self.assertIn("not bound", reason)
        self.assertTrue(schedules.get("look").enabled)
        self.assertEqual(self.job_count(), 0)

    def test_the_job_uses_the_credential(self):
        from mast.testsuite.unit.test_mast_credentials import seen
        self.schedule()
        job_id = schedules.fire_due([T0])["look"]
        jobs.run_next("w1")
        self.assertEqual(jobs.get(job_id).status, jobs.SUCCEEDED)
        self.assertEqual(seen, ["reader:" + PASSWORD])
        self.assertEqual({u.principal for u in store.uses("ro")}, {"schedule:look"})

    def test_run_now(self):
        self.schedule()
        schedules.update("look", ADMIN, enabled=False)
        job_id = schedules.run_now("look", ADMIN)
        self.assertEqual(jobs.get(job_id).schedule_id, schedules.get("look").id)
        with self.assertRaises(schedules.ScheduleError) as found:
            schedules.run_now("look", ADMIN)
        self.assertIn("still queued", str(found.exception))

    def test_two_schedulers_do_not_fire_twice(self):
        for n in range(5):
            self.schedule(name="look{}".format(n))
        barrier = threading.Barrier(4)
        results = []

        def fire():
            barrier.wait()
            results.append(schedules.fire_due([T0]))

        threads = [threading.Thread(target=fire) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        fired = [name for result in results for name in result]
        self.assertEqual(sorted(fired), ["look{}".format(n) for n in range(5)])
        self.assertEqual(self.job_count(), 5)


class TestFiringPostgres(PostgresMixin, TestFiring):
    pass


class TestQueueScheduler(ScheduleCase):

    def test_check_catches_up_and_runs_maintenance(self):
        self.schedule(cron_text="*/10 * * * *")
        done = threading.Event()

        def daily():
            done.set()
        daily.__name__ = "daily"
        scheduler = schedules.QueueScheduler(maintenance=[("5 2 * * *", daily)])
        self.assertEqual(scheduler.check(T0 + timedelta(seconds=10)), {"look": mock.ANY})
        # The same minute again does nothing
        self.assertEqual(scheduler.check(T0 + timedelta(seconds=40)), {})
        jobs.run_next("w1")
        # Twelve minutes later: 02:10 was missed and fires once, and the
        # maintenance of 02:05 runs
        fired = scheduler.check(T0 + timedelta(minutes=12))
        self.assertEqual(list(fired), ["look"])
        self.assertTrue(done.wait(10))
        self.assertEqual(schedules.get("look").last_fired_at, T0 + timedelta(minutes=10))

    def test_a_long_gap_is_skipped(self):
        self.schedule(cron_text="*/10 * * * *")
        scheduler = schedules.QueueScheduler()
        scheduler.check(T0 + timedelta(minutes=1))
        with self.assertLogs("mast.schedules", "WARNING"):
            self.assertEqual(scheduler.check(T0 + timedelta(hours=3, minutes=1)), {})


class TestWorkersRunIt(ScheduleCase):

    def start(self, worker):
        thread = threading.Thread(target=worker.run)
        thread.start()
        self.addCleanup(thread.join, 10)
        self.addCleanup(worker.abort)
        return thread

    def test_a_worker_queues_the_jobs_of_the_schedules(self):
        info = self.schedule(cron_text="* * * * *")
        lines = []
        worker = Worker(poll_seconds=0.02, report=lines.append)
        thread = self.start(worker)
        self.assertTrue(wait_for(lambda: jobs.list_jobs(schedule_id=info.id)))
        self.assertIn("queues the jobs of the schedules when they are due", lines)
        worker.stop()
        thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertTrue(wait_for(lambda: not worker.scheduler.is_alive()))

    def test_two_workers_queue_one_job(self):
        info = self.schedule(cron_text="* * * * *")
        real, checked = schedules.fire_due, []

        def fire_due(minutes):
            fired = real(minutes)
            checked.append(fired)
            return fired
        # They take no job, so that the queue shows what they queued
        with mock.patch.object(queue, "claim", return_value=None), \
                mock.patch.object(schedules, "fire_due", fire_due):
            first, second = Worker(poll_seconds=0.02), Worker(poll_seconds=0.02)
            self.start(first)
            self.start(second)
            self.assertTrue(wait_for(lambda: len(checked) >= 2))
            first.stop()
            second.stop()
        self.assertEqual(len(jobs.list_jobs(schedule_id=info.id)), 1)

    def test_the_daily_retention_runs_in_it(self):
        worker = Worker()
        with mock.patch.object(schedules.QueueScheduler, "start") as start:
            worker._start_scheduler()
        start.assert_called_once_with()
        scheduler = worker.scheduler
        self.assertEqual([fn for _, fn in scheduler.maintenance], [retention.daily])
        self.assertTrue(cron.is_due(scheduler.maintenance[0][0],
                                    datetime(2026, 1, 1, 3, 30, tzinfo=UTC)))
        with mock.patch.object(jobs.config, "retention_schedule", return_value=None), \
                mock.patch.object(schedules.QueueScheduler, "start"):
            worker._start_scheduler()
        self.assertEqual(worker.scheduler.maintenance, [])

    def test_a_worker_that_drains_or_is_told_not_to_does_not_run_it(self):
        self.schedule(cron_text="* * * * *")
        self.assertTrue(Worker().schedules)
        self.assertFalse(Worker(schedules=False).schedules)
        with mock.patch.object(jobs.config, "worker_schedules", return_value=False):
            self.assertFalse(Worker().schedules)
        worker = Worker(drain=True, poll_seconds=0.02)
        self.assertFalse(worker.schedules)
        worker.run()
        self.assertIsNone(worker.scheduler)
        self.assertEqual(self.job_count(), 0)

    def test_the_worker_of_mastd_runs_it_and_the_cron_plugin_does_not(self):
        self.assertTrue(WorkerPlugin().worker.schedules)
        plugin = cron.Plugin(crontab=os.path.join(self.tmp, "crontab"))
        self.assertFalse(hasattr(plugin, "queue_scheduler"))


class TestPassive(SqliteCase):

    known_targets = ("dp1", "dp2", "dp3")

    def test_passive_jobs_neither_wait_nor_block(self):
        exclusive = jobs.submit("test.work", {"appliances": ["dp1"]})
        self.assertEqual(jobs.claim("w1"), exclusive)
        passive = jobs.submit("test.look", {"appliances": ["dp1"]}, passive=True)
        normal = jobs.submit("test.look", {"appliances": ["dp1"]})
        # The passive job runs beside the exclusive job; the normal one waits
        self.assertEqual(jobs.claim("w2"), passive)
        self.assertIsNone(jobs.claim("w3"))
        self.assertTrue(jobs.get(passive).passive)
        self.assertEqual(jobs.get(passive).targets, ["dp1"])
        jobs.finish(exclusive, "w1", jobs.SUCCEEDED)
        self.assertEqual(jobs.claim("w3"), normal)

    def test_a_passive_job_does_not_hold_up_an_exclusive_one(self):
        passive = jobs.submit("test.look", {"appliances": ["dp1"]}, passive=True)
        self.assertEqual(jobs.claim("w1"), passive)
        exclusive = jobs.submit("test.work", {"appliances": ["dp1"]})
        self.assertEqual(jobs.claim("w2"), exclusive)

    def test_a_queued_passive_job_does_not_hold_up_others(self):
        jobs.submit("test.look", {"appliances": ["dp1"]}, passive=True,
                    reserved_for="nobody")
        exclusive = jobs.submit("test.work", {"appliances": ["dp1"]})
        self.assertEqual(jobs.claim("w1"), exclusive)

    def test_a_passive_slot_takes_only_passive_jobs(self):
        exclusive = jobs.submit("test.work", {"appliances": ["dp1"]})
        normal = jobs.submit("test.look", {"appliances": ["dp2"]})
        self.assertIsNone(jobs.claim("w1/p1", passive_only=True))
        passive = jobs.submit("test.look", {"appliances": ["dp1"]}, passive=True)
        self.assertEqual(jobs.claim("w1/p1", passive_only=True), passive)
        self.assertEqual(jobs.claim("w1/1"), exclusive)
        self.assertEqual(jobs.claim("w1/2"), normal)

    def test_a_long_job_does_not_stop_the_polls(self):
        """A worker that runs one job at a time still runs a passive
        job while a long one runs."""
        long_job = jobs.submit("test.steps", {"steps": 40, "appliances": ["dp1"]})
        worker = Worker(concurrency=1, passive_slots=1, poll_seconds=0.02,
                        schedules=False)
        thread = threading.Thread(target=worker.run)
        thread.start()
        self.addCleanup(thread.join, 10)
        self.addCleanup(worker.abort)
        self.assertTrue(wait_for(lambda: jobs.get(long_job).status == jobs.RUNNING))
        poll = jobs.submit("test.look", {"appliances": ["dp1"]}, passive=True)
        self.assertTrue(wait_for(lambda: jobs.get(poll).status == jobs.SUCCEEDED))
        self.assertEqual(jobs.get(long_job).status, jobs.RUNNING)
        self.assertTrue(jobs.get(poll).worker_id.endswith("/p1"))
        worker.stop()
        thread.join(30)
        self.assertEqual(Worker(passive_slots=0).passive_slots, 0)
        with mock.patch.object(jobs.config, "passive_slots", return_value=2):
            self.assertEqual(Worker().passive_slots, 2)

    def test_only_a_command_that_only_reads(self):
        with self.assertRaises(queue.NotPassive):
            jobs.submit("test.work", {"appliances": ["dp1"]}, passive=True)
        with self.assertRaises(queue.NotPassive):
            jobs.submit("test.look", {"appliances": ["dp1"]}, passive=True,
                        exclusive=True)
        self.assertEqual(jobs.list_jobs(), [])


class TestPassivePostgres(PostgresMixin, TestPassive):
    pass


# Retention
# =========


class RetentionCase(ScheduleCase):

    def setUp(self):
        super().setUp()
        self.home = tempfile.mkdtemp(dir=self.tmp)
        self.now = datetime.now(UTC)

    def job(self, days_ago, status=jobs.SUCCEEDED, schedule=False, parent=None,
            **extra):
        job_id = jobs.submit(
            "test.work", {"appliances": ["dp1"]}, parent_id=parent,
            principal=Principal("schedule:x", "admin") if schedule else LOCAL)
        when = self.now - timedelta(days=days_ago)
        with db.session() as session:
            row = session.get(Job, job_id)
            row.status = status
            row.created_at = when
            row.finished_at = None if status in (jobs.QUEUED, jobs.RUNNING) else when
            session.add(JobResult(job_id=job_id, text="t", html="", history=""))
        return job_id

    def ids(self):
        with db.session(write=False) as session:
            return set(session.scalars(select(Job.id)))

    def prune(self, **kwargs):
        return retention.prune(now=self.now, root=self.home, **kwargs)


class TestRetention(RetentionCase):

    def test_each_job_rule(self):
        keep = {self.job(10), self.job(89), self.job(6, schedule=True),
                self.job(29, jobs.FAILED, schedule=True),
                self.job(200, jobs.RUNNING), self.job(200, jobs.QUEUED)}
        gone = {self.job(91), self.job(8, schedule=True),
                self.job(31, jobs.FAILED, schedule=True),
                self.job(91, jobs.CANCELLED)}
        counts = self.prune()
        self.assertEqual(self.ids(), keep)
        self.assertEqual((counts["jobs_days"], counts["scheduled_jobs_days"],
                          counts["scheduled_failed_days"]), (2, 1, 1))
        self.assertFalse(gone & self.ids())
        # Their results went with them
        with db.session(write=False) as session:
            self.assertEqual(set(session.scalars(select(JobResult.job_id))), keep)

    def test_children_go_with_their_parent(self):
        parent = self.job(100)
        child = self.job(1, parent=parent)
        grandchild = self.job(1, parent=child)
        busy_parent = self.job(100)
        busy_child = self.job(1, jobs.RUNNING, parent=busy_parent)
        self.prune()
        self.assertEqual(self.ids(), {busy_parent, busy_child})
        self.assertNotIn(grandchild, self.ids())

    def test_batches(self):
        made = {self.job(100) for _ in range(7)}
        with mock.patch.object(retention, "BATCH", 3):
            self.assertEqual(self.prune()["jobs_days"], 7)
        self.assertFalse(made & self.ids())

    def test_dry_run_deletes_nothing(self):
        made = {self.job(100) for _ in range(4)}
        os.makedirs(os.path.join(self.home, "var", "log", "old"))
        path = os.path.join(self.home, "var", "log", "old", "a.log")
        open(path, "w").close()
        old = time.time() - 40 * 86400
        os.utime(path, (old, old))
        with mock.patch.object(retention, "BATCH", 3):
            counts = self.prune(dry_run=True)
        self.assertEqual((counts["jobs_days"], counts["logs_days"]), (4, 1))
        self.assertLessEqual(made, self.ids())
        self.assertTrue(os.path.exists(path))

    def test_job_files_go_with_the_job(self):
        from mast.web.catalog import job_dir
        job_id = self.job(100)
        with mock.patch.object(retention, "MAST_HOME", self.home):
            directory = os.path.join(self.home, job_dir(job_id))
            os.makedirs(directory)
            open(os.path.join(directory, "out.txt"), "w").close()
            self.prune()
        self.assertFalse(os.path.exists(directory))

    def test_uses_and_audit_events(self):
        old = self.now - timedelta(days=401)
        store._open("ro", "dp1", "job1", "olga")
        with db.session() as session:
            for row in session.scalars(select(CredentialUse)):
                row.used_at = old
            for row in session.scalars(select(AuditEvent)):
                row.at = old
        store._open("ro", "dp1", "job2", "olga")
        counts = self.prune()
        self.assertEqual(counts["credential_uses_days"], 1)
        self.assertGreaterEqual(counts["audit_events_days"], 1)
        self.assertEqual([u.job_id for u in store.uses("ro")], ["job2"])
        self.assertEqual(store.audit_events("ro"), [])

    def test_uploads_and_logs(self):
        old = time.time() - 40 * 86400
        uploads = os.path.join(self.home, "var", "www", "uploads")
        for name, mtime in (("old", old), ("new", time.time())):
            os.makedirs(os.path.join(uploads, name))
            os.utime(os.path.join(uploads, name), (mtime, mtime))
        logs = os.path.join(self.home, "var", "log")
        os.makedirs(os.path.join(logs, "me", "1-1"))
        os.makedirs(os.path.join(logs, "me", "2-2"))
        for path, mtime in ((os.path.join(logs, "me", "1-1", "a.log"), old),
                            (os.path.join(logs, "me", "2-2", "b.log"), old),
                            (os.path.join(logs, "me", "2-2", "c.log"), time.time()),
                            (os.path.join(logs, "flask.log"), old)):
            open(path, "w").close()
            os.utime(path, (mtime, mtime))
        counts = self.prune()
        self.assertEqual((counts["uploads_days"], counts["logs_days"]), (1, 3))
        self.assertEqual(os.listdir(uploads), ["new"])
        self.assertFalse(os.path.exists(os.path.join(logs, "me", "1-1")))
        self.assertEqual(os.listdir(os.path.join(logs, "me", "2-2")), ["c.log"])
        self.assertTrue(os.path.isdir(logs))

    def test_tmp(self):
        from mast.web.catalog import JOBS_DIR
        old = time.time() - 40 * 86400
        tmp = os.path.join(self.home, "tmp")

        def made(*parts, mtime=old):
            path = os.path.join(tmp, *parts)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            open(path, "w").close()
            os.utime(path, (mtime, mtime))
            return path

        made("cert-audit.xlsx")
        made("crypto-inventory-20260101000000.cdx.json")
        made("restore_normal_backup", "20260101", "dp1-results.xml")
        fresh = made("cert-file-audit.xlsx", mtime=time.time())
        placeholder = made(".empty")
        # The folder of a job of the web UI goes with its job, not here
        job_file = made(os.path.relpath(os.path.join(self.home, JOBS_DIR), tmp),
                        "abc", "out", "backup.zip")
        # An empty folder that a command just made stays; an old one goes
        os.makedirs(os.path.join(tmp, "just-made"))
        os.makedirs(os.path.join(tmp, "left-over"))
        os.utime(os.path.join(tmp, "left-over"), (old, old))
        backups = os.path.join(self.home, "var", "backups", "normal_backup")
        os.makedirs(backups)
        kept_backup = os.path.join(backups, "dp1-all-domains.zip")
        open(kept_backup, "w").close()
        os.utime(kept_backup, (old, old))

        self.assertEqual(self.prune(dry_run=True)["tmp_days"], 3)
        self.assertEqual(self.prune()["tmp_days"], 3)
        self.assertEqual(sorted(os.listdir(tmp)),
                         [".empty", "cert-file-audit.xlsx", "just-made", "web"])
        for path in (fresh, placeholder, job_file, kept_backup):
            self.assertTrue(os.path.exists(path), path)

    def test_backups_are_not_in_tmp_by_default(self):
        # The commands that take backups, or keep a copy of what they
        # delete or change, write to var/backups, which retention never
        # empties; the others write to tmp/
        from mast.cli import params as cli_params
        from mast.jobs import registry as jobs_registry
        from mast.plugin_utils.params import BACKUPS_DIR, OUT_DIR
        backups = {"datapower.normal-backup", "datapower.secure-backup",
                   "datapower.export", "datapower.del-file",
                   "datapower.clean-up", "datapower.predeploy",
                   "datapower.deploy", "datapower.firmware-upgrade"}
        found = {}
        for command in jobs_registry.commands():
            for spec in cli_params.options(command.model):
                if spec.widget == OUT_DIR:
                    found[command.id] = spec.default
        self.assertLessEqual(backups, set(found))
        for command_id, default in sorted(found.items()):
            with self.subTest(command=command_id):
                if command_id in backups:
                    self.assertEqual(default, BACKUPS_DIR)
                elif command_id == "datapower.git-deploy":
                    # Its own folder of results; git-deploy is not
                    # changed until it is redesigned
                    continue
                else:
                    self.assertEqual(os.path.normpath(default).split(os.sep)[0], "tmp")

    def test_zero_keeps_for_ever(self):
        self.job(1000)
        real = retention.config.retention_days
        with mock.patch.object(retention.config, "retention_days",
                               side_effect=lambda rule: None if rule == "jobs_days"
                               else real(rule)):
            counts = self.prune()
        self.assertNotIn("jobs_days", counts)
        self.assertEqual(len(self.ids()), 1)


class TestRetentionPostgres(PostgresMixin, TestRetention):
    pass


# Migrations
# ==========


class TestMigration(SqliteCase):
    """0007 goes down with schedules and their jobs, and up again; and
    no migration deletes the rows of the tables that refer to a table
    that it changes."""

    def count(self, query):
        engine = db._create_engine(self.url)
        self.addCleanup(engine.dispose)
        with engine.connect() as connection:
            return connection.execute(text(query)).scalar()

    def tables(self):
        from sqlalchemy import inspect
        engine = db._create_engine(self.url)
        self.addCleanup(engine.dispose)
        with engine.connect() as connection:
            return set(inspect(connection).get_table_names())

    def test_down_and_up(self):
        schedule = Schedule(
            name="s", command="test.look", params={}, cron="0 2 * * *",
            enabled=True, description="", created_by="os:me",
            created_at=T0, updated_at=T0)
        with db.session() as session:
            session.add(schedule)
        registry.add("dp1", "datapower")
        registry.fleet_add("lab", "datapower", members=["dp1"])
        job_id = jobs.submit("test.look", {"appliances": ["dp1"]},
                             schedule_id=schedule.id)
        jobs.claim("w1")
        jobs.finish(job_id, "w1", jobs.SUCCEEDED, text="kept")
        db.downgrade("0006")
        self.assertEqual(db.revision(), "0006")
        self.assertNotIn("schedules", self.tables())
        # The jobs, their results and the members of the environments stay
        self.assertEqual(self.count("SELECT count(*) FROM job_results"), 1)
        self.assertEqual(self.count("SELECT count(*) FROM environment_members"), 1)
        db.downgrade("0005")
        self.assertEqual(self.count("SELECT count(*) FROM environment_members"), 1)
        db.configure(self.url)
        self.assertEqual(jobs.result(job_id).text, "kept")
        self.assertIsNone(jobs.get(job_id).schedule_id)
        self.assertEqual(registry.members("lab"), ["dp1"])
        self.assertEqual(db.revision(), db.HEAD)


class TestMigrationPostgres(PostgresMixin, TestMigration):
    pass


# The command line
# ================


class TestCommandLine(ScheduleCase):

    def run_cli(self, *args, answer="y"):
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch("builtins.input", return_value=answer):
            # mast schedules NAME, or mast jobs NAME (prune)
            cli = schedules_cli if args[0] in schedules_cli.commands \
                else jobs_cli
            try:
                cli.run(list(args))
            except SystemExit as exc:
                code = exc.code or 0
        return code, out.getvalue() + err.getvalue()

    def test_add_list_run_disable_and_remove(self):
        code, out = self.run_cli(
            "add", "-n", "look", "-c", "credtest.look", "-t", "0 2 * * *",
            "-s", 'appliances=["lab"]', "-s", "credential=ro", "-d", "nightly")
        self.assertEqual(code, 0, out)
        self.assertIn("Next runs:", out)
        self.assertEqual(schedules.get("look").created_by, LOCAL.name)
        code, out = self.run_cli("list")
        self.assertRegex(out, r"look +yes +0 2 \* \* \* +credtest.look")
        code, out = self.run_cli("run", "-n", "look")
        self.assertIn("Queued the job", out)
        code, out = self.run_cli("run", "-n", "look")
        self.assertEqual(code, 1)
        self.assertIn("still queued", out)
        code, out = self.run_cli("update", "-n", "look", "-s",
                                 'appliances=["dp1"]')
        self.assertEqual(schedules.get("look").params["appliances"], ["dp1"])
        self.assertEqual(schedules.get("look").params["credential"], "ro")
        code, out = self.run_cli("disable", "-n", "look")
        self.assertIn("Paused", out)
        code, out = self.run_cli("enable", "-n", "look")
        self.assertTrue(schedules.get("look").enabled)
        code, out = self.run_cli("remove", "-n", "look", answer="n")
        self.assertIn("Nothing was removed", out)
        code, out = self.run_cli("remove", "-n", "look", "-y")
        self.assertEqual(schedules.list_schedules(), [])

    def test_errors_are_short(self):
        for args, message in (
                (("add", "-n", "x", "-c", "credtest.look", "-t", "0 2 * * *",
                  "-p", "not json"), "--params is not JSON"),
                (("add", "-n", "x", "-c", "credtest.look", "-t", "0 2 * * *",
                  "-s", "novalue"), "--set is NAME=VALUE"),
                (("add", "-n", "x", "-c", "credtest.look", "-t", "0 2 * * *",
                  "-s", 'appliances=["lab"]'), "needs a stored credential"),
                (("run", "-n", "nothing"), "There is no schedule")):
            with self.subTest(message=message):
                code, out = self.run_cli(*args)
                self.assertEqual(code, 1)
                self.assertIn(message, out)
                self.assertNotIn("Traceback", out)

    def test_prune(self):
        job_id = jobs.submit("test.work", {"appliances": ["dp1"]})
        with db.session() as session:
            row = session.get(Job, job_id)
            row.status = jobs.SUCCEEDED
            row.finished_at = datetime.now(UTC) - timedelta(days=100)
        with mock.patch.object(retention, "MAST_HOME", self.tmp):
            code, out = self.run_cli("prune", "--dry-run")
            self.assertRegex(out, r"jobs +1")
            self.assertEqual(len(jobs.list_jobs()), 1)
            code, out = self.run_cli("prune")
        self.assertRegex(out, r"Deleted")
        self.assertEqual(jobs.list_jobs(), [])


# The web API
# ===========


class TestScheduleApi(WebCase):

    def setUp(self):
        super().setUp()
        self.key_file = os.path.join(tempfile.mkdtemp(dir=self.tmp), "credentials.key")
        patcher = mock.patch("mast.jobs.config.credentials_key_file",
                             return_value=self.key_file)
        patcher.start()
        self.addCleanup(patcher.stop)
        registry.add("dp1", "datapower", "10.0.0.1")
        store.create("ro", "reader", PASSWORD, "collect", ["dp1"], principal=ADMIN)

    def call(self, method, path, body=None):
        return self.client.open("/api/v1" + path, method=method, json=body,
                                headers={"X-CSRF-Token": self.token()})

    def body(self, **changes):
        body = {"name": "domains", "command": "datapower.list-domains",
                "cron": "0 2 * * *", "description": "nightly",
                "params": {"appliances": ["dp1"], "credential": "ro",
                           "timeout": 30}}
        body.update(changes)
        return body

    def test_an_admin_manages_schedules(self):
        self.login("admin")
        answer = self.call("POST", "/schedules", self.body())
        self.assertEqual(answer.status_code, 201, answer.get_json())
        found = answer.get_json()
        self.assertEqual((found["created_by"], found["cron"]), ("admin", "0 2 * * *"))
        self.assertIsNotNone(found["next_run"])
        self.assertEqual(found["params"]["timeout"], 30)
        self.assertEqual(self.call("POST", "/schedules", self.body()).status_code, 409)
        self.assertEqual(self.call("POST", "/schedules", self.body(
            name="x", cron="bad")).status_code, 422)
        self.assertEqual(self.call("POST", "/schedules", self.body(
            name="x", params={"appliances": ["dp1"], "credentials": ["u:p"]})).status_code, 422)
        answer = self.call("PATCH", "/schedules/domains", {"cron": "*/5 * * * *",
                                                           "enabled": False})
        self.assertEqual((answer.get_json()["cron"], answer.get_json()["enabled"]),
                         ("*/5 * * * *", False))
        self.assertEqual(self.call("PATCH", "/schedules/nothing", {}).status_code, 404)
        answer = self.call("POST", "/schedules/domains/run")
        self.assertEqual(answer.status_code, 201)
        job_id = answer.get_json()["job_id"]
        self.assertEqual(self.call("POST", "/schedules/domains/run").status_code, 422)
        listed = self.client.get("/api/v1/jobs?schedule=domains").get_json()["jobs"]
        self.assertEqual([j["id"] for j in listed], [job_id])
        self.assertEqual(listed[0]["submitted_by"], "schedule:domains")
        self.assertEqual(self.client.get("/api/v1/jobs?schedule=nothing").status_code, 404)
        # The Jobs page shows by default only what people submitted,
        # and says how many jobs of schedules it leaves out
        mine = jobs.submit("datapower.list-domains", {"appliances": ["dp1"]},
                           principal=Principal("admin", "admin"))

        def listed(query):
            answer = self.client.get("/api/v1/jobs" + query).get_json()
            return [j["id"] for j in answer["jobs"]], answer.get("hidden_scheduled")

        self.assertEqual(listed("?scheduled=no"), ([mine], 1))
        self.assertEqual(listed("?scheduled=yes"), ([job_id], None))
        self.assertEqual(listed(""), ([mine, job_id], None))
        # The count follows the other filters
        self.assertEqual(listed("?scheduled=no&command=nothing"), ([], 0))
        self.assertEqual(listed("?scheduled=no&mine=1"), ([mine], 0))
        self.assertEqual(self.client.get("/api/v1/jobs?scheduled=maybe").status_code, 400)
        self.assertEqual(self.call("DELETE", "/schedules/domains").status_code, 204)
        # The jobs of a deleted schedule are still jobs of a schedule
        self.assertEqual(listed("?scheduled=yes"), ([job_id], None))
        self.assertEqual(self.client.get("/api/v1/schedules").get_json()["schedules"], [])

    def test_out_dir_from_the_browser_is_not_kept(self):
        store.create("rw", "admin", PASSWORD, "change", ["dp1"], principal=ADMIN)
        self.login("admin")
        answer = self.call("POST", "/schedules", self.body(
            name="backup", command="datapower.normal-backup", params={
                "appliances": ["dp1"], "credential": "rw", "Domain": ["all-domains"],
                "out_dir": "/etc"}))
        self.assertEqual(answer.status_code, 201, answer.get_json())
        # The default of the model: a scheduled backup is kept
        from mast.plugin_utils.params import BACKUPS_DIR
        self.assertEqual(answer.get_json()["params"]["out_dir"], BACKUPS_DIR)

    def test_an_upload_cannot_be_scheduled(self):
        self.login("admin")
        answer = self.call("POST", "/schedules", self.body(
            command="datapower.set-file", params={
                "appliances": ["dp1"], "credential": "ro", "file_in": "token"}))
        self.assertEqual(answer.status_code, 422)
        self.assertIn("uploaded file", json.dumps(answer.get_json()))

    def test_preview(self):
        self.login("viewer")
        answer = self.client.get("/api/v1/schedules/preview?cron=0+2+*+*+*")
        self.assertEqual(len(answer.get_json()["runs"]), 5)
        self.assertEqual(self.client.get("/api/v1/schedules/preview?cron=bad").status_code, 422)

    def test_others_cannot_change_them(self):
        self.login("admin")
        self.call("POST", "/schedules", self.body())
        for role in ("operator", "viewer"):
            with self.subTest(role=role):
                self.login(role)
                self.assertEqual(len(self.client.get("/api/v1/schedules")
                                     .get_json()["schedules"]), 1)
                for method, path, body in (
                        ("POST", "/schedules", self.body(name="other")),
                        ("PATCH", "/schedules/domains", {"enabled": False}),
                        ("DELETE", "/schedules/domains", None),
                        ("POST", "/schedules/domains/run", None)):
                    self.assertEqual(self.call(method, path, body).status_code, 403)
        self.assertTrue(schedules.get("domains").enabled)

    def test_the_admin_page(self):
        self.login("admin")
        page = self.client.get("/admin/schedules")
        self.assertEqual(page.status_code, 200)
        self.assertIn("admin-schedules.js", page.get_data(as_text=True))
        self.assertIn("target-bar", page.get_data(as_text=True))
        self.login("operator")
        self.assertEqual(self.client.get("/admin/schedules").status_code, 403)


if __name__ == "__main__":
    unittest.main()

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
Unit tests for child jobs (`mast.jobs.runner.run_children`) and
`mast.jobs.auth.principal_of`.
"""
import threading
import time
from datetime import datetime, timezone

from mast import jobs
from mast.auth import users
from mast.cli import Cli, Option, Text, say
from mast.jobs import auth, db, registry, runner
from mast.jobs.models import Schedule
from mast.jobs.auth import LOCAL, Forbidden, Principal
from mast.testsuite.unit.test_mast_auth import fast_hashes
from mast.testsuite.unit.test_mast_jobs import (
    PostgresMixin, SqliteCase, Work, wait_for)

# What the children saw
ran = []
started = threading.Event()


class Kid(Work):
    n: int = Option(0)
    names: list[str] = Option(default_factory=list)


def make_cli():
    cli = Cli(provider="kids")

    @cli.command("parent", exclusive=False, effect="read")
    def parent(params: Kid):
        """Run one child for each appliance."""
        say("starting children")
        children = runner.run_children(
            "kids.child", [{"appliances": [name], "n": n, "fail": params.fail,
                            "seconds": params.seconds}
                           for n, name in enumerate(params.appliances)])
        return Text(" ".join(jobs.get(c).status for c in children))

    @cli.command("child", exclusive=False, passive=True, effect="read")
    def child(params: Kid):
        """One appliance."""
        ran.append((params.n, runner.current_job()))
        started.set()
        time.sleep(params.seconds)
        if params.fail and params.n == 1:
            raise RuntimeError("child 1 failed")
        return Text("child {}".format(params.n))

    return cli


class ChildrenCase(SqliteCase):

    def setUp(self):
        super().setUp()
        registry.register("kids", make_cli())
        self.addCleanup(registry.unregister, "kids")
        ran[:] = []
        started.clear()


class TestChildren(ChildrenCase):

    def test_a_parent_runs_its_children(self):
        parent = jobs.submit("kids.parent", {"appliances": ["dp1", "dp2", "dp3"]})
        jobs.run_next("w1")
        info = jobs.get(parent)
        self.assertEqual(info.status, jobs.SUCCEEDED, info.error)
        self.assertEqual(jobs.result(parent).text,
                         "succeeded succeeded succeeded")
        children = jobs.list_jobs(parent_id=parent)
        self.assertEqual(len(children), 3)
        for child in children:
            self.assertEqual((child.parent_id, child.command, child.submitted_by,
                              child.passive),
                             (parent, "kids.child", LOCAL.name, True))
        # Each child ran as its own job, in this process
        self.assertEqual(sorted(n for n, _ in ran), [0, 1, 2])
        self.assertEqual({job for _, job in ran}, {c.id for c in children})
        self.assertIsNone(runner.current_job())

    def test_a_child_that_fails_does_not_stop_the_others(self):
        parent = jobs.submit("kids.parent", {"appliances": ["dp1", "dp2", "dp3"],
                                             "fail": True})
        jobs.run_next("w1")
        self.assertEqual(jobs.result(parent).text,
                         "succeeded failed succeeded")

    def test_cancelling_the_parent_cancels_its_children(self):
        parent = jobs.submit("kids.parent", {"appliances": ["dp1", "dp2"],
                                             "seconds": 30})
        thread = threading.Thread(target=jobs.run_next, args=("w1",))
        thread.start()
        self.addCleanup(thread.join, 30)
        self.assertTrue(started.wait(10))
        jobs.cancel(parent)
        thread.join(30)
        self.assertFalse(thread.is_alive())
        self.assertEqual(jobs.get(parent).status, jobs.CANCELLED)
        self.assertTrue(wait_for(lambda: all(
            c.cancel_requested or c.status == jobs.CANCELLED
            for c in jobs.list_jobs(parent_id=parent))))

    def test_only_inside_a_job(self):
        with self.assertRaises(RuntimeError):
            runner.run_children("kids.child", [{}])


class TestChildrenPostgres(PostgresMixin, TestChildren):
    pass


class TestPrincipalOf(SqliteCase):

    def setUp(self):
        super().setUp()
        fast_hashes(self)

    def test_each_kind(self):
        self.assertEqual(auth.principal_of("os:alice"), Principal("os:alice", "admin"))
        self.assertEqual(auth.principal_of("local").role, "admin")
        users.add("olga", "a long test password", "operator")
        self.assertEqual(auth.principal_of("olga"), Principal("olga", "operator"))
        users.add("victor", "a long test password", "viewer")
        with self.assertRaises(Forbidden):
            auth.principal_of("victor")
        with self.assertRaises(Forbidden):
            auth.principal_of("nobody")
        users.add("alice", "a long test password", "admin")
        now = datetime.now(timezone.utc)
        with db.session() as session:
            session.add(Schedule(
                name="nightly", command="test.look", params={},
                cron="0 2 * * *", enabled=True, description="",
                created_by="alice", created_at=now, updated_at=now))
        # A schedule has the role of the admin who made it, now
        self.assertEqual(auth.principal_of("schedule:nightly"),
                         Principal("schedule:nightly", "admin"))
        users.set_role("alice", "operator")
        self.assertEqual(auth.principal_of("schedule:nightly").role, "operator")
        with self.assertRaises(Forbidden):
            auth.principal_of("schedule:gone")

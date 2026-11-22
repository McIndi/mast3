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
Unit tests for mast.auth (users, roles, passwords), for the roles in
mast.jobs.auth, for mast-users, and for the way back from the
migrations of the job database.

The database tests use SQLite, and PostgreSQL as well when
MAST_TEST_JOBS_DB_URL is set (see test_mast_jobs).
"""
import contextlib
import io
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from sqlalchemy import create_engine, inspect, text

from mast import jobs
from mast.auth import passwords, roles, users
from mast.auth.models import User
from mast.jobs import auth, db, queue
from mast.jobs.models import Job
from mast.testsuite.unit.test_mast_jobs import (
    PostgresMixin, SqliteCase, submit)

GOOD = "correct horse battery"
OTHER = "another long password"


def fast_hashes(case):
    """Make scrypt cheap for a test. The tests of the real cost patch
    nothing."""
    for name, value in (("N", 2 ** 10), ("R", 8), ("P", 1)):
        patcher = mock.patch.object(passwords, name, value)
        patcher.start()
        case.addCleanup(patcher.stop)
    patcher = mock.patch.object(users, "_dummy_hash", None)
    patcher.start()
    case.addCleanup(patcher.stop)


class TestPasswords(unittest.TestCase):

    def test_round_trip(self):
        stored = passwords.hash_password(GOOD)
        self.assertTrue(stored.startswith("scrypt$32768$8$1$"))
        self.assertTrue(passwords.verify_password(GOOD, stored))
        self.assertFalse(passwords.verify_password(OTHER, stored))
        self.assertFalse(passwords.needs_rehash(stored))

    def test_each_hash_has_its_own_salt(self):
        self.assertNotEqual(passwords.hash_password(GOOD),
                            passwords.hash_password(GOOD))

    def test_a_bad_hash_does_not_verify(self):
        for stored in ("", "plain", "md5$1$2$3$4$5", None,
                       "scrypt$x$8$1$AAAA$AAAA"):
            with self.subTest(stored=stored):
                self.assertFalse(passwords.verify_password(GOOD, stored))
                self.assertTrue(passwords.needs_rehash(stored))

    def test_older_parameters_need_a_rehash(self):
        with mock.patch.object(passwords, "N", 2 ** 10):
            weak = passwords.hash_password(GOOD)
        self.assertTrue(passwords.verify_password(GOOD, weak))
        self.assertTrue(passwords.needs_rehash(weak))

    def test_strength(self):
        with self.assertRaises(passwords.WeakPassword):
            passwords.check_strength("short")
        with self.assertRaises(passwords.WeakPassword):
            passwords.check_strength("Administrator", "administrator")
        passwords.check_strength(GOOD, "admin")


class TestRoles(unittest.TestCase):
    """One test for each row of the matrix in the plan."""

    def check(self, action, viewer, operator, admin):
        for role, expected in (("viewer", viewer), ("operator", operator),
                               ("admin", admin)):
            with self.subTest(action=action, role=role):
                self.assertIs(roles.allowed(role, action), expected)

    def test_read(self):
        self.check("read", True, True, True)

    def test_submit(self):
        self.check("submit", False, True, True)

    def test_cancel(self):
        self.check("cancel-own", False, True, True)
        self.check("cancel-any", False, False, True)

    def test_use_credential(self):
        self.check("use-credential", False, True, True)
        self.check("use-change-credential", False, False, True)

    def test_manage(self):
        for action in ("manage-credential", "manage-appliance",
                       "manage-user", "manage-schedule"):
            self.check(action, False, False, True)

    def test_approve(self):
        self.check("approve", False, False, True)

    def test_an_unknown_role_may_do_nothing(self):
        for action in roles.ACTIONS:
            self.assertFalse(roles.allowed("root", action))
            self.assertFalse(roles.allowed(None, action))

    def test_an_unknown_action_is_a_mistake(self):
        with self.assertRaises(roles.UnknownAction):
            roles.allowed("admin", "everything")


class TestAuthorize(unittest.TestCase):
    viewer = auth.Principal("vera", "viewer")
    operator = auth.Principal("otto", "operator")
    admin = auth.Principal("ada", "admin")

    def test_local_is_the_os_account_and_an_admin(self):
        self.assertTrue(auth.LOCAL.name.startswith("os:"))
        self.assertEqual(auth.LOCAL.role, "admin")
        with mock.patch.object(auth.getpass, "getuser",
                               side_effect=OSError("no user")):
            self.assertTrue(auth.os_principal().name.startswith("os:"))

    def test_each_action(self):
        for action in roles.ACTIONS:
            for principal in (self.viewer, self.operator, self.admin):
                with self.subTest(action=action, role=principal.role):
                    if roles.allowed(principal.role, action):
                        auth.authorize(principal, action)
                    else:
                        with self.assertRaises(auth.Forbidden):
                            auth.authorize(principal, action)

    def test_cancel(self):
        auth.authorize(self.admin, "cancel", owner="otto")
        auth.authorize(self.admin, "cancel", owner="local")
        auth.authorize(self.operator, "cancel", owner="otto")
        with self.assertRaises(auth.Forbidden):
            auth.authorize(self.operator, "cancel", owner="ada")
        with self.assertRaises(auth.Forbidden):
            auth.authorize(self.operator, "cancel", owner="local")
        with self.assertRaises(auth.Forbidden):
            auth.authorize(self.viewer, "cancel", owner="vera")

    def test_a_principal_without_a_role_may_do_nothing(self):
        with self.assertRaises(auth.Forbidden):
            auth.authorize(object(), "read")


class TestQueueRoles(SqliteCase):
    """The queue asks authorize for each action."""
    viewer = auth.Principal("vera", "viewer")
    operator = auth.Principal("otto", "operator")

    def test_a_viewer_reads_but_does_not_submit(self):
        job_id = submit("dp1")
        self.assertEqual(jobs.get(job_id, self.viewer).id, job_id)
        self.assertEqual(len(jobs.list_jobs(principal=self.viewer)), 1)
        with self.assertRaises(jobs.Forbidden):
            queue.submit("test.work", {"appliances": ["dp1"]},
                         principal=self.viewer)

    def test_an_operator_cancels_only_own_jobs(self):
        mine = queue.submit("test.work", {"appliances": ["dp1"]},
                            principal=self.operator)
        theirs = submit("dp2")
        self.assertEqual(jobs.cancel(mine, self.operator).status,
                         jobs.CANCELLED)
        with self.assertRaises(jobs.Forbidden):
            jobs.cancel(theirs, self.operator)
        self.assertEqual(jobs.get(theirs).status, jobs.QUEUED)
        self.assertEqual(jobs.cancel(theirs).status, jobs.CANCELLED)

    def test_a_job_of_the_old_local_principal(self):
        # A job that MAST 3.15.2 queued: the principal was "local"
        job_id = submit("dp1")
        with db.session() as session:
            session.get(Job, job_id).submitted_by = "local"
        with self.assertRaises(jobs.Forbidden):
            jobs.cancel(job_id, self.operator)
        # The command line is an admin
        self.assertEqual(jobs.cancel(job_id).status, jobs.CANCELLED)


class TestQueueRolesPostgres(PostgresMixin, TestQueueRoles):
    pass


class TestUsers(SqliteCase):

    def setUp(self):
        super().setUp()
        fast_hashes(self)

    def test_add_and_get(self):
        info = users.add("Alice", GOOD, "operator")
        self.assertEqual(info.name, "alice")
        self.assertEqual(info.role, "operator")
        self.assertTrue(info.must_change)
        self.assertFalse(info.disabled)
        self.assertEqual(users.get("ALICE").id, info.id)
        self.assertEqual(users.get_by_id(info.id).name, "alice")
        self.assertIsNone(users.get_by_id(info.id + 100))
        self.assertTrue(users.any_users())
        self.assertNotIn("password_hash", info.model_dump())

    def test_no_users(self):
        self.assertFalse(users.any_users())
        self.assertEqual(users.list_users(), [])

    def test_the_hash_is_stored_not_the_password(self):
        users.add("alice", GOOD, "viewer")
        with db.session() as session:
            stored = session.query(User).one().password_hash
        self.assertNotIn(GOOD, stored)
        self.assertTrue(passwords.verify_password(GOOD, stored))

    def test_add_refuses(self):
        users.add("alice", GOOD, "viewer")
        with self.assertRaises(users.UserExists):
            users.add("ALICE", GOOD, "viewer")
        with self.assertRaises(users.InvalidRole):
            users.add("bob", GOOD, "root")
        with self.assertRaises(users.InvalidName):
            users.add("os:bob", GOOD, "viewer")
        with self.assertRaises(users.InvalidName):
            users.add("", GOOD, "viewer")
        with self.assertRaises(passwords.WeakPassword):
            users.add("bob", "short", "viewer")
        with self.assertRaises(passwords.WeakPassword):
            users.add("longusername1", "LongUserName1", "viewer")

    def test_authenticate(self):
        users.add("alice", GOOD, "viewer")
        info = users.authenticate("Alice", GOOD)
        self.assertEqual(info.name, "alice")
        self.assertIsNotNone(info.last_login_at)
        with self.assertRaises(users.LoginFailed) as caught:
            users.authenticate("alice", OTHER)
        self.assertEqual(caught.exception.reason, "password")

    def test_every_failure_has_the_same_message(self):
        users.add("alice", GOOD, "viewer")
        users.add("dave", GOOD, "viewer")
        users.disable("dave")
        messages = set()
        for name, password in (("alice", OTHER), ("nobody", GOOD),
                               ("dave", GOOD), ("bad name!", GOOD)):
            with self.assertRaises(users.LoginFailed) as caught:
                users.authenticate(name, password)
            messages.add(str(caught.exception))
        self.assertEqual(messages, {users.LoginFailed.MESSAGE})

    def test_an_unknown_user_costs_a_verify(self):
        users.add("alice", GOOD, "viewer")
        users._dummy()
        with mock.patch.object(passwords, "verify_password",
                               wraps=passwords.verify_password) as verify:
            for name in ("nobody", "bad name!"):
                with self.assertRaises(users.LoginFailed) as caught:
                    users.authenticate(name, GOOD)
                self.assertEqual(caught.exception.reason, "unknown")
            self.assertEqual(verify.call_count, 2)
            with self.assertRaises(users.LoginFailed):
                users.authenticate("alice", OTHER)
            self.assertEqual(verify.call_count, 3)

    def test_a_disabled_user_cannot_log_in(self):
        users.add("alice", GOOD, "viewer")
        users.disable("alice")
        with self.assertRaises(users.LoginFailed) as caught:
            users.authenticate("alice", GOOD)
        self.assertEqual(caught.exception.reason, "disabled")
        users.enable("alice")
        users.authenticate("alice", GOOD)

    def test_lockout_and_its_expiry(self):
        users.add("alice", GOOD, "viewer")
        now = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
        for attempt in range(users.MAX_FAILURES - 1):
            with self.assertRaises(users.LoginFailed) as caught:
                users.authenticate("alice", OTHER, now=now)
            self.assertEqual(caught.exception.reason, "password")
        with self.assertRaises(users.LoginFailed) as caught:
            users.authenticate("alice", OTHER, now=now)
        self.assertEqual(caught.exception.reason, "locked-now")
        # The right password does not help while the lock lasts
        with self.assertRaises(users.LoginFailed) as caught:
            users.authenticate(
                "alice", GOOD, now=now + timedelta(minutes=14))
        self.assertEqual(caught.exception.reason, "locked")
        later = now + timedelta(minutes=users.LOCK_MINUTES, seconds=1)
        self.assertEqual(
            users.authenticate("alice", GOOD, now=later).failed_logins, 0)

    def test_a_good_login_resets_the_count(self):
        users.add("alice", GOOD, "viewer")
        for _ in range(users.MAX_FAILURES - 1):
            with self.assertRaises(users.LoginFailed):
                users.authenticate("alice", OTHER)
        users.authenticate("alice", GOOD)
        with self.assertRaises(users.LoginFailed) as caught:
            users.authenticate("alice", OTHER)
        self.assertEqual(caught.exception.reason, "password")

    def test_a_login_rehashes_older_parameters(self):
        users.add("alice", GOOD, "viewer")
        with mock.patch.object(passwords, "N", 2 ** 11):
            users.authenticate("alice", GOOD)
        with db.session() as session:
            stored = session.query(User).one().password_hash
        self.assertTrue(stored.startswith("scrypt$2048$"))
        users.authenticate("alice", GOOD)

    def test_changes_give_a_new_epoch(self):
        epoch = users.add("alice", GOOD, "viewer").session_epoch
        for change in (lambda: users.set_password("alice", OTHER),
                       lambda: users.set_role("alice", "admin"),
                       lambda: users.disable("alice")):
            info = change()
            self.assertGreater(info.session_epoch, epoch)
            epoch = info.session_epoch
        self.assertEqual(users.enable("alice").session_epoch, epoch)
        self.assertEqual(users.new_epoch(users.get("alice").id)
                         .session_epoch, epoch + 1)

    def test_set_password(self):
        users.add("alice", GOOD, "viewer")
        info = users.set_password("alice", OTHER)
        self.assertFalse(info.must_change)
        self.assertTrue(users.set_password(
            "alice", OTHER + "!", temporary=True).must_change)
        users.authenticate("alice", OTHER + "!")

    def test_change_password(self):
        users.add("alice", GOOD, "viewer")
        with self.assertRaises(users.LoginFailed):
            users.change_password("alice", OTHER, OTHER + "!")
        with self.assertRaises(passwords.WeakPassword):
            users.change_password("alice", GOOD, GOOD)
        info = users.change_password("alice", GOOD, OTHER)
        self.assertFalse(info.must_change)
        users.authenticate("alice", OTHER)

    def test_delete(self):
        users.add("alice", GOOD, "viewer")
        users.add("bob", GOOD, "admin")
        users.delete("alice")
        self.assertEqual([u.name for u in users.list_users()], ["bob"])
        with self.assertRaises(users.UserNotFound):
            users.delete("alice")
        with self.assertRaises(users.UserNotFound):
            users.set_role("alice", "admin")


class TestUsersPostgres(PostgresMixin, TestUsers):
    pass


class TestMastUsers(SqliteCase):
    """The command line, mast-users."""

    def setUp(self):
        super().setUp()
        fast_hashes(self)
        from mast.auth.cli import cli
        self.cli = cli

    def run_cli(self, *args, typed=()):
        typed = list(typed)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("mast.auth.cli.getpass.getpass",
                        side_effect=lambda prompt="": typed.pop(0)), \
                contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            try:
                self.cli.run(list(args))
                status = 0
            except SystemExit as exc:
                status = exc.code or 0
        return status, out.getvalue(), err.getvalue()

    def test_add_list_and_change(self):
        status, out, err = self.run_cli(
            "add", "-u", "Alice", "-r", "admin", typed=[GOOD, GOOD])
        self.assertEqual(status, 0, err)
        self.assertIn("alice", out)
        self.assertEqual(users.get("alice").role, "admin")
        status, out, _ = self.run_cli("list")
        self.assertIn("alice", out)
        self.assertIn("admin", out)
        self.assertNotIn("scrypt", out)
        self.run_cli("set-role", "-u", "alice", "-r", "viewer")
        self.assertEqual(users.get("alice").role, "viewer")
        self.run_cli("disable", "-u", "alice")
        self.assertTrue(users.get("alice").disabled)
        self.run_cli("enable", "-u", "alice")
        self.assertFalse(users.get("alice").disabled)
        self.run_cli("set-password", "-u", "alice", typed=[OTHER, OTHER])
        self.assertFalse(users.get("alice").must_change)
        users.authenticate("alice", OTHER)
        self.run_cli("set-password", "-u", "alice", "-t",
                     typed=[GOOD, GOOD])
        self.assertTrue(users.get("alice").must_change)
        self.run_cli("delete", "-u", "alice")
        self.assertFalse(users.any_users())

    def test_the_password_must_be_typed_twice_the_same(self):
        status, _, err = self.run_cli(
            "add", "-u", "alice", "-r", "viewer", typed=[GOOD, OTHER])
        self.assertNotEqual(status, 0)
        self.assertIn("not the same", err)
        self.assertFalse(users.any_users())

    def test_errors_are_short(self):
        status, _, err = self.run_cli(
            "add", "-u", "alice", "-r", "viewer", typed=["short", "short"])
        self.assertNotEqual(status, 0)
        self.assertIn("at least 12", err)
        status, _, err = self.run_cli("delete", "-u", "nobody")
        self.assertNotEqual(status, 0)
        self.assertIn("no user nobody", err)

    def test_there_is_no_password_option(self):
        from mast.auth.cli import Add, SetPassword
        for model in (Add, SetPassword):
            self.assertNotIn("password", model.model_fields)


class TestMigrations(SqliteCase):
    """Every migration has a working downgrade(): a database of MAST
    3.15.2 (revision 0001) with jobs in it goes up to the head and back
    to 0001, and keeps its jobs."""

    def old_database(self):
        engine = create_engine(self.url)
        self.addCleanup(engine.dispose)
        # MAST 3.15.2 made the tables of revision 0001
        db._migrate(db._create_engine(self.url), "upgrade", "0001")
        return engine

    def tables(self, engine):
        return set(inspect(engine).get_table_names())

    def test_up_and_down_with_jobs(self):
        engine = self.old_database()
        self.assertEqual(db.revision(), "0001")
        self.assertNotIn("users", self.tables(engine))
        # Jobs of the old release, with the old principal
        with engine.begin() as connection:
            for n in range(3):
                connection.execute(text(
                    "INSERT INTO jobs (id, command, params, status, "
                    "exclusive, submitted_by, created_at, "
                    "cancel_requested) VALUES (:id, 'test.work', '{}', "
                    "'queued', true, 'local', :at, false)"),
                    {"id": "{:032x}".format(n),
                     "at": "2026-11-01 00:00:00"})
            connection.execute(text(
                "INSERT INTO job_results (job_id, text, html, history) "
                "VALUES (:id, 'the text', '<p>html</p>', '')"),
                {"id": "{:032x}".format(2)})
        db.configure(self.url)
        # The new MAST upgrades it when it opens it
        self.assertEqual(len(jobs.list_jobs()), 3)
        self.assertEqual(db.revision(), db.HEAD)
        self.assertIn("users", self.tables(engine))
        users.add("alice", GOOD, "admin")
        self.assertIsNone(jobs.result("{:032x}".format(2)).data)
        # The old job of "local" can be cancelled from the command line
        self.assertEqual(jobs.cancel("{:032x}".format(0)).status,
                         jobs.CANCELLED)
        # And back
        db.downgrade("0001")
        self.assertEqual(db.revision(), "0001")
        self.assertNotIn("users", self.tables(engine))
        with engine.connect() as connection:
            self.assertEqual(connection.execute(
                text("SELECT count(*) FROM jobs")).scalar(), 3)
            self.assertEqual(connection.execute(
                text("SELECT text FROM job_results")).scalar(), "the text")

    def setUp(self):
        super().setUp()
        fast_hashes(self)


class TestDowngradeCommand(SqliteCase):
    """mast-jobs downgrade-db, the way back to an older MAST."""

    def run_jobs(self, *args):
        from mast.jobs.cli import cli
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.run(list(args))
        return out.getvalue()

    def test_downgrade_db(self):
        job_id = submit("dp1")
        self.assertIn("With --yes",
                      self.run_jobs("downgrade-db", "-r", "0001"))
        self.assertEqual(db.revision(), db.HEAD)
        out = self.run_jobs("downgrade-db", "-r", "0001", "-y")
        self.assertIn("now at revision 0001", out)
        self.assertIn("already",
                      self.run_jobs("downgrade-db", "-r", "0001"))
        with db._create_engine(self.url).connect() as connection:
            self.assertEqual(connection.execute(
                text("SELECT id FROM jobs")).scalar(), job_id)


class TestDowngradeCommandPostgres(PostgresMixin, TestDowngradeCommand):
    pass


class TestMigrationsPostgres(PostgresMixin, TestMigrations):
    pass


if __name__ == "__main__":
    unittest.main()

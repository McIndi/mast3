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
Unit tests for mast.credentials: the key file, the store, the checks of
the queue and the runner, mast-credentials, and the API and the page of
the web UI.
"""
import base64
import contextlib
import html
import io
import json
import os
import stat
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from cryptography.fernet import Fernet
from pydantic import ValidationError
from sqlalchemy import select

from mast import jobs, keyfile, registry
from mast.cli import Cli, Table, say
from mast.credentials import TEST_COMMAND, keys, store, testing
from mast.credentials.cli import cli as credentials_cli
from mast.credentials.models import CredentialRecord, CredentialUse
from mast.jobs import db, queue
from mast.jobs import registry as commands
from mast.jobs.auth import Forbidden, Principal
from mast.jobs.models import Job, JobEvent, JobResult
from mast.plugin_utils.params import ApplianceOptions
from mast.testsuite.unit.test_mast_jobs import PostgresMixin, SqliteCase
from mast.testsuite.unit.web_helpers import WebCase

PASSWORD = "S3cret-Passw0rd"
ADMIN = Principal("alice", "admin")
OPERATOR = Principal("olga", "operator")
VIEWER = Principal("victor", "viewer")

# What the commands of the test namespace got as credentials
seen = []
# Whether they ran inside mast.cli.results.stored_credential, and its name
strict = []
names = []


class Look(ApplianceOptions):
    pass


def make_cli():
    cli = Cli(namespace="credtest")

    @cli.command("look", exclusive=False)
    def look(params: Look):
        """Only read; repeat the password in every form."""
        from mast.cli.results import (
            stored_credential_name, using_stored_credential)
        seen[:] = params.credentials
        strict[:] = [using_stored_credential()]
        names[:] = [stored_credential_name()]
        password = params.credentials[0].partition(":")[2] \
            if params.credentials else ""
        forms = "{} {} {}".format(
            password, html.escape(password),
            base64.b64encode(password.encode()).decode())
        say("looked with", forms)
        return Table(["appliance", "seen"],
                     [[name, forms] for name in params.appliances],
                     history="<Password>{}</Password>".format(password))

    @cli.command("change")
    def change(params: Look):
        """Change the appliances."""
        seen[:] = params.credentials
        return Table(["appliance"], [[name] for name in params.appliances])

    return cli


class CredentialsCase(SqliteCase):
    """An empty database, a key file of its own, two appliances and an
    environment."""

    def setUp(self):
        super().setUp()
        self.key_dir = tempfile.mkdtemp(dir=self.tmp)
        self.key_file = os.path.join(self.key_dir, "credentials.key")
        patcher = mock.patch("mast.jobs.config.credentials_key_file",
                             return_value=self.key_file)
        patcher.start()
        self.addCleanup(patcher.stop)
        commands.register("credtest", make_cli())
        self.addCleanup(commands.unregister, "credtest")
        registry.add("dp1", "10.0.0.1")
        registry.add("dp2", "10.0.0.2")
        registry.add("dp3", "10.0.0.3")
        registry.env_add("lab", members=["dp1", "dp2"])
        seen[:] = []

    def add(self, name="ro", purpose="collect", bindings=("lab",), **kwargs):
        return store.create(name, "reader", kwargs.pop("password", PASSWORD),
                            purpose, list(bindings), principal=ADMIN, **kwargs)


class TestKeyFile(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.dir))
        self.path = os.path.join(self.dir, "local", "keys")

    def test_read_add_and_keep(self):
        first = keyfile.read_keys(self.path)
        self.assertEqual(len(first), 1)
        Fernet(first[0])
        self.assertEqual(keyfile.read_keys(self.path), first)
        second = keyfile.add_key(self.path)
        self.assertEqual(len(second), 2)
        self.assertEqual(second[1:], first)
        self.assertEqual(keyfile.read_keys(self.path), second)
        third = keyfile.add_key(self.path)
        self.assertEqual(keyfile.keep_keys(self.path, 2), third[:2])
        self.assertEqual(keyfile.read_keys(self.path), third[:2])
        # read_or_create still reads the first line of a one-key file
        one = os.path.join(self.dir, "one")
        key = keyfile.read_or_create(one)
        self.assertEqual(keyfile.read_keys(one), [key])
        # No temporary file is left behind
        self.assertEqual(sorted(os.listdir(os.path.dirname(self.path))),
                         ["keys"])

    @unittest.skipUnless(os.name == "posix", "POSIX permissions")
    def test_the_file_is_private(self):
        keyfile.add_key(self.path)
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)

    @unittest.skipUnless(os.name == "posix", "POSIX permissions")
    def test_a_readable_key_file_is_refused(self):
        provider = keys.FileKeyProvider(self.path)
        os.makedirs(os.path.dirname(self.path), mode=0o700)
        provider.keys()
        os.chmod(self.path, 0o644)
        with self.assertRaises(keys.InsecureKeyFile) as found:
            provider.keys()
        self.assertIn("chmod 600 {}".format(self.path), str(found.exception))
        os.chmod(self.path, 0o600)
        os.chmod(os.path.dirname(self.path), 0o750)
        with self.assertRaises(keys.InsecureKeyFile) as found:
            provider.rotate()
        self.assertIn("chmod 700", str(found.exception))
        os.chmod(os.path.dirname(self.path), 0o700)
        self.assertEqual(len(provider.keys()), 1)

    @unittest.skipUnless(os.name == "posix", "POSIX permissions")
    def test_the_first_key_makes_the_directory_private(self):
        directory = os.path.dirname(self.path)
        os.makedirs(directory)
        os.chmod(directory, 0o755)
        with self.assertLogs("mast.credentials", "INFO"):
            keys.FileKeyProvider(self.path).keys()
        self.assertEqual(stat.S_IMODE(os.stat(directory).st_mode), 0o700)

    def test_only_the_file_provider_is_known(self):
        with mock.patch("mast.jobs.config.credentials_key_provider",
                        return_value="vault"):
            with self.assertRaises(keys.KeysUnavailable):
                keys.provider()


class TestStore(CredentialsCase):

    def test_create_get_list_never_show_the_password(self):
        made = self.add(description="for the collector")
        self.assertEqual(made.username, "reader")
        self.assertEqual(made.purpose, "collect")
        self.assertEqual([(b.kind, b.name) for b in made.bindings],
                         [("environment", "lab")])
        self.assertEqual(made.created_by, "alice")
        self.assertEqual(store.get("ro"), made)
        self.assertEqual(store.list_credentials(), [made])
        for shown in (made, store.get("ro"), store.list_credentials()):
            text = json.dumps(
                [item.model_dump(mode="json") for item in shown]
                if isinstance(shown, list) else shown.model_dump(mode="json"))
            self.assertNotIn(PASSWORD, text)
            self.assertNotIn("token", text)
        with db.session() as session:
            record = session.scalars(select(CredentialRecord)).one()
            self.assertNotIn(PASSWORD, record.token)

    def test_refusals(self):
        self.add()
        cases = (
            (store.NameTaken, lambda: self.add()),
            (store.InvalidValue, lambda: self.add("a b")),
            (store.InvalidValue, lambda: self.add("x" * 65)),
            (store.InvalidValue, lambda: store.create(
                "x", "us:er", PASSWORD, "collect", principal=ADMIN)),
            (store.InvalidValue, lambda: store.create(
                "x", "", PASSWORD, "collect", principal=ADMIN)),
            (store.InvalidValue, lambda: self.add("x", password="")),
            (store.InvalidValue, lambda: self.add("x", purpose="read")),
            (store.InvalidValue, lambda: self.add("x", bindings=["nothing"])),
            (store.InvalidValue, lambda: self.add("x", bindings=["10.0.0.9"])),
            (store.UnknownCredential, lambda: store.get("nothing")),
            (store.UnknownCredential, lambda: store.replace_secret(
                "nothing", "x", principal=ADMIN)),
            (Forbidden, lambda: store.create(
                "x", "u", PASSWORD, "collect", principal=OPERATOR)),
            (Forbidden, lambda: store.delete_credential(
                "ro", principal=OPERATOR)),
            (Forbidden, lambda: store.rotate_key(principal=VIEWER)),
        )
        for error, change in cases:
            with self.subTest(error=error.__name__):
                with self.assertRaises(error):
                    change()
        self.assertEqual([c.name for c in store.list_credentials()], ["ro"])

    def test_replace_update_rebind_and_delete(self):
        self.add()
        store.record_test("ro", True, False, principal=ADMIN)
        replaced = store.replace_secret("ro", "new-pass", principal=ADMIN,
                                        username="reader2")
        self.assertEqual(replaced.username, "reader2")
        self.assertIsNone(replaced.last_tested_at)
        self.assertIsNone(replaced.can_write)
        self.assertEqual(self.open("ro", "dp1"), ("reader2", "new-pass"))
        changed = store.update("ro", principal=ADMIN, purpose="change",
                               description="now for changes")
        self.assertEqual((changed.purpose, changed.description),
                         ("change", "now for changes"))
        rebound = store.rebind("ro", ["dp3", "lab"], principal=ADMIN)
        self.assertEqual([(b.kind, b.name) for b in rebound.bindings],
                         [("appliance", "dp3"), ("environment", "lab")])
        self.assertTrue(store.bound_to("ro", "dp3"))
        self.assertTrue(store.bound_to("ro", "dp2"))
        store.rebind("ro", [], principal=ADMIN)
        self.assertFalse(store.bound_to("ro", "dp1"))
        store.delete_credential("ro", principal=ADMIN)
        self.assertEqual(store.list_credentials(), [])

    def test_bindings_go_with_their_appliance_or_environment(self):
        self.add(bindings=["dp3", "lab"])
        registry.remove("dp3")
        registry.env_remove("lab")
        self.assertEqual(store.get("ro").bindings, [])
        self.assertFalse(store.bound_to("ro", "dp1"))

    def test_list_for_appliances(self):
        self.add("lab-only", bindings=["lab"])
        self.add("dp3-only", bindings=["dp3"])
        self.add("both", bindings=["lab", "dp3"])
        names = lambda *a: [c.name for c in store.list_credentials(list(a))]
        self.assertEqual(names("dp1"), ["both", "lab-only"])
        self.assertEqual(names("lab", "dp3"), ["both"])
        self.assertEqual(names("dp3"), ["both", "dp3-only"])
        # An unregistered address or an unknown name has none
        self.assertEqual(names("10.0.0.1"), [])
        self.assertEqual(names("nothing"), [])

    def open(self, name, appliance, job_id="j" * 32):
        return store._open(name, appliance, job_id, "olga")

    def test_open_records_the_use(self):
        self.add()
        self.assertEqual(self.open("ro", "dp1"), ("reader", PASSWORD))
        found = store.uses("ro")
        self.assertEqual([(u.appliance, u.principal, u.purpose)
                          for u in found], [("dp1", "olga", "collect")])
        self.assertIsNotNone(store.get("ro").last_used_at)
        later = datetime.now(timezone.utc) + timedelta(hours=1)
        self.assertEqual(store.uses("ro", since=later), [])
        with self.assertRaises(Forbidden):
            self.open("ro", "dp3")
        with self.assertRaises(Forbidden):
            self.open("ro", "10.0.0.1")

    def test_a_swapped_token_is_refused(self):
        self.add("one", password="one-pass")
        self.add("two", password="two-pass")
        with db.session() as session:
            one, two = session.scalars(
                select(CredentialRecord).order_by(CredentialRecord.name))
            two.token = one.token
        with self.assertRaises(store.TokenMismatch) as found:
            self.open("two", "dp1")
        self.assertNotIn("one-pass", str(found.exception))
        self.assertEqual(self.open("one", "dp1"), ("reader", "one-pass"))
        # A token that the user name of the row does not match
        with db.session() as session:
            record = session.scalars(select(CredentialRecord).where(
                CredentialRecord.name == "one")).one()
            record.username = "someone-else"
        with self.assertRaises(store.TokenMismatch):
            self.open("one", "dp1")

    def test_a_token_of_another_key_is_refused(self):
        self.add()
        os.remove(self.key_file)
        with self.assertRaises(store.TokenMismatch):
            self.open("ro", "dp1")

    def test_rotate_key(self):
        self.add("one", password="one-pass")
        self.add("two", password="two-pass")
        with db.session() as session:
            before = {r.name: r.token for r in session.scalars(
                select(CredentialRecord))}
        old_keys = keyfile.read_keys(self.key_file)
        self.assertEqual(store.rotate_key(principal=ADMIN), 2)
        new_keys = keyfile.read_keys(self.key_file)
        self.assertEqual(new_keys[1:], old_keys)
        with db.session() as session:
            after = {r.name: r.token for r in session.scalars(
                select(CredentialRecord))}
        for name in ("one", "two"):
            self.assertNotEqual(before[name], after[name])
            # The new token opens with the new key alone
            Fernet(new_keys[0]).decrypt(after[name].encode())
        # An old token still opens after a rotation
        with db.session() as session:
            record = session.scalars(select(CredentialRecord).where(
                CredentialRecord.name == "one")).one()
            record.token = before["one"]
        self.assertEqual(self.open("one", "dp1"), ("reader", "one-pass"))
        # A second rotation drops the first key
        store.rotate_key(principal=ADMIN)
        self.assertEqual(len(keyfile.read_keys(self.key_file)), 2)
        self.assertNotIn(old_keys[0], keyfile.read_keys(self.key_file))
        self.assertEqual(self.open("one", "dp1"), ("reader", "one-pass"))
        self.assertEqual(self.open("two", "dp1"), ("reader", "two-pass"))

    def test_a_rotation_changes_nothing_if_a_token_does_not_open(self):
        self.add()
        with db.session() as session:
            session.scalars(select(CredentialRecord)).one().token = "broken"
        old_keys = keyfile.read_keys(self.key_file)
        with self.assertRaises(store.TokenMismatch):
            store.rotate_key(principal=ADMIN)
        self.assertEqual(keyfile.read_keys(self.key_file), old_keys)

    def test_the_audit_trail_has_every_change_and_no_secret(self):
        self.add()
        store.replace_secret("ro", "second-pass", principal=ADMIN)
        store.update("ro", principal=ADMIN, description="x")
        store.rebind("ro", ["dp1"], principal=ADMIN)
        store.record_test("ro", True, True, principal=ADMIN, appliance="dp1")
        store.rotate_key(principal=ADMIN)
        store.delete_credential("ro", principal=ADMIN)
        events = store.audit_events()
        self.assertEqual([e.action for e in reversed(events)], [
            "credential.create", "credential.replace", "credential.update",
            "credential.rebind", "credential.test", "credential.rotate-key",
            "credential.delete"])
        self.assertEqual({e.principal for e in events}, {"alice"})
        self.assertEqual(events[-1].detail["bindings"], ["environment:lab"])
        text = json.dumps([e.model_dump(mode="json") for e in events])
        self.assertNotIn(PASSWORD, text)
        self.assertNotIn("second-pass", text)
        self.assertEqual(len(store.audit_events("ro")), 6)


class TestStorePostgres(PostgresMixin, TestStore):
    pass


class TestUse(CredentialsCase):
    """The checks of the queue before a job with a stored credential is
    queued, and what the runner does with it."""

    def submit(self, command="credtest.look", appliances=("dp1",),
               principal=OPERATOR, **params):
        params.setdefault("credential", "ro")
        return jobs.submit(command, dict(appliances=list(appliances),
                                         **params), principal=principal)

    def assert_refused(self, message, **kwargs):
        before = len(jobs.list_jobs())
        with self.assertRaises(Forbidden) as found:
            self.submit(**kwargs)
        self.assertIn(message, str(found.exception))
        self.assertEqual(len(jobs.list_jobs()), before)

    def test_each_refusal(self):
        self.add()
        self.add("rw", purpose="change")
        registry.add("dp-open", "10.0.0.8", tls_mode="none")
        store.rebind("ro", ["lab", "dp-open"], principal=ADMIN)
        cases = (
            ("There is no stored credential", dict(credential="nothing")),
            ("not bound to the appliance dp3", dict(appliances=["dp3"])),
            ("10.0.0.1 is not a registered appliance",
             dict(appliances=["10.0.0.1"])),
            ("is not an appliance", dict(appliances=["bad!name"])),
            ("tls_mode none", dict(appliances=["dp-open"])),
            ("only by commands that only read",
             dict(command="credtest.change")),
            ("may not submit", dict(principal=VIEWER)),
            ("Only an admin may use it", dict(credential="rw")),
            ("Only an admin may use it",
             dict(credential="rw", command="credtest.change")),
        )
        for message, kwargs in cases:
            with self.subTest(message=message):
                self.assert_refused(message, **kwargs)

    def test_what_is_allowed(self):
        self.add()
        self.add("rw", purpose="change")
        self.submit(appliances=["lab"])
        self.submit(appliances=["dp1", "dp2"], principal=ADMIN)
        self.submit(credential="rw", command="credtest.change",
                    principal=ADMIN)
        self.assertEqual(len(jobs.list_jobs()), 3)

    def test_credentials_and_a_stored_credential_exclude_each_other(self):
        with self.assertRaises(ValidationError) as found:
            Look(appliances=["dp1"], credentials=["u:p"], credential="ro")
        self.assertIn("not both", str(found.exception))
        with self.assertRaises(ValidationError) as found:
            Look(appliances=["dp1"], credential="ro", no_check_hostname=True)
        self.assertIn("no_check_hostname", str(found.exception))

    def test_the_job_keeps_only_the_name(self):
        self.add()
        job_id = self.submit()
        self.assertEqual(self.raw_params(job_id)["credential"], "ro")
        self.assertNotIn(PASSWORD, json.dumps(self.raw_params(job_id)))
        self.assertNotIn("reader", json.dumps(self.raw_params(job_id)))
        self.assertEqual(jobs.get(job_id).params["credential"], "ro")

    def test_the_runner_opens_it_and_redacts_the_password(self):
        self.add()
        job_id = self.submit(appliances=["lab"])
        jobs.run_next("w1")
        self.assertEqual(jobs.get(job_id).status, jobs.SUCCEEDED)
        self.assertEqual(seen, ["reader:" + PASSWORD])
        found = jobs.result(job_id)
        stored = [found.text, found.html, found.history,
                  json.dumps(found.data)]
        stored += [e.message for e in jobs.events(job_id)]
        with db.session() as session:
            stored.append(json.dumps(session.get(Job, job_id).params))
            stored += [r.text + r.html + r.history for r in session.scalars(
                select(JobResult))]
            stored += [e.message for e in session.scalars(select(JobEvent))]
        everything = "\n".join(stored)
        self.assertNotIn(PASSWORD, everything)
        self.assertNotIn(html.escape(PASSWORD), everything)
        self.assertNotIn(base64.b64encode(PASSWORD.encode()).decode(),
                         everything)
        self.assertIn("********", found.text)
        # One use for each appliance of the environment
        self.assertEqual(sorted(u.appliance for u in store.uses("ro")),
                         ["dp1", "dp2"])
        self.assertEqual({u.job_id for u in store.uses("ro")}, {job_id})
        self.assertEqual({u.principal for u in store.uses("ro")}, {"olga"})

    def test_a_job_with_it_checks_every_connection(self):
        # mast.datapower.datapower.tls refuses unchecked connections
        # inside stored_credential
        self.add()
        self.submit()
        jobs.run_next("w1")
        self.assertEqual((strict, names), ([True], ["ro"]))
        jobs.submit("credtest.look", {"appliances": ["dp1"],
                                      "credentials": ["u:p"]})
        jobs.run_next("w1")
        self.assertEqual((strict, names), ([False], [None]))

    def test_the_runner_checks_the_binding_again(self):
        self.add()
        job_id = self.submit()
        store.rebind("ro", ["dp3"], principal=ADMIN)
        jobs.run_next("w1")
        self.assertEqual(jobs.get(job_id).status, jobs.FAILED)
        self.assertIn("not bound to the appliance dp1", jobs.get(job_id).error)
        self.assertEqual(seen, [])
        self.assertEqual(store.uses("ro"), [])

    def test_a_deleted_credential_fails_the_job(self):
        self.add()
        job_id = self.submit()
        store.delete_credential("ro", principal=ADMIN)
        jobs.run_next("w1")
        self.assertEqual(jobs.get(job_id).status, jobs.FAILED)
        self.assertIn("There is no stored credential", jobs.get(job_id).error)

    def test_a_command_without_the_option_is_not_affected(self):
        # The commands of mast.testsuite.unit.test_mast_jobs have no
        # stored credential
        job_id = jobs.submit("test.work", {"appliances": ["dp1"],
                                           "credentials": ["u:p"]})
        jobs.run_next("w1")
        self.assertEqual(jobs.get(job_id).status, jobs.SUCCEEDED)
        self.assertEqual(jobs.get(job_id).status, jobs.SUCCEEDED)


class TestUsePostgres(PostgresMixin, TestUse):
    pass


class TestTesting(CredentialsCase):

    def finish(self, job_id, status, rows):
        claimed = jobs.claim("w1", job_id=job_id)
        self.assertEqual(claimed, job_id)
        data = {"kind": "table",
                "columns": ["appliance", "works", "firmware", "can change",
                            "problem"], "rows": rows}
        queue.finish(job_id, "w1", status, text="t", html="h", data=data)

    def test_record_reads_the_outcome_of_the_job(self):
        self.add()
        job_id = jobs.submit(TEST_COMMAND, testing.params("ro", "dp1"),
                             principal=ADMIN)
        with self.assertRaises(testing.NotATest):
            testing.record("ro", job_id, principal=ADMIN)
        self.finish(job_id, jobs.SUCCEEDED,
                    [["dp1", "yes", "IDG.11.0.0.3", "yes", ""]])
        with self.assertRaises(testing.NotATest):
            testing.record("other", job_id, principal=ADMIN)
        with self.assertRaises(Forbidden):
            testing.record("ro", job_id, principal=OPERATOR)
        outcome = testing.record("ro", job_id, principal=ADMIN)
        self.assertTrue(outcome.ok)
        self.assertTrue(outcome.can_write)
        self.assertEqual(outcome.firmware, "IDG.11.0.0.3")
        found = store.get("ro")
        self.assertTrue(found.last_test_ok)
        self.assertTrue(found.can_write)
        self.assertIsNotNone(found.last_tested_at)

    def test_a_failed_test(self):
        self.add()
        job_id = jobs.submit(TEST_COMMAND, testing.params("ro", "dp1"),
                             principal=ADMIN)
        self.finish(job_id, jobs.FAILED, [["dp1", "no", "", "", "refused"]])
        outcome = testing.record("ro", job_id, principal=ADMIN)
        self.assertFalse(outcome.ok)
        self.assertIsNone(outcome.can_write)
        self.assertEqual(outcome.problem, "refused")
        self.assertFalse(store.get("ro").last_test_ok)
        self.assertIsNone(store.get("ro").can_write)

    def test_the_test_command_only_reads(self):
        command = commands.resolve(TEST_COMMAND)
        self.assertFalse(command.exclusive)
        self.assertIn("credential",
                      [spec.name for spec in __import__(
                          "mast.cli.params", fromlist=["options"]).options(
                              command.model)])


class TestCheckCredentialsCommand(unittest.TestCase):
    """The rows of system.check-credentials, with a fake appliance."""

    def appliance(self, version="IDG.11.0.0.3", ping="OK", fail=None):
        from lxml import etree
        appliance = mock.Mock(hostname="dp1")
        xml = etree.fromstring(
            "<r><Version>{}</Version></r>".format(version) if version
            else "<r/>")
        if fail:
            appliance.get_status.side_effect = fail
        appliance.get_status.return_value = mock.Mock(xml=xml)
        appliance.do_action.return_value = mock.Mock(text=ping)
        return appliance

    def test_rows(self):
        from mast.datapower.system.system import _check_credentials
        self.assertEqual(_check_credentials(self.appliance()),
                         ["dp1", "yes", "IDG.11.0.0.3", "yes", ""])
        denied = "<dp:log-event level='error'>Permission Denied.</dp:log-event>"
        self.assertEqual(_check_credentials(self.appliance(ping=denied)),
                         ["dp1", "yes", "IDG.11.0.0.3", "no", ""])
        row = _check_credentials(self.appliance(version=None))
        self.assertEqual(row[1], "no")
        self.assertIn("change its password", row[4])
        row = _check_credentials(self.appliance(fail=OSError("refused")))
        self.assertEqual(row[1:4], ["no", "", ""])
        self.assertIn("refused", row[4])
        ping = self.appliance()
        _check_credentials(ping)
        ping.do_action.assert_called_once_with("Ping", RemoteHost="127.0.0.1")


class TestMastCredentials(CredentialsCase):

    def run_cli(self, *argv, stdin=""):
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch("sys.stdin", io.StringIO(stdin)):
            try:
                credentials_cli.run(list(argv))
            except SystemExit as exc:
                code = exc.code or 0
        return code, out.getvalue(), err.getvalue()

    def test_add_list_show_and_delete(self):
        code, out, err = self.run_cli(
            "add", "-n", "ro", "-u", "reader", "-p", "collect", "-b", "lab",
            "-b", "dp3", "--password-stdin", stdin=PASSWORD + "\n")
        self.assertEqual(code, 0, err)
        self.assertIn("bound to: env:lab dp3", out)
        self.assertEqual(self.open(), ("reader", PASSWORD))
        for argv, shown in ((("list",), "ro"), (("show", "-n", "ro"), "ro"),
                            (("audit",), "credential:ro"),
                            (("uses", "-n", "ro"), "os:test"),
                            (("list", "-a", "dp1"), "ro")):
            with self.subTest(argv=argv):
                code, out, err = self.run_cli(*argv)
                self.assertEqual(code, 0, err)
                self.assertIn(shown, out)
                self.assertNotIn(PASSWORD, out + err)
        code, out, err = self.run_cli("delete", "-n", "ro", "-y")
        self.assertEqual(code, 0, err)
        self.assertEqual(store.list_credentials(), [])

    def open(self):
        return store._open("ro", "dp1", "j" * 32, "os:test")

    def test_the_password_is_asked_twice(self):
        with mock.patch("getpass.getpass", side_effect=["one", "two"]):
            code, out, err = self.run_cli(
                "add", "-n", "ro", "-u", "reader", "-p", "collect")
        self.assertEqual(code, 1)
        self.assertIn("not the same", err)
        with mock.patch("getpass.getpass", side_effect=[PASSWORD, PASSWORD]):
            code, out, err = self.run_cli(
                "add", "-n", "ro", "-u", "reader", "-p", "collect", "-b",
                "dp1")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.open(), ("reader", PASSWORD))

    def test_replace_rebind_update_and_rotate(self):
        self.add(bindings=["dp1"])
        code, _, err = self.run_cli("replace", "-n", "ro", "--password-stdin",
                                    stdin="pass-two\n")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.open(), ("reader", "pass-two"))
        code, out, err = self.run_cli("rebind", "-n", "ro", "-b", "lab")
        self.assertEqual(code, 0, err)
        self.assertIn("env:lab", out)
        code, out, err = self.run_cli("update", "-n", "ro", "-p", "change")
        self.assertIn("(change, user reader)", out)
        code, out, err = self.run_cli("rotate-key", "-y")
        self.assertEqual(code, 0, err)
        self.assertEqual(len(keyfile.read_keys(self.key_file)), 2)
        self.assertEqual(self.open(), ("reader", "pass-two"))

    def test_errors_are_short(self):
        code, out, err = self.run_cli(
            "add", "-n", "ro", "-u", "reader", "-p", "collect", "-b",
            "nothing", "--password-stdin", stdin=PASSWORD + "\n")
        self.assertEqual(code, 1)
        self.assertIn("mast-credentials: 'nothing' is not a registered", err)
        code, out, err = self.run_cli("show", "-n", "nothing")
        self.assertEqual(code, 1)
        self.assertIn("There is no stored credential nothing", err)

    def test_test_records_the_outcome(self):
        self.add()
        rows = [["dp1", "yes", "IDG.11.0.0.3", "yes", ""]]

        def check(params):
            from mast.datapower.system.system import CredentialChecks
            seen[:] = params.credentials
            return CredentialChecks(rows)

        command = commands.resolve(TEST_COMMAND)
        with mock.patch.object(command, "fn", check):
            code, out, err = self.run_cli("test", "-n", "ro", "-a", "dp1")
        self.assertEqual(code, 0, err)
        self.assertEqual(seen, ["reader:" + PASSWORD])
        self.assertIn("works on dp1", out)
        self.assertIn("WARNING", out)
        self.assertTrue(store.get("ro").last_test_ok)
        self.assertTrue(store.get("ro").can_write)
        self.assertNotIn(PASSWORD, out + err)
        # A refused test is not queued
        code, out, err = self.run_cli("test", "-n", "ro", "-a", "dp3")
        self.assertEqual(code, 1)
        self.assertIn("not bound to the appliance dp3", err)


class TestCommandLineRefusal(CredentialsCase):

    def test_a_refused_job_is_a_short_error(self):
        self.add()
        cli = commands.cli_for("credtest")
        err = io.StringIO()
        with contextlib.redirect_stderr(err), \
                self.assertRaises(SystemExit) as found:
            cli.run(["change", "-a", "dp1", "--credential", "ro"])
        self.assertEqual(found.exception.code, 1)
        self.assertIn("error: The credential ro is for collecting",
                      err.getvalue())
        self.assertNotIn("Traceback", err.getvalue())
        self.assertEqual(jobs.list_jobs(), [])


class TestCredentialsApi(WebCase):

    def setUp(self):
        super().setUp()
        self.key_file = os.path.join(tempfile.mkdtemp(dir=self.tmp), "k")
        patcher = mock.patch("mast.jobs.config.credentials_key_file",
                             return_value=self.key_file)
        patcher.start()
        self.addCleanup(patcher.stop)
        commands.register("credtest", make_cli())
        self.addCleanup(commands.unregister, "credtest")
        registry.add("dp1", "10.0.0.1")
        registry.add("dp3", "10.0.0.3")
        registry.env_add("lab", members=["dp1"])

    def call(self, method, path, body=None, client=None):
        client = client or self.client
        return client.open("/api/v1" + path, method=method, json=body,
                           headers={"X-CSRF-Token": self.token(client)})

    def add(self):
        return self.call("POST", "/credentials", {
            "name": "ro", "username": "reader", "password": PASSWORD,
            "purpose": "collect", "bindings": ["lab"],
            "description": "for reading"})

    def test_an_admin_manages_credentials_and_never_sees_a_password(self):
        self.login("admin")
        answers = [self.add()]
        self.assertEqual(answers[0].status_code, 201)
        self.assertEqual(self.add().status_code, 409)
        answers.append(self.call("GET", "/credentials"))
        answers.append(self.call("PATCH", "/credentials/ro", {
            "password": "pass-two", "bindings": ["lab", "dp3"],
            "description": "changed"}))
        self.assertEqual(answers[-1].get_json()["description"], "changed")
        self.assertEqual(len(answers[-1].get_json()["bindings"]), 2)
        answers.append(self.call("GET", "/credentials/ro/uses"))
        answers.append(self.call("GET", "/credentials/ro/audit"))
        answers.append(self.call("GET", "/credentials?appliances=dp1"))
        for answer in answers:
            self.assertLess(answer.status_code, 300)
            body = answer.get_data(as_text=True)
            self.assertNotIn(PASSWORD, body)
            self.assertNotIn("pass-two", body)
            self.assertNotIn("token", body)
        self.assertEqual(store._open("ro", "dp3", "j" * 32, "x"),
                         ("reader", "pass-two"))
        self.assertEqual(self.call("PATCH", "/credentials/ro", {
            "purpose": "nothing"}).status_code, 422)
        self.assertEqual(self.call("POST", "/credentials", {
            "name": "x", "username": "u", "password": "p",
            "purpose": "collect", "bindings": ["nothing"]}).status_code, 422)
        self.assertEqual(self.call("DELETE", "/credentials/ro").status_code,
                         204)
        self.assertEqual(self.call("DELETE", "/credentials/ro").status_code,
                         404)

    def test_an_operator_lists_names_but_cannot_manage(self):
        self.login("admin")
        self.add()
        operator = self.app.test_client()
        self.login("operator", client=operator)
        found = self.call("GET", "/credentials?appliances=dp1",
                          client=operator).get_json()["credentials"]
        self.assertEqual([c["name"] for c in found], ["ro"])
        self.assertEqual(self.call("GET", "/credentials?appliances=dp3",
                                   client=operator).get_json(),
                         {"credentials": []})
        for method, path in (("POST", "/credentials"),
                             ("PATCH", "/credentials/ro"),
                             ("DELETE", "/credentials/ro"),
                             ("POST", "/credentials/ro/tests"),
                             ("GET", "/credentials/ro/uses"),
                             ("GET", "/credentials/ro/audit")):
            with self.subTest(method=method, path=path):
                self.assertEqual(self.call(method, path, {},
                                           client=operator).status_code, 403)
        self.assertEqual(operator.get("/admin/credentials").status_code, 403)

    def test_a_viewer_cannot_list(self):
        self.login("viewer")
        self.assertEqual(self.call("GET", "/credentials").status_code, 403)

    def test_an_operator_runs_a_job_with_it(self):
        self.login("admin")
        self.add()
        operator = self.app.test_client()
        self.login("operator", client=operator)
        answer = self.call("POST", "/jobs", {
            "command": "credtest.look",
            "params": {"appliances": ["dp1"], "credential": "ro"}},
            client=operator)
        self.assertEqual(answer.status_code, 201, answer.get_data())
        job_id = answer.get_json()["job_id"]
        self.assertEqual(jobs.get(job_id).params["credential"], "ro")
        refused = self.call("POST", "/jobs", {
            "command": "credtest.change",
            "params": {"appliances": ["dp1"], "credential": "ro"}},
            client=operator)
        self.assertEqual(refused.status_code, 403)
        self.assertIn("only read", refused.get_json()["error"])
        both = self.call("POST", "/jobs", {
            "command": "credtest.look",
            "params": {"appliances": ["dp1"], "credential": "ro",
                       "credentials": ["u:p"]}}, client=operator)
        self.assertEqual(both.status_code, 422)
        # Wait for the job, which this process runs: no worker is alive
        for _ in range(100):
            if jobs.get(job_id).finished:
                break
            time.sleep(0.05)
        self.assertEqual(jobs.get(job_id).status, jobs.SUCCEEDED)
        result = self.call("GET", "/jobs/{}/result".format(job_id),
                           client=operator).get_data(as_text=True)
        self.assertNotIn(PASSWORD, result)

    def test_a_test_through_the_api(self):
        self.login("admin")
        self.add()
        command = commands.resolve(TEST_COMMAND)

        def check(params):
            from mast.datapower.system.system import CredentialChecks
            return CredentialChecks([["dp1", "yes", "IDG.11", "no", ""]])

        with mock.patch.object(command, "fn", check):
            self.assertEqual(self.call("POST", "/credentials/ro/tests", {})
                             .status_code, 422)
            answer = self.call("POST", "/credentials/ro/tests",
                               {"appliance": "dp1"})
            self.assertEqual(answer.status_code, 201)
            job_id = answer.get_json()["job_id"]
            for _ in range(100):
                if jobs.get(job_id).finished:
                    break
                time.sleep(0.05)
        recorded = self.call("POST", "/credentials/ro/tests/" + job_id)
        self.assertEqual(recorded.status_code, 200, recorded.get_data())
        self.assertEqual(recorded.get_json()["outcome"]["can_write"], False)
        self.assertTrue(recorded.get_json()["credential"]["last_test_ok"])
        other = jobs.submit("credtest.look", {"appliances": ["dp1"]})
        self.assertEqual(self.call("POST", "/credentials/ro/tests/" + other)
                         .status_code, 409)

    def test_choices_from_the_appliances_with_a_stored_credential(self):
        from mast.datapower.system.system import OBJECTS_HEADER
        from mast.plugin_utils import choices
        self.login("admin")
        self.add()
        operator = self.app.test_client()
        self.login("operator", client=operator)
        command = commands.resolve(choices.LOOKUP_COMMAND)
        got = []

        def list_objects(params):
            got.append((params.ObjectClass, list(params.appliances),
                        list(params.credentials)))
            return Table(OBJECTS_HEADER, [["app", "no", "dp1"],
                                          ["default", "yes", "dp1"]])

        def ask(appliances, credential="ro"):
            return self.call("POST", "/choices/configured:Domain", {
                "appliances": appliances, "credential": credential},
                client=operator)

        with mock.patch.object(command, "fn", list_objects):
            answer = ask(["dp1"])
            self.assertEqual(answer.status_code, 200, answer.get_data())
            self.assertEqual(answer.get_json()["choices"], ["default"])
            # The queue checked the credential, the command got it, and
            # the use is recorded as a passive job of the operator
            self.assertEqual(got, [("Domain", ["dp1"],
                                    ["reader:" + PASSWORD])])
            job = jobs.get(answer.get_json()["job_id"])
            self.assertEqual((job.command, job.submitted_by, job.passive),
                             (choices.LOOKUP_COMMAND, "operator", True))
            self.assertEqual([use.job_id for use in store.uses("ro")],
                             [job.id])
            self.assertNotIn(PASSWORD, answer.get_data(as_text=True))
            # The refusals of the queue: an appliance that it is not
            # bound to, a credential that does not exist
            refused = ask(["dp3"])
            self.assertEqual(refused.status_code, 403)
            self.assertEqual(ask(["dp1"], credential="nothing").status_code, 403)
            self.assertEqual(len(got), 1)
        # A lookup that fails says why
        with mock.patch.object(command, "fn", side_effect=RuntimeError("down")):
            failed = ask(["dp1"])
        self.assertEqual(failed.status_code, 502)
        self.assertIn("down", failed.get_json()["error"])
        # A viewer may not ask
        viewer = self.app.test_client()
        self.login("viewer", client=viewer)
        self.assertEqual(self.call("POST", "/choices/configured:Domain", {
            "appliances": ["dp1"], "credential": "ro"}, client=viewer)
            .status_code, 403)

    def test_the_admin_page(self):
        self.login("admin")
        page = self.client.get("/admin/credentials")
        self.assertEqual(page.status_code, 200)
        text = page.get_data(as_text=True)
        self.assertIn("admin-credentials.js", text)
        self.assertIn('href="/admin/credentials"', text)

    def test_changes_are_logged_without_the_password(self):
        self.login("admin")
        with self.assertLogs("mast.credentials", "INFO") as logs:
            self.add()
            self.call("PATCH", "/credentials/ro", {"password": "pass-two"})
        self.assertIn("Stored the credential ro (by admin", logs.output[0])
        self.assertNotIn(PASSWORD, "\n".join(logs.output))
        self.assertNotIn("pass-two", "\n".join(logs.output))


class TestMigration(CredentialsCase):
    """0005 goes down with credentials in the database, and up again."""

    def raw(self):
        # An engine that does not upgrade the tables, as db.engine() does
        from sqlalchemy import inspect, text
        engine = db._create_engine(self.url)
        self.addCleanup(engine.dispose)
        with engine.connect() as connection:
            tables = set(inspect(connection).get_table_names())
            counts = {table: connection.execute(text(
                "SELECT count(*) FROM {}".format(table))).scalar()
                for table in ("jobs", "appliances") if table in tables}
        return tables, counts

    def test_down_and_up(self):
        self.add()
        jobs.submit("credtest.look", {"appliances": ["dp1"],
                                      "credential": "ro"}, principal=ADMIN)
        jobs.run_next("w1")
        self.assertEqual(len(store.uses("ro")), 1)
        new = {"credentials", "credential_bindings", "credential_uses",
               "audit_events"}
        self.assertLessEqual(new, self.raw()[0])
        db.downgrade("0004")
        self.assertEqual(db.revision(), "0004")
        tables, counts = self.raw()
        self.assertFalse(new & tables)
        # The registry and the jobs stay
        self.assertEqual(counts, {"jobs": 1, "appliances": 3})
        # A newer MAST upgrades the tables again when it opens them
        db.configure(self.url)
        self.assertEqual(store.list_credentials(), [])
        self.assertEqual(db.revision(), db.HEAD)
        self.add()
        db.downgrade("0001")
        self.assertEqual(db.revision(), "0001")
        tables, counts = self.raw()
        self.assertEqual(counts, {"jobs": 1})


class TestMigrationPostgres(PostgresMixin, TestMigration):
    pass

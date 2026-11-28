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
"""Unit tests for the identities of MAST 4.0 step 6: tokens
(mast.auth.tokens), service accounts, who the command line acts for
(mast.auth.identity, mast --as), mast login, logout and whoami, mast
users add-service and the tokens, and tokens on the API."""
import contextlib
import io
import os
import stat
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from mast import main as mast_main
from mast import permissions
from mast.auth import identity, session, tokens, users
from mast.auth.models import ApiToken
from mast.jobs import auth, db
from mast.testsuite.unit.test_mast_auth import fast_hashes
from mast.testsuite.unit.test_mast_jobs import PostgresMixin, SqliteCase
from mast.testsuite.unit.web_helpers import PASSWORD, WebCase


def _private(case):
    """Keep the identity of the command line to the test: its own config
    folder, no token in the environment, and nothing cached."""
    folder = tempfile.mkdtemp(prefix="mast-identity-")
    patcher = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": folder,
                                           "APPDATA": folder})
    patcher.start()
    case.addCleanup(patcher.stop)
    for name in ("MAST_TOKEN", "MAST_TOKEN_FILE"):
        if name in os.environ:
            value = os.environ.pop(name)
            case.addCleanup(os.environ.__setitem__, name, value)
    identity.reset()
    case.addCleanup(identity.reset)
    home = tempfile.mkdtemp(prefix="mast-identity-home-")
    patcher = mock.patch.object(identity, "_home", lambda: home)
    patcher.start()
    case.addCleanup(patcher.stop)
    return home


def _quiet(function, *args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            status = function(*args)
        except SystemExit as exc:
            status = exc.code
    return status or 0, out.getvalue(), err.getvalue()


class IdentityCase(SqliteCase):

    def setUp(self):
        super().setUp()
        fast_hashes(self)
        self.home = _private(self)
        users.add("alice", PASSWORD, "operator")
        users.set_password("alice", PASSWORD, False)


class TestTokens(IdentityCase):

    def test_a_token_works_until_it_is_revoked(self):
        info, token = tokens.issue("alice", "laptop", principal="alice")
        self.assertTrue(token.startswith("mast_{}_".format(info.id)))
        self.assertIsNone(info.last_used_at)
        self.assertEqual(tokens.authenticate(token).name, "alice")
        found, = tokens.list_tokens("alice")
        self.assertIsNotNone(found.last_used_at)
        self.assertTrue(found.active)
        # Only a hash of the secret is stored
        with db.session(write=False) as s:
            stored = s.get(ApiToken, info.id).secret_hash
        self.assertNotIn(stored, token)
        self.assertNotIn(token.split("_", 2)[2], repr(found.json()))
        tokens.revoke(info.id)
        with self.assertRaisesRegex(tokens.BadToken, "revoked"):
            tokens.authenticate(token)

    def test_tokens_that_do_not_work(self):
        info, token = tokens.issue("alice", "x")
        for bad in ("", "nonsense", token + "x", token[:-1] + (
                "A" if token[-1] != "A" else "B"),
                "mast_999_" + token.split("_", 2)[2]):
            with self.subTest(bad=bad):
                with self.assertRaises(tokens.BadToken):
                    tokens.authenticate(bad)
        # Expired
        _, old = tokens.issue("alice", "x", hours=1)
        with db.session() as s:
            s.get(ApiToken, int(old.split("_")[1])).expires_at = \
                datetime.now(timezone.utc) - timedelta(minutes=1)
        with self.assertRaisesRegex(tokens.BadToken, "expired"):
            tokens.authenticate(old)
        # A disabled user, and a deleted one
        users.disable("alice")
        with self.assertRaisesRegex(tokens.BadToken, "disabled"):
            tokens.authenticate(token)
        users.enable("alice")
        self.assertEqual(tokens.authenticate(token).name, "alice")
        users.delete("alice")
        with self.assertRaises(tokens.BadToken):
            tokens.authenticate(token)
        self.assertEqual(tokens.list_tokens(), [])

    def test_revoke_all(self):
        tokens.issue("alice", "a")
        _, token = tokens.issue("alice", "b")
        self.assertEqual(tokens.revoke_all("alice"), 2)
        with self.assertRaises(tokens.BadToken):
            tokens.authenticate(token)


class TestTokensPostgres(PostgresMixin, TestTokens):
    pass


class TestServiceAccounts(IdentityCase):

    def test_it_has_no_password(self):
        found = users.add_service("nightly", "viewer")
        self.assertEqual((found.kind, found.must_change), ("service", False))
        self.assertEqual(users.get("alice").kind, "person")
        with self.assertRaises(users.LoginFailed) as caught:
            users.authenticate("nightly", "anything at all")
        self.assertEqual(caught.exception.reason, "service")
        with self.assertRaises(users.UserExists):
            users.add_service("alice", "viewer")
        # It is in the groups of its role, as every user
        self.assertEqual(permissions.groups_of("nightly"), ["viewer"])


class TestCliIdentity(IdentityCase):

    def test_without_a_token_it_is_the_account_of_the_os(self):
        self.assertEqual(auth.cli_principal(), auth.os_principal())

    def test_the_order_of_the_tokens(self):
        _, mine = tokens.issue("alice", "login")
        users.add_service("nightly", "viewer")
        _, service = tokens.issue("nightly", "file")
        users.add_service("script", "admin")
        _, script = tokens.issue("script", "env")
        identity.write_token(identity.login_file(), mine)
        self.assertEqual(auth.cli_principal(),
                         auth.Principal("alice", "operator"))
        path = os.path.join(self.home, "service.token")
        identity.write_token(path, service)
        with mock.patch.dict(os.environ, {"MAST_TOKEN_FILE": path}):
            identity.reset()
            self.assertEqual(auth.cli_principal().name, "nightly")
            with mock.patch.dict(os.environ, {"MAST_TOKEN": script}):
                identity.reset()
                self.assertEqual(auth.cli_principal().name, "script")

    def test_a_token_that_does_not_work_is_an_error(self):
        info, token = tokens.issue("alice", "login")
        identity.write_token(identity.login_file(), token)
        tokens.revoke(info.id)
        # Never the account of the operating system instead
        with self.assertRaisesRegex(identity.IdentityError,
                                    "revoked.*mast login"):
            auth.cli_principal()
        with mock.patch.dict(os.environ, {"MAST_TOKEN_FILE": os.path.join(
                self.home, "none")}):
            identity.reset()
            with self.assertRaisesRegex(identity.IdentityError,
                                        "Cannot read"):
                auth.cli_principal()

    @unittest.skipIf(sys.platform == "win32", "POSIX modes")
    def test_a_token_file_is_only_for_its_owner(self):
        path = identity.service_token_file("nightly")
        identity.write_token(path, "mast_1_" + "x" * 30)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(path))
                                      .st_mode), 0o700)

    def test_mast_as_a_service_account(self):
        users.add_service("nightly", "viewer")
        _, token = tokens.issue("nightly", "file")
        identity.write_token(identity.service_token_file("nightly"), token)
        with mock.patch.dict(os.environ, {}):
            status, out, err = _quiet(mast_main.main,
                                      ["--as", "nightly", "whoami"])
            self.assertEqual(status, 0, err)
            self.assertIn("nightly (viewer), with the token of", out)
        identity.reset()
        with mock.patch.dict(os.environ, {}):
            status, _, err = _quiet(mast_main.main,
                                    ["--as=nobody", "whoami"])
        self.assertEqual(status, 2)
        self.assertIn("no token file of the service account nobody", err)
        # A token that stops working is a short error of mast
        tokens.revoke_all("nightly")
        identity.reset()
        with mock.patch.dict(os.environ, {}):
            status, _, err = _quiet(mast_main.main,
                                    ["--as", "nightly", "jobs", "list"])
        self.assertEqual(status, 2)
        self.assertIn("was revoked", err)
        self.assertNotIn("Traceback", err)


class TestLogin(IdentityCase):

    def login(self, *args, password=PASSWORD):
        with mock.patch.object(sys, "stdin", io.StringIO(password + "\n")):
            return _quiet(session.login, list(args) + ["--password-stdin"])

    def test_login_whoami_and_logout(self):
        status, out, err = self.login("-u", "alice")
        self.assertEqual(status, 0, err)
        self.assertIn("Logged in as alice until", out)
        found, = tokens.list_tokens("alice")
        self.assertIsNotNone(found.expires_at)
        self.assertLessEqual(found.expires_at - found.created_at,
                             timedelta(hours=12))
        self.assertEqual(auth.cli_principal().name, "alice")
        _, out, _ = _quiet(session.whoami, [])
        self.assertIn("alice (operator), with the token of", out)
        status, out, _ = _quiet(session.logout, [])
        self.assertIn("Logged out", out)
        self.assertFalse(os.path.exists(identity.login_file()))
        self.assertFalse(tokens.list_tokens("alice")[0].active)
        self.assertEqual(auth.cli_principal(), auth.os_principal())
        _, out, _ = _quiet(session.whoami, [])
        self.assertIn("no token", out)

    def test_who_cannot_log_in(self):
        status, _, wrong = self.login("-u", "alice", password="wrong")
        self.assertEqual(status, 1)
        self.assertIn("not correct", wrong)
        users.add_service("nightly", "viewer")
        status, _, err = self.login("-u", "nightly", password="x")
        # The same answer: it does not tell what nightly is
        self.assertEqual((status, err), (1, wrong))
        users.add("bob", PASSWORD, "viewer")
        status, _, err = self.login("-u", "bob")
        self.assertEqual(status, 1)
        self.assertIn("new password first", err)
        self.assertFalse(os.path.exists(identity.login_file()))


class TestMastUsersService(IdentityCase):

    def run_cli(self, *args):
        from mast.auth.cli import cli
        return _quiet(cli.run, list(args))

    def test_add_service_rotate_and_revoke(self):
        status, out, err = self.run_cli(
            "add-service", "-u", "nightly", "-r", "viewer",
            "-g", "datapower-reader")
        self.assertEqual(status, 0, err)
        path = identity.service_token_file("nightly")
        self.assertIn(path, out)
        self.assertIn("mast --as nightly", out)
        with open(path) as fin:
            first = fin.read().strip()
        self.assertNotIn(first, out)
        self.assertEqual(tokens.authenticate(first).name, "nightly")
        self.assertIn("nightly", permissions.get_group(
            "datapower-reader").members)
        status, out, _ = self.run_cli("list")
        self.assertIn("service", out)
        status, out, _ = self.run_cli("rotate-token", "-u", "nightly")
        self.assertIn("Revoked 1 token(s)", out)
        with self.assertRaises(tokens.BadToken):
            tokens.authenticate(first)
        with open(path) as fin:
            second = fin.read().strip()
        status, out, _ = self.run_cli("tokens")
        self.assertIn("service account nightly", out)
        self.assertNotIn(second.split("_", 2)[2], out)
        token_id = int(second.split("_")[1])
        self.run_cli("revoke-token", "-i", str(token_id))
        with self.assertRaises(tokens.BadToken):
            tokens.authenticate(second)
        status, out, _ = self.run_cli("tokens", "-a")
        self.assertIn("revoked", out)

    def test_print_the_token_and_people(self):
        status, out, _ = self.run_cli("add-service", "-u", "remote", "-r",
                                      "viewer", "--print-token")
        token = out.strip().splitlines()[-1]
        self.assertEqual(tokens.authenticate(token).name, "remote")
        self.assertFalse(os.path.exists(identity.service_token_file(
            "remote")))
        status, _, err = self.run_cli("rotate-token", "-u", "alice")
        self.assertEqual(status, 1)
        self.assertIn("not a service account", err)
        status, _, err = self.run_cli("add-service", "-u", "x", "-r",
                                      "viewer", "-g", "no-such-group")
        self.assertEqual(status, 1)


class TestApiTokens(WebCase):

    def setUp(self):
        super().setUp()
        _private(self)

    def test_a_script_uses_a_token(self):
        _, token = tokens.issue("admin", "script")
        headers = {"Authorization": "Bearer " + token}
        answer = self.client.get("/api/v1/groups", headers=headers)
        self.assertEqual(answer.status_code, 200)
        # A change needs no CSRF token: there is no cookie to abuse
        answer = self.client.post("/api/v1/groups", headers=headers,
                                  json={"name": "scripted"})
        self.assertEqual(answer.status_code, 201, answer.get_json())
        # A viewer's token is a viewer
        _, viewer = tokens.issue("viewer", "script")
        answer = self.client.get("/api/v1/groups", headers={
            "Authorization": "Bearer " + viewer})
        self.assertEqual(answer.status_code, 403)
        tokens.revoke_all("admin")
        answer = self.client.get("/api/v1/groups", headers=headers)
        self.assertEqual(answer.status_code, 401)
        self.assertIn("revoked", answer.get_json()["error"])

    def test_pages_take_no_token(self):
        _, token = tokens.issue("admin", "script")
        answer = self.client.get("/jobs", headers={
            "Authorization": "Bearer " + token})
        self.assertEqual(answer.status_code, 302)

    def test_a_service_account_cannot_log_in(self):
        users.add_service("nightly", "admin")
        answer = self.login("nightly", "anything")
        self.assertNotEqual(getattr(answer, "status_code", None), 200)
        self.assertEqual(self.client.get("/api/v1/groups").status_code, 401)


class TestMigration(SqliteCase):
    """4005 adds the kind of each user and the tokens, and refuses to go
    down while there is a service account."""

    def test_down_and_up(self):
        fast_hashes(self)
        users.add("vera", PASSWORD, "viewer")
        tokens.issue("vera", "x")
        users.add_service("nightly", "viewer")
        with self.assertRaisesRegex(RuntimeError, "nightly"):
            db.downgrade("4004")
        db.configure(self.url)
        users.delete("nightly")
        db.downgrade("4004")
        self.assertEqual(db.revision(), "4004")
        db.configure(self.url)
        self.assertEqual(users.get("vera").kind, "person")
        self.assertEqual(db.revision(), db.HEAD)
        self.assertEqual(tokens.list_tokens(), [])


class TestMigrationPostgres(PostgresMixin, TestMigration):
    pass


if __name__ == "__main__":
    unittest.main()

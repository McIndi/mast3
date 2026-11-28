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
_module_: `mast.credentials.cli`

`mast credentials`: the stored credentials (see `mast.credentials`).

* `add`, `replace`, `update`, `rebind`, `delete`: Store a credential,
give it a new password, change its description or purpose, change the
appliances and environments that it may be used for, delete it.
* `list`, `show`: The credentials, never with a password.
* `test`: Run a job with the credential on one appliance, and store
whether it works and whether it can change the appliance.
* `rotate-key`: A new key for the credential store.
* `uses`, `audit`: Which jobs used a credential; what admins changed.

The password is asked for (it is not shown as it is typed), or read
from the first line of standard input with `--password-stdin`. It is
never an option, so that it is not in the history of the shell or in
the list of processes.

Whoever can run `mast credentials` on the MAST host can read the
database and the key file, so it needs no login; it is an admin.
"""
import getpass
import sys
from datetime import datetime, timezone
from typing import Literal

from mast.cli import Cli, Option, Params, Table, Text
from mast.plugins import TEST_CREDENTIALS
from mast.roots import CORE
from mast.credentials import keys, store, testing
from mast.jobs.auth import Forbidden, cli_principal

__all__ = ["cli"]

cli = Cli(description="Manage the stored credentials of MAST.",
          provider=CORE, prog="mast credentials", queued=False)

Purpose = Literal["collect", "change"]
CredentialType = Literal["password", "ssh-key"]


def _fail(message):
    print("mast credentials: {}".format(message), file=sys.stderr)
    sys.exit(1)


def _do(change, *args, **kwargs):
    try:
        return change(*args, **kwargs)
    except (store.CredentialError, keys.KeysUnavailable, Forbidden,
            ValueError) as exc:
        _fail(exc)


def _password(from_stdin):
    if from_stdin:
        line = sys.stdin.readline()
        password = line[:-1] if line.endswith("\n") else line
        if password.endswith("\r"):
            password = password[:-1]
    else:
        password = getpass.getpass("Password: ")
        if getpass.getpass("The same password again: ") != password:
            _fail("The two passwords are not the same.")
    if not password:
        _fail("The password must not be empty.")
    return password


def _passphrase(from_stdin):
    """The passphrase of a private key; empty for a key without one."""
    if from_stdin:
        line = sys.stdin.readline()
        return line.rstrip("\r\n")
    passphrase = getpass.getpass("Passphrase of the key (empty for none): ")
    if passphrase and getpass.getpass("The same passphrase again: ") \
            != passphrase:
        _fail("The two passphrases are not the same.")
    return passphrase


def _secret(credential_type, key_file, from_stdin):
    """The keyword arguments of the secret of a credential of
    `credential_type`: a password, asked for; or the private key of
    `key_file` and its passphrase."""
    if credential_type == store.PASSWORD:
        if key_file:
            _fail("--key-file is for an ssh-key credential.")
        return {"password": _password(from_stdin)}
    if not key_file:
        _fail("An ssh-key credential needs --key-file, the private key.")
    try:
        with open(key_file, encoding="utf-8") as fin:
            private_key = fin.read()
    except (OSError, UnicodeDecodeError) as exc:
        _fail("Cannot read {}: {}".format(key_file, exc))
    return {"private_key": private_key,
            "passphrase": _passphrase(from_stdin) or None}


def _when(time):
    return time.strftime("%Y-%m-%d %H:%M:%S UTC") if time else ""


def _yes_no(value):
    return "" if value is None else ("yes" if value else "no")


def _bindings(credential):
    return " ".join(
        "{}{}".format("fleet:" if binding.kind == "fleet" else "",
                      binding.name)
        for binding in credential.bindings)


def _show(credential):
    lines = ["{} ({} {}, user {})".format(
        credential.name, credential.purpose, credential.type,
        credential.username)]
    rows = (("bound to", _bindings(credential)),
            ("description", credential.description),
            ("created", "{} by {}".format(_when(credential.created_at),
                                          credential.created_by)),
            ("updated", "{} by {}".format(_when(credential.updated_at),
                                          credential.updated_by)),
            ("last used", _when(credential.last_used_at)),
            ("last test", "{} ({})".format(
                _when(credential.last_tested_at),
                "works" if credential.last_test_ok else "failed")
             if credential.last_tested_at else ""),
            ("can change targets", _yes_no(credential.can_write)))
    for label, value in rows:
        if value:
            lines.append("  {}: {}".format(label, value))
    return "\n".join(lines)


class NameParams(Params):
    name: str = Option(short="n", help="The name of the credential.")


class Add(NameParams):
    username: str = Option(short="u", help="The user name on the targets.")
    type: CredentialType = Option(
        "password", short="t",
        help="password: a user name and a password, which is asked for. "
             "ssh-key: a user name and a private key (--key-file). A "
             "target takes only the types of its kind (mast plugins kinds).")
    key_file: str | None = Option(
        None, help="The private key file (OpenSSH or PEM) of an ssh-key "
                   "credential. Its passphrase, if it has one, is asked "
                   "for.")
    purpose: Purpose = Option(
        short="p",
        help="collect: only for commands that only read (the status "
             "collector, reports). change: for any command; only admins "
             "may use it.")
    bind: list[str] = Option(
        default_factory=list, short="b",
        help="A target or a fleet that the credential may be used for "
             "(see mast targets), of a kind that takes its type.")
    description: str = Option("", short="d", help="A note for the admins.")
    password_stdin: bool = Option(
        False, help="Read the password (or the passphrase of the key) "
                    "from the first line of standard input, for scripts.")


@cli.command("add", effect="change", id="add-credential")
def add(params: Add):
    """Store a new credential: a password, which is asked for, or with
    -t ssh-key a private key from a file."""
    secret = _secret(params.type, params.key_file, params.password_stdin)
    return Text("Stored " + _show(_do(
        store.create, params.name, params.username, purpose=params.purpose,
        bindings=params.bind, principal=cli_principal(),
        description=params.description, type=params.type, **secret)))


class Replace(NameParams):
    username: str | None = Option(
        None, short="u", help="A new user name. The default is to keep it.")
    key_file: str | None = Option(
        None, help="The new private key file of an ssh-key credential.")
    password_stdin: bool = Option(
        False, help="Read the password (or the passphrase of the key) "
                    "from the first line of standard input, for scripts.")


@cli.command("replace", effect="change", id="replace-credential")
def replace(params: Replace):
    """Give a credential a new secret of its type (a password, or a
    private key) and a new user name. The result of its last test is
    cleared."""
    credential = _do(store.get, params.name)
    secret = _secret(credential.type, params.key_file, params.password_stdin)
    return Text("Replaced the secret of " + _show(_do(
        store.replace_secret, params.name, principal=cli_principal(),
        username=params.username, **secret)))


class Update(NameParams):
    description: str | None = Option(
        None, short="d", help="A new note for the admins.")
    purpose: Purpose | None = Option(None, short="p", help="A new purpose.")


@cli.command("update", effect="change", id="update-credential")
def update(params: Update):
    """Change the description or the purpose of a credential."""
    return Text("Updated " + _show(_do(
        store.update, params.name, principal=cli_principal(),
        description=params.description, purpose=params.purpose)))


class Rebind(NameParams):
    bind: list[str] = Option(
        default_factory=list, short="b",
        help="A target or a fleet that the credential may be used for. "
             "Without any, the credential cannot be used.")


@cli.command("rebind", effect="change", id="rebind-credential")
def rebind(params: Rebind):
    """Change the targets and fleets that a credential may be used
    for."""
    return Text("Rebound " + _show(_do(
        store.rebind, params.name, params.bind, principal=cli_principal())))


class Delete(NameParams):
    yes: bool = Option(False, short="y", help="Do not ask to confirm.")


@cli.command("delete", effect="change", id="delete-credential")
def delete(params: Delete):
    """Delete a credential. A queued job that names it fails when it
    starts."""
    if not params.yes:
        answer = input("Delete the credential {}? [y/N] ".format(params.name))
        if answer.strip().lower() not in ("y", "yes"):
            return Text("Nothing was deleted.")
    _do(store.delete_credential, params.name, principal=cli_principal())
    return Text("Deleted the credential {}.".format(params.name))


class ListParams(Params):
    appliances: list[str] = Option(
        default_factory=list, short="a",
        help="Only the credentials that can be used on all of these "
             "appliances or environments.")


@cli.command("list", effect="read", id="list-credentials")
def list_credentials(params: ListParams):
    """List the credentials (never a password)."""
    found = _do(store.list_credentials, params.appliances or None)
    return Table(
        ["name", "username", "type", "purpose", "bound to", "last used",
         "last test", "can change"],
        [(c.name, c.username, c.type, c.purpose, _bindings(c),
          _when(c.last_used_at),
          "" if c.last_tested_at is None else
          ("works" if c.last_test_ok else "failed"), _yes_no(c.can_write))
         for c in found])


@cli.command("show", effect="read", id="show-credential")
def show(params: NameParams):
    """Show one credential (never its password)."""
    return Text(_show(_do(store.get, params.name)))


class Test(NameParams):
    appliance: str = Option(
        short="a", target=True,
        help="The registered appliance to test it on.")


@cli.command("test", effect="read", needs=(TEST_CREDENTIALS,),
             id="test-credential")
def test(params: Test):
    """Test a credential on one target, through the job queue, with the
    test of the kind of the target: for DataPower, read the firmware
    version, and find out whether the user can change the appliance. The
    outcome is stored with the credential."""
    # Imported here: only this command uses the queue
    from mast.jobs import client, queue, runner
    job_id = queue.new_job_id()
    worker_id = runner.new_worker_id()
    try:
        command = testing.command(params.appliance)
    except testing.NotATest as exc:
        _fail(exc)
    _do(queue.submit, command,
        testing.params(params.name, params.appliance), principal=cli_principal(),
        attached=True, job_id=job_id)
    client.follow(job_id, principal=cli_principal(), worker_id=worker_id)
    outcome = _do(testing.record, params.name, job_id, principal=cli_principal())
    credential = _do(store.get, params.name)
    lines = ["The credential {} {} on {}.".format(
        params.name, "works" if outcome.ok else "does not work",
        params.appliance)]
    if outcome.problem:
        lines.append(outcome.problem)
    if outcome.can_write and credential.purpose == store.COLLECT:
        lines.append(
            "WARNING: this credential is for collecting, but its user can "
            "change the appliance. Give it a user that may only read.")
    print("\n".join(lines), file=sys.stderr if not outcome.ok else sys.stdout)
    if not outcome.ok:
        sys.exit(1)


class RotateKey(Params):
    yes: bool = Option(False, short="y", help="Do not ask to confirm.")


@cli.command("rotate-key", effect="change", id="rotate-credential-key")
def rotate_key(params: RotateKey):
    """Make a new key for the credential store, encrypt every credential
    again with it, and drop the keys older than the one before it. Copy
    the key file to where the backups of the key are, after."""
    if not params.yes:
        answer = input("Make a new key and encrypt every credential again? "
                       "[y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            return Text("Nothing was changed.")
    count = _do(store.rotate_key, principal=cli_principal())
    return Text("Made a new key and encrypted {} credential(s) again. "
                "Back up the key file now.".format(count))


def _since(text):
    if not text:
        return None
    try:
        when = datetime.fromisoformat(text)
    except ValueError:
        _fail("--since is a date, or a date and a time, such as "
              "2026-11-22 or 2026-11-22T08:00.")
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


class Uses(NameParams):
    since: str = Option(
        "", short="s",
        help="Only the uses since this date (and time), in UTC, such as "
             "2026-11-22 or 2026-11-22T08:00.")


@cli.command("uses", effect="read", id="list-credential-uses")
def uses(params: Uses):
    """List the jobs that used a credential, the newest first."""
    found = _do(store.uses, params.name, since=_since(params.since))
    return Table(["used", "job", "appliance", "by", "purpose"],
                 [(_when(use.used_at), use.job_id[:8], use.appliance,
                   use.principal, use.purpose) for use in found])


class AuditParams(Params):
    name: str | None = Option(
        None, short="n", help="Only the changes to this credential.")


@cli.command("audit", effect="read", id="credential-audit")
def audit(params: AuditParams):
    """List the changes to the credential store, the newest first."""
    found = _do(store.audit_events, params.name)
    return Table(["when", "by", "action", "object", "detail"],
                 [(_when(event.at), event.principal, event.action,
                   event.object, " ".join(
                       "{}={}".format(key, value)
                       for key, value in sorted(event.detail.items())))
                  for event in found])

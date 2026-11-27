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
_module_: `mast.credentials.store`

The stored credentials: a user name and a password for appliances,
which an admin stores once and operators then use by name.

* The password is encrypted with the keys of `mast.credentials.keys`
(a `MultiFernet`: the newest key encrypts, any key decrypts). What is
encrypted is `{"v": 1, "id": <id of the row>, "username": ...,
"password": ...}`. On decrypt, the id and the user name must be those
of the row, so that a token that is copied into another row is
refused.
* A credential is bound to appliances and environments. It can be
used only on a registered appliance that it is bound to, directly or
through an environment.
* The purpose of a credential is `collect` (it is used only by
commands whose effect is `read`) or `change`.

No function of this module returns the password, except `_open`. Only
the job runner calls `_open`, with the credential of a job, just
before the command runs. Every change by an admin is written to
`audit_events`, and every use by a job to `credential_uses`. Neither
has a secret in it.
"""
import json
import re
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, select

from mast.cli import READ as READ_EFFECT
from mast.credentials import keys as _keys
from mast.credentials.models import (
    AuditEvent, CredentialBinding, CredentialRecord, CredentialUse)
from mast.jobs import db
from mast.jobs.auth import LOCAL, Forbidden, authorize

__all__ = [
    "Credential", "Binding", "Use", "Audit", "PURPOSES", "COLLECT", "CHANGE",
    "CredentialError", "UnknownCredential", "NameTaken", "InvalidValue",
    "TokenMismatch",
    "create", "replace_secret", "update", "rebind", "delete_credential",
    "list_credentials", "get", "uses", "audit_events", "rotate_key",
    "record_test", "bound_to", "check_use", "check_targets",
]

COLLECT = "collect"
CHANGE = "change"
PURPOSES = (COLLECT, CHANGE)

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@-]{0,63}$")
_VERSION = 1


class CredentialError(ValueError):
    """A change to the credential store is not valid."""


class UnknownCredential(CredentialError, LookupError):
    """There is no credential with that name."""


class NameTaken(CredentialError):
    """There is already a credential with that name."""


class InvalidValue(CredentialError):
    """A value of a credential is not valid."""


class TokenMismatch(CredentialError):
    """The encrypted secret of a credential is not the one of its row,
    or cannot be decrypted with the keys."""


class Binding(BaseModel):
    """
    _class_: `mast.credentials.store.Binding`

    An appliance (`kind="appliance"`) or an environment
    (`kind="environment"`) that a credential is bound to.
    """
    model_config = ConfigDict(frozen=True)

    kind: str
    name: str


class Credential(BaseModel):
    """
    _class_: `mast.credentials.store.Credential`

    A stored credential, without its password.
    """
    model_config = ConfigDict(frozen=True)

    id: int
    name: str
    username: str
    purpose: str
    description: str = ""
    bindings: list[Binding] = []
    created_by: str
    created_at: datetime
    updated_by: str
    updated_at: datetime
    last_used_at: datetime | None = None
    last_tested_at: datetime | None = None
    last_test_ok: bool | None = None
    can_write: bool | None = None


class Use(BaseModel):
    """
    _class_: `mast.credentials.store.Use`

    One use of a credential by a job, on one appliance.
    """
    model_config = ConfigDict(frozen=True, from_attributes=True)

    id: int
    credential: str
    job_id: str
    appliance: str
    principal: str
    purpose: str
    used_at: datetime


class Audit(BaseModel):
    """
    _class_: `mast.credentials.store.Audit`

    One change in the audit trail.
    """
    model_config = ConfigDict(frozen=True, from_attributes=True)

    id: int
    at: datetime
    principal: str
    action: str
    object: str
    detail: dict


def _now():
    return datetime.now(timezone.utc)


def _fernet(provider=None):
    keys = (provider or _keys.provider()).keys()
    return MultiFernet([Fernet(key) for key in keys])


def _seal(fernet, record_id, username, password):
    plain = json.dumps({"v": _VERSION, "id": record_id,
                        "username": username, "password": password})
    return fernet.encrypt(plain.encode("utf-8")).decode("ascii")


def _unseal(fernet, record):
    """Return the password of `record`. Raises `TokenMismatch`."""
    try:
        plain = json.loads(fernet.decrypt(record.token.encode("ascii")))
    except (InvalidToken, ValueError, TypeError):
        raise TokenMismatch(
            "The secret of the credential {} cannot be decrypted with the "
            "keys of the credential store.".format(record.name)) from None
    if not isinstance(plain, dict) or plain.get("v") != _VERSION \
            or plain.get("id") != record.id \
            or plain.get("username") != record.username:
        raise TokenMismatch(
            "The secret of the credential {} belongs to another "
            "credential. It is refused.".format(record.name))
    return plain.get("password")


def _audit(session, principal, action, name, **detail):
    session.add(AuditEvent(
        at=_now(), principal=getattr(principal, "name", str(principal)),
        action=action, object="credential:{}".format(name), detail=detail))


def _check_name(name):
    if not isinstance(name, str) or not _NAME.match(name):
        raise InvalidValue(
            "A credential name has 1 to 64 letters, digits or the "
            "characters _ . @ -, and starts with a letter or a digit: "
            "{!r}".format(name))
    return name


def _check_username(username):
    if not isinstance(username, str) or not username.strip() \
            or ":" in username or len(username) > 255 \
            or username != username.strip():
        raise InvalidValue(
            "A user name is 1 to 255 characters, without a colon and "
            "without spaces at the start or the end.")
    return username


def _check_password(password):
    if not isinstance(password, str) or not password:
        raise InvalidValue("The password must not be empty.")
    return password


def _check_purpose(purpose):
    if purpose not in PURPOSES:
        raise InvalidValue("The purpose is one of {}.".format(
            ", ".join(PURPOSES)))
    return purpose


def _record(session, name):
    record = session.scalars(select(CredentialRecord).where(
        CredentialRecord.name == name)).first()
    if record is None:
        raise UnknownCredential("There is no stored credential {}.".format(
            name))
    return record


def _bindings(session, record):
    # Imported here: the registry tables are in the same database
    from mast.registry.models import (
        FleetRecord as EnvironmentRecord, TargetRecord as ApplianceRecord)
    found = []
    for binding in session.scalars(select(CredentialBinding).where(
            CredentialBinding.credential_id == record.id)
            .order_by(CredentialBinding.id)):
        if binding.target_id is not None:
            target = session.get(ApplianceRecord, binding.target_id)
            if target is not None:
                found.append(Binding(kind="appliance", name=target.name))
        elif binding.fleet_id is not None:
            target = session.get(EnvironmentRecord, binding.fleet_id)
            if target is not None:
                found.append(Binding(kind="environment", name=target.name))
    return found


def _view(session, record):
    return Credential(
        id=record.id, name=record.name, username=record.username,
        purpose=record.purpose, description=record.description,
        bindings=_bindings(session, record),
        created_by=record.created_by, created_at=record.created_at,
        updated_by=record.updated_by, updated_at=record.updated_at,
        last_used_at=record.last_used_at,
        last_tested_at=record.last_tested_at,
        last_test_ok=record.last_test_ok, can_write=record.can_write)


def _set_bindings(session, record, bindings):
    """Bind `record` to `bindings`: names of environments or of
    appliances (an environment first, as `mast.registry.resolve`
    does). Return the bindings as `Binding`s."""
    from mast.registry.models import (
        FleetRecord as EnvironmentRecord, TargetRecord as ApplianceRecord)
    if isinstance(bindings, str):
        bindings = [bindings]
    rows = []
    seen = set()
    for name in bindings or ():
        name = (name or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        environment = session.scalars(select(EnvironmentRecord).where(
            EnvironmentRecord.name == name)).first()
        if environment is not None:
            rows.append(CredentialBinding(
                credential_id=record.id, fleet_id=environment.id))
            continue
        appliance = session.scalars(select(ApplianceRecord).where(
            ApplianceRecord.name == name)).first()
        if appliance is None:
            raise InvalidValue(
                "{!r} is not a registered appliance or environment. A "
                "credential can be bound only to those (see "
                "mast targets).".format(name))
        rows.append(CredentialBinding(
            credential_id=record.id, target_id=appliance.id))
    session.execute(delete(CredentialBinding).where(
        CredentialBinding.credential_id == record.id))
    for row in rows:
        session.add(row)
    session.flush()
    return _bindings(session, record)


def _binding_names(found):
    return ["{}:{}".format(binding.kind, binding.name) for binding in found]


def create(name, username, password, purpose, bindings=(),
           principal=LOCAL, description=""):
    """
    _function_: `mast.credentials.store.create(name, username, password,
    purpose, bindings=(), principal=LOCAL, description="")`

    Store a new credential and return it as a `Credential`.

    Parameters:

    * `name`: The name that jobs use for it.
    * `username`, `password`: The user name and password on the
    appliances.
    * `purpose`: `collect` or `change`.
    * `bindings`: The names of the appliances and environments that it
    may be used for.
    * `principal`: The admin who stores it.
    * `description`: A note for the admins.
    """
    authorize(principal, "manage-credential")
    _check_name(name)
    _check_username(username)
    _check_password(password)
    _check_purpose(purpose)
    fernet = _fernet()
    now = _now()
    with db.session() as session:
        exists = session.scalars(select(CredentialRecord.id).where(
            CredentialRecord.name == name)).first()
        if exists is not None:
            raise NameTaken("There is already a credential {}.".format(name))
        record = CredentialRecord(
            name=name, username=username, token="", purpose=purpose,
            description=str(description or ""),
            created_by=principal.name, created_at=now,
            updated_by=principal.name, updated_at=now)
        session.add(record)
        session.flush()
        record.token = _seal(fernet, record.id, username, password)
        found = _set_bindings(session, record, bindings)
        _audit(session, principal, "credential.create", name,
               username=username, purpose=purpose,
               bindings=_binding_names(found))
        return _view(session, record)


def replace_secret(name, password, principal=LOCAL, username=None):
    """
    _function_: `mast.credentials.store.replace_secret(name, password,
    principal=LOCAL, username=None)`

    Give the credential `name` a new password, and a new user name if
    `username` is given. The result of the last test is cleared.
    """
    authorize(principal, "manage-credential")
    _check_password(password)
    if username is not None:
        _check_username(username)
    fernet = _fernet()
    with db.session() as session:
        record = _record(session, name)
        old_username = record.username
        if username is not None:
            record.username = username
        record.token = _seal(fernet, record.id, record.username, password)
        record.updated_by = principal.name
        record.updated_at = _now()
        record.last_tested_at = None
        record.last_test_ok = None
        record.can_write = None
        detail = {}
        if record.username != old_username:
            detail["username"] = record.username
        _audit(session, principal, "credential.replace", name, **detail)
        return _view(session, record)


def update(name, principal=LOCAL, description=None, purpose=None):
    """
    _function_: `mast.credentials.store.update(name, principal=LOCAL,
    description=None, purpose=None)`

    Change the description or the purpose of a credential.
    """
    authorize(principal, "manage-credential")
    if purpose is not None:
        _check_purpose(purpose)
    with db.session() as session:
        record = _record(session, name)
        detail = {}
        if description is not None:
            record.description = str(description)
            detail["description"] = True
        if purpose is not None and purpose != record.purpose:
            detail["purpose"] = [record.purpose, purpose]
            record.purpose = purpose
        record.updated_by = principal.name
        record.updated_at = _now()
        _audit(session, principal, "credential.update", name, **detail)
        return _view(session, record)


def rebind(name, bindings, principal=LOCAL):
    """
    _function_: `mast.credentials.store.rebind(name, bindings,
    principal=LOCAL)`

    Bind the credential `name` to `bindings` (names of appliances and
    environments) in place of what it was bound to.
    """
    authorize(principal, "manage-credential")
    with db.session() as session:
        record = _record(session, name)
        before = _binding_names(_bindings(session, record))
        found = _set_bindings(session, record, bindings)
        record.updated_by = principal.name
        record.updated_at = _now()
        _audit(session, principal, "credential.rebind", name,
               before=before, after=_binding_names(found))
        return _view(session, record)


def delete_credential(name, principal=LOCAL):
    """
    _function_: `mast.credentials.store.delete_credential(name,
    principal=LOCAL)`

    Delete the credential `name`, its bindings and its uses. A queued
    job that names it fails when it starts.
    """
    authorize(principal, "manage-credential")
    with db.session() as session:
        record = _record(session, name)
        session.execute(delete(CredentialUse).where(
            CredentialUse.credential_id == record.id))
        session.execute(delete(CredentialBinding).where(
            CredentialBinding.credential_id == record.id))
        _audit(session, principal, "credential.delete", name,
               username=record.username)
        session.delete(record)


def _bound_ids(session, record):
    """Return the ids of the appliances that `record` is bound to,
    directly or through an environment."""
    from mast.registry.models import FleetMember as EnvironmentMember
    ids = set()
    environments = []
    for binding in session.scalars(select(CredentialBinding).where(
            CredentialBinding.credential_id == record.id)):
        if binding.target_id is not None:
            ids.add(binding.target_id)
        elif binding.fleet_id is not None:
            environments.append(binding.fleet_id)
    if environments:
        ids.update(session.scalars(
            select(EnvironmentMember.target_id).where(
                EnvironmentMember.fleet_id.in_(environments))))
    return ids


def list_credentials(appliances=None):
    """
    _function_: `mast.credentials.store.list_credentials(appliances=None)`

    Return every credential (`Credential`, without the password), by
    name. If `appliances` (names of appliances or environments) is
    given, return only the credentials that can be used on every one
    of them: each name resolves to registered appliances that the
    credential is bound to. A name that is not registered matches no
    credential.
    """
    from mast import registry
    targets = None
    if appliances:
        try:
            resolved = registry.resolve(list(appliances))
        except registry.UnknownName:
            return []
        if not resolved:
            return []
        targets = {item.id for item in resolved}
    with db.session(write=False) as session:
        found = []
        for record in session.scalars(
                select(CredentialRecord).order_by(CredentialRecord.name)):
            if targets is not None \
                    and not targets <= _bound_ids(session, record):
                continue
            found.append(_view(session, record))
        return found


def get(name):
    """
    _function_: `mast.credentials.store.get(name)`

    Return the credential `name` (`Credential`, without the password).
    Raises `UnknownCredential`.
    """
    with db.session(write=False) as session:
        return _view(session, _record(session, name))


def uses(name, since=None, limit=200):
    """
    _function_: `mast.credentials.store.uses(name, since=None, limit=200)`

    Return the uses of the credential `name` (`Use`), the newest first.
    `since` is a `datetime`: only the uses after it.
    """
    with db.session(write=False) as session:
        record = _record(session, name)
        query = select(CredentialUse).where(
            CredentialUse.credential_id == record.id)
        if since is not None:
            query = query.where(CredentialUse.used_at >= since)
        query = query.order_by(CredentialUse.used_at.desc(),
                               CredentialUse.id.desc()).limit(limit)
        return [Use(id=use.id, credential=record.name, job_id=use.job_id,
                    appliance=use.appliance, principal=use.principal,
                    purpose=use.purpose, used_at=use.used_at)
                for use in session.scalars(query)]


def audit_events(name=None, limit=200):
    """
    _function_: `mast.credentials.store.audit_events(name=None, limit=200)`

    Return the changes to the credential store (`Audit`), the newest
    first. If `name` is given, only those of that credential.
    """
    with db.session(write=False) as session:
        query = select(AuditEvent)
        if name is not None:
            query = query.where(
                AuditEvent.object == "credential:{}".format(name))
        query = query.order_by(AuditEvent.at.desc(),
                               AuditEvent.id.desc()).limit(limit)
        return [Audit.model_validate(event)
                for event in session.scalars(query)]


def rotate_key(principal=LOCAL):
    """
    _function_: `mast.credentials.store.rotate_key(principal=LOCAL)`

    Make a new key and encrypt every credential again with it, in one
    transaction. Then drop the keys that are older than the one before
    the new key. Return the number of credentials.

    Before anything changes, every credential must decrypt with the
    keys there are now. If one does not, nothing changes and
    `TokenMismatch` says which.
    """
    authorize(principal, "manage-credential")
    provider = _keys.provider()
    with db.session(write=False) as session:
        old = _fernet(provider)
        for record in session.scalars(select(CredentialRecord)):
            _unseal(old, record)
    provider.rotate()
    fernet = _fernet(provider)
    with db.session() as session:
        records = list(session.scalars(select(CredentialRecord)))
        for record in records:
            record.token = fernet.rotate(
                record.token.encode("ascii")).decode("ascii")
        _audit(session, principal, "credential.rotate-key", "*",
               credentials=len(records))
    provider.prune(2)
    return len(records)


def record_test(name, ok, can_write=None, principal=LOCAL, appliance=None,
                job_id=None):
    """
    _function_: `mast.credentials.store.record_test(name, ok,
    can_write=None, principal=LOCAL, appliance=None, job_id=None)`

    Record the result of a test of the credential `name`: whether it
    worked, and whether the user can change the appliance (None if
    that is not known).
    """
    authorize(principal, "manage-credential")
    with db.session() as session:
        record = _record(session, name)
        record.last_tested_at = _now()
        record.last_test_ok = bool(ok)
        if can_write is not None:
            record.can_write = bool(can_write)
        _audit(session, principal, "credential.test", name, ok=bool(ok),
               can_write=can_write, appliance=appliance, job_id=job_id)
        return _view(session, record)


def bound_to(name, appliance):
    """
    _function_: `mast.credentials.store.bound_to(name, appliance)`

    Return True if the credential `name` is bound to the registered
    appliance `appliance`, directly or through an environment.
    """
    from mast import registry
    found = registry.by_name(appliance)
    if found is None:
        return False
    with db.session(write=False) as session:
        return found.id in _bound_ids(session, _record(session, name))


def check_targets(name, appliances):
    """
    _function_: `mast.credentials.store.check_targets(name, appliances)`

    Return `(credential, targets)`: the `Credential` called `name` and
    the `mast.registry.Target`s that `appliances` (names that a user
    typed) stand for. Raises `Forbidden` if there is no such
    credential, or if one of the targets is not registered, not bound
    to it, has a TLS endpoint that is not checked (trust `none`), or an
    endpoint whose pin must be reviewed.
    """
    from mast import registry
    try:
        credential = get(name)
    except UnknownCredential as exc:
        raise Forbidden(str(exc)) from None
    try:
        targets = registry.resolve(list(appliances or ()))
    except registry.UnknownName as exc:
        raise Forbidden(str(exc)) from None
    with db.session(write=False) as session:
        bound = _bound_ids(session, _record(session, name))
    for target in targets:
        if target.id not in bound:
            raise Forbidden(
                "The credential {} is not bound to the appliance {}. An "
                "admin can bind it (mast credentials rebind).".format(
                    name, target.name))
        for trust in target.trust:
            if trust.protocol == "tls" and trust.mode == "none":
                raise Forbidden(
                    "MAST does not check the TLS certificate of {} ({}: "
                    "trust none). A stored credential is not sent to "
                    "it.".format(target.name, trust.endpoint))
            if trust.review:
                raise Forbidden(
                    "The pin of {} ({}) must be reviewed first: {}. A stored "
                    "credential is not sent to it until an admin reviews "
                    "it (mast targets review or pin).".format(
                        target.name, trust.endpoint, trust.review))
    return credential, targets


def check_use(name, appliances, principal, command=None, effect=None):
    """
    _function_: `mast.credentials.store.check_use(name, appliances,
    principal, command=None, effect=None)`

    Raise `Forbidden` if `principal` may not run the command `command`
    on `appliances` with the credential `name`. The queue calls this
    before it queues a job with a stored credential. Return the
    `Credential`.

    * The credential exists, and every appliance is a registered
    appliance that it is bound to and whose certificate is checked
    (see `check_targets`).
    * The principal may use stored credentials (`use-credential`).
    * A `collect` credential is used only by a command whose `effect`
    is `read` (see `mast.cli.Command.effect`). A command that declares
    no effect counts as one that changes.
    * A `change` credential needs `use-change-credential` (admins).
    """
    credential, _ = check_targets(name, appliances)
    authorize(principal, "use-credential", command=command)
    if credential.purpose == COLLECT and effect != READ_EFFECT:
        raise Forbidden(
            "The credential {} is for collecting (purpose collect). It "
            "can be used only by commands that only read; {} can change "
            "the appliances.".format(name, command or "this command"))
    if credential.purpose == CHANGE:
        try:
            authorize(principal, "use-change-credential", command=command)
        except Forbidden:
            raise Forbidden(
                "The credential {} can change the appliances (purpose "
                "change). Only an admin may use it.".format(name)) from None
    return credential


def _open(name, appliance, job_id, principal):
    """
    _function_: `mast.credentials.store._open(name, appliance, job_id,
    principal)`

    Return `(username, password)` of the credential `name`, for the job
    `job_id` on the registered appliance `appliance` (its name). Only
    the job runner calls this. It records the use, as `principal` (the
    name of whoever submitted the job). Raises `Forbidden` if the
    credential is not bound to the appliance, and `TokenMismatch` if
    its secret is not its own.
    """
    from mast import registry
    target = registry.by_name(appliance)
    if target is None:
        raise Forbidden("{} is not a registered appliance.".format(appliance))
    fernet = _fernet()
    with db.session() as session:
        record = _record(session, name)
        if target.id not in _bound_ids(session, record):
            raise Forbidden(
                "The credential {} is not bound to the appliance "
                "{}.".format(name, appliance))
        password = _unseal(fernet, record)
        now = _now()
        record.last_used_at = now
        session.add(CredentialUse(
            credential_id=record.id, job_id=job_id, appliance=target.name,
            principal=str(principal), purpose=record.purpose, used_at=now))
        return record.username, password

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
_module_: `mast.auth.users`

The local users of MAST: add, change and remove them, and check a
login.

A user has a name, a role (see `mast.auth.roles`) and a password.
Nothing here returns the hash of a password: the functions return a
`UserInfo`, which does not have it.

A login (`authenticate`) takes the same time whether the user exists
or not, and it fails with the same error whatever the reason. After
`MAX_FAILURES` failed logins in a row the user is locked for
`LOCK_MINUTES` minutes.

`set_password`, `set_role` and `disable` give the user a new session
epoch, which logs out every session of the user in the web UI.
"""
import re
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select

from mast.auth import passwords, roles
from mast.auth.models import User
from mast.jobs import db

__all__ = [
    "UserInfo", "UserNotFound", "UserExists", "InvalidName", "InvalidRole",
    "LoginFailed", "add", "set_password", "change_password", "set_role",
    "disable", "enable", "delete", "get", "get_by_id", "list_users",
    "any_users", "authenticate", "new_epoch", "MAX_FAILURES",
    "LOCK_MINUTES", "add_service", "PERSON", "SERVICE",
]

MAX_FAILURES = 5
LOCK_MINUTES = 15

# Lower case letters, digits and . _ @ -, starting with a letter or a
# digit. There is no ":", so that no user can have the name of a
# principal that is not a user ("os:...", "schedule:...").
_NAME = re.compile(r"^[a-z0-9][a-z0-9._@-]{0,63}$")


class UserNotFound(LookupError):
    """There is no user with this name or id."""


class UserExists(ValueError):
    """There is already a user with this name."""


class InvalidName(ValueError):
    """The name cannot be the name of a user."""


class InvalidRole(ValueError):
    """The role is not one of `mast.auth.roles.ROLES`."""


class LoginFailed(Exception):
    """
    _class_: `mast.auth.users.LoginFailed`

    A login failed. The message is the same for every reason, so that
    it does not tell whether the user exists. `reason` says why, for
    the log only: `"unknown"`, `"password"`, `"disabled"`, `"locked"`
    or `"locked-now"` (this failure locked the user), or `"service"`
    (a service account, which has no password).
    """
    MESSAGE = "The user name or the password is not correct."

    def __init__(self, reason):
        super().__init__(self.MESSAGE)
        self.reason = reason


class UserInfo(BaseModel):
    """
    _class_: `mast.auth.users.UserInfo`

    What MAST knows about a user, without the hash of the password.
    """
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: int
    name: str
    role: str
    kind: str = "person"
    disabled: bool
    must_change: bool
    session_epoch: int
    failed_logins: int
    locked_until: datetime | None
    created_at: datetime
    last_login_at: datetime | None


# The kinds of user
PERSON = "person"
SERVICE = "service"


def _now():
    return datetime.now(timezone.utc)


def normalize(name):
    """
    _function_: `mast.auth.users.normalize(name)`

    Return `name` in lower case, without spaces around it. Raises
    `InvalidName` if it cannot be the name of a user.
    """
    name = (name or "").strip().lower()
    if not _NAME.match(name):
        raise InvalidName(
            "A user name has 1 to 64 lower case letters, digits, '.', "
            "'_', '@' or '-', and starts with a letter or a digit: "
            "{!r}".format(name))
    return name


def _check_role(role):
    if role not in roles.ROLES:
        raise InvalidRole("A role is one of {}, not {!r}".format(
            ", ".join(roles.ROLES), role))


def _require(session, name):
    user = session.scalars(
        select(User).where(User.name == normalize(name))).first()
    if user is None:
        raise UserNotFound(name)
    return user


def add(name, password, role):
    """
    _function_: `mast.auth.users.add(name, password, role)`

    Add a user and return its `UserInfo`. The password is temporary:
    the user must choose a new one at the first login.

    Raises `InvalidName`, `InvalidRole`, `UserExists` or
    `mast.auth.passwords.WeakPassword`.

    Parameters:

    * `name`: The name of the user. It is kept in lower case.
    * `password`: The first password.
    * `role`: `"viewer"`, `"operator"` or `"admin"`.
    """
    name = normalize(name)
    _check_role(role)
    passwords.check_strength(password, name)
    hashed = passwords.hash_password(password)
    with db.session() as session:
        if session.scalars(select(User.id).where(User.name == name)).first():
            raise UserExists(name)
        user = User(
            name=name, password_hash=hashed, role=role, kind=PERSON,
            disabled=False, must_change=True, session_epoch=1,
            failed_logins=0, locked_until=None, created_at=_now(),
            last_login_at=None)
        session.add(user)
        session.flush()
        found = UserInfo.model_validate(user)
    _sync_groups(name, role)
    return found


def add_service(name, role):
    """
    _function_: `mast.auth.users.add_service(name, role)`

    Add a service account and return its `UserInfo`: an identity for
    cron jobs, scripts and services. It has no password, cannot log in
    to the web UI, and uses only tokens (`mast.auth.tokens`).
    """
    import secrets as _secrets
    name = normalize(name)
    _check_role(role)
    # A hash of a password that nobody knows: no password works
    hashed = passwords.hash_password(_secrets.token_urlsafe(48))
    with db.session() as session:
        if session.scalars(select(User.id).where(User.name == name)).first():
            raise UserExists(name)
        user = User(
            name=name, password_hash=hashed, role=role, kind=SERVICE,
            disabled=False, must_change=False, session_epoch=1,
            failed_logins=0, locked_until=None, created_at=_now(),
            last_login_at=None)
        session.add(user)
        session.flush()
        found = UserInfo.model_validate(user)
    _sync_groups(name, role)
    return found


def _sync_groups(name, role):
    """Until enforcement uses the permission sets (step 6 of
    docs/design/mast-4.0.md), the role decides what a user may do, and
    the user is in the groups of the role."""
    # Imported here: mast.permissions imports this module
    from mast import permissions
    permissions.sync_role(name, role)


def set_password(name, password, temporary=False):
    """
    _function_: `mast.auth.users.set_password(name, password,
    temporary=False)`

    Give a user a new password. This logs out every session of the
    user, and ends a lock after failed logins.

    Parameters:

    * `name`: The name of the user.
    * `password`: The new password.
    * `temporary`: If True, the user must choose a new password at
    the next login.
    """
    passwords.check_strength(password, normalize(name))
    hashed = passwords.hash_password(password)
    with db.session() as session:
        user = _require(session, name)
        user.password_hash = hashed
        user.must_change = bool(temporary)
        user.failed_logins = 0
        user.locked_until = None
        user.session_epoch += 1
        return UserInfo.model_validate(user)


def change_password(name, current, password):
    """
    _function_: `mast.auth.users.change_password(name, current,
    password)`

    A user changes their own password. `current` must be the password
    that they have now: a wrong one counts as a failed login and
    raises `LoginFailed`. Returns the `UserInfo` with the new session
    epoch.
    """
    if current == password:
        raise passwords.WeakPassword(
            "The new password must not be the same as the old one.")
    passwords.check_strength(password, normalize(name))
    authenticate(name, current)
    return set_password(name, password)


def set_role(name, role):
    """
    _function_: `mast.auth.users.set_role(name, role)`

    Give a user another role. This logs out every session of the
    user.
    """
    _check_role(role)
    with db.session() as session:
        user = _require(session, name)
        user.role = role
        user.session_epoch += 1
        found = UserInfo.model_validate(user)
    _sync_groups(name, role)
    return found


def disable(name):
    """
    _function_: `mast.auth.users.disable(name)`

    Stop a user from logging in, and log out every session of the
    user. The user and the jobs of the user stay.
    """
    with db.session() as session:
        user = _require(session, name)
        user.disabled = True
        user.session_epoch += 1
        return UserInfo.model_validate(user)


def enable(name):
    """
    _function_: `mast.auth.users.enable(name)`

    Let a disabled user log in again.
    """
    with db.session() as session:
        user = _require(session, name)
        user.disabled = False
        return UserInfo.model_validate(user)


def new_epoch(user_id):
    """
    _function_: `mast.auth.users.new_epoch(user_id)`

    Log out every session of a user, for example at a logout. Returns
    the `UserInfo`, or None if there is no such user.
    """
    with db.session() as session:
        user = session.get(User, user_id)
        if user is None:
            return None
        user.session_epoch += 1
        return UserInfo.model_validate(user)


def delete(name):
    """
    _function_: `mast.auth.users.delete(name)`

    Remove a user. The jobs that the user submitted stay, with the
    name of the user.
    """
    with db.session() as session:
        session.delete(_require(session, name))


def get(name):
    """
    _function_: `mast.auth.users.get(name)`

    Return the `UserInfo` of a user. Raises `UserNotFound`.
    """
    with db.session(write=False) as session:
        return UserInfo.model_validate(_require(session, name))


def get_by_id(user_id):
    """
    _function_: `mast.auth.users.get_by_id(user_id)`

    Return the `UserInfo` of the user with the id `user_id`, or None.
    """
    with db.session(write=False) as session:
        user = session.get(User, user_id)
        return None if user is None else UserInfo.model_validate(user)


def list_users():
    """
    _function_: `mast.auth.users.list_users()`

    Return every user, by name, as a list of `UserInfo`.
    """
    with db.session(write=False) as session:
        return [UserInfo.model_validate(user) for user in
                session.scalars(select(User).order_by(User.name))]


def any_users():
    """
    _function_: `mast.auth.users.any_users()`

    Return True if there is at least one user.
    """
    with db.session(write=False) as session:
        return bool(session.scalar(select(func.count(User.id))))


_dummy_hash = None


def _dummy():
    # The hash that a login for an unknown user is checked against, so
    # that it takes as long as a login for a user who exists
    global _dummy_hash
    if _dummy_hash is None or passwords.needs_rehash(_dummy_hash):
        _dummy_hash = passwords.hash_password("not the password of anyone")
    return _dummy_hash


def authenticate(name, password, now=None):
    """
    _function_: `mast.auth.users.authenticate(name, password, now=None)`

    Check a login. Return the `UserInfo` of the user if `password` is
    the password of the user, and the user is neither disabled nor
    locked. Otherwise raise `LoginFailed`.

    The password is checked in every case, so that a login for an
    unknown, disabled or locked user takes as long as any other.

    A failed login counts against the user. The `MAX_FAILURES`th
    failure in a row locks the user for `LOCK_MINUTES` minutes. A good
    login sets the count back to 0, and stores a new hash if the old
    one was made with weaker parameters.

    Parameters:

    * `name`: The name that the user typed.
    * `password`: The password that the user typed.
    * `now`: The time, for tests.
    """
    now = now or _now()
    try:
        name = normalize(name)
    except InvalidName:
        passwords.verify_password(password, _dummy())
        raise LoginFailed("unknown")
    with db.session() as session:
        user = session.scalars(select(User).where(User.name == name)).first()
        if user is None:
            passwords.verify_password(password, _dummy())
            raise LoginFailed("unknown")
        good = passwords.verify_password(password, user.password_hash)
        if user.disabled:
            raise LoginFailed("disabled")
        if user.kind == SERVICE:
            raise LoginFailed("service")
        if user.locked_until is not None and user.locked_until > now:
            raise LoginFailed("locked")
        if not good:
            if user.locked_until is not None:
                # The lock is over: count again from the start
                user.locked_until = None
                user.failed_logins = 0
            user.failed_logins += 1
            if user.failed_logins >= MAX_FAILURES:
                user.failed_logins = 0
                user.locked_until = now + timedelta(minutes=LOCK_MINUTES)
                failure = LoginFailed("locked-now")
            else:
                failure = LoginFailed("password")
        else:
            failure = None
            user.failed_logins = 0
            user.locked_until = None
            user.last_login_at = now
            if passwords.needs_rehash(user.password_hash):
                user.password_hash = passwords.hash_password(password)
            info = UserInfo.model_validate(user)
    # The session commits the count of failures before the error
    if failure is not None:
        raise failure
    return info

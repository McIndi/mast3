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
_module_: `mast.auth.tokens`

The tokens of the users of MAST: how the command line and scripts say
who they are (step 6 of docs/design/mast-4.0.md).

A token is `mast_<id>_<secret>`. MAST stores only the SHA-256 of the
secret, so a token is shown once, when it is made. A token works while
its user exists and is not disabled, until it expires or is revoked.

* `mast login` makes a token for a person, which expires (see
`mast.auth.identity`).
* A service account (`mast users add-service`) has a token in a file
that only the owner of the MAST home can read, for cron jobs, scripts
and services: `mast --as NAME ...`.
"""
import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from mast.auth.models import ApiToken, User
from mast.auth.users import UserNotFound, normalize
from mast.jobs import db

__all__ = ["TokenInfo", "BadToken", "issue", "authenticate", "revoke",
           "revoke_all", "list_tokens"]

_TOKEN = re.compile(r"^mast_([0-9]+)_([A-Za-z0-9_-]{20,})$")


class BadToken(Exception):
    """A token that does not work: unknown, revoked, expired, of a
    disabled or deleted user, or not a token. The message says which,
    without the token."""


@dataclass(frozen=True)
class TokenInfo(object):
    """
    _class_: `mast.auth.tokens.TokenInfo`

    A token, without its secret.
    """
    id: int
    user: str
    name: str
    created_at: datetime
    created_by: str
    last_used_at: datetime = None
    expires_at: datetime = None
    revoked_at: datetime = None

    @property
    def active(self):
        now = datetime.now(timezone.utc)
        return self.revoked_at is None and (
            self.expires_at is None or self.expires_at > now)

    def json(self):
        return {"id": self.id, "user": self.user, "name": self.name,
                "created_at": self.created_at.isoformat(),
                "created_by": self.created_by,
                "last_used_at": _iso(self.last_used_at),
                "expires_at": _iso(self.expires_at),
                "revoked_at": _iso(self.revoked_at), "active": self.active}


def _iso(value):
    return None if value is None else value.isoformat()


def _now():
    return datetime.now(timezone.utc)


def _hash(secret):
    return hashlib.sha256(secret.encode("ascii")).hexdigest()


def _info(record, user_name):
    return TokenInfo(id=record.id, user=user_name, name=record.name,
                     created_at=record.created_at,
                     created_by=record.created_by,
                     last_used_at=record.last_used_at,
                     expires_at=record.expires_at,
                     revoked_at=record.revoked_at)


def _who(principal):
    return getattr(principal, "name", None) or str(principal)


def issue(user, name, hours=None, principal="mast"):
    """
    _function_: `mast.auth.tokens.issue(user, name, hours=None,
    principal="mast")`

    Make a token for the user `user` and return `(TokenInfo, token)`.
    The token is not stored: give it to its owner now. It expires after
    `hours`, or never.
    """
    secret = secrets.token_urlsafe(32)
    now = _now()
    with db.session() as session:
        found = session.scalars(select(User).where(
            User.name == normalize(user))).first()
        if found is None:
            raise UserNotFound(user)
        record = ApiToken(
            user_id=found.id, name=str(name or "")[:200],
            secret_hash=_hash(secret), created_at=now,
            created_by=_who(principal),
            expires_at=None if hours is None else now + timedelta(
                hours=hours))
        session.add(record)
        session.flush()
        return _info(record, found.name), "mast_{}_{}".format(record.id,
                                                               secret)


def authenticate(token):
    """
    _function_: `mast.auth.tokens.authenticate(token)`

    Return the `mast.auth.users.UserInfo` of the user of `token`, and
    mark the token as used. Raises `BadToken`.
    """
    # Imported here: mast.auth.users imports nothing of this module
    from mast.auth.users import UserInfo
    match = _TOKEN.match((token or "").strip())
    if match is None:
        raise BadToken("This is not a token of MAST.")
    token_id, secret = int(match.group(1)), match.group(2)
    now = _now()
    with db.session() as session:
        record = session.get(ApiToken, token_id)
        if record is None or not hmac.compare_digest(
                record.secret_hash, _hash(secret)):
            raise BadToken("The token is not known.")
        if record.revoked_at is not None:
            raise BadToken("The token was revoked.")
        if record.expires_at is not None and record.expires_at <= now:
            raise BadToken("The token has expired. Log in again (mast "
                           "login).")
        user = session.get(User, record.user_id)
        if user is None or user.disabled:
            raise BadToken("The user of the token is disabled or gone.")
        record.last_used_at = now
        return UserInfo.model_validate(user)


def revoke(token_id, user=None):
    """
    _function_: `mast.auth.tokens.revoke(token_id, user=None)`

    Revoke the token `token_id` (of the user `user`, if given). Raises
    `BadToken` if there is no such token.
    """
    with db.session() as session:
        record = session.get(ApiToken, token_id)
        owner = None if record is None else session.get(User, record.user_id)
        if record is None or owner is None or (
                user is not None and owner.name != normalize(user)):
            raise BadToken("There is no token {}.".format(token_id))
        if record.revoked_at is None:
            record.revoked_at = _now()
        return _info(record, owner.name)


def revoke_all(user):
    """
    _function_: `mast.auth.tokens.revoke_all(user)`

    Revoke every token of the user `user`. Return how many.
    """
    now = _now()
    with db.session() as session:
        found = session.scalars(select(User).where(
            User.name == normalize(user))).first()
        if found is None:
            raise UserNotFound(user)
        count = 0
        for record in session.scalars(select(ApiToken).where(
                ApiToken.user_id == found.id,
                ApiToken.revoked_at.is_(None))):
            record.revoked_at = now
            count += 1
        return count


def list_tokens(user=None):
    """
    _function_: `mast.auth.tokens.list_tokens(user=None)`

    Return the tokens (of the user `user`, if given), newest first,
    without their secrets.
    """
    with db.session(write=False) as session:
        query = select(ApiToken, User.name).join(
            User, User.id == ApiToken.user_id).order_by(ApiToken.id.desc())
        if user is not None:
            query = query.where(User.name == normalize(user))
        return [_info(record, name) for record, name in session.execute(query)]

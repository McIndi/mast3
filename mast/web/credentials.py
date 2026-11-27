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
_module_: `mast.web.credentials`

The JSON routes of the credential store (`mast.credentials`), on the
blueprint of `mast.web.api`.

* `GET /api/v1/credentials?appliances=...`: The credentials (never a
password). With `appliances`, only those that can be used on every
one of them. Operators and admins.
* `POST /api/v1/credentials`: `{name, username, type, password,
purpose, bindings, description}`; for `type` `ssh-key`, `private_key`
and `passphrase` in place of `password`. Admins.
* `PATCH /api/v1/credentials/<name>`: Any of `{password, username}`
or `{private_key, passphrase, username}` (a new secret of its type),
`{description, purpose}` and `{bindings}`. Admins.
* `DELETE /api/v1/credentials/<name>`. Admins.
* `POST /api/v1/credentials/<name>/tests`: `{appliance}`. Starts a
test job and answers `{job_id}`. Admins.
* `POST /api/v1/credentials/<name>/tests/<job_id>`: Stores the outcome
of that test job, once it has ended. Admins.
* `GET /api/v1/credentials/<name>/uses` and `.../audit`. Admins.

A password or a private key goes in only in the body of a POST or a
PATCH. No answer has one. Every change is logged to `mast.credentials`,
without the secret.
"""
import flask

from mast.credentials import keys, store, testing
from mast.jobs import auth as jobs_auth
from mast.jobs import client as jobs_client
from mast.logging import make_logger
from mast.web.api import blueprint, error
from mast.web.auth import principal

__all__ = ["credential_json"]


def credential_json(credential):
    """
    _function_: `mast.web.credentials.credential_json(credential)`

    Return a `mast.credentials.store.Credential` as JSON types. It has
    no password, because a `Credential` has none.
    """
    return credential.model_dump(mode="json")


def _body():
    body = flask.request.get_json(silent=True)
    return body if isinstance(body, dict) else {}


def _store_error(exc):
    if isinstance(exc, store.UnknownCredential):
        return error(404, str(exc))
    if isinstance(exc, store.NameTaken):
        return error(409, str(exc), {"name": str(exc)})
    if isinstance(exc, keys.KeysUnavailable):
        make_logger("mast.credentials").error(str(exc))
        return error(503, "The keys of the credential store cannot be "
                          "read. See the log of MAST.")
    return error(422, str(exc))


def _changed(message):
    make_logger("mast.credentials").info("{} (by {} from {})".format(
        message, principal().name, flask.request.remote_addr))


def _text(value):
    return value if isinstance(value, str) else ""


def _names(value):
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


@blueprint.get("/credentials")
def list_credentials():
    """
    _function_: `mast.web.credentials.list_credentials()`

    Exposed at: `/api/v1/credentials` via GET
    """
    jobs_auth.authorize(principal(), "use-credential")
    appliances = flask.request.args.getlist("appliances")
    try:
        found = store.list_credentials(appliances or None)
    except store.CredentialError as exc:
        return _store_error(exc)
    return flask.jsonify({"credentials": [credential_json(c) for c in found]})


@blueprint.post("/credentials")
def add_credential():
    """
    _function_: `mast.web.credentials.add_credential()`

    Exposed at: `/api/v1/credentials` via POST
    """
    who = principal()
    jobs_auth.authorize(who, "manage-credential")
    body = _body()
    try:
        found = store.create(
            _text(body.get("name")), _text(body.get("username")),
            purpose=_text(body.get("purpose")),
            bindings=_names(body.get("bindings")), principal=who,
            description=_text(body.get("description")),
            type=_text(body.get("type") or store.PASSWORD), **_secret(body))
    except (store.CredentialError, keys.KeysUnavailable) as exc:
        return _store_error(exc)
    _changed("Stored the credential {}".format(found.name))
    return flask.jsonify(credential_json(found)), 201


def _secret(body):
    """The secret in a body: a password, or a private key and its
    passphrase."""
    if "private_key" in body:
        passphrase = body.get("passphrase")
        return {"private_key": _text(body.get("private_key")),
                "passphrase": _text(passphrase) if passphrase else None}
    return {"password": _text(body.get("password"))}


@blueprint.patch("/credentials/<name>")
def change_credential(name):
    """
    _function_: `mast.web.credentials.change_credential(name)`

    Exposed at: `/api/v1/credentials/<name>` via PATCH
    """
    who = principal()
    jobs_auth.authorize(who, "manage-credential")
    body = _body()
    try:
        found = store.get(name)
        if "password" in body or "private_key" in body:
            username = body.get("username")
            found = store.replace_secret(
                name, principal=who,
                username=_text(username) if username is not None else None,
                **_secret(body))
            _changed("Replaced the secret of the credential {}".format(name))
        if "description" in body or "purpose" in body:
            found = store.update(
                name, principal=who,
                description=_text(body["description"])
                if "description" in body else None,
                purpose=_text(body["purpose"]) if "purpose" in body else None)
            _changed("Changed the credential {}".format(name))
        if "bindings" in body:
            found = store.rebind(name, _names(body.get("bindings")),
                                 principal=who)
            _changed("Rebound the credential {}".format(name))
    except (store.CredentialError, keys.KeysUnavailable) as exc:
        return _store_error(exc)
    return flask.jsonify(credential_json(found))


@blueprint.delete("/credentials/<name>")
def delete_credential(name):
    """
    _function_: `mast.web.credentials.delete_credential(name)`

    Exposed at: `/api/v1/credentials/<name>` via DELETE
    """
    who = principal()
    jobs_auth.authorize(who, "manage-credential")
    try:
        store.delete_credential(name, principal=who)
    except store.CredentialError as exc:
        return _store_error(exc)
    _changed("Deleted the credential {}".format(name))
    return "", 204


@blueprint.post("/credentials/<name>/tests")
def start_test(name):
    """
    _function_: `mast.web.credentials.start_test(name)`

    Start a test of the credential on `{appliance}`. The answer is
    `{job_id}`; the job page shows it. When it has ended, POST to
    `.../tests/<job_id>` stores the outcome.

    Exposed at: `/api/v1/credentials/<name>/tests` via POST
    """
    who = principal()
    jobs_auth.authorize(who, "manage-credential")
    appliance = _text(_body().get("appliance")).strip()
    if not appliance:
        return error(422, "Choose an appliance to test it on.",
                     {"appliance": "Choose an appliance."})
    try:
        store.get(name)
    except store.CredentialError as exc:
        return _store_error(exc)
    try:
        command = testing.command(appliance)
    except testing.NotATest as exc:
        return error(422, str(exc), {"appliance": str(exc)})
    job_id = jobs_client.start(command, testing.params(name, appliance),
                               principal=who)
    _changed("Started a test of the credential {} on {} (job {})".format(
        name, appliance, job_id))
    return flask.jsonify({"job_id": job_id}), 201


@blueprint.post("/credentials/<name>/tests/<job_id>")
def record_test(name, job_id):
    """
    _function_: `mast.web.credentials.record_test(name, job_id)`

    Store the outcome of a test job that has ended. The answer is
    `{outcome, credential}`; 409 if the job has not ended.

    Exposed at: `/api/v1/credentials/<name>/tests/<job_id>` via POST
    """
    who = principal()
    jobs_auth.authorize(who, "manage-credential")
    try:
        outcome = testing.record(name, job_id, principal=who)
        found = store.get(name)
    except testing.NotATest as exc:
        return error(409, str(exc))
    except store.CredentialError as exc:
        return _store_error(exc)
    return flask.jsonify({"outcome": outcome.json(),
                          "credential": credential_json(found)})


@blueprint.get("/credentials/<name>/uses")
def credential_uses(name):
    """
    _function_: `mast.web.credentials.credential_uses(name)`

    Exposed at: `/api/v1/credentials/<name>/uses` via GET
    """
    jobs_auth.authorize(principal(), "manage-credential")
    try:
        found = store.uses(name)
    except store.CredentialError as exc:
        return _store_error(exc)
    return flask.jsonify({"uses": [use.model_dump(mode="json")
                                   for use in found]})


@blueprint.get("/credentials/<name>/audit")
def credential_audit(name):
    """
    _function_: `mast.web.credentials.credential_audit(name)`

    Exposed at: `/api/v1/credentials/<name>/audit` via GET
    """
    jobs_auth.authorize(principal(), "manage-credential")
    return flask.jsonify({"events": [
        event.model_dump(mode="json")
        for event in store.audit_events(name)]})

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
_module_: `mast.registry`

The appliances and the environments that MAST knows, in the job
database. Before MAST kept them in `hosts.conf` (the aliases),
`environments.conf` (the environments) and the sections of
`appliances.conf` for single appliances; `mast-appliances import` moves
them here, and `mast-appliances export` writes them back.

A name that a user types (`-a NAME`) is resolved by `resolve`:

* the name of an environment becomes its appliances, in order;
* the name of an appliance becomes that appliance;
* any other name that is a hostname or an IP address stays an
*unregistered* target, with the defaults of `[global]` in
`appliances.conf`;
* anything else raises `UnknownName`.

The defaults of all appliances (`[global]` in `appliances.conf`) are
settings, not records, and stay in that file.

The functions return pydantic views (`Appliance`, `EnvironmentRec`),
never the database rows.
"""
import ipaddress
import re
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, func, select

from mast.jobs import db
from mast.registry.models import (
    ApplianceRecord, EnvironmentMember, EnvironmentRecord)

__all__ = [
    "Appliance", "EnvironmentRec", "RegistryError", "UnknownName",
    "NameTaken", "InvalidValue", "add", "update", "rename", "set_pin",
    "remove", "get", "by_name", "by_address", "is_address",
    "list_appliances", "env_add", "env_update", "env_remove",
    "env_set_members", "env_get", "env_by_name", "env_list",
    "which_environments", "resolve", "is_environment", "members",
    "TLS_MODES", "FIELDS",
]

TLS_MODES = ("ca", "pin", "none")
SCHEMES = ("http", "https")

# The fields of an appliance that add and update take
FIELDS = (
    "address", "soma_port", "web_port", "ssh_port", "soma_scheme",
    "soma_uri", "soma_spec_file", "retry_interval", "tls_mode",
    "ca_bundle", "poll", "notes",
)
_PORTS = ("soma_port", "web_port", "ssh_port")

# A name that a user can type: no spaces, no commas, no slashes
_NAME = re.compile(r"^[A-Za-z0-9_.:@\[\]-]{1,255}$")
_HOSTNAME = re.compile(
    r"^(?=.{1,253}$)([A-Za-z0-9_]([A-Za-z0-9_-]{0,61}[A-Za-z0-9_])?)"
    r"(\.[A-Za-z0-9_]([A-Za-z0-9_-]{0,61}[A-Za-z0-9_])?)*\.?$")


class RegistryError(ValueError):
    """A change to the registry was refused. The message says why."""


class UnknownName(RegistryError, LookupError):
    """No appliance or environment has this name, and it is not a
    hostname or an IP address."""


class NameTaken(RegistryError):
    """An appliance or an environment already has this name."""


class InvalidValue(RegistryError):
    """A field has a value that it cannot have."""


class Appliance(BaseModel):
    """
    _class_: `mast.registry.Appliance`

    An appliance. `registered` is False for a target that is not in
    the registry (its `name` and `address` are what the user typed, and
    every other field is the default). A field that is None takes the
    default of `[global]` in `appliances.conf`.
    """
    model_config = ConfigDict(from_attributes=True, frozen=True)

    name: str
    address: str
    registered: bool = True
    id: int | None = None
    soma_port: int | None = None
    web_port: int | None = None
    ssh_port: int | None = None
    soma_scheme: str | None = None
    soma_uri: str | None = None
    soma_spec_file: str | None = None
    retry_interval: int | None = None
    tls_mode: str = "ca"
    ca_bundle: str | None = None
    pin_sha256: str | None = None
    pinned_at: datetime | None = None
    pinned_by: str | None = None
    ssh_host_key: str | None = None
    ssh_pinned_at: datetime | None = None
    ssh_pinned_by: str | None = None
    poll: bool = False
    notes: str = ""
    environments: list[str] = []
    created_at: datetime | None = None
    updated_at: datetime | None = None


class EnvironmentRec(BaseModel):
    """
    _class_: `mast.registry.EnvironmentRec`

    An environment: a named group of appliances. `members` are the
    names of its appliances, in order. `production` marks the
    environments where some posture rules are stricter.
    """
    model_config = ConfigDict(frozen=True)

    id: int
    name: str
    description: str = ""
    production: bool = False
    members: list[str] = []


def _now():
    return datetime.now(timezone.utc)


def is_address(name):
    """
    _function_: `mast.registry.is_address(name)`

    Return True if `name` is a hostname or an IP address (v4 or v6,
    with or without brackets).
    """
    if not isinstance(name, str) or not name:
        return False
    try:
        ipaddress.ip_address(name.strip("[]"))
        return True
    except ValueError:
        pass
    return bool(_HOSTNAME.match(name))


def _check_name(name):
    if not isinstance(name, str) or not _NAME.match(name):
        raise InvalidValue(
            "A name has 1 to 255 letters, digits or the characters "
            "_ . : @ [ ] -, and no spaces: {!r}".format(name))
    return name


def _check_fields(fields):
    unknown = set(fields) - set(FIELDS)
    if unknown:
        raise InvalidValue("Unknown fields: {}".format(
            ", ".join(sorted(unknown))))
    values = dict(fields)
    for port in _PORTS:
        if values.get(port) is not None:
            try:
                values[port] = int(values[port])
            except (TypeError, ValueError):
                raise InvalidValue("{} must be a number".format(port))
            if not 0 < values[port] < 65536:
                raise InvalidValue("{} must be from 1 to 65535".format(port))
    if values.get("retry_interval") is not None:
        try:
            values["retry_interval"] = int(values["retry_interval"])
        except (TypeError, ValueError):
            raise InvalidValue("retry_interval must be a number")
    if values.get("soma_scheme") not in (None,) + SCHEMES:
        raise InvalidValue("soma_scheme is one of {}".format(
            ", ".join(SCHEMES)))
    if "tls_mode" in values and values["tls_mode"] not in TLS_MODES:
        raise InvalidValue("tls_mode is one of {}".format(
            ", ".join(TLS_MODES)))
    if "address" in values:
        if not is_address(values["address"]):
            raise InvalidValue("The address must be a hostname or an IP "
                               "address: {!r}".format(values["address"]))
    for text in ("soma_uri", "soma_spec_file", "ca_bundle"):
        if values.get(text) == "":
            values[text] = None
    if "notes" in values and values["notes"] is None:
        values["notes"] = ""
    if "poll" in values:
        values["poll"] = bool(values["poll"])
    return values


def _view(session, record):
    names = session.scalars(
        select(EnvironmentRecord.name)
        .join(EnvironmentMember,
              EnvironmentMember.environment_id == EnvironmentRecord.id)
        .where(EnvironmentMember.appliance_id == record.id)
        .order_by(EnvironmentRecord.name)).all()
    data = {column: getattr(record, column)
            for column in Appliance.model_fields
            if hasattr(record, column)}
    data["environments"] = list(names)
    data["registered"] = True
    return Appliance(**data)


def _env_view(session, record):
    return EnvironmentRec(
        id=record.id, name=record.name, description=record.description,
        production=record.production,
        members=list(session.scalars(
            select(ApplianceRecord.name)
            .join(EnvironmentMember,
                  EnvironmentMember.appliance_id == ApplianceRecord.id)
            .where(EnvironmentMember.environment_id == record.id)
            .order_by(EnvironmentMember.position))))


def _appliance(session, name):
    return session.scalars(
        select(ApplianceRecord).where(ApplianceRecord.name == name)).first()


def _environment(session, name):
    return session.scalars(
        select(EnvironmentRecord).where(EnvironmentRecord.name == name)).first()


def _require(session, name):
    record = _appliance(session, name)
    if record is None:
        raise UnknownName("There is no appliance {}.".format(name))
    return record


def _require_env(session, name):
    record = _environment(session, name)
    if record is None:
        raise UnknownName("There is no environment {}.".format(name))
    return record


def add(name, address=None, **fields):
    """
    _function_: `mast.registry.add(name, address=None, **fields)`

    Add an appliance and return its `Appliance`.

    Parameters:

    * `name`: What users type. No appliance or environment may have it
    already.
    * `address`: The hostname or the IP address to connect to. The
    default is `name`.
    * `fields`: Any of `FIELDS`: the ports, `soma_scheme`,
    `soma_uri`, `soma_spec_file`, `retry_interval`, `tls_mode` (`ca`
    by default), `ca_bundle`, `poll` and `notes`.
    """
    _check_name(name)
    values = _check_fields(dict(fields, address=address or name))
    now = _now()
    with db.session() as session:
        if _appliance(session, name) or _environment(session, name):
            raise NameTaken("The name {} is taken.".format(name))
        record = ApplianceRecord(
            name=name, tls_mode=values.pop("tls_mode", "ca"),
            poll=values.pop("poll", False), notes=values.pop("notes", ""),
            created_at=now, updated_at=now, **values)
        session.add(record)
        session.flush()
        return _view(session, record)


def update(name, **fields):
    """
    _function_: `mast.registry.update(name, **fields)`

    Change the fields of an appliance (any of `FIELDS`; None sets a
    field back to its default) and return its `Appliance`.
    """
    values = _check_fields(fields)
    with db.session() as session:
        record = _require(session, name)
        for field, value in values.items():
            if field == "tls_mode" and value is None:
                value = "ca"
            setattr(record, field, value)
        record.updated_at = _now()
        session.flush()
        return _view(session, record)


def rename(name, new_name):
    """
    _function_: `mast.registry.rename(name, new_name)`

    Give an appliance another name. It stays in its environments.
    """
    _check_name(new_name)
    with db.session() as session:
        record = _require(session, name)
        if new_name != name and (_appliance(session, new_name)
                                 or _environment(session, new_name)):
            raise NameTaken("The name {} is taken.".format(new_name))
        record.name = new_name
        record.updated_at = _now()
        session.flush()
        return _view(session, record)


def set_pin(name, **fields):
    """
    _function_: `mast.registry.set_pin(name, **fields)`

    Set the fields of the pins of an appliance (`pin_sha256`,
    `pinned_at`, `pinned_by`, `ssh_host_key`, `ssh_pinned_at`,
    `ssh_pinned_by`), which `update` does not change.
    """
    allowed = {"pin_sha256", "pinned_at", "pinned_by", "ssh_host_key",
               "ssh_pinned_at", "ssh_pinned_by"}
    if set(fields) - allowed:
        raise InvalidValue("Unknown fields: {}".format(
            ", ".join(sorted(set(fields) - allowed))))
    with db.session() as session:
        record = _require(session, name)
        for field, value in fields.items():
            setattr(record, field, value)
        record.updated_at = _now()
        session.flush()
        return _view(session, record)


def remove(name):
    """
    _function_: `mast.registry.remove(name)`

    Remove an appliance, and take it out of its environments.
    """
    with db.session() as session:
        record = _require(session, name)
        session.execute(delete(EnvironmentMember).where(
            EnvironmentMember.appliance_id == record.id))
        session.delete(record)


def get(name):
    """
    _function_: `mast.registry.get(name)`

    Return the `Appliance` called `name`. Raises `UnknownName`.
    """
    with db.session(write=False) as session:
        return _view(session, _require(session, name))


def by_name(name):
    """
    _function_: `mast.registry.by_name(name)`

    Return the `Appliance` called `name`, or None.
    """
    with db.session(write=False) as session:
        record = _appliance(session, name)
        return None if record is None else _view(session, record)


def by_address(address):
    """
    _function_: `mast.registry.by_address(address)`

    Return the `Appliance` whose address is `address` (compared
    without case), or None. If several have it, the first by name.
    """
    with db.session(write=False) as session:
        record = session.scalars(
            select(ApplianceRecord)
            .where(func.lower(ApplianceRecord.address)
                   == (address or "").strip().lower())
            .order_by(ApplianceRecord.name)).first()
        return None if record is None else _view(session, record)


def list_appliances():
    """
    _function_: `mast.registry.list_appliances()`

    Return every appliance, by name.
    """
    with db.session(write=False) as session:
        return [_view(session, record) for record in session.scalars(
            select(ApplianceRecord).order_by(ApplianceRecord.name))]


def _set_members(session, environment, members):
    session.execute(delete(EnvironmentMember).where(
        EnvironmentMember.environment_id == environment.id))
    seen = []
    for member in members:
        if member in seen:
            continue
        record = _appliance(session, member)
        if record is None:
            raise UnknownName("There is no appliance {}. Add it first.".format(
                member))
        session.add(EnvironmentMember(
            environment_id=environment.id, appliance_id=record.id,
            position=len(seen)))
        seen.append(member)


def env_add(name, description="", production=False, members=()):
    """
    _function_: `mast.registry.env_add(name, description="",
    production=False, members=())`

    Add an environment with these appliances (each must be in the
    registry) and return its `EnvironmentRec`.
    """
    _check_name(name)
    with db.session() as session:
        if _appliance(session, name) or _environment(session, name):
            raise NameTaken("The name {} is taken.".format(name))
        record = EnvironmentRecord(
            name=name, description=description or "",
            production=bool(production))
        session.add(record)
        session.flush()
        _set_members(session, record, members)
        session.flush()
        return _env_view(session, record)


def env_update(name, description=None, production=None):
    """
    _function_: `mast.registry.env_update(name, description=None,
    production=None)`

    Change the description or the production flag of an environment.
    """
    with db.session() as session:
        record = _require_env(session, name)
        if description is not None:
            record.description = description
        if production is not None:
            record.production = bool(production)
        session.flush()
        return _env_view(session, record)


def env_set_members(name, members):
    """
    _function_: `mast.registry.env_set_members(name, members)`

    Make `members` (names of appliances, in order) the appliances of
    an environment.
    """
    with db.session() as session:
        record = _require_env(session, name)
        _set_members(session, record, members)
        session.flush()
        return _env_view(session, record)


def env_remove(name):
    """
    _function_: `mast.registry.env_remove(name)`

    Remove an environment. Its appliances stay.
    """
    with db.session() as session:
        record = _require_env(session, name)
        session.execute(delete(EnvironmentMember).where(
            EnvironmentMember.environment_id == record.id))
        session.delete(record)


def env_get(name):
    """
    _function_: `mast.registry.env_get(name)`

    Return the `EnvironmentRec` called `name`. Raises `UnknownName`.
    """
    with db.session(write=False) as session:
        return _env_view(session, _require_env(session, name))


def env_by_name(name):
    """
    _function_: `mast.registry.env_by_name(name)`

    Return the `EnvironmentRec` called `name`, or None.
    """
    with db.session(write=False) as session:
        record = _environment(session, name)
        return None if record is None else _env_view(session, record)


def env_list():
    """
    _function_: `mast.registry.env_list()`

    Return every environment, by name.
    """
    with db.session(write=False) as session:
        return [_env_view(session, record) for record in session.scalars(
            select(EnvironmentRecord).order_by(EnvironmentRecord.name))]


def is_environment(name):
    """
    _function_: `mast.registry.is_environment(name)`

    Return True if an environment is called `name`.
    """
    with db.session(write=False) as session:
        return _environment(session, name) is not None


def members(name):
    """
    _function_: `mast.registry.members(name)`

    Return the names of the appliances of the environment `name`, in
    order, or None if there is no such environment.
    """
    found = env_by_name(name)
    return None if found is None else list(found.members)


def which_environments(name):
    """
    _function_: `mast.registry.which_environments(name)`

    Return the names of the environments that the appliance `name` is
    in (an empty list for an unregistered name).
    """
    found = by_name(name)
    return [] if found is None else list(found.environments)


def resolve(names):
    """
    _function_: `mast.registry.resolve(names)`

    Return the `Appliance`s that `names` stand for, in order and
    without repeats (see the top of this module). Raises
    `UnknownName` for a name that is neither registered nor a
    hostname or an IP address.
    """
    if isinstance(names, str):
        names = [names]
    # Imported here: it imports this module
    from mast.registry.files import warn_legacy
    warn_legacy()
    found = []
    seen = set()

    def take(appliance):
        if appliance.name not in seen:
            seen.add(appliance.name)
            found.append(appliance)

    with db.session(write=False) as session:
        for name in names:
            name = (name or "").strip()
            if not name:
                continue
            environment = _environment(session, name)
            if environment is not None:
                for member in _env_view(session, environment).members:
                    take(_view(session, _appliance(session, member)))
                continue
            record = _appliance(session, name)
            if record is not None:
                take(_view(session, record))
            elif is_address(name):
                take(Appliance(name=name, address=name.strip("[]"),
                               registered=False))
            else:
                raise UnknownName(
                    "{!r} is not an appliance, an environment, a hostname "
                    "or an IP address.".format(name))
    return found

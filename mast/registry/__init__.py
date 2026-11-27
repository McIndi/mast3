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

The targets and the fleets that MAST knows, in the job database.

A **target** has an id that never changes, a name, a base kind (such as
`datapower`, see `mast.plugins.Kind`), an address, and the values of
the attributes of its kind (`soma_port`, ...). It can have extensions:
kinds that extend its base kind (`apic-gateway` on a DataPower
appliance), each with the values of its own attributes, and a kind that
a host has more than once (two Django sites) once for each entry. Every
change raises the `revision` of the target.

Each **endpoint** of a target (a port that MAST connects to, declared
by its kind) has a **trust** record: how its identity is checked. A TLS
endpoint is checked by a certificate authority (`ca`), a pinned
certificate (`pin`) or not at all (`none`); an SSH endpoint by a pinned
host key. When the address of a target or the port of an endpoint
changes, a pin is no longer known to be right: the endpoint is marked
for review, and MAST does not connect to it until the pin is reviewed
(`review`) or replaced (`pin`).

A **fleet** is a named group of targets of one kind: each member is of
that kind, or of a kind that extends it.

A name that a user types (`-a NAME`) is resolved by `resolve`: a fleet
becomes its targets, in order, and a target is itself. Commands run
only on registered targets; anything else raises `UnknownName`.

The functions return pydantic views (`Target`, `Fleet`), never the
database rows.
"""
import ipaddress
import re
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, func, select

from mast import plugins
from mast.jobs import db
from mast.registry.models import (
    FleetMember, FleetRecord, TargetExtension, TargetRecord, TrustRecord)

__all__ = [
    "Target", "Extension", "Trust", "Fleet", "RegistryError", "UnknownName",
    "NameTaken", "InvalidValue", "add", "update", "rename", "extend",
    "update_extension", "unextend", "set_trust", "pin", "unpin", "review",
    "flag_mismatch", "remove", "get", "by_name", "by_id", "by_address",
    "list_targets", "fleet_add", "fleet_update", "fleet_set_members",
    "fleet_remove", "fleet_get", "fleet_by_name", "fleet_list", "is_fleet",
    "members", "which_fleets", "resolve", "is_address", "kinds_of", "is_a",
    "capabilities_of", "accepts", "check_attributes", "TLS_MODES",
    "credential_types_of",
    "SSH_MODES",
]

TLS_MODES = ("ca", "pin", "none")
SSH_MODES = ("pin", "none")

# A name that a user can type: no spaces, no commas, no slashes
_NAME = re.compile(r"^[A-Za-z0-9_.:@\[\]-]{1,255}$")
_HOSTNAME = re.compile(
    r"^(?=.{1,253}$)([A-Za-z0-9_]([A-Za-z0-9_-]{0,61}[A-Za-z0-9_])?)"
    r"(\.[A-Za-z0-9_]([A-Za-z0-9_-]{0,61}[A-Za-z0-9_])?)*\.?$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TRUE = ("1", "true", "yes", "on")
_FALSE = ("0", "false", "no", "off")


class RegistryError(ValueError):
    """A change to the registry was refused. The message says why."""


class UnknownName(RegistryError, LookupError):
    """No target or fleet has this name."""


class NameTaken(RegistryError):
    """A target or a fleet already has this name."""


class InvalidValue(RegistryError):
    """A field has a value that it cannot have."""


class Trust(BaseModel):
    """
    _class_: `mast.registry.Trust`

    How the identity of one endpoint of a target is checked.

    * `scope`: `""` for an endpoint of the base kind, `KIND` or
    `KIND:ENTRY` for one of an extension.
    * `endpoint`, `protocol` (`tls` or `ssh`), `port_attribute`,
    `title`: As the kind declares the endpoint (`mast.plugins.Endpoint`).
    * `mode`: TLS: `ca`, `pin` or `none`. SSH: `pin` or `none`.
    * `ca_bundle`: The PEM certificates for `ca`, or None for the trust
    store of the system.
    * `pin`: TLS: the SHA-256 of the certificate. SSH: the host key, or
    its fingerprint `SHA256:...`.
    * `mismatch`, `mismatch_at`: What the endpoint showed the last time
    it did not match the pin.
    * `review`: Why the pin must be reviewed before MAST connects, or
    None.
    """
    model_config = ConfigDict(frozen=True)

    scope: str = ""
    endpoint: str
    protocol: str = "tls"
    port_attribute: str | None = None
    title: str = ""
    mode: str = "ca"
    ca_bundle: str | None = None
    pin: str | None = None
    pinned_at: datetime | None = None
    pinned_by: str | None = None
    mismatch: str | None = None
    mismatch_at: datetime | None = None
    review: str | None = None


class Extension(BaseModel):
    """
    _class_: `mast.registry.Extension`

    One extension of a target: its `kind`, its `entry` (`""` for a kind
    that the target has once), and the values of its attributes.
    """
    model_config = ConfigDict(frozen=True)

    kind: str
    entry: str = ""
    attributes: dict = {}
    schema_version: int = 1

    @property
    def scope(self):
        """The scope of its endpoints: `KIND` or `KIND:ENTRY`."""
        return self.kind if not self.entry else "{}:{}".format(
            self.kind, self.entry)


class Target(BaseModel):
    """
    _class_: `mast.registry.Target`

    A registered target. `attributes` are the values that are set; use
    `attr(name)` for a value or the default of its kind. `trust` has one
    `Trust` for each endpoint of its kinds.
    """
    model_config = ConfigDict(frozen=True)

    id: int
    name: str
    kind: str
    address: str
    attributes: dict = {}
    # The types of stored credential that it takes, or None for all that
    # its kinds take (see credential_types_of)
    credential_types: list[str] | None = None
    schema_version: int = 1
    revision: int = 1
    notes: str = ""
    extensions: list[Extension] = []
    trust: list[Trust] = []
    fleets: list[str] = []
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def attr(self, name, default=None):
        """
        _method_: `mast.registry.Target.attr(self, name, default=None)`

        The value of the attribute `name` of the base kind: the value
        that is set, else the default of the kind, else `default`.
        """
        if self.attributes.get(name) is not None:
            return self.attributes[name]
        found = plugins.schema(self.kind).get(name)
        if found is not None and found.default is not None:
            return found.default
        return default

    def trust_for(self, endpoint, scope=""):
        """
        _method_: `mast.registry.Target.trust_for(self, endpoint, scope="")`

        The `Trust` of an endpoint, or None if the target has no such
        endpoint.
        """
        for item in self.trust:
            if item.endpoint == endpoint and item.scope == scope:
                return item
        return None

    def extension(self, kind, entry=""):
        """The `Extension` of `kind` (and `entry`), or None."""
        for item in self.extensions:
            if item.kind == kind and item.entry == entry:
                return item
        return None


class Fleet(BaseModel):
    """
    _class_: `mast.registry.Fleet`

    A fleet: a named group of targets of one `kind`. `members` are the
    names of its targets, in order. `production` marks the fleets where
    some posture rules are stricter.
    """
    model_config = ConfigDict(frozen=True)

    id: int
    name: str
    kind: str
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


def _check_address(address):
    if not is_address(address):
        raise InvalidValue("The address must be a hostname or an IP "
                           "address: {!r}".format(address))
    return address.strip("[]")


def _check_ca_bundle(text):
    import ssl
    if not isinstance(text, str) or "-----BEGIN CERTIFICATE-----" not in text:
        raise InvalidValue("ca_bundle is one or more PEM certificates "
                           "(-----BEGIN CERTIFICATE-----).")
    try:
        ssl.create_default_context(cadata=text)
    except (ssl.SSLError, ValueError) as exc:
        raise InvalidValue("ca_bundle has no certificate that can be "
                           "read: {}".format(exc))


def _value(kind, attribute, value):
    """Check one value of an attribute, and return it in its type. A
    value from the command line is a string."""
    label = "{} of {}".format(attribute.name, kind)
    if attribute.type in ("int", "port"):
        if isinstance(value, bool):
            raise InvalidValue("{} is a number".format(label))
        try:
            value = int(value)
        except (TypeError, ValueError):
            raise InvalidValue("{} is a number, not {!r}".format(label, value))
        if attribute.type == "port" and not 0 < value < 65536:
            raise InvalidValue("{} is a port, from 1 to 65535".format(label))
    elif attribute.type == "bool":
        if isinstance(value, str):
            if value.lower() in _TRUE:
                value = True
            elif value.lower() in _FALSE:
                value = False
        if not isinstance(value, bool):
            raise InvalidValue("{} is yes or no, not {!r}".format(
                label, value))
    else:
        if not isinstance(value, str):
            raise InvalidValue("{} is text".format(label))
        if attribute.type == "str" and "\n" in value:
            raise InvalidValue("{} is one line".format(label))
    if attribute.choices and value not in attribute.choices:
        raise InvalidValue("{} is one of {}, not {!r}".format(
            label, ", ".join(str(c) for c in attribute.choices), value))
    return value


def check_attributes(kind, values, current=None):
    """
    _function_: `mast.registry.check_attributes(kind, values,
    current=None)`

    Return the attributes of a target or an extension of `kind` after
    `values` are set on `current` (the values that are set now): each
    value checked and in its type, a value of None removed. Raises
    `InvalidValue` for an attribute that the kind does not have, a bad
    value, or a required attribute without a value, and for a kind that
    no installed plugin gives.
    """
    if kind not in plugins.kinds():
        raise InvalidValue(
            "No installed plugin gives the kind {!r}: install it to change "
            "these targets (see mast plugins kinds).".format(kind))
    schema = plugins.schema(kind)
    result = dict(current or {})
    for name, value in (values or {}).items():
        attribute = schema.get(name)
        if attribute is None:
            raise InvalidValue(
                "The kind {} has no attribute {!r}. It has: {}.".format(
                    kind, name, ", ".join(schema) or "none"))
        if value is None or value == "":
            result.pop(name, None)
        else:
            result[name] = _value(kind, attribute, value)
    missing = [a.name for a in schema.values()
               if a.required and result.get(a.name) is None
               and a.default is None]
    if missing:
        raise InvalidValue("A target of the kind {} needs {}.".format(
            kind, ", ".join(missing)))
    return result


def _trust_view(record, endpoint, scope=""):
    data = dict(scope=scope, endpoint=endpoint.name,
                protocol=endpoint.protocol, port_attribute=endpoint.port,
                title=endpoint.title)
    if record is None:
        data["mode"] = "ca" if endpoint.protocol == "tls" else "none"
    else:
        for column in ("mode", "ca_bundle", "pin", "pinned_at", "pinned_by",
                       "mismatch", "mismatch_at", "review"):
            data[column] = getattr(record, column)
    return Trust(**data)


def _view(session, record):
    fleets = session.scalars(
        select(FleetRecord.name)
        .join(FleetMember, FleetMember.fleet_id == FleetRecord.id)
        .where(FleetMember.target_id == record.id)
        .order_by(FleetRecord.name)).all()
    extensions = [Extension(kind=row.kind, entry=row.entry,
                            attributes=dict(row.attributes or {}),
                            schema_version=row.schema_version)
                  for row in session.scalars(
                      select(TargetExtension)
                      .where(TargetExtension.target_id == record.id)
                      .order_by(TargetExtension.kind, TargetExtension.entry))]
    rows = {(row.scope, row.endpoint): row for row in session.scalars(
        select(TrustRecord).where(TrustRecord.target_id == record.id))}
    trust = []
    scopes = [("", record.kind)] + [(e.scope, e.kind) for e in extensions]
    for scope, kind in scopes:
        for endpoint in plugins.endpoints_of(kind).values():
            trust.append(_trust_view(rows.pop((scope, endpoint.name), None),
                                     endpoint, scope))
    # The trust of an endpoint that the installed plugins do not declare
    # (a plugin that is missing) is kept and shown as it is
    for (scope, name), row in sorted(rows.items()):
        trust.append(_trust_view(row, plugins.Endpoint(name, "tls", ""),
                                 scope))
    return Target(
        id=record.id, name=record.name, kind=record.kind,
        address=record.address, attributes=dict(record.attributes or {}),
        credential_types=None if record.credential_types is None
        else list(record.credential_types),
        schema_version=record.schema_version, revision=record.revision,
        notes=record.notes, extensions=extensions, trust=trust,
        fleets=list(fleets), created_at=record.created_at,
        updated_at=record.updated_at)


def _fleet_view(session, record):
    return Fleet(
        id=record.id, name=record.name, kind=record.kind,
        description=record.description, production=record.production,
        members=list(session.scalars(
            select(TargetRecord.name)
            .join(FleetMember, FleetMember.target_id == TargetRecord.id)
            .where(FleetMember.fleet_id == record.id)
            .order_by(FleetMember.position))))


def _target(session, name):
    return session.scalars(
        select(TargetRecord).where(TargetRecord.name == name)).first()


def _fleet(session, name):
    return session.scalars(
        select(FleetRecord).where(FleetRecord.name == name)).first()


def _require(session, name):
    record = _target(session, name)
    if record is None:
        raise UnknownName("There is no target {}.".format(name))
    return record


def _require_fleet(session, name):
    record = _fleet(session, name)
    if record is None:
        raise UnknownName("There is no fleet {}.".format(name))
    return record


def _touch(record):
    record.revision = (record.revision or 0) + 1
    record.updated_at = _now()


def _add_trust(session, record, kind, scope):
    for endpoint in plugins.endpoints_of(kind).values():
        session.add(TrustRecord(
            target_id=record.id, scope=scope, endpoint=endpoint.name,
            mode="ca" if endpoint.protocol == "tls" else "none"))


def _trust_row(session, record, endpoint, scope, create=True):
    """The TrustRecord of an endpoint of a target. Raises `InvalidValue`
    for an endpoint that the target does not have."""
    target = _view(session, record)
    found = target.trust_for(endpoint, scope)
    if found is None:
        raise InvalidValue("{} has no endpoint {}{}. It has: {}.".format(
            record.name, endpoint, " in " + scope if scope else "",
            ", ".join((t.scope + "/" if t.scope else "") + t.endpoint
                      for t in target.trust) or "none"))
    row = session.scalars(select(TrustRecord).where(
        TrustRecord.target_id == record.id, TrustRecord.scope == scope,
        TrustRecord.endpoint == endpoint)).first()
    if row is None and create:
        row = TrustRecord(target_id=record.id, scope=scope, endpoint=endpoint,
                          mode=found.mode)
        session.add(row)
    return row, found


def _ask_review(session, record, scope, endpoints, reason):
    """Mark the pinned endpoints among `endpoints` (names; None for all)
    of `scope` for review."""
    query = select(TrustRecord).where(TrustRecord.target_id == record.id,
                                      TrustRecord.pin.is_not(None))
    if scope is not None:
        query = query.where(TrustRecord.scope == scope)
    for row in session.scalars(query):
        if endpoints is None or row.endpoint in endpoints:
            row.review = reason


def _ports_changed(kind, before, after):
    """The endpoints of `kind` whose port attribute changed."""
    return [e.name for e in plugins.endpoints_of(kind).values()
            if before.get(e.port) != after.get(e.port)]


def add(name, kind, address=None, attributes=None, notes="",
        credential_types=None):
    """
    _function_: `mast.registry.add(name, kind, address=None,
    attributes=None, notes="", credential_types=None)`

    Add a target and return its `Target`.

    * `name`: What users type. No target or fleet may have it already.
    * `kind`: Its base kind, which never changes. A plugin that is used
    must give it.
    * `address`: The hostname or the IP address to connect to. The
    default is `name`.
    * `attributes`: The values of the attributes of the kind (see
    `mast plugins kinds`); the others take their defaults.
    * `credential_types`: The types of stored credential that it takes,
    of those of the kind; None for all of them.
    """
    _check_name(name)
    found = plugins.kinds().get(kind)
    if found is None:
        raise InvalidValue(
            "No installed plugin gives the kind {!r}. The kinds are: {} "
            "(mast plugins kinds).".format(kind, ", ".join(
                sorted(k for k, v in plugins.kinds().items()
                       if v.extends is None))))
    if found.extends is not None:
        raise InvalidValue(
            "{} extends {}: add a target of its base kind {}, then the "
            "extension (mast targets extend).".format(
                kind, found.extends, plugins.base_of(kind)))
    values = check_attributes(kind, attributes)
    credential_types = _check_credential_types([kind], credential_types)
    address = _check_address(address or name)
    now = _now()
    with db.session() as session:
        if _target(session, name) or _fleet(session, name):
            raise NameTaken("The name {} is taken.".format(name))
        record = TargetRecord(
            name=name, kind=kind, address=address, attributes=values,
            credential_types=credential_types,
            schema_version=found.version, revision=1, notes=notes or "",
            created_at=now, updated_at=now)
        session.add(record)
        session.flush()
        _add_trust(session, record, kind, "")
        session.flush()
        return _view(session, record)


_ALL = object()


def update(name, address=None, attributes=None, notes=None,
           credential_types=_ALL):
    """
    _function_: `mast.registry.update(name, address=None,
    attributes=None, notes=None, credential_types=...)`

    Change the address, attributes (a value of None sets one back to
    its default) or notes of a target, and return its `Target`. A new
    address marks every pinned endpoint for review; a new port marks
    that endpoint.

    `credential_types` sets the types of stored credential that the
    target takes, as it is configured now (a list of the types of its
    kinds; None for all of them). A credential of another type is then
    refused on it.
    """
    with db.session() as session:
        record = _require(session, name)
        if attributes:
            before = dict(record.attributes or {})
            after = check_attributes(record.kind, attributes, before)
            changed = _ports_changed(record.kind, before, after)
            if changed:
                _ask_review(session, record, "", changed,
                            "the port changed")
            record.attributes = after
            record.schema_version = plugins.schema_version(record.kind)
        if address is not None:
            address = _check_address(address)
            if address != record.address:
                _ask_review(session, record, None, None,
                            "the address changed from {} to {}".format(
                                record.address, address))
                record.address = address
        if credential_types is not _ALL:
            record.credential_types = _check_credential_types(
                kinds_of(_view(session, record)), credential_types)
        if notes is not None:
            record.notes = notes
        _touch(record)
        session.flush()
        return _view(session, record)


def rename(name, new_name):
    """
    _function_: `mast.registry.rename(name, new_name)`

    Give a target another name. Its id, its fleets and its credentials
    stay.
    """
    _check_name(new_name)
    with db.session() as session:
        record = _require(session, name)
        if new_name != name and (_target(session, new_name)
                                 or _fleet(session, new_name)):
            raise NameTaken("The name {} is taken.".format(new_name))
        record.name = new_name
        _touch(record)
        session.flush()
        return _view(session, record)


def _check_extension(record, kind):
    found = plugins.kinds().get(kind)
    if found is None:
        raise InvalidValue("No installed plugin gives the kind {!r} (mast "
                           "plugins kinds).".format(kind))
    if found.extends is None:
        raise InvalidValue("{} is a base kind, not an extension.".format(kind))
    if plugins.base_of(kind) != record.kind:
        raise InvalidValue(
            "{} extends {}, and {} is a {}: an extension extends the base "
            "kind of the target.".format(kind, plugins.base_of(kind),
                                         record.name, record.kind))
    return found


def extend(name, kind, entry="", attributes=None):
    """
    _function_: `mast.registry.extend(name, kind, entry="",
    attributes=None)`

    Add the extension `kind` to the target `name`, with the values of
    its attributes. Give `entry` (a name) for a kind that the target has
    more than once. Return the `Target`.
    """
    entry = entry or ""
    if entry:
        _check_name(entry)
    with db.session() as session:
        record = _require(session, name)
        found = _check_extension(record, kind)
        if session.scalars(select(TargetExtension).where(
                TargetExtension.target_id == record.id,
                TargetExtension.kind == kind,
                TargetExtension.entry == entry)).first():
            raise NameTaken("{} already has the extension {}{}.".format(
                name, kind, " " + entry if entry else ""))
        values = check_attributes(kind, attributes)
        row = TargetExtension(target_id=record.id, kind=kind, entry=entry,
                              attributes=values, schema_version=found.version)
        session.add(row)
        session.flush()
        scope = kind if not entry else "{}:{}".format(kind, entry)
        _add_trust(session, record, kind, scope)
        _touch(record)
        session.flush()
        return _view(session, record)


def _require_extension(session, record, kind, entry):
    row = session.scalars(select(TargetExtension).where(
        TargetExtension.target_id == record.id,
        TargetExtension.kind == kind,
        TargetExtension.entry == (entry or ""))).first()
    if row is None:
        raise UnknownName("{} has no extension {}{}.".format(
            record.name, kind, " " + entry if entry else ""))
    return row


def update_extension(name, kind, entry="", attributes=None):
    """
    _function_: `mast.registry.update_extension(name, kind, entry="",
    attributes=None)`

    Change the attributes of an extension of a target. A new port marks
    that endpoint of the extension for review.
    """
    with db.session() as session:
        record = _require(session, name)
        row = _require_extension(session, record, kind, entry)
        before = dict(row.attributes or {})
        after = check_attributes(kind, attributes, before)
        changed = _ports_changed(kind, before, after)
        if changed:
            scope = kind if not entry else "{}:{}".format(kind, entry)
            _ask_review(session, record, scope, changed, "the port changed")
        row.attributes = after
        row.schema_version = plugins.schema_version(kind)
        _touch(record)
        session.flush()
        return _view(session, record)


def unextend(name, kind, entry=""):
    """
    _function_: `mast.registry.unextend(name, kind, entry="")`

    Remove an extension from a target, with the trust of its endpoints.
    The target leaves the fleets of that kind.
    """
    with db.session() as session:
        record = _require(session, name)
        row = _require_extension(session, record, kind, entry)
        scope = kind if not entry else "{}:{}".format(kind, entry)
        session.execute(delete(TrustRecord).where(
            TrustRecord.target_id == record.id, TrustRecord.scope == scope))
        session.delete(row)
        session.flush()
        target = _view(session, record)
        for fleet in session.scalars(
                select(FleetRecord)
                .join(FleetMember, FleetMember.fleet_id == FleetRecord.id)
                .where(FleetMember.target_id == record.id)):
            if not is_a(target, fleet.kind):
                session.execute(delete(FleetMember).where(
                    FleetMember.fleet_id == fleet.id,
                    FleetMember.target_id == record.id))
        _touch(record)
        session.flush()
        return _view(session, record)


def set_trust(name, endpoint, mode=None, ca_bundle=None, scope=""):
    """
    _function_: `mast.registry.set_trust(name, endpoint, mode=None,
    ca_bundle=None, scope="")`

    Set how the identity of an endpoint is checked: `mode` (TLS: `ca`,
    `pin` or `none`; SSH: `pin` or `none`) and, for `ca`, `ca_bundle`
    (PEM; `""` for the trust store of the system). `pin` needs a pinned
    certificate or host key (see `pin`).
    """
    with db.session() as session:
        record = _require(session, name)
        row, found = _trust_row(session, record, endpoint, scope)
        modes = TLS_MODES if found.protocol == "tls" else SSH_MODES
        if mode is not None:
            if mode not in modes:
                raise InvalidValue("The trust of a {} endpoint is one of "
                                   "{}.".format(found.protocol.upper(),
                                                ", ".join(modes)))
            if mode == "pin" and not row.pin:
                raise InvalidValue(
                    "The trust of {} {} cannot be pin before it is pinned. "
                    "Pin it with mast targets pin -n {} -e {}.".format(
                        name, endpoint, name, endpoint))
            row.mode = mode
        if ca_bundle is not None:
            if found.protocol != "tls":
                raise InvalidValue("Only a TLS endpoint has a CA bundle.")
            if ca_bundle:
                _check_ca_bundle(ca_bundle)
            row.ca_bundle = ca_bundle or None
        _touch(record)
        session.flush()
        return _view(session, record)


def pin(name, endpoint, value, principal, scope=""):
    """
    _function_: `mast.registry.pin(name, endpoint, value, principal,
    scope="")`

    Pin the identity of an endpoint and check it by that pin from now
    on: for TLS, the SHA-256 of the certificate (64 lowercase hex
    digits); for SSH, the host key (`"<type> <base64>"`) or its
    fingerprint (`SHA256:...`). `principal` pinned it. An earlier
    mismatch and a review are cleared.
    """
    with db.session() as session:
        record = _require(session, name)
        row, found = _trust_row(session, record, endpoint, scope)
        if found.protocol == "tls":
            if not isinstance(value, str) or not _SHA256.match(value):
                raise InvalidValue("A TLS pin is the SHA-256 of the "
                                   "certificate, 64 lowercase hex digits.")
        elif not isinstance(value, str) or not value.strip() \
                or len(value) > 16384:
            raise InvalidValue("An SSH pin is '<type> <base64>' or "
                               "SHA256:...")
        row.pin = value.strip()
        row.mode = "pin"
        row.pinned_at = _now()
        row.pinned_by = principal
        row.mismatch = row.mismatch_at = row.review = None
        _touch(record)
        session.flush()
        return _view(session, record)


def unpin(name, endpoint, scope=""):
    """
    _function_: `mast.registry.unpin(name, endpoint, scope="")`

    Remove the pin of an endpoint. A TLS endpoint is then checked by a
    certificate authority (`ca`); an SSH endpoint is not checked.
    """
    with db.session() as session:
        record = _require(session, name)
        row, found = _trust_row(session, record, endpoint, scope)
        row.pin = row.pinned_at = row.pinned_by = None
        row.mismatch = row.mismatch_at = row.review = None
        row.mode = "ca" if found.protocol == "tls" else "none"
        _touch(record)
        session.flush()
        return _view(session, record)


def review(name, endpoint, principal, scope=""):
    """
    _function_: `mast.registry.review(name, endpoint, principal,
    scope="")`

    Keep the pin of an endpoint that was marked for review: `principal`
    checked that it is still right.
    """
    with db.session() as session:
        record = _require(session, name)
        row, _ = _trust_row(session, record, endpoint, scope)
        if row.review is None:
            raise InvalidValue("{} {} is not marked for review.".format(
                name, endpoint))
        row.review = None
        row.pinned_at = _now()
        row.pinned_by = principal
        _touch(record)
        session.flush()
        return _view(session, record)


def flag_mismatch(name, endpoint, got, scope=""):
    """
    _function_: `mast.registry.flag_mismatch(name, endpoint, got,
    scope="")`

    Remember that the endpoint showed `got` (the SHA-256 of a
    certificate, the fingerprint of a host key), which is not its pin.
    An unknown target or endpoint is ignored.
    """
    with db.session() as session:
        record = _target(session, name)
        if record is None:
            return
        row = session.scalars(select(TrustRecord).where(
            TrustRecord.target_id == record.id, TrustRecord.scope == scope,
            TrustRecord.endpoint == endpoint)).first()
        if row is None:
            return
        row.mismatch = str(got)[:100]
        row.mismatch_at = _now()


def remove(name):
    """
    _function_: `mast.registry.remove(name)`

    Remove a target, its extensions and trust, and take it out of its
    fleets.
    """
    with db.session() as session:
        record = _require(session, name)
        for model in (FleetMember, TrustRecord, TargetExtension):
            session.execute(delete(model).where(
                model.target_id == record.id))
        session.delete(record)


def get(name):
    """
    _function_: `mast.registry.get(name)`

    Return the `Target` called `name`. Raises `UnknownName`.
    """
    with db.session(write=False) as session:
        return _view(session, _require(session, name))


def by_name(name):
    """
    _function_: `mast.registry.by_name(name)`

    Return the `Target` called `name`, or None.
    """
    with db.session(write=False) as session:
        record = _target(session, name)
        return None if record is None else _view(session, record)


def by_id(target_id):
    """
    _function_: `mast.registry.by_id(target_id)`

    Return the `Target` with the id `target_id`, or None.
    """
    with db.session(write=False) as session:
        record = session.get(TargetRecord, target_id)
        return None if record is None else _view(session, record)


def by_address(address, kind=None):
    """
    _function_: `mast.registry.by_address(address, kind=None)`

    Return the `Target` (of `kind`, if given) whose address is `address`
    (compared without case), or None. If several have it, the first by
    name.
    """
    with db.session(write=False) as session:
        for record in session.scalars(
                select(TargetRecord)
                .where(func.lower(TargetRecord.address)
                       == (address or "").strip("[] ").lower())
                .order_by(TargetRecord.name)):
            target = _view(session, record)
            if kind is None or is_a(target, kind):
                return target
        return None


def list_targets(kind=None):
    """
    _function_: `mast.registry.list_targets(kind=None)`

    Return every target, by name: of `kind` (see `is_a`), if given.
    """
    with db.session(write=False) as session:
        found = [_view(session, record) for record in session.scalars(
            select(TargetRecord).order_by(TargetRecord.name))]
    return [t for t in found if kind is None or is_a(t, kind)]


def kinds_of(target):
    """
    _function_: `mast.registry.kinds_of(target)`

    Return the kinds of a `Target`: its base kind, then the kinds of its
    extensions.
    """
    found = [target.kind]
    for extension in target.extensions:
        if extension.kind not in found:
            found.append(extension.kind)
    return found


def is_a(target, kind):
    """
    _function_: `mast.registry.is_a(target, kind)`

    True if the `Target` is of `kind`: its base kind or one of its
    extensions is `kind`, or extends it. A kind that no installed plugin
    gives matches only by its name.
    """
    for own in kinds_of(target):
        if own == kind or kind in plugins.lineage(own):
            return True
    return False


def _kind_credential_types(kinds):
    found = set()
    for kind in kinds:
        found.update(plugins.credential_types(kind))
    return found


def credential_types_of(target):
    """
    _function_: `mast.registry.credential_types_of(target)`

    Return the types of stored credential that a `Target` takes: those
    that it is set to take (how it is configured: a host whose SSH
    takes only keys), or all that its kinds take.
    """
    taken = _kind_credential_types(kinds_of(target))
    if target.credential_types is None:
        return frozenset(taken)
    return frozenset(set(target.credential_types) & taken)


def _check_credential_types(kinds, values):
    """The types of credential to set on a target of `kinds`: a list
    that its kinds take, or None for all of them. Raises
    `InvalidValue`."""
    if values is None:
        return None
    if isinstance(values, str):
        values = [values]
    taken = _kind_credential_types(kinds)
    values = sorted(set(values))
    wrong = [v for v in values if v not in taken]
    if wrong or not values:
        raise InvalidValue(
            "A target of {} takes the credential types {}; {} is not one "
            "of them.".format(" + ".join(kinds),
                              ", ".join(sorted(taken)) or "none",
                              ", ".join(wrong) or "none of them"))
    return values


def capabilities_of(target):
    """
    _function_: `mast.registry.capabilities_of(target)`

    Return the capabilities of a `Target`: those of its base kind and of
    each of its extensions.
    """
    found = set()
    for own in kinds_of(target):
        found.update(plugins.capabilities(own))
    return frozenset(found)


def accepts(target, targets=(), needs=()):
    """
    _function_: `mast.registry.accepts(target, targets=(), needs=())`

    True if a command that accepts the kinds `targets` and needs the
    capabilities `needs` (see `mast.cli.Command`) takes the `Target`.
    """
    if targets and not any(is_a(target, kind) for kind in targets):
        return False
    return set(needs) <= capabilities_of(target)


def _set_members(session, fleet, members):
    session.execute(delete(FleetMember).where(
        FleetMember.fleet_id == fleet.id))
    seen = []
    for member in members:
        if member in seen:
            continue
        record = _target(session, member)
        if record is None:
            raise UnknownName("There is no target {}. Add it first.".format(
                member))
        if not is_a(_view(session, record), fleet.kind):
            raise InvalidValue(
                "{} is not a {}: the fleet {} holds only targets of that "
                "kind.".format(member, fleet.kind, fleet.name))
        session.add(FleetMember(fleet_id=fleet.id, target_id=record.id,
                                position=len(seen)))
        seen.append(member)


def fleet_add(name, kind, description="", production=False, members=()):
    """
    _function_: `mast.registry.fleet_add(name, kind, description="",
    production=False, members=())`

    Add a fleet of targets of `kind` (a base kind or an extension), with
    these members, and return its `Fleet`.
    """
    _check_name(name)
    if kind not in plugins.kinds():
        raise InvalidValue("No installed plugin gives the kind {!r} (mast "
                           "plugins kinds).".format(kind))
    with db.session() as session:
        if _target(session, name) or _fleet(session, name):
            raise NameTaken("The name {} is taken.".format(name))
        record = FleetRecord(name=name, kind=kind,
                             description=description or "",
                             production=bool(production))
        session.add(record)
        session.flush()
        _set_members(session, record, members)
        session.flush()
        return _fleet_view(session, record)


def fleet_update(name, description=None, production=None):
    """
    _function_: `mast.registry.fleet_update(name, description=None,
    production=None)`

    Change the description or the production flag of a fleet. Its kind
    never changes.
    """
    with db.session() as session:
        record = _require_fleet(session, name)
        if description is not None:
            record.description = description
        if production is not None:
            record.production = bool(production)
        session.flush()
        return _fleet_view(session, record)


def fleet_set_members(name, members):
    """
    _function_: `mast.registry.fleet_set_members(name, members)`

    Make `members` (names of targets of the kind of the fleet, in order)
    the targets of a fleet.
    """
    with db.session() as session:
        record = _require_fleet(session, name)
        _set_members(session, record, members)
        session.flush()
        return _fleet_view(session, record)


def fleet_remove(name):
    """
    _function_: `mast.registry.fleet_remove(name)`

    Remove a fleet. Its targets stay.
    """
    with db.session() as session:
        record = _require_fleet(session, name)
        session.execute(delete(FleetMember).where(
            FleetMember.fleet_id == record.id))
        session.delete(record)


def fleet_get(name):
    """
    _function_: `mast.registry.fleet_get(name)`

    Return the `Fleet` called `name`. Raises `UnknownName`.
    """
    with db.session(write=False) as session:
        return _fleet_view(session, _require_fleet(session, name))


def fleet_by_name(name):
    """
    _function_: `mast.registry.fleet_by_name(name)`

    Return the `Fleet` called `name`, or None.
    """
    with db.session(write=False) as session:
        record = _fleet(session, name)
        return None if record is None else _fleet_view(session, record)


def fleet_list():
    """
    _function_: `mast.registry.fleet_list()`

    Return every fleet, by name.
    """
    with db.session(write=False) as session:
        return [_fleet_view(session, record) for record in session.scalars(
            select(FleetRecord).order_by(FleetRecord.name))]


def is_fleet(name):
    """
    _function_: `mast.registry.is_fleet(name)`

    Return True if a fleet is called `name`.
    """
    with db.session(write=False) as session:
        return _fleet(session, name) is not None


def members(name):
    """
    _function_: `mast.registry.members(name)`

    Return the names of the targets of the fleet `name`, in order, or
    None if there is no such fleet.
    """
    found = fleet_by_name(name)
    return None if found is None else list(found.members)


def which_fleets(name):
    """
    _function_: `mast.registry.which_fleets(name)`

    Return the names of the fleets that the target `name` is in (an
    empty list for a name that is not registered).
    """
    found = by_name(name)
    return [] if found is None else list(found.fleets)


def resolve(names):
    """
    _function_: `mast.registry.resolve(names)`

    Return the `Target`s that `names` stand for, in order and without
    repeats: a fleet becomes its targets, a target is itself. Raises
    `UnknownName` for any other name: commands run only on registered
    targets.
    """
    if isinstance(names, str):
        names = [names]
    # Imported here: it imports this module
    from mast.registry.files import warn_legacy
    warn_legacy()
    found = []
    seen = set()

    def take(target):
        if target.id not in seen:
            seen.add(target.id)
            found.append(target)

    with db.session(write=False) as session:
        for name in names:
            name = (name or "").strip()
            if not name:
                continue
            fleet = _fleet(session, name)
            if fleet is not None:
                for member in _fleet_view(session, fleet).members:
                    take(_view(session, _target(session, member)))
                continue
            record = _target(session, name)
            if record is not None:
                take(_view(session, record))
                continue
            hint = ""
            if is_address(name):
                same = session.scalars(
                    select(TargetRecord.name)
                    .where(func.lower(TargetRecord.address)
                           == name.strip("[]").lower())
                    .order_by(TargetRecord.name)).all()
                if len(same) == 1:
                    hint = " The target {} has this address.".format(same[0])
                elif same:
                    hint = " The targets {} have this address.".format(
                        " and ".join(same))
                else:
                    hint = (" Register it first: mast targets add -n NAME "
                            "-k KIND -A {}.".format(name))
            raise UnknownName("{!r} is not a registered target or "
                              "fleet.{}".format(name, hint))
    return found

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
_module_: `mast.registry.cli`

`mast targets` and `mast fleets`: the targets and the fleets that MAST
knows (see `mast.registry`).

`mast targets`:

* `add`, `update`, `remove`, `list`, `show`: The targets. The settings
of a target are the attributes of its kind (`mast plugins kinds`),
given as `-s NAME=VALUE`.
* `extend`, `set-extension`, `unextend`: The extensions of a target.
* `trust`, `pin`, `unpin`, `review`: How the identity of each endpoint
of a target is checked (see `mast.trust`).
* `import`, `export`: The `hosts.conf`, `environments.conf` and
`appliances.conf` of MAST 3.x.

`mast fleets`: `add`, `update`, `set-members`, `remove`, `list`.

Whoever can run these on the MAST host can read the database, so they
need no login; they are an admin.
"""
import sys
from typing import Literal

from mast import registry
from mast.cli import Cli, Option, Params, Table, Text
from mast.roots import CORE
from mast.registry import files

__all__ = ["targets", "fleets"]

targets = Cli(description="Manage the targets of MAST: register them, and "
                          "check who they are (their TLS certificates and "
                          "SSH host keys).",
              provider=CORE, prog="mast targets", queued=False)
fleets = Cli(description="Manage the fleets of MAST: named groups of targets "
                         "of one kind.",
             provider=CORE, prog="mast fleets", queued=False)

YesNo = Literal["yes", "no"]


def _fail(message):
    print("mast targets: {}".format(message), file=sys.stderr)
    sys.exit(1)


def _do(change, *args, **kwargs):
    try:
        return change(*args, **kwargs)
    except registry.RegistryError as exc:
        _fail(exc)


def _settings(values):
    """`NAME=VALUE` strings as a dict."""
    found = {}
    for item in values or ():
        name, sep, value = item.partition("=")
        if not sep or not name.strip():
            _fail("A setting is NAME=VALUE, not {!r}. The names are the "
                  "attributes of the kind (mast plugins kinds).".format(item))
        found[name.strip()] = value
    return found


def _endpoint(text):
    """`ENDPOINT` or `SCOPE/ENDPOINT` as (scope, endpoint)."""
    scope, _, endpoint = (text or "").rpartition("/")
    return scope, endpoint


class NameParams(Params):
    name: str = Option(short="n", help="The name of the target.")


class Add(NameParams):
    kind: str = Option(
        short="k", help="The base kind of the target, such as datapower "
                        "(see mast plugins kinds). It never changes.")
    address: str | None = Option(
        None, short="A",
        help="The hostname or the IP address to connect to. The default "
             "is the name.")
    set: list[str] = Option(
        default_factory=list, short="s",
        help="An attribute of the kind, as NAME=VALUE (for example "
             "soma_port=5550). One that is not given takes its default.")
    notes: str = Option("", help="Notes about the target.")
    credential_type: list[str] = Option(
        default_factory=list,
        help="A type of stored credential that the target takes (give it "
             "again for each). The default is every type of its kind.")


class Update(NameParams):
    address: str | None = Option(
        None, short="A",
        help="A new address. Each pinned endpoint is then marked for "
             "review.")
    set: list[str] = Option(
        default_factory=list, short="s",
        help="An attribute of the kind, as NAME=VALUE. A new port marks "
             "its pinned endpoint for review.")
    clear: list[str] = Option(
        default_factory=list,
        help="An attribute to set back to its default.")
    rename: str | None = Option(None, help="A new name for the target.")
    notes: str | None = Option(None, help="Notes about the target.")
    credential_type: list[str] = Option(
        default_factory=list,
        help="A type of stored credential that the target takes now, as "
             "it is configured (give it again for each): for example "
             "ssh-key alone once a host takes no passwords. A credential "
             "of another type is then refused on it.")
    any_credential_type: bool = Option(
        False, help="The target takes every type of credential of its "
                    "kinds again.")


def _trust_line(trust):
    label = trust.endpoint if not trust.scope else "{}/{}".format(
        trust.scope, trust.endpoint)
    text = "{} ({}): {}".format(label, trust.protocol, trust.mode)
    if trust.pin:
        text += " " + trust.pin[:20] + ("..." if len(trust.pin) > 20 else "")
    if trust.ca_bundle:
        text += ", {} CA certificate(s)".format(
            trust.ca_bundle.count("-----BEGIN CERTIFICATE-----"))
    if trust.mismatch:
        text += "; CHANGED: it showed {}".format(trust.mismatch)
    if trust.review:
        text += "; REVIEW: {}".format(trust.review)
    return text


def _show(target):
    from mast import plugins
    lines = ["{} ({} at {}, id {}, revision {})".format(
        target.name, target.kind, target.address, target.id, target.revision)]
    schema = plugins.schema(target.kind)
    for name, attribute in schema.items():
        value = target.attributes.get(name)
        if value is not None:
            lines.append("  {}: {}".format(name, value))
        elif attribute.default is not None:
            lines.append("  {}: {} (default)".format(name, attribute.default))
    for name, value in target.attributes.items():
        if name not in schema:
            lines.append("  {}: {} (not an attribute of the installed "
                         "kind)".format(name, value))
    for extension in target.extensions:
        lines.append("  extension {}{}{}".format(
            extension.kind, " " + extension.entry if extension.entry else "",
            ": " + ", ".join("{}={}".format(k, v) for k, v in
                             extension.attributes.items())
            if extension.attributes else ""))
    lines.append("  credential types: {}{}".format(
        ", ".join(sorted(registry.credential_types_of(target))) or "none",
        "" if target.credential_types is None else " (set on the target)"))
    for trust in target.trust:
        lines.append("  trust " + _trust_line(trust))
    if target.fleets:
        lines.append("  fleets: " + " ".join(target.fleets))
    if target.notes:
        lines.append("  notes: " + target.notes)
    return "\n".join(lines)


@targets.command("add", effect="change", id="add-target")
def add(params: Add):
    """Add a target of a kind (see mast plugins kinds). The attributes of
    the kind are given as -s NAME=VALUE; one that is not given takes its
    default. Its endpoints are checked by a certificate authority (TLS)
    or not at all (SSH) until they are pinned (mast targets pin)."""
    return Text("Added " + _show(_do(
        registry.add, params.name, params.kind, params.address,
        _settings(params.set), params.notes,
        params.credential_type or None)))


@targets.command("update", effect="change", id="update-target")
def update(params: Update):
    """Change the address or the attributes of a target, or rename it.
    Its kind never changes. A new address marks every pinned endpoint
    for review, and a new port its endpoint: MAST does not connect to it
    until an admin reviews it (mast targets review or pin)."""
    values = _settings(params.set)
    for name in params.clear:
        values[name] = None
    if params.credential_type and params.any_credential_type:
        _fail("Give --credential-type or --any-credential-type, not both.")
    types = {}
    if params.credential_type:
        types["credential_types"] = params.credential_type
    elif params.any_credential_type:
        types["credential_types"] = None
    found = _do(registry.update, params.name, address=params.address,
                attributes=values, notes=params.notes, **types)
    if params.rename:
        found = _do(registry.rename, params.name, params.rename)
    return Text("Updated " + _show(found))


@targets.command("remove", effect="change", id="remove-target")
def remove(params: NameParams):
    """Remove a target, and take it out of its fleets."""
    _do(registry.remove, params.name)
    return Text("Removed {}.".format(params.name))


class ListTargets(Params):
    kind: str | None = Option(
        None, short="k", help="Only the targets of this kind (or of a kind "
                              "that extends it).")


@targets.command("list", effect="read", id="list-targets")
def list_targets(params: ListTargets):
    """List the targets, with the trust of each endpoint. CHANGED: the
    last connection found another certificate or host key than the
    pinned one. REVIEW: the address or a port changed, and the pin must
    be reviewed."""
    found = registry.list_targets(params.kind)
    if not found:
        return Text("There are no targets. Add one with mast targets add, or "
                    "import hosts.conf and environments.conf with mast "
                    "targets import.")

    def trust(target):
        parts = []
        for item in target.trust:
            text = "{}:{}".format(item.endpoint, item.mode)
            if item.mismatch:
                text += " CHANGED"
            if item.review:
                text += " REVIEW"
            parts.append(text)
        return " ".join(parts)

    return Table(
        ("Name", "Kind", "Address", "Trust", "Fleets", "Revision"),
        [(t.name, " + ".join(registry.kinds_of(t)), t.address, trust(t),
          " ".join(t.fleets), t.revision) for t in found])


@targets.command("show", effect="read", id="show-target")
def show(params: NameParams):
    """Show a target: its id, kind, address, revision, attributes (with
    the defaults of its kind), extensions, the trust of each endpoint and
    its fleets."""
    return Text(_show(_do(registry.get, params.name)))


class Extend(NameParams):
    kind: str = Option(short="k", help="The kind of the extension.")
    entry: str = Option(
        "", short="e", help="A name for this one, for a kind that the target "
                            "has more than once (two Django sites).")
    set: list[str] = Option(
        default_factory=list, short="s",
        help="An attribute of the extension, as NAME=VALUE.")


@targets.command("extend", effect="change", id="extend-target")
def extend(params: Extend):
    """Add an extension to a target: a kind that extends its base kind
    (mast plugins kinds), with its attributes. The target is then of that
    kind too, for the commands and the fleets of that kind."""
    return Text("Extended " + _show(_do(
        registry.extend, params.name, params.kind, params.entry,
        _settings(params.set))))


class SetExtension(Extend):
    clear: list[str] = Option(
        default_factory=list,
        help="An attribute to set back to its default.")


@targets.command("set-extension", effect="change", id="set-target-extension")
def set_extension(params: SetExtension):
    """Change the attributes of an extension of a target. A new port
    marks its pinned endpoint for review."""
    values = _settings(params.set)
    for name in params.clear:
        values[name] = None
    return Text("Updated " + _show(_do(
        registry.update_extension, params.name, params.kind, params.entry,
        values)))


class Unextend(NameParams):
    kind: str = Option(short="k", help="The kind of the extension.")
    entry: str = Option("", short="e", help="Its entry, if it has one.")


@targets.command("unextend", effect="change", id="unextend-target")
def unextend(params: Unextend):
    """Remove an extension from a target, with the trust of its
    endpoints. The target leaves the fleets of that kind."""
    return Text("Updated " + _show(_do(
        registry.unextend, params.name, params.kind, params.entry)))


class TrustParams(NameParams):
    endpoint: str = Option(
        short="e", help="The endpoint, such as soma or ssh (mast targets "
                        "show), or SCOPE/ENDPOINT for one of an extension.")


class SetTrust(TrustParams):
    mode: Literal["ca", "pin", "none"] | None = Option(
        None, short="m",
        help="How its identity is checked: ca (a trusted certificate "
             "authority; TLS only), pin (the certificate or host key that "
             "mast targets pin stored) or none (not at all; stored "
             "credentials are refused).")
    ca_bundle_file: str | None = Option(
        None, help="A PEM file with the certificate authorities that sign "
                   "the certificate of the endpoint, for the mode ca.")
    system_ca: bool = Option(
        False, help="Use the trust store of the system for the mode ca "
                    "(remove the CA bundle).")


@targets.command("trust", effect="change", id="trust-target")
def set_trust(params: SetTrust):
    """Set how the identity of an endpoint of a target is checked: its
    mode, and for ca the certificate authorities."""
    bundle = None
    if params.ca_bundle_file:
        try:
            with open(params.ca_bundle_file, encoding="utf-8") as fin:
                bundle = fin.read()
        except (OSError, UnicodeDecodeError) as exc:
            _fail("Cannot read {}: {}".format(params.ca_bundle_file, exc))
    elif params.system_ca:
        bundle = ""
    scope, endpoint = _endpoint(params.endpoint)
    return Text("Updated " + _show(_do(
        registry.set_trust, params.name, endpoint, params.mode, bundle,
        scope)))


class Pin(NameParams):
    endpoint: list[str] = Option(
        default_factory=list, short="e",
        help="An endpoint to pin (mast targets show). The default is every "
             "endpoint of the target.")
    fingerprint: str | None = Option(
        None, short="f",
        help="Pin this fingerprint, which you checked on the target, "
             "without connecting: the SHA-256 of the certificate (hex) for "
             "a TLS endpoint, or the SHA256:... of the host key, as "
             "ssh-keygen -l shows it, for an SSH endpoint. Give one "
             "endpoint with it.")
    yes: bool = Option(False, short="y", help="Do not ask to confirm.")


def _confirm(question, yes):
    if yes:
        return True
    try:
        answer = input(question + " [y/N] ")
    except EOFError:
        answer = ""
    return answer.strip().lower() in ("y", "yes")


def _print_found(name, target, label, item):
    if item["protocol"] == "ssh":
        print("The SSH host key of {} {} ({}:{}):".format(
            name, label, target.address, item["port"]))
        print("  {:12} {}".format("Type:", item["type"]))
        print("  {:12} {}".format("Fingerprint:", item["fingerprint"]))
        return
    print("The certificate of {} {} ({}:{}):".format(
        name, label, target.address, item["port"]))
    for title, key in (("Subject", "subject"), ("Issuer", "issuer"),
                       ("Valid from", "not_before"),
                       ("Valid until", "not_after"), ("SHA-256", "sha256")):
        print("  {:12} {}".format(title + ":", item[key]))


def _pin_value(item):
    return item["key"] if item["protocol"] == "ssh" else item["sha256"]


@targets.command("pin", effect="change", id="pin-target")
def pin(params: Pin):
    """Pin the TLS certificate or the SSH host key of the endpoints of a
    target, and check them by that pin from now on. This is how a stored
    credential can be used with a target that has a self-signed
    certificate.

    It connects to each endpoint (without a credential), shows the
    certificate or the host key and asks to confirm. Check what it shows
    on the target itself, for example on its console, before you
    confirm. With --fingerprint it pins the fingerprint that you give and
    does not connect."""
    from mast import trust
    from mast.jobs.auth import LOCAL
    target = _do(registry.get, params.name)
    if params.fingerprint:
        if len(params.endpoint) != 1:
            _fail("Give one endpoint (-e) with --fingerprint.")
        scope, endpoint = _endpoint(params.endpoint[0])
        found = target.trust_for(endpoint, scope)
        if found is None:
            _fail("{} has no endpoint {}.".format(params.name,
                                                  params.endpoint[0]))
        try:
            if found.protocol == "ssh":
                value = trust.normalize_ssh_pin(params.fingerprint)
                if not value.startswith("SHA256:"):
                    raise ValueError("give the SHA256:... fingerprint")
            else:
                value = trust.normalize_fingerprint(params.fingerprint)
        except ValueError as exc:
            _fail(exc)
        _do(registry.pin, params.name, endpoint, value, LOCAL.name, scope)
        return Text("Pinned {} of {}: {}.".format(
            params.endpoint[0], params.name, value))
    found = trust.preview(target, params.endpoint or None)
    if params.endpoint and not found:
        _fail("{} has no endpoint {}.".format(params.name,
                                              ", ".join(params.endpoint)))
    lines, failed = [], False
    for label, item in found.items():
        if "error" in item:
            print(item["error"])
            failed = True
            continue
        _print_found(params.name, target, label, item)
        if _confirm("Pin it?", params.yes):
            _do(registry.pin, params.name, item["endpoint"], _pin_value(item),
                LOCAL.name, item["scope"])
            lines.append("Pinned {} of {}.".format(label, params.name))
        else:
            lines.append("{} of {} was not pinned.".format(label,
                                                           params.name))
    if failed:
        if lines:
            print("\n".join(lines))
        sys.exit(1)
    return Text("\n".join(lines))


@targets.command("unpin", effect="change", id="unpin-target")
def unpin(params: TrustParams):
    """Remove the pin of an endpoint of a target: a TLS endpoint is then
    checked by a certificate authority (ca), an SSH endpoint not at
    all."""
    scope, endpoint = _endpoint(params.endpoint)
    _do(registry.unpin, params.name, endpoint, scope)
    return Text("Removed the pin of {} of {}.".format(params.endpoint,
                                                      params.name))


class Review(TrustParams):
    yes: bool = Option(False, short="y", help="Do not ask to confirm.")


@targets.command("review", effect="change", id="review-target")
def review(params: Review):
    """Review the pin of an endpoint that is marked for review (its
    address or port changed). It connects to the endpoint (without a
    credential) and shows what it has now. If that is the pinned
    certificate or host key, it asks to keep the pin; if not, pin the new
    one with mast targets pin after you checked it."""
    from mast import trust
    from mast.jobs.auth import LOCAL
    target = _do(registry.get, params.name)
    scope, endpoint = _endpoint(params.endpoint)
    current = target.trust_for(endpoint, scope)
    if current is None:
        _fail("{} has no endpoint {}.".format(params.name, params.endpoint))
    if not current.review:
        return Text("{} of {} is not marked for review.".format(
            params.endpoint, params.name))
    print("{} of {} must be reviewed: {}.".format(
        params.endpoint, params.name, current.review))
    found = trust.preview(target, [params.endpoint])
    item = next(iter(found.values()), {"error": "It was not found."})
    if "error" in item:
        _fail(item["error"])
    _print_found(params.name, target, params.endpoint, item)
    if current.protocol == "ssh":
        matches = trust.ssh_fingerprint(item["key"]) == \
            trust.ssh_fingerprint(current.pin) \
            if not current.pin.startswith("SHA256:") \
            else trust.ssh_fingerprint(item["key"]) == current.pin
    else:
        matches = item["sha256"] == current.pin
    if not matches:
        _fail("This is not the pinned {}. Check it on the target, then pin "
              "it with mast targets pin -n {} -e {}.".format(
                  "host key" if current.protocol == "ssh" else "certificate",
                  params.name, params.endpoint))
    if not _confirm("It is the pinned one. Keep the pin?", params.yes):
        return Text("The pin of {} of {} is still marked for review.".format(
            params.endpoint, params.name))
    _do(registry.review, params.name, endpoint, LOCAL.name, scope)
    return Text("Kept the pin of {} of {}.".format(params.endpoint,
                                                   params.name))


class FleetParams(Params):
    name: str = Option(short="n", help="The name of the fleet.")


class FleetAdd(FleetParams):
    kind: str = Option(
        short="k", help="The kind of its targets (a base kind or an "
                        "extension). It never changes.")
    description: str = Option("", short="d", help="What it is.")
    production: bool = Option(
        False, short="p", help="It is a production fleet.")
    members: list[str] = Option(
        default_factory=list, short="m",
        help="A target in it, by name.")


class FleetUpdate(FleetParams):
    description: str | None = Option(None, short="d", help="What it is.")
    production: YesNo | None = Option(
        None, short="p", help="Whether it is a production fleet.")


class FleetMembers(FleetParams):
    members: list[str] = Option(
        default_factory=list, short="m",
        help="A target in it, by name. The list replaces the members.")


def _show_fleet(fleet):
    return "{} ({}){}: {}".format(
        fleet.name, fleet.kind, " (production)" if fleet.production else "",
        " ".join(fleet.members) or "no targets")


@fleets.command("add", effect="change", id="add-fleet")
def fleet_add(params: FleetAdd):
    """Add a fleet of targets of one kind. Each member must be of that
    kind, or of a kind that extends it."""
    return Text("Added " + _show_fleet(_do(
        registry.fleet_add, params.name, params.kind, params.description,
        params.production, params.members)))


@fleets.command("update", effect="change", id="update-fleet")
def fleet_update(params: FleetUpdate):
    """Change the description or the production flag of a fleet."""
    production = None if params.production is None \
        else params.production == "yes"
    return Text("Updated " + _show_fleet(_do(
        registry.fleet_update, params.name, params.description, production)))


@fleets.command("set-members", effect="change", id="set-fleet-members")
def fleet_set_members(params: FleetMembers):
    """Set the targets of a fleet."""
    return Text("Set " + _show_fleet(_do(
        registry.fleet_set_members, params.name, params.members)))


@fleets.command("remove", effect="change", id="remove-fleet")
def fleet_remove(params: FleetParams):
    """Remove a fleet. Its targets stay."""
    _do(registry.fleet_remove, params.name)
    return Text("Removed the fleet {}.".format(params.name))


@fleets.command("list", effect="read", id="list-fleets")
def fleet_list(params: Params):
    """List the fleets."""
    found = registry.fleet_list()
    if not found:
        return Text("There are no fleets.")
    return Table(("Name", "Kind", "Production", "Targets", "Description"),
                 [(f.name, f.kind, "yes" if f.production else "",
                   " ".join(f.members), f.description) for f in found])


class Import(Params):
    hosts: str | None = Option(
        None, help="hosts.conf. The default is etc/local/hosts.conf.")
    environments: str | None = Option(
        None, help="environments.conf. The default is "
                   "etc/local/environments.conf.")
    appliances: str | None = Option(
        None, help="appliances.conf. The default is "
                   "etc/local/appliances.conf.")
    dry_run: bool = Option(
        False, short="d", help="Only show what would be done.")


@targets.command("import", effect="change", id="import-targets")
def import_files(params: Import):
    """Move the appliances and environments of hosts.conf,
    environments.conf and appliances.conf of MAST 3.x into the registry.
    Every alias of hosts.conf, every name in environments.conf and every
    section of appliances.conf for one appliance becomes a target of the
    kind datapower, and every environment a fleet of that kind. Then the
    files are renamed to *.imported (appliances.conf keeps its [global]
    section). A target or a fleet that is registered already is kept as
    it is."""
    return Text("\n".join(files.import_files(
        params.hosts, params.environments, params.appliances,
        dry_run=params.dry_run)))


class Export(Params):
    out_dir: str | None = Option(
        None, short="o", help="Where to write the files. The default is "
                              "etc/local.")
    force: bool = Option(
        False, short="f", help="Replace files that are there.")


@targets.command("export", effect="read", id="export-targets")
def export_files(params: Export):
    """Write the targets and the fleets of the kind datapower as
    hosts.conf, environments.conf and appliances.conf, which MAST 3.15.2
    reads, to go back to it."""
    try:
        paths = files.export_files(params.out_dir, force=params.force)
    except FileExistsError as exc:
        _fail("{} is there already. Give --force to replace it.".format(exc))
    return Text("Wrote " + ", ".join(paths))

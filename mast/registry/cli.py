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

`mast targets` and `mast fleets`: the targets (appliances) and the
fleets (environments) that MAST knows (see `mast.registry`).

`mast targets`:

* `add`, `update`, `remove`, `list`: The targets.
* `pin`, `unpin`: The pinned TLS certificate and SSH host key of a
target (see `mast.datapower.datapower.tls`).
* `import`: Move `hosts.conf`, `environments.conf` and the sections of
`appliances.conf` for single appliances into the registry.
* `export`: Write the registry back as those files.

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
                          "pin their TLS certificates and SSH host keys.",
              provider=CORE, prog="mast targets", queued=False)
fleets = Cli(description="Manage the fleets of MAST: named groups of targets.",
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


class NameParams(Params):
    name: str = Option(short="n", help="The name of the appliance.")


class Settings(Params):
    address: str | None = Option(
        None, short="A",
        help="The hostname or the IP address to connect to. The default "
             "is the name.")
    soma_port: int | None = Option(None, help="The port of SOMA.")
    web_port: int | None = Option(None, help="The port of the WebGUI.")
    ssh_port: int | None = Option(None, help="The port of SSH.")
    soma_scheme: Literal["https", "http"] | None = Option(
        None, help="The scheme of SOMA.")
    soma_uri: str | None = Option(None, help="The URI of SOMA.")
    soma_spec_file: str | None = Option(
        None, help="The SOMA spec file, for example etc/v10.6.0.11-C.xml.")
    retry_interval: int | None = Option(
        None, help="Seconds before a failed request to SOMA is tried again.")
    tls_mode: Literal["ca", "pin", "none"] | None = Option(
        None, help="How the TLS certificate of the appliance is checked: "
                   "ca (a trusted certificate authority), pin (the "
                   "certificate that mast targets pin stored) or none "
                   "(not at all; stored credentials are refused).")
    ca_bundle_file: str | None = Option(
        None, help="A PEM file with the certificate authorities that sign "
                   "the certificate of the appliance, for the TLS check "
                   "ca. The default is the trust store of the system.")
    poll: YesNo | None = Option(
        None, help="Collect the status of the appliance.")
    notes: str | None = Option(None, help="Notes about the appliance.")


def _fields(params, clear=()):
    values = {}
    if getattr(params, "ca_bundle_file", None):
        try:
            with open(params.ca_bundle_file, encoding="utf-8") as fin:
                values["ca_bundle"] = fin.read()
        except (OSError, UnicodeDecodeError) as exc:
            _fail("Cannot read {}: {}".format(params.ca_bundle_file, exc))
    for field in registry.FIELDS:
        value = getattr(params, field, None)
        if value is None:
            continue
        values[field] = value == "yes" if field == "poll" else value
    for field in clear:
        if field not in registry.FIELDS or field == "address":
            _fail("{} cannot be cleared.".format(field))
        values[field] = None
    return values


class Add(NameParams, Settings):
    pass


class Update(NameParams, Settings):
    rename: str | None = Option(None, help="A new name for the appliance.")
    clear: list[str] = Option(
        default_factory=list,
        help="A setting to set back to its default (soma_port, "
             "soma_spec_file and so on).")


def _show(appliance):
    lines = ["{} ({})".format(appliance.name, appliance.address)]
    for field in registry.FIELDS + ("pin_sha256", "ssh_host_key", "environments"):
        value = getattr(appliance, field)
        if field == "ca_bundle" and value:
            value = "{} certificate(s)".format(
                value.count("-----BEGIN CERTIFICATE-----"))
        if value not in (None, "", []) and field != "address":
            lines.append("  {}: {}".format(
                field, " ".join(value) if isinstance(value, list) else value))
    return "\n".join(lines)


@targets.command("add", id="add-target")
def add(params: Add):
    """Add an appliance. A setting that is not given takes the default of
    [global] in appliances.conf."""
    return Text("Added " + _show(_do(
        registry.add, params.name, **_fields(params))))


@targets.command("update", id="update-target")
def update(params: Update):
    """Change the settings of an appliance, or rename it."""
    found = _do(registry.update, params.name,
                **_fields(params, params.clear))
    if params.rename:
        found = _do(registry.rename, params.name, params.rename)
    return Text("Updated " + _show(found))


@targets.command("remove", id="remove-target")
def remove(params: NameParams):
    """Remove an appliance, and take it out of its environments."""
    _do(registry.remove, params.name)
    return Text("Removed {}.".format(params.name))


@targets.command("list", id="list-targets")
def list_appliances(params: Params):
    """List the appliances. CHANGED: the last connection found another
    certificate or SSH host key than the pinned one."""
    found = registry.list_appliances()
    if not found:
        return Text("There are no appliances. Add one with "
                    "mast targets add, or import hosts.conf and "
                    "environments.conf with mast targets import.")
    def check(mode, mismatch):
        return mode + (" CHANGED" if mismatch else "")

    return Table(
        ("Name", "Address", "SOMA port", "TLS", "SSH key", "Poll",
         "Environments"),
        [(a.name, a.address, a.soma_port or "",
          check(a.tls_mode, a.tls_mismatch),
          check("pinned" if a.ssh_host_key else "", a.ssh_mismatch).strip(),
          "yes" if a.poll else "", " ".join(a.environments))
         for a in found])


class Pin(NameParams):
    ssh: bool = Option(
        False, help="Pin the SSH host key, not the TLS certificate.")
    both: bool = Option(
        False, help="Pin the TLS certificate and the SSH host key.")
    fingerprint: str | None = Option(
        None, short="f",
        help="Pin this fingerprint, which you checked on the appliance, "
             "without connecting: the SHA-256 of the certificate (hex), "
             "or with --ssh the SHA256:... of the host key, as "
             "ssh-keygen -l shows it.")
    yes: bool = Option(False, short="y", help="Do not ask to confirm.")


def _confirm(question, yes):
    if yes:
        return True
    try:
        answer = input(question + " [y/N] ")
    except EOFError:
        answer = ""
    return answer.strip().lower() in ("y", "yes")


@targets.command("pin", id="pin-target")
def pin(params: Pin):
    """Pin the TLS certificate of the SOMA port of an appliance, and so
    check its TLS by that pin (the TLS check pin), or with --ssh its SSH
    host key. This is how a stored credential can be used with an
    appliance that has a self-signed certificate.

    It connects to the appliance (without a credential), shows the
    certificate or the host key and asks to confirm. Check what it
    shows on the appliance itself, for example on its console (show
    crypto ... or the SSH banner), before you confirm. With
    --fingerprint it pins the fingerprint that you give and does not
    connect."""
    # Imported here: reading a certificate is of the DataPower code
    from mast.datapower.datapower import tls
    from mast.jobs.auth import LOCAL
    if params.both and params.fingerprint:
        _fail("--fingerprint is for one pin: give it with or without "
              "--ssh, not with --both.")
    record = _do(registry.get, params.name)
    kinds = ["tls", "ssh"] if params.both else (
        ["ssh"] if params.ssh else ["tls"])
    lines = []
    if params.fingerprint:
        try:
            if kinds == ["ssh"]:
                value = tls.normalize_ssh_pin(params.fingerprint)
                if not value.startswith("SHA256:"):
                    raise ValueError("give the SHA256:... fingerprint")
            else:
                value = tls.normalize_fingerprint(params.fingerprint)
        except ValueError as exc:
            _fail(exc)
        if kinds == ["ssh"]:
            _do(registry.pin_ssh, params.name, value, LOCAL.name)
            return Text("Pinned the SSH host key {} of {}.".format(
                value, params.name))
        _do(registry.pin_tls, params.name, value, LOCAL.name)
        return Text("Pinned the certificate {} of {}. Its TLS check is "
                    "pin.".format(value, params.name))
    found = tls.preview(record, tls="tls" in kinds, ssh="ssh" in kinds)
    failed = False
    if "tls" in found:
        cert = found["tls"]
        if "error" in cert:
            print(cert["error"])
            failed = True
        else:
            print("The certificate of {} ({}:{}):".format(
                params.name, record.address, cert["port"]))
            for label, key in (("Subject", "subject"), ("Issuer", "issuer"),
                               ("Valid from", "not_before"),
                               ("Valid until", "not_after"),
                               ("SHA-256", "sha256")):
                print("  {:12} {}".format(label + ":", cert[key]))
            if _confirm("Pin this certificate?", params.yes):
                _do(registry.pin_tls, params.name, cert["sha256"], LOCAL.name)
                lines.append("Pinned the certificate of {}. Its TLS check "
                             "is pin.".format(params.name))
            else:
                lines.append("The certificate of {} was not pinned.".format(
                    params.name))
    if "ssh" in found:
        key = found["ssh"]
        if "error" in key:
            print(key["error"])
            failed = True
        else:
            print("The SSH host key of {} ({}:{}):".format(
                params.name, record.address, key["port"]))
            print("  {:12} {}".format("Type:", key["type"]))
            print("  {:12} {}".format("Fingerprint:", key["fingerprint"]))
            if _confirm("Pin this host key?", params.yes):
                _do(registry.pin_ssh, params.name, key["key"], LOCAL.name)
                lines.append("Pinned the SSH host key of {}.".format(
                    params.name))
            else:
                lines.append("The SSH host key of {} was not pinned.".format(
                    params.name))
    if failed:
        if lines:
            print("\n".join(lines))
        sys.exit(1)
    return Text("\n".join(lines))


class Unpin(NameParams):
    ssh: bool = Option(False, help="Remove the pinned SSH host key.")
    both: bool = Option(
        False, help="Remove the pinned certificate and SSH host key.")


@targets.command("unpin", id="unpin-target")
def unpin(params: Unpin):
    """Remove the pinned certificate of an appliance (its TLS check
    becomes ca), or with --ssh its pinned SSH host key."""
    tls = params.both or not params.ssh
    ssh = params.both or params.ssh
    _do(registry.unpin, params.name, tls=tls, ssh=ssh)
    return Text("Removed the {} of {}.".format(
        " and the ".join(
            ([] if not tls else ["pinned certificate"]) +
            ([] if not ssh else ["pinned SSH host key"])), params.name))


class EnvParams(Params):
    name: str = Option(short="n", help="The name of the environment.")


class EnvAdd(EnvParams):
    description: str = Option("", short="d", help="What it is.")
    production: bool = Option(
        False, short="p", help="It is a production environment.")
    members: list[str] = Option(
        default_factory=list, short="m",
        help="An appliance in it, by name.")


class EnvUpdate(EnvParams):
    description: str | None = Option(None, short="d", help="What it is.")
    production: YesNo | None = Option(
        None, short="p", help="Whether it is a production environment.")


class EnvMembers(EnvParams):
    members: list[str] = Option(
        default_factory=list, short="m",
        help="An appliance in it, by name. The list replaces the members.")


def _show_env(environment):
    return "{}{}: {}".format(
        environment.name, " (production)" if environment.production else "",
        " ".join(environment.members) or "no appliances")


@fleets.command("add", id="add-fleet")
def env_add(params: EnvAdd):
    """Add an environment."""
    return Text("Added " + _show_env(_do(
        registry.env_add, params.name, params.description,
        params.production, params.members)))


@fleets.command("update", id="update-fleet")
def env_update(params: EnvUpdate):
    """Change the description or the production flag of an environment."""
    production = None if params.production is None \
        else params.production == "yes"
    return Text("Updated " + _show_env(_do(
        registry.env_update, params.name, params.description, production)))


@fleets.command("set-members", id="set-fleet-members")
def env_set_members(params: EnvMembers):
    """Set the appliances of an environment."""
    return Text("Set " + _show_env(_do(
        registry.env_set_members, params.name, params.members)))


@fleets.command("remove", id="remove-fleet")
def env_remove(params: EnvParams):
    """Remove an environment. Its appliances stay."""
    _do(registry.env_remove, params.name)
    return Text("Removed the environment {}.".format(params.name))


@fleets.command("list", id="list-fleets")
def env_list(params: Params):
    """List the environments."""
    found = registry.env_list()
    if not found:
        return Text("There are no environments.")
    return Table(("Name", "Production", "Appliances", "Description"),
                 [(e.name, "yes" if e.production else "",
                   " ".join(e.members), e.description) for e in found])


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


@targets.command("import", id="import-targets")
def import_files(params: Import):
    """Move the appliances and environments of hosts.conf,
    environments.conf and appliances.conf into the registry. Every
    alias of hosts.conf, every name in environments.conf and every
    section of appliances.conf for one appliance becomes an appliance.
    Then the files are renamed to *.imported (appliances.conf keeps its
    [global] section). An appliance or an environment that is
    registered already is kept as it is."""
    return Text("\n".join(files.import_files(
        params.hosts, params.environments, params.appliances,
        dry_run=params.dry_run)))


class Export(Params):
    out_dir: str | None = Option(
        None, short="o", help="Where to write the files. The default is "
                              "etc/local.")
    force: bool = Option(
        False, short="f", help="Replace files that are there.")


@targets.command("export", id="export-targets")
def export_files(params: Export):
    """Write the registry as hosts.conf, environments.conf and
    appliances.conf, which MAST 3.15.2 reads, to go back to it."""
    try:
        paths = files.export_files(params.out_dir, force=params.force)
    except FileExistsError as exc:
        _fail("{} is there already. Give --force to replace it.".format(exc))
    return Text("Wrote " + ", ".join(paths))

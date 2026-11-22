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

`mast-appliances`: the appliances and the environments that MAST
knows (see `mast.registry`).

* `add`, `update`, `remove`, `list`: The appliances.
* `env-add`, `env-update`, `env-set-members`, `env-remove`, `env-list`:
The environments.
* `import`: Move `hosts.conf`, `environments.conf` and the sections of
`appliances.conf` for single appliances into the registry.
* `export`: Write the registry back as those files, to go back to an
older MAST.

Whoever can run `mast-appliances` on the MAST host can read the
database, so it needs no login; it is an admin.
"""
import sys
from typing import Literal

from mast import registry
from mast.cli import Cli, Option, Params, Table, Text
from mast.registry import files

__all__ = ["cli"]

cli = Cli(description="Manage the appliances and environments of MAST.",
          prog="mast-appliances")

YesNo = Literal["yes", "no"]


def _fail(message):
    print("mast-appliances: {}".format(message), file=sys.stderr)
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
                   "certificate that mast-appliances pin stored) or none "
                   "(not at all; stored credentials are refused).")
    poll: YesNo | None = Option(
        None, help="Collect the status of the appliance.")
    notes: str | None = Option(None, help="Notes about the appliance.")


def _fields(params, clear=()):
    values = {}
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
    for field in registry.FIELDS + ("environments",):
        value = getattr(appliance, field)
        if value not in (None, "", []) and field != "address":
            lines.append("  {}: {}".format(
                field, " ".join(value) if isinstance(value, list) else value))
    return "\n".join(lines)


@cli.command("add")
def add(params: Add):
    """Add an appliance. A setting that is not given takes the default of
    [global] in appliances.conf."""
    return Text("Added " + _show(_do(
        registry.add, params.name, **_fields(params))))


@cli.command("update")
def update(params: Update):
    """Change the settings of an appliance, or rename it."""
    found = _do(registry.update, params.name,
                **_fields(params, params.clear))
    if params.rename:
        found = _do(registry.rename, params.name, params.rename)
    return Text("Updated " + _show(found))


@cli.command("remove")
def remove(params: NameParams):
    """Remove an appliance, and take it out of its environments."""
    _do(registry.remove, params.name)
    return Text("Removed {}.".format(params.name))


@cli.command("list")
def list_appliances(params: Params):
    """List the appliances."""
    found = registry.list_appliances()
    if not found:
        return Text("There are no appliances. Add one with "
                    "mast-appliances add, or import hosts.conf and "
                    "environments.conf with mast-appliances import.")
    return Table(
        ("Name", "Address", "SOMA port", "TLS", "Poll", "Environments"),
        [(a.name, a.address, a.soma_port or "", a.tls_mode,
          "yes" if a.poll else "", " ".join(a.environments))
         for a in found])


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


@cli.command("env-add")
def env_add(params: EnvAdd):
    """Add an environment."""
    return Text("Added " + _show_env(_do(
        registry.env_add, params.name, params.description,
        params.production, params.members)))


@cli.command("env-update")
def env_update(params: EnvUpdate):
    """Change the description or the production flag of an environment."""
    production = None if params.production is None \
        else params.production == "yes"
    return Text("Updated " + _show_env(_do(
        registry.env_update, params.name, params.description, production)))


@cli.command("env-set-members")
def env_set_members(params: EnvMembers):
    """Set the appliances of an environment."""
    return Text("Set " + _show_env(_do(
        registry.env_set_members, params.name, params.members)))


@cli.command("env-remove")
def env_remove(params: EnvParams):
    """Remove an environment. Its appliances stay."""
    _do(registry.env_remove, params.name)
    return Text("Removed the environment {}.".format(params.name))


@cli.command("env-list")
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


@cli.command("import")
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


@cli.command("export")
def export_files(params: Export):
    """Write the registry as hosts.conf, environments.conf and
    appliances.conf, which MAST 3.15.2 reads, to go back to it."""
    try:
        paths = files.export_files(params.out_dir, force=params.force)
    except FileExistsError as exc:
        _fail("{} is there already. Give --force to replace it.".format(exc))
    return Text("Wrote " + ", ".join(paths))

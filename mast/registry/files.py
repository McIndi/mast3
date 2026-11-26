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
_module_: `mast.registry.files`

The way into the appliance registry from the configuration files of
MAST 3.15.2 and older, and the way back.

* `import_files`: Make appliances from the aliases of `hosts.conf`,
from every name in `environments.conf`, and from the sections of
`appliances.conf` for single appliances; make the environments; then
rename the files to `*.imported`, so that it is clear that MAST no
longer reads them.
* `export_files`: Write the registry as those three files, for a
return to an older MAST.
* `warn_legacy`: Say once, in the log and on stderr, that a home still
has `hosts.conf` or `environments.conf` that were not imported.
"""
import configparser
import os
import sys

from mast import registry
from mast.config import MAST_HOME
from mast.logging import make_logger

__all__ = ["import_files", "export_files", "legacy_files", "warn_legacy",
           "SETTINGS"]

# The settings of appliances.conf that an appliance can have
SETTINGS = ("soma_port", "web_port", "ssh_port", "soma_scheme", "soma_uri",
            "soma_spec_file", "retry_interval")
_INTS = ("soma_port", "web_port", "ssh_port", "retry_interval")

_warned = False


def _local(name):
    return os.path.join(MAST_HOME, "etc", "local", name)


def _read(path):
    config = configparser.ConfigParser(strict=False, interpolation=None)
    if path and os.path.exists(path):
        config.read(path)
    return config


def _settings(section):
    values = {}
    for key in SETTINGS:
        if key in section and section[key].strip():
            value = section[key].strip()
            values[key] = int(value) if key in _INTS else value
    return values


def plan(hosts=None, environments=None, appliances=None):
    """
    _function_: `mast.registry.files.plan(hosts=None, environments=None,
    appliances=None)`

    Read the three files and return `(appliances, environments)`:
    `{name: {"address": ..., <settings>}}` and `{name: [members]}`, in
    the order of the files. Nothing is changed.
    """
    hosts_conf = _read(hosts)
    env_conf = _read(environments)
    app_conf = _read(appliances)
    aliases = dict(hosts_conf.items("hosts")) \
        if hosts_conf.has_section("hosts") else {}
    found = {}
    for alias, address in aliases.items():
        found[alias] = {"address": address.strip()}
    envs = {}
    for section in env_conf.sections():
        members = env_conf.get(section, "appliances", fallback="").split()
        envs[section] = members
        for member in members:
            found.setdefault(member, {"address": member})
    for section in app_conf.sections():
        if section == "global":
            continue
        entry = found.setdefault(
            section, {"address": aliases.get(section, section)})
        entry.update(_settings(app_conf[section]))
    return found, envs


def import_files(hosts=None, environments=None, appliances=None,
                 dry_run=False, rename=True):
    """
    _function_: `mast.registry.files.import_files(hosts=None,
    environments=None, appliances=None, dry_run=False, rename=True)`

    Put the appliances and the environments of the three files in the
    registry, and return a list of lines that say what was done. An
    appliance or an environment that is in the registry already is left
    as it is. With `dry_run`, nothing is changed.

    After an import (not a dry run), `hosts.conf` and
    `environments.conf` are renamed to `*.imported`. If
    `appliances.conf` had sections for single appliances, it is renamed
    too, and a new `appliances.conf` keeps only its `[global]` section.

    Parameters:

    * `hosts`, `environments`, `appliances`: The paths of the files.
    The defaults are the files in `etc/local`.
    """
    hosts = hosts or _local("hosts.conf")
    environments = environments or _local("environments.conf")
    appliances = appliances or _local("appliances.conf")
    found, envs = plan(hosts, environments, appliances)
    lines = []
    verb = "Would add" if dry_run else "Added"
    for name, entry in found.items():
        entry = dict(entry)
        address = entry.pop("address")
        if registry.by_name(name) is not None:
            lines.append("Kept the appliance {}: it is registered "
                         "already.".format(name))
            continue
        if dry_run:
            registry._check_name(name)
            lines.append("{} the appliance {} ({}){}".format(
                verb, name, address, _describe(entry)))
            continue
        try:
            registry.add(name, address, **entry)
        except registry.RegistryError as exc:
            lines.append("Did not add the appliance {}: {}".format(name, exc))
            continue
        lines.append("{} the appliance {} ({}){}".format(
            verb, name, address, _describe(entry)))
    for name, members in envs.items():
        if registry.env_by_name(name) is not None:
            lines.append("Kept the environment {}: it is registered "
                         "already.".format(name))
            continue
        if not dry_run:
            try:
                registry.env_add(name, members=members)
            except registry.RegistryError as exc:
                lines.append("Did not add the environment {}: {}".format(
                    name, exc))
                continue
        lines.append("{} the environment {}: {}".format(
            verb, name, " ".join(members)))
    if not dry_run and rename:
        lines.extend(_rename(hosts, environments, appliances))
    if not found and not envs:
        lines.append("There was nothing to import.")
    return lines


def _describe(settings):
    if not settings:
        return ""
    return ", " + ", ".join(
        "{} {}".format(key, value) for key, value in settings.items())


def _rename(hosts, environments, appliances):
    lines = []
    for path in (hosts, environments):
        if os.path.exists(path):
            os.replace(path, path + ".imported")
            lines.append("Renamed {} to {}.imported".format(path, path))
    app_conf = _read(appliances)
    singles = [s for s in app_conf.sections() if s != "global"]
    if singles and os.path.exists(appliances):
        os.replace(appliances, appliances + ".imported")
        lines.append("Renamed {} to {}.imported".format(
            appliances, appliances))
        if app_conf.has_section("global"):
            kept = configparser.ConfigParser(interpolation=None)
            kept["global"] = dict(app_conf.items("global"))
            with open(appliances, "w") as fout:
                kept.write(fout)
            lines.append("Wrote {} with its [global] section".format(
                appliances))
    return lines


def export_files(out_dir=None, force=False):
    """
    _function_: `mast.registry.files.export_files(out_dir=None,
    force=False)`

    Write the registry as `hosts.conf`, `environments.conf` and
    `appliances.conf` in `out_dir` (`etc/local` by default), as MAST
    3.15.2 reads them, and return the paths. `appliances.conf` keeps
    the `[global]` section of the one in `etc/local`. A file that is
    there already is only replaced with `force`; otherwise
    `FileExistsError` is raised and nothing is written.
    """
    out_dir = out_dir or os.path.join(MAST_HOME, "etc", "local")
    paths = [os.path.join(out_dir, name) for name in
             ("hosts.conf", "environments.conf", "appliances.conf")]
    if not force:
        for path in paths:
            if os.path.exists(path):
                raise FileExistsError(path)
    current = _read(_local("appliances.conf"))
    hosts = configparser.ConfigParser(interpolation=None)
    hosts.optionxform = str
    hosts["hosts"] = {}
    settings = configparser.ConfigParser(interpolation=None)
    settings.optionxform = str
    if current.has_section("global"):
        settings["global"] = dict(current.items("global"))
    for appliance in registry.list_appliances():
        if appliance.address != appliance.name:
            hosts["hosts"][appliance.name] = appliance.address
        values = {key: str(getattr(appliance, key)) for key in SETTINGS
                  if getattr(appliance, key) is not None}
        if values:
            settings[appliance.name] = values
    environments = configparser.ConfigParser(interpolation=None)
    for environment in registry.env_list():
        environments[environment.name] = {
            "appliances": " ".join(environment.members)}
    os.makedirs(out_dir, exist_ok=True)
    for path, config in zip(paths, (hosts, environments, settings)):
        with open(path, "w") as fout:
            config.write(fout)
    return paths


def legacy_files():
    """
    _function_: `mast.registry.files.legacy_files()`

    Return the paths of `hosts.conf` and `environments.conf` in
    `etc/local` that are there and have entries: files that MAST no
    longer reads.
    """
    found = []
    hosts = _read(_local("hosts.conf"))
    if hosts.has_section("hosts") and hosts.items("hosts"):
        found.append(_local("hosts.conf"))
    if _read(_local("environments.conf")).sections():
        found.append(_local("environments.conf"))
    return found


def warn_legacy():
    """
    _function_: `mast.registry.files.warn_legacy()`

    If `legacy_files` finds files, say once in this process (in the
    log and on stderr) that MAST no longer reads them, and how to
    import them.
    """
    global _warned
    if _warned:
        return
    _warned = True
    found = legacy_files()
    if not found:
        return
    message = (
        "MAST keeps the appliances and the environments in its database "
        "now, and no longer reads {}. Import them once with "
        "`mast targets import`.".format(" and ".join(found)))
    make_logger("mast.registry").warning(message)
    print("Warning: " + message, file=sys.stderr)

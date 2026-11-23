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
_module_: `mast.datapower.status.collector`

`mast-status collect`: ask the appliances for their status and keep it
in the job database (`mast.datapower.status.models`), for the
Appliances page.

One job polls all of its appliances, several at a time. The requests to
one appliance are one after the other, with a short pause between them,
because DataPower answers a quick series of requests with
"Authentication failure". An appliance that cannot be polled is
recorded as such, with the reason, and the job goes on with the others.

Each poll asks the providers `FirmwareVersion3` (or `FirmwareVersion`
on older firmware), `DateTimeStatus`, `CPUUsage`, `MemoryStatus`,
`SystemUsage`, `FilesystemStatus` and `ObjectStatus` of `default`. The
`Domain` objects of `default` give the state of each domain.
`ObjectStatus` covers one domain only, so the other domains that are up
are asked every `object_status_minutes`, and their objects that are
down are kept in between.

An object is down when it is enabled but not up. The objects that an
admin disabled are not counted. A value that the appliance does not
give is None. DataPower answers `CPUUsage` only while `Statistics` is
enabled in the `default` domain (it is disabled out of the box); the
state of `Statistics` is kept in `detail`, so that the page can say
why the CPU is unknown.
"""
import contextvars
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from pydantic import field_validator, model_validator
from sqlalchemy import select

from mast.cli import Cli, Option, Table, say
from mast.cli.results import stored_credential_name
from mast.logging import make_logger
from mast.plugin_utils.params import ApplianceParams, timeout_option

__all__ = ["cli", "Collect", "collect", "poll", "parse_uptime"]

cli = Cli(namespace="status")

_STATUS = "{http://www.datapower.com/schemas/management}status"
_UPTIME = re.compile(r"^\s*(?:(\d+)\s+days?\s*)?(\d+):(\d{2}):(\d{2})\s*$")

# The columns of status_latest that a poll fills
VALUES = (
    "firmware", "delivery", "model", "serial", "uptime_seconds",
    "appliance_time", "cpu_10s", "cpu_1m", "mem_used_pct", "load_pct",
    "fs_free_encrypted_mb", "fs_free_temporary_mb", "fs_total_temporary_mb",
    "fs_free_internal_mb", "domains_total", "domains_down", "objects_down")


def _now():
    return datetime.now(timezone.utc)


class Collect(ApplianceParams):
    timeout: int = timeout_option(15)
    workers: int = Option(
        8, short="w",
        help="How many appliances to poll at the same time.")
    request_delay: float = Option(
        0.1,
        help="The seconds to wait between two requests to one appliance.")
    object_status_minutes: int = Option(
        15,
        help="How often, in minutes, to ask the domains other than "
             "default for their objects. default is asked each time.")

    @model_validator(mode="before")
    @classmethod
    def _polled_appliances(cls, data):
        """With no appliances, every appliance whose status is
        collected (mast-appliances update --poll yes)."""
        if isinstance(data, dict) and not data.get("appliances"):
            from mast import registry
            polled = [a.name for a in registry.list_appliances() if a.poll]
            if not polled:
                raise ValueError(
                    "no appliance is polled: give the appliances, or turn "
                    "polling on with mast-appliances update -n NAME --poll yes")
            data = dict(data, appliances=polled)
        return data

    @field_validator("workers")
    @classmethod
    def _at_least_one(cls, value):
        if value < 1:
            raise ValueError("must be 1 or more")
        return value

    @field_validator("request_delay", "object_status_minutes")
    @classmethod
    def _not_negative(cls, value):
        if value < 0:
            raise ValueError("must be 0 or more")
        return value


def parse_uptime(text):
    """
    _function_: `mast.datapower.status.collector.parse_uptime(text)`

    The seconds in an uptime of DataPower, such as `5 days 15:51:12`,
    or None.
    """
    found = _UPTIME.match(text or "")
    if not found:
        return None
    days, hours, minutes, seconds = (int(x or 0) for x in found.groups())
    return ((days * 24 + hours) * 60 + minutes) * 60 + seconds


def _text(entry, tag):
    if entry is None:
        return None
    found = entry.find(tag)
    if found is None or found.text is None:
        return None
    return found.text.strip()


def _number(entry, tag, kind=float):
    try:
        return kind(_text(entry, tag))
    except (TypeError, ValueError):
        return None


class _Poller(object):
    """The requests of one poll of one appliance."""

    def __init__(self, appliance, delay):
        self.appliance = appliance
        self.delay = delay
        self.first = True

    def entries(self, provider, domain="default"):
        """The entries of a status provider, as XML elements."""
        if not self.first and self.delay:
            time.sleep(self.delay)
        self.first = False
        response = self.appliance.get_status(provider, domain=domain)
        node = response.xml.find(".//" + _STATUS)
        return [] if node is None else list(node)

    def entry(self, provider):
        found = self.entries(provider)
        return found[0] if found else None

    def firmware(self):
        try:
            entry = self.entry("FirmwareVersion3")
        except Exception:
            entry = None
        if entry is None or _text(entry, "Version") is None:
            # Older firmware has no FirmwareVersion3
            entry = self.entry("FirmwareVersion")
        model = _text(entry, "ModelType")
        if model in (None, "", "n/a"):
            model = _text(entry, "MachineType") or model
        return {"firmware": _text(entry, "Version"),
                "delivery": _text(entry, "DeliveryType"),
                "model": model, "serial": _text(entry, "Serial")}


def _objects_down(entries, domain):
    return [{"class": _text(e, "Class"), "name": _text(e, "Name"),
             "domain": domain, "event_code": _text(e, "EventCode")}
            for e in entries
            if _text(e, "AdminState") == "enabled"
            and _text(e, "OpState") not in (None, "up")]


def poll(appliance, previous=None, now=None, delay=0.1,
         object_status_minutes=15):
    """
    _function_: `mast.datapower.status.collector.poll(appliance,
    previous=None, now=None, delay=0.1, object_status_minutes=15)`

    Ask one appliance (a `DataPower`) for its status. Return the values
    of `VALUES` and `detail`. Raises what the appliance raises.

    `previous` is the `detail` of the last poll: the objects of a domain
    that is not asked this time are taken from it.
    """
    now = now or _now()
    poller = _Poller(appliance, delay)
    values = poller.firmware()

    clock = poller.entry("DateTimeStatus")
    values["uptime_seconds"] = parse_uptime(
        _text(clock, "uptime2") or _text(clock, "uptime"))
    if _text(clock, "time"):
        values["appliance_time"] = " ".join(
            part for part in (_text(clock, "time"), _text(clock, "timezone"))
            if part)
    else:
        values["appliance_time"] = None

    cpu = poller.entry("CPUUsage")
    values["cpu_10s"] = _number(cpu, "tenSeconds")
    values["cpu_1m"] = _number(cpu, "oneMinute")
    values["mem_used_pct"] = _number(poller.entry("MemoryStatus"), "Usage")
    values["load_pct"] = _number(poller.entry("SystemUsage"), "Load")
    disk = poller.entry("FilesystemStatus")
    values["fs_free_encrypted_mb"] = _number(disk, "FreeEncrypted", int)
    values["fs_free_temporary_mb"] = _number(disk, "FreeTemporary", int)
    values["fs_total_temporary_mb"] = _number(disk, "TotalTemporary", int)
    values["fs_free_internal_mb"] = _number(disk, "FreeInternal", int)

    objects = poller.entries("ObjectStatus")
    domains = [{"name": _text(e, "Name"), "op_state": _text(e, "OpState"),
                "admin_state": _text(e, "AdminState")}
               for e in objects if _text(e, "Class") == "Domain"]
    down = _objects_down(objects, "default")
    previous = previous or {}
    checked = dict(previous.get("objects_checked_at") or {})
    checked["default"] = now.isoformat()
    kept = previous.get("objects_down") or []
    every = timedelta(minutes=object_status_minutes)
    for domain in domains:
        name = domain["name"]
        if name == "default" or domain["op_state"] != "up":
            continue
        last = checked.get(name)
        if last and now - datetime.fromisoformat(last) < every:
            down += [o for o in kept if o.get("domain") == name]
            continue
        down += _objects_down(poller.entries("ObjectStatus", domain=name),
                              name)
        checked[name] = now.isoformat()
    statistics = [_text(e, "AdminState") for e in objects
                  if _text(e, "Class") == "Statistics"]
    names = {d["name"] for d in domains}
    values["domains_total"] = len(domains)
    values["domains_down"] = sum(
        1 for d in domains
        if d["admin_state"] == "enabled" and d["op_state"] != "up")
    values["objects_down"] = len(down)
    values["detail"] = {
        "domains": sorted(domains, key=lambda d: d["name"] or ""),
        "objects_down": down,
        # The admin state of Statistics in default: without it, no CPU
        "statistics": statistics[0] if statistics else None,
        "objects_checked_at": {k: v for k, v in checked.items()
                               if k in names or k == "default"},
    }
    return values


def _error(exc):
    text = "{}: {}".format(type(exc).__name__, exc).strip()
    return text.splitlines()[0][:500] if text else type(exc).__name__


class Collected(Table):
    """The table of a collect, with the outcome of each appliance."""

    def __init__(self, rows, oks, history=""):
        super().__init__(
            ("Appliance", "Reachable", "Firmware", "CPU %", "Memory %",
             "Load %", "Free temporary MB", "Domains up", "Objects down",
             "Problem"), rows, history=history)
        self._oks = oks

    def outcomes(self):
        return list(self._oks)


def _fmt(value):
    return "" if value is None else str(int(value)) \
        if isinstance(value, float) and value.is_integer() else str(value)


@cli.command("collect", category="status", exclusive=False, passive=True)
def collect(params: Collect):
    """Ask the appliances for their status, and keep it for the
Appliances page of the web UI. With no appliances, every appliance
whose status is collected (mast-appliances update --poll yes). An appliance
that does not answer is recorded as such; the others are still polled.
Run it on a schedule with a stored credential for collecting."""
    from mast.datapower.status import store
    logger = make_logger("mast.status")
    env = params.environment()
    now = _now()
    registered = {a.hostname: a._record for a in env.appliances
                  if getattr(a, "_record", None) is not None}
    previous = store.previous_details(
        [r.id for r in registered.values()])

    def one(appliance):
        record = registered.get(appliance.hostname)
        try:
            values = poll(
                appliance,
                previous=previous.get(record.id) if record else None,
                now=now, delay=params.request_delay,
                object_status_minutes=params.object_status_minutes)
            say("Polled {}".format(appliance.hostname))
            return appliance, values, None
        except Exception as exc:
            logger.warning("Could not poll {}: {}".format(
                appliance.hostname, type(exc).__name__))
            say("Could not poll {}: {}".format(
                appliance.hostname, _error(exc)))
            return appliance, None, _error(exc)

    # Each poll runs in a copy of this context, so that it has what the
    # job set: the strict checks of a stored credential, the progress
    # and the cancel checks
    with ThreadPoolExecutor(
            max_workers=min(params.workers, max(len(env.appliances), 1)),
            thread_name_prefix="mast-status") as pool:
        futures = [pool.submit(contextvars.copy_context().run, one, a)
                   for a in env.appliances]
        polled = [f.result() for f in futures]

    store.save(polled, registered, now,
               credential=params.credential or stored_credential_name())
    rows, oks = [], []
    for appliance, values, error in polled:
        values = values or {}
        domains = values.get("domains_total")
        rows.append([
            appliance.hostname, "no" if error else "yes",
            values.get("firmware") or "", _fmt(values.get("cpu_1m")),
            _fmt(values.get("mem_used_pct")), _fmt(values.get("load_pct")),
            _fmt(values.get("fs_free_temporary_mb")),
            "" if domains is None else "{}/{}".format(
                domains - (values.get("domains_down") or 0), domains),
            _fmt(values.get("objects_down")),
            error or ("" if appliance.hostname in registered
                      else "Not registered: not kept")])
        oks.append(error is None)
    from mast.plugin_utils.results import history
    return Collected(rows, oks, history=history(env))

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
_module_: `mast.datapower.crypto.rules`

The posture rules of the crypto inventory. A rule is a function that
looks at something and yields `Finding`s. There are two kinds:

* A **snapshot rule** (`@rule`) looks at one snapshot of one appliance
(a `View`). Its findings are computed when the snapshot is kept, and
stored with it (`crypto_findings`).
* A **MAST self-check** (`@check`) looks at the setup of MAST itself
(the registry and the stored credentials, a `Setup`). It is computed
each time it is shown, because it is about MAST now, not about an
appliance at the time of a snapshot.

Each rule has an id, a severity, a title and a pack. The packs are
`baseline` (`rules.baseline`), `mast` (`rules.selfcheck`) and, later,
`pqc`. The thresholds are in `etc/default/crypto.conf`;
`etc/local/crypto.conf` overrides them, and its `[rules] disabled`
turns rules off.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone

__all__ = ["Finding", "Rule", "View", "Setup", "Settings", "rule", "check",
           "RULES", "SEVERITIES", "PACKS", "settings", "snapshot_rules",
           "self_checks"]

# From the worst
SEVERITIES = ("critical", "high", "medium", "low", "info")
PACKS = ("baseline", "mast", "pqc")

# id -> Rule, in the order the rules were registered
RULES = {}


@dataclass
class Finding(object):
    """
    _class_: `mast.datapower.crypto.rules.Finding`

    One finding. A rule gives `message` and `remediation`, and either
    an `obj` (an object of the snapshot) or a `subject` (text: a file,
    an appliance, a credential). `severity` is the rule's, unless the
    rule gives another (an expiry that is nearer is worse). `domain`,
    `name` and `appliance` are what exceptions match; they come from
    `obj` if there is one.
    """
    message: str
    remediation: str
    obj: object = None
    subject: str = ""
    severity: str | None = None
    domain: str | None = None
    name: str | None = None
    appliance: str | None = None
    rule_id: str = ""


@dataclass
class Rule(object):
    id: str
    severity: str
    title: str
    pack: str
    kind: str
    function: object
    doc: str = ""

    def run(self, target):
        found = []
        for finding in self.function(target) or ():
            finding.rule_id = self.id
            finding.severity = finding.severity or self.severity
            if finding.obj is not None:
                finding.domain = finding.domain or finding.obj.domain
                finding.name = finding.name or finding.obj.name
                finding.subject = finding.subject or "{}/{}/{}".format(
                    finding.obj.domain, finding.obj.class_, finding.obj.name)
            if not finding.name:
                finding.name = finding.subject
            if isinstance(target, View):
                finding.appliance = finding.appliance or target.appliance_name
            found.append(finding)
        return found


def _register(kind, id, severity, title, pack):
    if severity not in SEVERITIES:
        raise ValueError("{}: severity {!r}".format(id, severity))
    if pack not in PACKS:
        raise ValueError("{}: pack {!r}".format(id, pack))

    def decorate(function):
        if id in RULES:
            raise ValueError("two rules {}".format(id))
        doc = " ".join((function.__doc__ or "").split())
        RULES[id] = Rule(id, severity, title, pack, kind, function, doc)
        return function
    return decorate


def rule(id, severity, title, pack="baseline"):
    """
    _function_: `mast.datapower.crypto.rules.rule(id, severity, title,
    pack="baseline")`

    Register a snapshot rule: a function of a `View` that yields
    `Finding`s.
    """
    return _register("snapshot", id, severity, title, pack)


def check(id, severity, title):
    """
    _function_: `mast.datapower.crypto.rules.check(id, severity, title)`

    Register a MAST self-check: a function of a `Setup` that yields
    `Finding`s.
    """
    return _register("mast", id, severity, title, "mast")


# The settings
# ============

_DEFAULTS = {
    ("expiry", "high_days"): 30,
    ("expiry", "medium_days"): 60,
    ("expiry", "low_days"): 90,
    ("keys", "rsa_min_bits"): 2048,
    ("credentials", "unused_days"): 180,
    ("exceptions", "max_days"): 365,
    ("score", "critical"): 40,
    ("score", "high"): 15,
    ("score", "medium"): 5,
    ("score", "low"): 1,
    ("score", "info"): 0,
}


@dataclass
class Settings(object):
    """
    _class_: `mast.datapower.crypto.rules.Settings`

    The thresholds of the rules, from `crypto.conf`, with the defaults
    of `_DEFAULTS` for what it does not say.
    """
    values: dict = field(default_factory=lambda: dict(_DEFAULTS))
    disabled: frozenset = frozenset()

    def get(self, section, option):
        return self.values[(section, option)]

    def weight(self, severity):
        return self.values.get(("score", severity), 0)


def settings():
    """
    _function_: `mast.datapower.crypto.rules.settings()`

    The `Settings` of `etc/default/crypto.conf` and
    `etc/local/crypto.conf`. A value that is not a whole number of 0 or
    more is ignored (the default stays) and logged.
    """
    from mast.config import get_config
    from mast.logging import make_logger
    config = get_config("crypto.conf")
    values = dict(_DEFAULTS)
    for (section, option), default in _DEFAULTS.items():
        if not config.has_option(section, option):
            continue
        text = config.get(section, option, raw=True).strip()
        try:
            number = int(text)
            if number < 0:
                raise ValueError(text)
        except ValueError:
            make_logger("mast.crypto").warning(
                "crypto.conf: [{}] {} = {!r} is not a whole number; using "
                "{}".format(section, option, text, default))
            continue
        values[(section, option)] = number
    disabled = frozenset()
    if config.has_option("rules", "disabled"):
        disabled = frozenset(
            part.strip() for part in
            config.get("rules", "disabled", raw=True).split(",")
            if part.strip())
    return Settings(values, disabled)


def _load():
    # The rules register themselves when their modules are imported
    from . import baseline, selfcheck  # noqa: F401


def snapshot_rules(settings):
    _load()
    return [r for r in RULES.values()
            if r.kind == "snapshot" and r.id not in settings.disabled]


def self_checks(settings):
    _load()
    return [r for r in RULES.values()
            if r.kind == "mast" and r.id not in settings.disabled]


# What the rules look at
# ======================

class View(object):
    """
    _class_: `mast.datapower.crypto.rules.View`

    One snapshot, read from the database, for the snapshot rules:

    * `snapshot`: the `CryptoSnapshot`; `now` is when it was collected.
    * `appliance_name`, `production` (the appliance is in an
    environment marked production).
    * `objects` (by id), `by_key` (by `(domain, class, name)`),
    `certs` (by the id of their object), `files`.
    * `uses[id]` and `used_by[id]`: `(other_id, via)` of the edges.
    * `settings`: the `Settings`.
    * `unread_domains`: the domains that could not be read, so what is
    in them is not known.
    """

    def __init__(self, snapshot, objects, certs, files, edges,
                 appliance_name="", production=False, settings=None):
        self.snapshot = snapshot
        self.now = snapshot.collected_at
        if self.now is not None and self.now.tzinfo is None:
            self.now = self.now.replace(tzinfo=timezone.utc)
        self.appliance_name = appliance_name
        self.production = production
        self.settings = settings or Settings()
        self.objects = {o.id: o for o in objects}
        self.by_key = {(o.domain, o.class_, o.name): o for o in objects}
        self.certs = {c.object_id: c for c in certs}
        self.files = list(files)
        self.uses = {}
        self.used_by = {}
        for edge in edges:
            self.uses.setdefault(edge.from_object_id, []).append(
                (edge.to_object_id, edge.via))
            self.used_by.setdefault(edge.to_object_id, []).append(
                (edge.from_object_id, edge.via))
        self.unread_domains = {
            p["domain"] for p in (snapshot.problems or [])
            if p.get("domain") and not p.get("object")}

    @classmethod
    def load(cls, snapshot_id, settings=None):
        """The `View` of the snapshot `snapshot_id` (None if there is
        none)."""
        from sqlalchemy import select

        from mast import registry
        from mast.jobs import db

        from ..models import (CryptoCert, CryptoEdge, CryptoFile,
                              CryptoObject, CryptoSnapshot)
        with db.session(write=False) as session:
            snapshot = session.get(CryptoSnapshot, snapshot_id)
            if snapshot is None:
                return None

            def rows(model):
                return list(session.scalars(
                    select(model).where(model.snapshot_id == snapshot_id)))
            objects, certs = rows(CryptoObject), rows(CryptoCert)
            files, edges = rows(CryptoFile), rows(CryptoEdge)
            appliance_id = snapshot.appliance_id
        name, production = "", False
        for appliance in registry.list_appliances():
            if appliance.id == appliance_id:
                name = appliance.name
                production = _in_production(appliance)
                break
        return cls(snapshot, objects, certs, files, edges, name, production,
                   settings)

    def crypto(self, *classes):
        """The crypto and TLS objects (those with `attrs`) of `classes`
        (all if none) that are enabled and in the running
        configuration."""
        return [o for o in self.objects.values()
                if o.attrs is not None and o.running
                and o.admin_state != "disabled"
                and (not classes or o.class_ in classes)]

    def users(self, obj, via=None):
        """The objects that refer to `obj` (through `via`, if given)."""
        return [self.objects[i] for i, v in self.used_by.get(obj.id, ())
                if i in self.objects and (via is None or v == via)]

    def describe_users(self, obj, limit=3):
        users = sorted({"{} {}".format(u.class_, u.name)
                        for u in self.users(obj)})
        if not users:
            return "nothing in the configuration refers to it"
        text = ", ".join(users[:limit])
        if len(users) > limit:
            text += " and {} more".format(len(users) - limit)
        return "used by " + text


def _in_production(appliance):
    from mast import registry
    production = {e.name for e in registry.env_list() if e.production}
    return any(name in production for name in appliance.environments)


@dataclass
class Setup(object):
    """
    _class_: `mast.datapower.crypto.rules.Setup`

    The setup of MAST, for the self-checks: the registered
    `appliances`, the stored `credentials` (without secrets), `now` and
    the `settings`.
    """
    appliances: list
    credentials: list
    now: datetime
    settings: Settings

    @classmethod
    def load(cls, now=None, settings_=None):
        from mast import registry
        from mast.credentials import store
        return cls(registry.list_appliances(), store.list_credentials(),
                   now or datetime.now(timezone.utc), settings_ or settings())

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
_module_: `mast.permissions`

Who may do what: permission sets of rules over paths, given to groups
of users (step 5 of docs/design/mast-4.0.md).

**Paths.** Every command and every resource of MAST has a path of three
segments:

| Path | What |
|---|---|
| `mast/PROVIDER/COMMAND` | A command of a plugin: `mast/datapower/get-status` is `mast datapower get-status` |
| `mast/core/NAME` | A command of core (`mast/core/add-target` is `mast targets add`), or an action of core (`CORE_ACTIONS`) |
| `mast/credentials/NAME` | A stored credential |
| `mast/targets/NAME`, `mast/fleets/NAME` | A target, a fleet |

A path in a rule is exact, or ends in one `*`, which matches the rest of
the last segment: `mast/datapower/list-*` matches every command of
`mast datapower` whose name starts with `list-`, and `mast/datapower/*`
every command of it. A `*` matches within the last segment only, and
nowhere else.

**Capabilities.** `r` discovers commands and reads resources and
results, `w` changes a resource or permits the change effects of a
command, `x` executes a command or uses a resource (a credential).
Running a command needs `x`; one whose effect is `change` needs `w`
too.

**Rules.** A rule is `{"path", "allow": [...], "deny": [...],
"scope"}`. The default is deny: a capability is granted by a rule that
allows it and matches the path, and an explicit denial that applies
wins for the capability that it denies. A rule that concerns targets
has a scope: `{"all": true}`, `{"targets": [...]}` or `{"fleets":
[...]}`; names are resolved to ids when the set is saved, so a renamed
target stays in scope. A rule without a scope grants no target
access, and a denial without a scope applies to every target.

**Groups.** Users belong to groups, and groups have permission sets.
What a user may do is the union of the rules of the sets of all their
groups. MAST makes the groups `viewer`, `operator` and `admin` with the
sets of the same names; they can be changed. A plugin's commands are
granted only by sets that name them: installing a plugin grants nothing.
A plugin defines groups with the rules of their sets
(`mast.plugins.Group`): MAST makes each one without members when it
first sees it (`sync_plugin_groups`), and an admin puts users in it. A
newer version of the plugin updates the set of a group, until an admin
changes that set; a group that an admin removed is not made again.

These rules decide what every user may do: `mast.jobs.auth.authorize`
asks `decide` and `check_command` for every action of MAST.
"""
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import delete, select

from mast.jobs import db
from mast.permissions.models import (
    GroupMember, GroupPermissionSet, PermissionSet, PluginGroup, UserGroup)

__all__ = [
    "CAPABILITIES", "CORE_ACTIONS", "SEED", "PermissionError",
    "InvalidRule", "UnknownName", "NameTaken", "Rule", "PermissionSetInfo",
    "GroupInfo", "Match", "Decision", "check_path", "path_matches",
    "check_rule", "add_set", "update_set", "add_rule", "remove_rule",
    "remove_set", "get_set", "list_sets", "add_group", "update_group",
    "remove_group", "get_group", "list_groups", "grant", "revoke",
    "add_member", "remove_member", "groups_of", "rules_of", "decide",
    "check_command", "ROLE_GROUPS", "sync_plugin_groups", "may_run_any",
    "set_groups",
]

CAPABILITIES = ("r", "w", "x")

# Actions of core that are not commands, and have a path mast/core/NAME
CORE_ACTIONS = {
    "cancel-own-job": "Cancel a job that one submitted oneself.",
}

# The roots of the paths that are resources, not commands
RESOURCE_ROOTS = ("credentials", "targets", "fleets")

_NAME = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")
_SEGMENT = re.compile(r"^[A-Za-z0-9_.:@\[\]-]+$")
_SET_NAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")


def _rules(*items):
    return [dict(item) for item in items]


# The permission sets that MAST makes, and the groups that have them
# (both can be changed later)
SEED = {
    "viewer": ("Read the non-secret operational views of core: the jobs, "
               "the workers, the schedules, the targets and fleets, the "
               "plugins and the crypto posture.", _rules(*(
                   {"path": "mast/core/" + name, "allow": ["r", "x"]}
                   for name in ("list-jobs", "show-job", "follow-job",
                                "list-workers", "list-schedules",
                                "list-targets", "show-target",
                                "list-fleets", "list-plugins",
                                "list-kinds", "crypto-posture",
                                "crypto-rules", "crypto-exceptions",
                                "crypto-export")),
                   {"path": "mast/targets/*", "allow": ["r"]},
                   {"path": "mast/fleets/*", "allow": ["r"]})),
    "operator": ("Cancel the jobs that one submitted, and take the crypto "
                 "inventory and probe the TLS of every target. Running the "
                 "commands of a plugin needs a set that names them.", _rules(
                     {"path": "mast/core/cancel-own-job", "allow": ["x"]},
                     {"path": "mast/core/crypto-inventory", "allow": ["x"],
                      "scope": {"all": True}},
                     {"path": "mast/core/crypto-probe", "allow": ["x"],
                      "scope": {"all": True}})),
    "admin": ("Manage the identities, the permissions, the targets, the "
              "fleets, the credentials, the schedules and the jobs of "
              "core.", _rules(
                  {"path": "mast/core/*", "allow": ["r", "w", "x"],
                   "scope": {"all": True}},
                  {"path": "mast/credentials/*", "allow": ["r", "w", "x"],
                   "scope": {"all": True}},
                  {"path": "mast/targets/*", "allow": ["r", "w", "x"],
                   "scope": {"all": True}},
                  {"path": "mast/fleets/*", "allow": ["r", "w", "x"],
                   "scope": {"all": True}})),
}

# The groups that the roles of MAST 3.x and of the first 4.0 homes were
# (migrations 4004 and 4006): an operator is also a viewer
ROLE_GROUPS = {
    "viewer": ("viewer",),
    "operator": ("viewer", "operator"),
    "admin": ("viewer", "operator", "admin"),
}


class PermissionError(ValueError):
    """A change to the permission sets or groups was refused."""


class InvalidRule(PermissionError):
    """A rule or a path is not valid. The message says why."""


class UnknownName(PermissionError, LookupError):
    """There is no permission set, group or user with that name."""


class NameTaken(PermissionError):
    """A permission set or a group already has that name."""


@dataclass(frozen=True)
class Rule(object):
    """
    _class_: `mast.permissions.Rule`

    One rule, as it is stored: `scope` is None, `{"all": True}`, or
    `{"targets": [ids]}` / `{"fleets": [ids]}`.
    """
    path: str
    allow: tuple = ()
    deny: tuple = ()
    scope: dict = None

    def json(self, names=True):
        """The rule as JSON types; with `names`, the ids of a scope are
        shown as names."""
        data = {"path": self.path}
        if self.allow:
            data["allow"] = list(self.allow)
        if self.deny:
            data["deny"] = list(self.deny)
        if self.scope is not None:
            data["scope"] = _scope_names(self.scope) if names \
                else dict(self.scope)
        return data


@dataclass(frozen=True)
class PermissionSetInfo(object):
    """
    _class_: `mast.permissions.PermissionSetInfo`

    A permission set: its `name`, `description`, `rules` (`Rule`s) and
    the `groups` that have it.
    """
    id: int
    name: str
    description: str
    rules: tuple
    groups: tuple
    updated_by: str
    updated_at: datetime

    def json(self):
        return {"name": self.name, "description": self.description,
                "rules": [rule.json() for rule in self.rules],
                "groups": list(self.groups), "updated_by": self.updated_by,
                "updated_at": self.updated_at.isoformat()}


@dataclass(frozen=True)
class GroupInfo(object):
    """
    _class_: `mast.permissions.GroupInfo`

    A group: its `name`, `description`, `sets` (names of permission
    sets) and `members` (names of users).
    """
    id: int
    name: str
    description: str
    sets: tuple
    members: tuple

    def json(self):
        return {"name": self.name, "description": self.description,
                "sets": list(self.sets), "members": list(self.members)}


@dataclass(frozen=True)
class Match(object):
    """One rule that matched a question: of the set `set`, which the
    groups `groups` of the user have, at `index` in the set."""
    set: str
    groups: tuple
    index: int
    rule: Rule

    def json(self):
        return {"set": self.set, "groups": list(self.groups),
                "index": self.index, "rule": self.rule.json()}


@dataclass(frozen=True)
class Decision(object):
    """
    _class_: `mast.permissions.Decision`

    Whether `user` may use `capability` on `path` (for the target
    `target`, if any), and the rules that decided: `allowed_by`, and
    `denied_by`, which win.
    """
    user: str
    capability: str
    path: str
    target: str = None
    allowed: bool = False
    allowed_by: tuple = field(default=())
    denied_by: tuple = field(default=())

    @property
    def reason(self):
        what = "{} {} {}{}".format(
            self.capability, self.path,
            "" if self.target is None else "on " + self.target, "")
        if self.denied_by:
            return "{} is denied {} by {}".format(
                self.user, what.strip(), _sources(self.denied_by))
        if self.allowed:
            return "{} is allowed {} by {}".format(
                self.user, what.strip(), _sources(self.allowed_by))
        return "{} is not allowed {}: no rule allows it".format(
            self.user, what.strip())

    def json(self):
        return {"user": self.user, "capability": self.capability,
                "path": self.path, "target": self.target,
                "allowed": self.allowed, "reason": self.reason,
                "allowed_by": [m.json() for m in self.allowed_by],
                "denied_by": [m.json() for m in self.denied_by]}


def _sources(matches):
    return "; ".join("{} (rule {}, {} of {})".format(
        m.rule.path, m.index + 1, m.set, ", ".join(m.groups) or "no group")
        for m in matches)


def _now():
    return datetime.now(timezone.utc)


# Paths
# =====


def check_path(path):
    """
    _function_: `mast.permissions.check_path(path)`

    Return `path` if it is a valid path of a rule: `mast/ROOT/NAME`,
    where the last segment may end in one `*`. Raises `InvalidRule`.
    """
    if not isinstance(path, str):
        raise InvalidRule("A path is text: {!r}".format(path))
    parts = path.split("/")
    if len(parts) != 3 or parts[0] != "mast":
        raise InvalidRule(
            "A path is mast/ROOT/NAME (mast/datapower/get-status, "
            "mast/credentials/dp-reader), not {!r}".format(path))
    _, root, name = parts
    if root not in ("core",) + RESOURCE_ROOTS and not _NAME.match(root):
        raise InvalidRule("{!r}: {!r} is not core, a resource root or the "
                          "name of a plugin".format(path, root))
    stem = name[:-1] if name.endswith("*") else name
    if "*" in stem:
        raise InvalidRule(
            "{!r}: a * may only end the path, as in mast/datapower/list-*"
            .format(path))
    if stem and not _SEGMENT.match(stem):
        raise InvalidRule("{!r}: {!r} is not a name".format(path, stem))
    if not name:
        raise InvalidRule("{!r}: the name is missing".format(path))
    return path


def path_matches(pattern, path):
    """
    _function_: `mast.permissions.path_matches(pattern, path)`

    True if the path of a rule (`pattern`) matches `path`.
    """
    if pattern.endswith("*"):
        prefix = pattern[:-1]
        return path.startswith(prefix) and "/" not in path[len(prefix):]
    return pattern == path


# Rules
# =====


def _scope_ids(scope):
    """The scope of a rule with names, as it is stored: with ids."""
    if scope is None:
        return None
    if not isinstance(scope, dict) or len(scope) != 1:
        raise InvalidRule("A scope is {{\"all\": true}}, {{\"targets\": "
                          "[...]}} or {{\"fleets\": [...]}}, not {!r}".format(
                              scope))
    # Imported here: the registry uses the job database
    from mast import registry
    (key, value), = scope.items()
    if key == "all":
        if value is not True:
            raise InvalidRule("A scope of all targets is {\"all\": true}.")
        return {"all": True}
    if key not in ("targets", "fleets") or isinstance(value, str) \
            or not isinstance(value, (list, tuple)) or not value:
        raise InvalidRule("A scope names targets or fleets: {\"targets\": "
                          "[\"dp1\"]} or {\"fleets\": [\"lab\"]}.")
    ids = []
    for name in value:
        if isinstance(name, int):
            ids.append(name)
            continue
        found = registry.by_name(name) if key == "targets" \
            else registry.fleet_by_name(name)
        if found is None:
            raise InvalidRule("There is no {} {!r}.".format(key[:-1], name))
        ids.append(found.id)
    return {key: sorted(set(ids))}


def _scope_names(scope):
    """The scope of a stored rule, with names in place of ids."""
    if not scope or "all" in scope:
        return dict(scope or {})
    from mast import registry
    key, ids = next(iter(scope.items()))
    names = []
    for found_id in ids:
        if key == "targets":
            found = registry.by_id(found_id)
        else:
            found = next((f for f in registry.fleet_list()
                          if f.id == found_id), None)
        names.append(found.name if found else "#{} (removed)".format(found_id))
    return {key: names}


def _capabilities(value, what):
    if value is None:
        return ()
    if isinstance(value, str):
        value = list(value.replace(",", ""))
    found = []
    for capability in value:
        if capability not in CAPABILITIES:
            raise InvalidRule("{} is r, w or x, not {!r}".format(
                what, capability))
        if capability not in found:
            found.append(capability)
    return tuple(c for c in CAPABILITIES if c in found)


def check_rule(rule):
    """
    _function_: `mast.permissions.check_rule(rule)`

    Return a `Rule` from a dict `{"path", "allow", "deny", "scope"}`,
    with the names of its scope resolved to ids. Raises `InvalidRule`.
    """
    if isinstance(rule, Rule):
        return rule
    if not isinstance(rule, dict):
        raise InvalidRule("A rule is an object with a path, not {!r}".format(
            rule))
    unknown = set(rule) - {"path", "allow", "deny", "scope"}
    if unknown:
        raise InvalidRule("A rule has path, allow, deny and scope, not "
                          "{}".format(", ".join(sorted(unknown))))
    path = check_path(rule.get("path"))
    allow = _capabilities(rule.get("allow"), "allow")
    deny = _capabilities(rule.get("deny"), "deny")
    if not allow and not deny:
        raise InvalidRule("{}: a rule allows or denies something".format(
            path))
    return Rule(path=path, allow=allow, deny=deny,
                scope=_scope_ids(rule.get("scope")))


def _stored(rules):
    return [rule.json(names=False) for rule in rules]


def _loaded(rows):
    return tuple(Rule(path=r["path"], allow=tuple(r.get("allow", ())),
                      deny=tuple(r.get("deny", ())), scope=r.get("scope"))
                 for r in rows or ())


# Permission sets
# ===============


def _check_set_name(name):
    if not isinstance(name, str) or not _SET_NAME.match(name):
        raise InvalidRule("A name has 1 to 64 lower-case letters, digits or "
                          "_ . -: {!r}".format(name))
    return name


def _set(session, name):
    found = session.scalars(select(PermissionSet).where(
        PermissionSet.name == name)).first()
    if found is None:
        raise UnknownName("There is no permission set {}.".format(name))
    return found


def _set_view(session, record):
    groups = session.scalars(
        select(UserGroup.name)
        .join(GroupPermissionSet, GroupPermissionSet.group_id == UserGroup.id)
        .where(GroupPermissionSet.set_id == record.id)
        .order_by(UserGroup.name)).all()
    return PermissionSetInfo(
        id=record.id, name=record.name, description=record.description,
        rules=_loaded(record.rules), groups=tuple(groups),
        updated_by=record.updated_by, updated_at=record.updated_at)


def _who(principal):
    return getattr(principal, "name", None) or str(principal)


def add_set(name, rules=(), description="", principal="os:mast"):
    """
    _function_: `mast.permissions.add_set(name, rules=(),
    description="", principal="os:mast")`

    Add a permission set with these rules (dicts, see `check_rule`).
    """
    sync_plugin_groups()
    _check_set_name(name)
    checked = [check_rule(rule) for rule in rules]
    with db.session() as session:
        if session.scalars(select(PermissionSet.id).where(
                PermissionSet.name == name)).first():
            raise NameTaken("There is already a permission set {}.".format(
                name))
        record = PermissionSet(name=name, description=description or "",
                               rules=_stored(checked),
                               updated_by=_who(principal), updated_at=_now())
        session.add(record)
        session.flush()
        return _set_view(session, record)


def update_set(name, rules=None, description=None, principal="os:mast"):
    """
    _function_: `mast.permissions.update_set(name, rules=None,
    description=None, principal="os:mast")`

    Replace the rules and/or the description of a permission set.
    """
    sync_plugin_groups()
    checked = None if rules is None else [check_rule(rule) for rule in rules]
    with db.session() as session:
        record = _set(session, name)
        if checked is not None:
            record.rules = _stored(checked)
        if description is not None:
            record.description = description
        record.updated_by = _who(principal)
        record.updated_at = _now()
        session.flush()
        return _set_view(session, record)


def add_rule(name, rule, principal="os:mast"):
    """
    _function_: `mast.permissions.add_rule(name, rule,
    principal="os:mast")`

    Add one rule to the end of a permission set.
    """
    found = get_set(name)
    return update_set(name, list(found.rules) + [check_rule(rule)],
                      principal=principal)


def remove_rule(name, number, principal="os:mast"):
    """
    _function_: `mast.permissions.remove_rule(name, number,
    principal="os:mast")`

    Remove the rule `number` (from 1, as `show` numbers them).
    """
    rules = list(get_set(name).rules)
    if not 1 <= number <= len(rules):
        raise InvalidRule("{} has the rules 1 to {}.".format(name, len(rules)))
    del rules[number - 1]
    return update_set(name, rules, principal=principal)


def remove_set(name):
    """
    _function_: `mast.permissions.remove_set(name)`

    Remove a permission set. The groups that had it no longer do.
    """
    sync_plugin_groups()
    with db.session() as session:
        record = _set(session, name)
        session.execute(delete(GroupPermissionSet).where(
            GroupPermissionSet.set_id == record.id))
        session.delete(record)


def get_set(name):
    """
    _function_: `mast.permissions.get_set(name)`

    Return the `PermissionSetInfo` called `name`. Raises `UnknownName`.
    """
    sync_plugin_groups()
    with db.session(write=False) as session:
        return _set_view(session, _set(session, name))


def list_sets():
    """
    _function_: `mast.permissions.list_sets()`

    Return every permission set, by name.
    """
    sync_plugin_groups()
    with db.session(write=False) as session:
        return [_set_view(session, record) for record in session.scalars(
            select(PermissionSet).order_by(PermissionSet.name))]


# Groups
# ======


def _group(session, name):
    found = session.scalars(select(UserGroup).where(
        UserGroup.name == name)).first()
    if found is None:
        raise UnknownName("There is no group {}.".format(name))
    return found


def _user_id(session, name):
    from mast.auth.models import User
    from mast.auth.users import normalize
    found = session.scalars(select(User.id).where(
        User.name == normalize(name))).first()
    if found is None:
        raise UnknownName("There is no user {}.".format(name))
    return found


def _group_view(session, record):
    from mast.auth.models import User
    sets = session.scalars(
        select(PermissionSet.name)
        .join(GroupPermissionSet, GroupPermissionSet.set_id == PermissionSet.id)
        .where(GroupPermissionSet.group_id == record.id)
        .order_by(PermissionSet.name)).all()
    members = session.scalars(
        select(User.name)
        .join(GroupMember, GroupMember.user_id == User.id)
        .where(GroupMember.group_id == record.id)
        .order_by(User.name)).all()
    return GroupInfo(id=record.id, name=record.name,
                     description=record.description, sets=tuple(sets),
                     members=tuple(members))


def add_group(name, description="", sets=()):
    """
    _function_: `mast.permissions.add_group(name, description="",
    sets=())`

    Add a group with these permission sets (names).
    """
    sync_plugin_groups()
    _check_set_name(name)
    with db.session() as session:
        if session.scalars(select(UserGroup.id).where(
                UserGroup.name == name)).first():
            raise NameTaken("There is already a group {}.".format(name))
        record = UserGroup(name=name, description=description or "")
        session.add(record)
        session.flush()
        for set_name in sets:
            session.add(GroupPermissionSet(
                group_id=record.id, set_id=_set(session, set_name).id))
        session.flush()
        return _group_view(session, record)


def update_group(name, description=None):
    """
    _function_: `mast.permissions.update_group(name, description=None)`

    Change the description of a group.
    """
    sync_plugin_groups()
    with db.session() as session:
        record = _group(session, name)
        if description is not None:
            record.description = description
        session.flush()
        return _group_view(session, record)


def remove_group(name):
    """
    _function_: `mast.permissions.remove_group(name)`

    Remove a group. Its users and permission sets stay.
    """
    sync_plugin_groups()
    with db.session() as session:
        record = _group(session, name)
        for model in (GroupMember, GroupPermissionSet):
            session.execute(delete(model).where(
                model.group_id == record.id))
        session.delete(record)


def get_group(name):
    """
    _function_: `mast.permissions.get_group(name)`

    Return the `GroupInfo` called `name`. Raises `UnknownName`.
    """
    sync_plugin_groups()
    with db.session(write=False) as session:
        return _group_view(session, _group(session, name))


def list_groups():
    """
    _function_: `mast.permissions.list_groups()`

    Return every group, by name.
    """
    sync_plugin_groups()
    with db.session(write=False) as session:
        return [_group_view(session, record) for record in session.scalars(
            select(UserGroup).order_by(UserGroup.name))]


def grant(group, set_name):
    """
    _function_: `mast.permissions.grant(group, set_name)`

    Give the group `group` the permission set `set_name`.
    """
    sync_plugin_groups()
    with db.session() as session:
        record = _group(session, group)
        found = _set(session, set_name)
        if session.get(GroupPermissionSet, (record.id, found.id)) is None:
            session.add(GroupPermissionSet(group_id=record.id,
                                           set_id=found.id))
        session.flush()
        return _group_view(session, record)


def revoke(group, set_name):
    """
    _function_: `mast.permissions.revoke(group, set_name)`

    Take the permission set `set_name` from the group `group`.
    """
    sync_plugin_groups()
    with db.session() as session:
        record = _group(session, group)
        found = _set(session, set_name)
        session.execute(delete(GroupPermissionSet).where(
            GroupPermissionSet.group_id == record.id,
            GroupPermissionSet.set_id == found.id))
        session.flush()
        return _group_view(session, record)


def add_member(group, user):
    """
    _function_: `mast.permissions.add_member(group, user)`

    Put the user `user` in the group `group`.
    """
    sync_plugin_groups()
    with db.session() as session:
        record = _group(session, group)
        user_id = _user_id(session, user)
        if session.get(GroupMember, (record.id, user_id)) is None:
            session.add(GroupMember(group_id=record.id, user_id=user_id))
        session.flush()
        return _group_view(session, record)


def remove_member(group, user):
    """
    _function_: `mast.permissions.remove_member(group, user)`

    Take the user `user` out of the group `group`.
    """
    sync_plugin_groups()
    with db.session() as session:
        record = _group(session, group)
        user_id = _user_id(session, user)
        session.execute(delete(GroupMember).where(
            GroupMember.group_id == record.id,
            GroupMember.user_id == user_id))
        session.flush()
        return _group_view(session, record)


def groups_of(user):
    """
    _function_: `mast.permissions.groups_of(user)`

    Return the names of the groups of the user `user`, by name.
    """
    sync_plugin_groups()
    with db.session(write=False) as session:
        user_id = _user_id(session, user)
        return list(session.scalars(
            select(UserGroup.name)
            .join(GroupMember, GroupMember.group_id == UserGroup.id)
            .where(GroupMember.user_id == user_id)
            .order_by(UserGroup.name)))


# The groups of the plugins
# =========================

# What sync_plugin_groups last made the database agree with, in this
# process
_synced = {}


def _plugin_marker(provider):
    return "plugin:" + provider


def sync_plugin_groups():
    """
    _function_: `mast.permissions.sync_plugin_groups()`

    Make the groups that the installed plugins define, each with a
    permission set of its name and no members, the first time that MAST
    sees them; and bring a set up to date with a newer version of its
    plugin, unless an admin changed it. A group that was made once is
    not made again after an admin removed it. Return the names of the
    groups that were made now.

    The functions of this module call this before they read the groups
    and the sets, once in a process for each version of the plugins.
    """
    from mast import plugins
    wanted = {}
    for provider, manifest in plugins.manifests().items():
        for group in manifest.groups:
            wanted[group.name] = (provider, group)
    key = tuple(sorted((name, repr(group))
                       for name, (_, group) in wanted.items()))
    if _synced.get(db.engine()) == key:
        return []
    made = []
    with db.session() as session:
        seen = {row.name for row in session.scalars(select(PluginGroup))}
        for name, (provider, group) in sorted(wanted.items()):
            try:
                rules = [check_rule(rule) for rule in group.rules]
            except PermissionError:
                continue
            record = session.scalars(select(PermissionSet).where(
                PermissionSet.name == name)).first()
            if name in seen:
                if record is not None and \
                        record.updated_by == _plugin_marker(provider) and (
                            record.rules != _stored(rules) or
                            record.description != group.description):
                    record.rules = _stored(rules)
                    record.description = group.description
                    record.updated_at = _now()
                continue
            session.add(PluginGroup(name=name, provider=provider,
                                    made_at=_now()))
            taken = session.scalars(select(UserGroup.id).where(
                UserGroup.name == name)).first()
            if record is not None or taken is not None:
                # An admin has the name already: theirs stays
                continue
            record = PermissionSet(
                name=name, description=group.description,
                rules=_stored(rules), updated_by=_plugin_marker(provider),
                updated_at=_now())
            session.add(record)
            session.flush()
            new = UserGroup(name=name, description=group.description)
            session.add(new)
            session.flush()
            session.add(GroupPermissionSet(group_id=new.id,
                                           set_id=record.id))
            made.append(name)
    _synced[db.engine()] = key
    return made


# Decisions
# =========


def rules_of(user):
    """
    _function_: `mast.permissions.rules_of(user)`

    Return every rule that applies to the user `user`, as `Match`es:
    the rules of the permission sets of all their groups, each once.
    """
    sync_plugin_groups()
    with db.session(write=False) as session:
        user_id = _user_id(session, user)
        rows = session.execute(
            select(PermissionSet, UserGroup.name)
            .join(GroupPermissionSet,
                  GroupPermissionSet.set_id == PermissionSet.id)
            .join(UserGroup, UserGroup.id == GroupPermissionSet.group_id)
            .join(GroupMember, GroupMember.group_id == UserGroup.id)
            .where(GroupMember.user_id == user_id)
            .order_by(PermissionSet.name, UserGroup.name)).all()
        by_set = {}
        for record, group in rows:
            entry = by_set.setdefault(record.name, (record, []))
            entry[1].append(group)
        found = []
        for name, (record, groups) in sorted(by_set.items()):
            for index, rule in enumerate(_loaded(record.rules)):
                found.append(Match(set=name, groups=tuple(groups),
                                   index=index, rule=rule))
        return found


def _covers(scope, target):
    """True if the scope of a rule covers the target (an id and the ids
    of its fleets)."""
    if target is None:
        return True
    if not scope:
        return False
    if scope.get("all"):
        return True
    target_id, fleet_ids = target
    if target_id in scope.get("targets", ()):
        return True
    return bool(set(fleet_ids) & set(scope.get("fleets", ())))


def _target(name):
    """(id, fleet ids) of the target `name`. Raises `UnknownName`."""
    from mast import registry
    found = registry.by_name(name)
    if found is None:
        raise UnknownName("There is no target {}.".format(name))
    fleets = {f.name: f.id for f in registry.fleet_list()}
    return found.id, [fleets[f] for f in found.fleets if f in fleets]


def decide(user, capability, path, target=None, rules=None):
    """
    _function_: `mast.permissions.decide(user, capability, path,
    target=None, rules=None)`

    Return the `Decision` whether the user `user` may use `capability`
    (`r`, `w` or `x`) on `path`, for the target called `target` if
    given. `rules` are the user's `Match`es (`rules_of`), to ask several
    questions at once.
    """
    if capability not in CAPABILITIES:
        raise InvalidRule("A capability is r, w or x, not {!r}".format(
            capability))
    check_path(path)
    if "*" in path:
        raise InvalidRule("A question is about one path, without *.")
    rules = rules_of(user) if rules is None else rules
    where = None if target is None else _target(target)
    allowed, denied = [], []
    for match in rules:
        rule = match.rule
        if not path_matches(rule.path, path):
            continue
        if capability in rule.deny and (
                where is None or rule.scope is None
                or _covers(rule.scope, where)):
            denied.append(match)
        if capability in rule.allow and _covers(rule.scope, where):
            allowed.append(match)
    return Decision(user=user, capability=capability, path=path,
                    target=target, allowed=bool(allowed) and not denied,
                    allowed_by=tuple(allowed), denied_by=tuple(denied))


def check_command(user, command_id, targets=()):
    """
    _function_: `mast.permissions.check_command(user, command_id,
    targets=())`

    Return the `Decision`s that running the command `command_id` on
    `targets` (names of targets and fleets) needs: `x`, and `w` for a
    command whose effect is `change`, for each target. The user may run
    it if every one is allowed.
    """
    from mast import registry
    from mast.jobs import registry as commands
    command = commands.resolve(command_id)
    needed = ["x"] + (["w"] if command.effect == "change" else [])
    names = [t.name for t in registry.resolve(list(targets))] \
        if targets else [None]
    rules = rules_of(user)
    return [decide(user, capability, command.path, name, rules)
            for name in names for capability in needed]


def may_run_any(user):
    """
    _function_: `mast.permissions.may_run_any(user)`

    Return True if a rule of the user allows `x` on the commands of a
    plugin, on some targets: whether the user may run anything at all
    (for the web UI, before it knows the command).
    """
    for match in rules_of(user):
        rule = match.rule
        root = rule.path.split("/")[1]
        if root == "core" or root in RESOURCE_ROOTS:
            continue
        if "x" in rule.allow and rule.scope is not None:
            return True
    return False


def set_groups(user, groups):
    """
    _function_: `mast.permissions.set_groups(user, groups)`

    Put the user `user` in exactly the groups `groups`. Raises
    `UnknownName` for a group that does not exist, before it changes
    anything.
    """
    sync_plugin_groups()
    wanted = list(dict.fromkeys(groups))
    for group in wanted:
        get_group(group)
    current = groups_of(user)
    for group in current:
        if group not in wanted:
            remove_member(group, user)
    for group in wanted:
        if group not in current:
            add_member(group, user)

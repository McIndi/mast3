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
_module_: `mast.permissions.cli`

`mast permissions` and `mast groups`: the permission sets and the
groups of users (see `mast.permissions`).

`mast permissions`:

* `add`, `update`, `remove`, `list`, `show`: The permission sets. A set
is given as a JSON file (`-f`), or built rule by rule.
* `add-rule`, `remove-rule`: One rule of a set.
* `check`: Whether a user may do something, and the rules that decide.
* `effective`: Every rule that applies to a user.

`mast groups`: `add`, `update`, `remove`, `list`, `show`, `grant` and
`revoke` (a permission set), `add-member` and `remove-member` (a user).

Whoever can run these on the MAST host can read the database, so they
need no login; they are an admin.
"""
import json
import sys

from mast import permissions
from mast.cli import Cli, Option, Params, Table, Text
from mast.jobs.auth import cli_principal
from mast.roots import CORE

__all__ = ["permissions_cli", "groups_cli"]

permissions_cli = Cli(
    description="Manage the permission sets of MAST: rules over the paths "
                "of commands and resources (mast/datapower/get-status, "
                "mast/credentials/dp-reader), with r, w and x.",
    provider=CORE, prog="mast permissions", queued=False)
groups_cli = Cli(
    description="Manage the groups of users of MAST, and the permission "
                "sets that they have.",
    provider=CORE, prog="mast groups", queued=False)


def _fail(message, prog="mast permissions"):
    print("{}: {}".format(prog, message), file=sys.stderr)
    sys.exit(1)


def _do(change, *args, **kwargs):
    from mast import registry
    from mast.jobs import registry as commands
    try:
        return change(*args, **kwargs)
    except (permissions.PermissionError, registry.RegistryError,
            commands.UnknownCommand) as exc:
        _fail(exc)


def _rule_line(number, rule):
    parts = [rule.path]
    if rule.allow:
        parts.append("allow " + "".join(rule.allow))
    if rule.deny:
        parts.append("deny " + "".join(rule.deny))
    scope = rule.json()["scope"] if rule.scope is not None else None
    if scope is not None:
        key = next(iter(scope))
        parts.append("all targets" if key == "all" else "{}: {}".format(
            key, " ".join(scope[key])))
    return "  {}. {}".format(number, ", ".join(parts))


def _show_set(found):
    lines = ["{}{}".format(found.name, ": " + found.description
                           if found.description else "")]
    lines += [_rule_line(n + 1, rule) for n, rule in enumerate(found.rules)]
    if not found.rules:
        lines.append("  no rules")
    lines.append("  groups: {}".format(" ".join(found.groups) or "none"))
    return "\n".join(lines)


class SetParams(Params):
    name: str = Option(short="n", help="The name of the permission set.")


class AddSet(SetParams):
    description: str = Option("", short="d", help="What it is for.")
    file: str | None = Option(
        None, short="f",
        help="A JSON file with the rules: a list of rules, or an object "
             "with \"rules\" (and \"description\"). A rule is {\"path\": "
             "\"mast/datapower/list-*\", \"allow\": [\"r\", \"x\"], "
             "\"scope\": {\"fleets\": [\"lab\"]}}.")


def _read(path):
    """(rules, description or None) from a JSON file."""
    try:
        with open(path, encoding="utf-8") as fin:
            data = json.load(fin)
    except (OSError, ValueError) as exc:
        _fail("Cannot read {}: {}".format(path, exc))
    if isinstance(data, list):
        return data, None
    if isinstance(data, dict) and isinstance(data.get("rules"), list):
        return data["rules"], data.get("description")
    _fail("{} is a list of rules, or an object with \"rules\".".format(path))


@permissions_cli.command("add", effect="change", id="add-permission-set")
def add_set(params: AddSet):
    """Add a permission set, with the rules of a JSON file or none (add
    them with add-rule). It grants nothing until a group has it (mast
    groups grant)."""
    rules, description = _read(params.file) if params.file else ([], None)
    return Text("Added " + _show_set(_do(
        permissions.add_set, params.name, rules,
        params.description or description or "",
        principal=cli_principal())))


class UpdateSet(SetParams):
    description: str | None = Option(None, short="d", help="What it is for.")
    file: str | None = Option(
        None, short="f", help="A JSON file whose rules replace the rules.")


@permissions_cli.command("update", effect="change",
                         id="update-permission-set")
def update_set(params: UpdateSet):
    """Replace the rules of a permission set with those of a JSON file,
    or change its description."""
    rules, description = _read(params.file) if params.file else (None, None)
    return Text("Updated " + _show_set(_do(
        permissions.update_set, params.name, rules,
        params.description if params.description is not None
        else description, principal=cli_principal())))


@permissions_cli.command("remove", effect="change",
                         id="remove-permission-set")
def remove_set(params: SetParams):
    """Remove a permission set. The groups that had it no longer do."""
    _do(permissions.remove_set, params.name)
    return Text("Removed the permission set {}.".format(params.name))


@permissions_cli.command("list", effect="read", id="list-permission-sets")
def list_sets(params: Params):
    """List the permission sets."""
    return Table(("Name", "Rules", "Groups", "Description"),
                 [(s.name, len(s.rules), " ".join(s.groups), s.description)
                  for s in permissions.list_sets()])


@permissions_cli.command("show", effect="read", id="show-permission-set")
def show_set(params: SetParams):
    """Show a permission set: its rules, numbered, and its groups."""
    return Text(_show_set(_do(permissions.get_set, params.name)))


class AddRule(SetParams):
    path: str = Option(
        short="p", help="The path: mast/PROVIDER/COMMAND, mast/core/NAME, "
                        "mast/credentials/NAME, mast/targets/NAME or "
                        "mast/fleets/NAME, which may end in *.")
    allow: str = Option("", help="What it allows: some of r, w and x "
                                 "(rx, rwx).")
    deny: str = Option("", help="What it denies, which wins over any "
                                "allow.")
    all_targets: bool = Option(
        False, help="The rule applies to every target.")
    target: list[str] = Option(
        default_factory=list, short="t",
        help="A target that the rule applies to.")
    fleet: list[str] = Option(
        default_factory=list,
        help="A fleet whose targets the rule applies to.")


@permissions_cli.command("add-rule", effect="change",
                         id="add-permission-rule")
def add_rule(params: AddRule):
    """Add a rule to the end of a permission set. A rule that allows a
    command on targets needs a scope (--all-targets, -t or --fleet): one
    without grants no target."""
    scopes = [bool(params.all_targets), bool(params.target),
              bool(params.fleet)]
    if sum(scopes) > 1:
        _fail("Give one scope: --all-targets, -t or --fleet.")
    rule = {"path": params.path, "allow": params.allow, "deny": params.deny}
    if params.all_targets:
        rule["scope"] = {"all": True}
    elif params.target:
        rule["scope"] = {"targets": params.target}
    elif params.fleet:
        rule["scope"] = {"fleets": params.fleet}
    return Text("Updated " + _show_set(_do(
        permissions.add_rule, params.name, rule,
        principal=cli_principal())))


class RemoveRule(SetParams):
    number: int = Option(short="r", help="The number of the rule (show).")


@permissions_cli.command("remove-rule", effect="change",
                         id="remove-permission-rule")
def remove_rule(params: RemoveRule):
    """Remove a rule of a permission set, by its number."""
    return Text("Updated " + _show_set(_do(
        permissions.remove_rule, params.name, params.number,
        principal=cli_principal())))


class Check(Params):
    user: str = Option(short="u", help="The user.")
    path: str | None = Option(
        None, short="p", help="The path to ask about, without *.")
    capability: str = Option(
        "x", short="c", help="With --path: r, w or x.")
    command: str | None = Option(
        None, help="A command id (datapower.save): x, and w for one that "
                   "changes.")
    appliances: list[str] = Option(
        default_factory=list, short="a",
        help="A target or a fleet that the question is about.")


@permissions_cli.command("check", effect="read", id="check-permission")
def check(params: Check):
    """Say whether a user may do something, and which rules decide: a
    capability on a path (--path, -c), or running a command (--command)
    on targets (-a)."""
    if bool(params.path) == bool(params.command):
        _fail("Give --path or --command.")
    if params.command:
        decisions = _do(permissions.check_command, params.user,
                        params.command, params.appliances)
    else:
        from mast import registry
        names = [t.name for t in _do(registry.resolve, params.appliances)] \
            if params.appliances else [None]
        rules = _do(permissions.rules_of, params.user)
        decisions = [_do(permissions.decide, params.user, params.capability,
                         params.path, name, rules) for name in names]
    lines = [("allowed: " if d.allowed else "denied: ") + d.reason
             for d in decisions]
    verdict = all(d.allowed for d in decisions)
    lines.append("{} {}.".format(params.user, "may" if verdict else
                                 "may not"))
    return Text("\n".join(lines))


class UserParams(Params):
    user: str = Option(short="u", help="The user.")


@permissions_cli.command("effective", effect="read",
                         id="show-effective-permissions")
def effective(params: UserParams):
    """Show every rule that applies to a user: of each permission set of
    each of their groups."""
    groups = _do(permissions.groups_of, params.user)
    matches = _do(permissions.rules_of, params.user)
    rows = [(m.set, " ".join(m.groups), m.index + 1,
             _rule_line(m.index + 1, m.rule).split(". ", 1)[1])
            for m in matches]
    if not rows:
        return Text("{} is in {} and has no rules.".format(
            params.user, " ".join(groups) or "no group"))
    return Table(("Set", "Groups", "Rule", "What"), rows)


class GroupParams(Params):
    name: str = Option(short="n", help="The name of the group.")


def _show_group(found):
    return "{}{}\n  sets: {}\n  members: {}".format(
        found.name, ": " + found.description if found.description else "",
        " ".join(found.sets) or "none", " ".join(found.members) or "none")


def _gdo(change, *args, **kwargs):
    try:
        return change(*args, **kwargs)
    except permissions.PermissionError as exc:
        _fail(exc, "mast groups")


class AddGroup(GroupParams):
    description: str = Option("", short="d", help="What it is for.")
    set: list[str] = Option(
        default_factory=list, short="s",
        help="A permission set that it has.")


@groups_cli.command("add", effect="change", id="add-group")
def add_group(params: AddGroup):
    """Add a group of users, with permission sets."""
    return Text("Added " + _show_group(_gdo(
        permissions.add_group, params.name, params.description,
        params.set)))


class UpdateGroup(GroupParams):
    description: str = Option(short="d", help="What it is for.")


@groups_cli.command("update", effect="change", id="update-group")
def update_group(params: UpdateGroup):
    """Change the description of a group."""
    return Text("Updated " + _show_group(_gdo(
        permissions.update_group, params.name, params.description)))


@groups_cli.command("remove", effect="change", id="remove-group")
def remove_group(params: GroupParams):
    """Remove a group. Its users and permission sets stay."""
    _gdo(permissions.remove_group, params.name)
    return Text("Removed the group {}.".format(params.name))


@groups_cli.command("list", effect="read", id="list-groups")
def list_groups(params: Params):
    """List the groups, their permission sets and members."""
    return Table(("Name", "Sets", "Members", "Description"),
                 [(g.name, " ".join(g.sets), " ".join(g.members),
                   g.description) for g in permissions.list_groups()])


@groups_cli.command("show", effect="read", id="show-group")
def show_group(params: GroupParams):
    """Show a group."""
    return Text(_show_group(_gdo(permissions.get_group, params.name)))


class GroupSet(GroupParams):
    set: str = Option(short="s", help="The permission set.")


@groups_cli.command("grant", effect="change", id="grant-permission-set")
def grant(params: GroupSet):
    """Give a group a permission set."""
    return Text("Updated " + _show_group(_gdo(
        permissions.grant, params.name, params.set)))


@groups_cli.command("revoke", effect="change", id="revoke-permission-set")
def revoke(params: GroupSet):
    """Take a permission set from a group."""
    return Text("Updated " + _show_group(_gdo(
        permissions.revoke, params.name, params.set)))


class GroupUser(GroupParams):
    user: str = Option(short="u", help="The user.")


@groups_cli.command("add-member", effect="change", id="add-group-member")
def add_member(params: GroupUser):
    """Put a user in a group."""
    return Text("Updated " + _show_group(_gdo(
        permissions.add_member, params.name, params.user)))


@groups_cli.command("remove-member", effect="change",
                    id="remove-group-member")
def remove_member(params: GroupUser):
    """Take a user out of a group."""
    return Text("Updated " + _show_group(_gdo(
        permissions.remove_member, params.name, params.user)))

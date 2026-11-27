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
_module_: `mast.main`

The `mast` command, the one command line of MAST:

    mast [--home PATH] ROOT [ARGUMENTS ...]

`ROOT` is a root of core (`mast targets`, `mast jobs`, `mast web`, see
`mast.roots`) or the provider of a plugin (`mast datapower`). Every
plugin command is `mast PROVIDER COMMAND`.

MAST runs in its home. The home is `--home`, else the `MAST_HOME`
environment variable, else the current folder or the folder of the
interpreter (see `mast.home`). `mast`, `mast --help` and
`mast ROOT --help` for a plugin need no home.
"""
import sys

from mast import home, plugins, roots

__all__ = ["main", "entry"]

PROG = "mast"


def _usage():
    lines = ["usage: mast [--home PATH] ROOT [ARGUMENTS ...]", "",
             "McIndi's Automated Solutions Tool.", "", "core:"]
    width = max(len(name) for name in roots.ROOTS) + 2
    for name, root in roots.ROOTS.items():
        lines.append("  {}{}".format(name.ljust(width), root.summary))
    found = plugins.manifests()
    lines += ["", "plugins:"]
    if found:
        width = max([width] + [len(name) + 2 for name in found])
        for name, manifest in sorted(found.items()):
            lines.append("  {}{}".format(name.ljust(width), manifest.title))
    else:
        lines.append("  (none installed)")
    failed = plugins.failures()
    if failed:
        lines += ["", "left out (see mast plugins list):"]
        lines += ["  {}".format(f.name) for f in failed]
    lines += ["", "Run `mast ROOT --help` for the commands of a root."]
    return "\n".join(lines) + "\n"


def _fail(message):
    sys.stderr.write("{}: error: {}\n".format(PROG, message))
    return 2


def _split(argv):
    """Return (home or None, root or None, the rest). Raises ValueError."""
    argv = list(argv)
    home_path = None
    while argv and argv[0].startswith("-"):
        option = argv.pop(0)
        if option in ("-h", "--help"):
            return home_path, None, []
        if option == "--home":
            if not argv:
                raise ValueError("--home needs a PATH")
            home_path = argv.pop(0)
        elif option.startswith("--home="):
            home_path = option.partition("=")[2]
        elif option == "--version":
            return home_path, "version", []
        else:
            raise ValueError("unknown option {}".format(option))
    if not argv:
        return home_path, None, []
    return home_path, argv[0], argv[1:]


def _enter(home_path):
    try:
        home.enter(home_path)
    except home.MastHomeNotFound as exc:
        sys.stderr.write("{}\n".format(exc))
        raise SystemExit(2)


def main(argv=None):
    """
    _function_: `mast.main.main(argv=None)`

    Run the `mast` command with `argv` (the default is
    `sys.argv[1:]`). Return the exit status, or raise `SystemExit`.
    """
    argv = sys.argv[1:] if argv is None else argv
    try:
        home_path, root, rest = _split(argv)
    except ValueError as exc:
        return _fail(exc)
    if root is None:
        sys.stdout.write(_usage())
        return 0

    found = roots.ROOTS.get(root)
    if found is not None:
        if found.program:
            if root != "version":
                _enter(home_path)
            program = roots.load(found.target)
            return program(rest) or 0
        _enter(home_path)
        cli = roots.load(found.target)
        return cli.run(rest) or 0

    if root in plugins.manifests():
        _enter(home_path)
        # Imported here: the registry imports the configuration
        from mast.jobs import registry
        try:
            cli = registry.cli_for(root)
            # Check every command of the plugin before one runs
            registry.commands(root)
        except registry.UnknownCommand as exc:
            return _fail(exc)
        if plugins.failure(root) is None:
            return cli.run(rest) or 0

    failed = plugins.failure(root)
    if failed is not None:
        return _fail("the plugin {} was left out: {}. {}".format(
            root, failed.reason, failed.fix))
    return _fail("{!r} is not a root of mast. Run `mast --help`.".format(root))


def entry():
    """
    _function_: `mast.main.entry()`

    The console script `mast`.
    """
    sys.exit(main())

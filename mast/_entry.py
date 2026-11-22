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
_module_: `mast._entry`

The console scripts of `pyproject.toml` (`mast-system`, `mast-web` and
so on) call the functions in this module. Each function does what the
launcher of the same name does: it enters `MAST_HOME` (see `mast.home`)
and runs a module as `python -m` does.

The launchers in the installer do not use these functions. They call
`./mast -m <module>`.
"""
import runpy
import sys

from mast import home


def _run(module):
    """
    _function_: `mast._entry._run(module)`

    Enter `MAST_HOME` and run `module` as `python -m module` does. If
    MAST cannot find its home, print how to fix it and exit with 2.
    """
    try:
        home.enter()
    except home.MastHomeNotFound as e:
        print(e, file=sys.stderr)
        sys.exit(2)
    runpy.run_module(module, run_name="__main__", alter_sys=True)


def accounts():
    _run("mast.datapower.accounts")


def appliances():
    _run("mast.registry")


def backups():
    _run("mast.datapower.backups")


def credentials():
    _run("mast.credentials")


def crypto():
    _run("mast.datapower.crypto")


def deployment():
    _run("mast.datapower.deployment")


def developer():
    _run("mast.datapower.developer")


def jobs():
    _run("mast.jobs")


def network():
    _run("mast.datapower.network")


def ssh():
    _run("mast.datapower.ssh")


def system():
    _run("mast.datapower.system")


def users():
    _run("mast.auth")


def version():
    _run("mast")


def web():
    _run("mast.web")


def worker():
    _run("mast.jobs.worker")


def daemon():
    _run("mast._daemon")


def testsuite():
    _run("mast.testsuite")

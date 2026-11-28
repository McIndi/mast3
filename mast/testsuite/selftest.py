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
# Copyright 2015-2024, McIndi Solutions, All rights reserved.
"""
_module_: `mast.testsuite.selftest`

`mast selftest`: run the tests of MAST in this installation.

The tests never run in the home itself. They submit jobs, register
appliances and write to the database, and in a home that is in use
those are its database, its registered appliances and the jobs that its
workers run. `mast selftest` makes a temporary copy of the files of the
home (`contrib`, `doc`, `etc`, `bin` and `usrbin`, without `etc/local`
and the private keys), and runs the tests in a new process in that
copy, with an empty database and no appliances. The copy is removed
when the tests end.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from mast import __version__
from mast.cli import Cli
from mast.cli import Params, Option
from mast.logging import make_logger

here = os.path.dirname(__file__)

# Set in the process that runs the tests, to the home that it copied
ISOLATED = "MAST_SELFTEST_ISOLATED"

# What of the home the copy has: the configuration and the files that
# come with MAST. Not the database and logs (var), tmp, the interpreter
# (python) or the launchers.
COPIED = ("bin", "contrib", "doc", "etc", "usrbin")


class Main(Params):
    out_file: str = Option(
        "stdout", short="o",
        help='If given, the path and filename of where you would like '
             'the results written. Defaults to `stdout`. Will overwrite '
             'given file')
    unit: bool = Option(
        False, short="u",
        help='If specified, run the unit tests')
    integration: bool = Option(
        False, short="i",
        help='If specified, run the integration tests')
    regression: bool = Option(
        False, short="r",
        help='If specified, run the regression tests')
    All: bool = Option(False, short="A")
    version: bool = Option(
        False, short="v",
        help='Print version number and exit')


def _ignore(source, names):
    """What `copy_home` leaves out of one directory."""
    parts = os.path.normpath(source).split(os.sep)
    if parts[-1] == "etc":
        return [name for name in names if name == "local"]
    if parts[-2:] == ["crypto", "private"]:
        return list(names)
    return []


def copy_home(home, into):
    """
    _function_: `mast.testsuite.selftest.copy_home(home, into)`

    Copy what the tests need of the MAST home `home` into the new folder
    `into`, and return its path: the configuration without
    `etc/local`, and none of the database, the logs, the private keys or
    the interpreter.
    """
    target = os.path.join(into, "mast_home")
    os.makedirs(target)
    for name in COPIED:
        source = os.path.join(home, name)
        if os.path.isdir(source):
            shutil.copytree(source, os.path.join(target, name),
                            ignore=_ignore)
    for name in ("etc/local", "tmp", "var/log", "var/jobs"):
        os.makedirs(os.path.join(target, *name.split("/")), exist_ok=True)
    return target


def suites(params):
    """The test suites that `params` asks for."""
    names = []
    if params.unit or params.All:
        names.append("unit")
    if params.integration or params.All:
        names.append("integration")
    if params.regression or params.All:
        names.append("regression")
    found = []
    for name in names:
        start = os.path.join(here, name)
        found.append(unittest.defaultTestLoader.discover(
            start_dir=start, top_level_dir=start))
    return found


def run_tests(params):
    """Run the suites in this process (the copy of the home). Return
    True if every test passed."""
    suite = unittest.TestSuite(suites(params))
    if params.out_file == "stdout":
        result = unittest.TextTestRunner(
            stream=sys.stdout, verbosity=0).run(suite)
    else:
        with open(params.out_file, "w") as fp_out:
            result = unittest.TextTestRunner(
                stream=fp_out, verbosity=0).run(suite)
    return result.wasSuccessful()


def main(params: Main):
    """mast.testsuite

A testsuite for MAST for IBM DataPower. It runs in a temporary copy of
this MAST home, with an empty database and no registered appliances,
so that it never changes the home or talks to its appliances. The
following options can be specified:"""
    global failed
    if params.version:
        print("mast.testsuite - version {}".format(__version__))
        sys.exit(0)
    if not suites(params) and not os.environ.get(ISOLATED):
        print("Give -u, -i, -r or -A to choose the tests to run.")
        return
    if os.environ.get(ISOLATED):
        failed = not run_tests(params)
        return
    home = os.environ["MAST_HOME"]
    out_file = params.out_file
    if out_file != "stdout":
        out_file = os.path.abspath(out_file)
    work = tempfile.mkdtemp(prefix="mast-selftest-")
    try:
        copy = copy_home(home, work)
        env = dict(os.environ, MAST_HOME=copy)
        env[ISOLATED] = home
        # The tests act for their own users, never for the token of the
        # person who runs them (mast.auth.identity)
        env["XDG_CONFIG_HOME"] = env["APPDATA"] = os.path.join(work,
                                                               "config")
        env.pop("MAST_TOKEN", None)
        env.pop("MAST_TOKEN_FILE", None)
        args = ["-o", out_file]
        for flag, wanted in (("-u", params.unit), ("-i", params.integration),
                             ("-r", params.regression), ("-A", params.All)):
            if wanted:
                args.append(flag)
        print("Running the tests in a copy of this home: {}".format(copy),
              flush=True)
        done = subprocess.run(
            [sys.executable, "-m", "mast.testsuite.selftest"] + args,
            cwd=copy, env=env)
        failed = done.returncode != 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


# Set by main. mast selftest exits with 1 when a test failed, so that a
# script or a CI job can check the result.
failed = False


def program(argv):
    """`mast selftest`: run the suites that `argv` names."""
    cli = Cli(main=main, description=main.__doc__, prog="mast selftest")
    try:
        cli.run(argv)
    except SystemExit as exc:
        if exc.code not in (None, 0):
            raise
    except Exception:
        make_logger("error").exception(
            "An unhandled exception occurred during execution")
        raise
    return 1 if failed else 0


if __name__ == "__main__":
    # The process in the copy of the home (see main)
    from mast import home as _home
    _home.enter(os.environ["MAST_HOME"])
    sys.exit(program(sys.argv[1:]))

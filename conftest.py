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
Prepare a MAST_HOME for pytest runs from a source checkout.

MAST reads MAST_HOME when `mast.config` is imported, and it runs with
its home as the current directory. An installed MAST sets both in its
launchers. For pytest, this file makes a private copy of
`files/mast_home` and enters it before any test module is imported. The copy keeps test runs from writing into
the repository.

A MAST_HOME that is already set (after `source set-env`, for example)
is replaced as well: the tests submit jobs, register appliances and
write to the database, and in a home that is in use they would reach
its appliances through its workers. `mast selftest` runs the same tests
in a copy of its home for the same reason.
"""
import atexit
import os
import shutil
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))

# The integration tests can run the launchers of a home (see
# test_cli_container.py). A relative path is relative to where pytest
# was started, not to the test home below.
if os.environ.get("MAST_TEST_HOME"):
    os.environ["MAST_TEST_HOME"] = os.path.abspath(os.environ["MAST_TEST_HOME"])

_tmp = tempfile.mkdtemp(prefix="mast-test-home-")
_home = os.path.join(_tmp, "mast_home")
shutil.copytree(os.path.join(_HERE, "files", "mast_home"), _home)
for _name in ("etc/local", "tmp", "var/log"):
    os.makedirs(os.path.join(_home, *_name.split("/")), exist_ok=True)
# MAST runs with its home as the current directory, as the
# launchers do. mast.home only finds the home here; it does not
# import mast.config.
from mast import home
home.enter(_home)
atexit.register(shutil.rmtree, _tmp, ignore_errors=True)

# The tests act for their own users, never for the token of the person
# who runs them (mast login, mast --as, MAST_TOKEN): see
# mast.auth.identity
os.environ["XDG_CONFIG_HOME"] = os.environ["APPDATA"] = os.path.join(
    _tmp, "config")
os.environ.pop("MAST_TOKEN", None)
os.environ.pop("MAST_TOKEN_FILE", None)

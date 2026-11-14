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

MAST reads MAST_HOME when `mast.config` is imported, and it finds the
SOMA spec file relative to the current directory. An installed MAST
sets both in its invocation scripts. A source checkout has neither, so
this file gives pytest a private copy of `files/mast_home` before any
test module is imported. The copy keeps test runs from writing into
the repository.

If MAST_HOME is already set, this file does not change it.
"""
import atexit
import os
import shutil
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))

if "MAST_HOME" not in os.environ:
    _tmp = tempfile.mkdtemp(prefix="mast-test-home-")
    _home = os.path.join(_tmp, "mast_home")
    shutil.copytree(os.path.join(_HERE, "files", "mast_home"), _home)
    for _name in ("etc/local", "tmp", "var/log"):
        os.makedirs(os.path.join(_home, *_name.split("/")), exist_ok=True)
    os.environ["MAST_HOME"] = _home
    os.chdir(_home)
    atexit.register(shutil.rmtree, _tmp, ignore_errors=True)

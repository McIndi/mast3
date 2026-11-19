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
Private copies of a MAST home for the tests that run its launchers.

MAST_TEST_HOME names the home under test: a development home made by
`./mast-setup --development-mode`, or an unzipped installer.
"""
import os
import shutil

__all__ = ["TEST_HOME", "copy_home"]

TEST_HOME = os.environ.get("MAST_TEST_HOME")

# Not copied: the runtime files of the home, and the interpreter, which
# is linked. var/www (the web GUI) is copied.
_NOT_COPIED = {
    os.path.join("etc", "local"), os.path.join("var", "log"),
    os.path.join("var", "run"), os.path.join("var", "jobs"), "tmp",
    "python", ".mast-setup",
}


def copy_home(source, destination):
    """
    Copy the MAST home `source` to `destination` without its runtime
    files, and make the empty folders that MAST writes to. Symlinks
    (the links of a development home into the checkout) stay symlinks.
    The interpreter is linked, not copied.
    """
    def ignore(directory, names):
        relative = os.path.relpath(directory, source)
        return [n for n in names
                if os.path.normpath(os.path.join(relative, n)) in _NOT_COPIED]
    shutil.copytree(source, destination, symlinks=True, ignore=ignore)
    os.symlink(os.path.join(source, "python"),
               os.path.join(destination, "python"),
               target_is_directory=True)
    for name in (os.path.join("etc", "local"), "tmp",
                 os.path.join("var", "log"), os.path.join("var", "run")):
        os.makedirs(os.path.join(destination, name), exist_ok=True)

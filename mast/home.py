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
_module_: `mast.home`

This module finds `MAST_HOME`, the folder that holds the configuration
(`etc`), the logs (`var/log`), the web files (`var/www`) and, in an
installed MAST, the Python interpreter and the launchers.

MAST runs with its home as the current directory. The launchers
(`mast-system`, `mast-web` and so on) set `MAST_HOME` and change to
it before they start Python, and some paths in the configuration are
relative to it (for example `soma_spec_file` in `appliances.conf` and
the `out_dir` defaults). `enter()` does the same as the launchers, for
code that starts MAST in another way, such as the console scripts of
a `pip install`.

A folder is a home if it contains `etc/default/server.conf`. That file
is in every home.

There are three functions in this module:

1. `find_home()`: Return the home from the `MAST_HOME` environment
variable, from the current directory and its parents, or from the
interpreter (the Python of an installer is in its home).
2. `find_from(path)`: Return the home that contains `path`.
3. `enter(home=None)`: Set `MAST_HOME` and change to the home.
"""
import os
import sys

MARKER = os.path.join("etc", "default", "server.conf")

HELP = (
    "Set MAST_HOME, run MAST from its folder with its launchers, or make "
    "a development home with ./mast-setup --development-mode."
)


class MastHomeNotFound(RuntimeError):
    """MAST could not find its home."""


def is_home(path):
    """
    _function_: `mast.home.is_home(path)`

    Return `True` if `path` is a MAST home, that is if it contains
    `etc/default/server.conf`.
    """
    return os.path.isfile(os.path.join(path, MARKER))


def find_from(path):
    """
    _function_: `mast.home.find_from(path)`

    Return the absolute path of the nearest folder, `path` or one of its
    parents, that is a MAST home. Raise `MastHomeNotFound` if there is
    none.

    `path` can be a file. The Windows service uses this with
    `sys.executable`, which is under `MAST_HOME/python`.

    Parameters:

    * `path`: The file or folder to start from
    """
    current = os.path.abspath(path)
    while True:
        if os.path.isdir(current) and is_home(current):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            raise MastHomeNotFound(
                "No MAST home contains {}. {}".format(path, HELP))
        current = parent


def find_home():
    """
    _function_: `mast.home.find_home()`

    Return the absolute path of `MAST_HOME`. The order is:

    1. The `MAST_HOME` environment variable.
    2. The current directory, or its nearest parent that is a home.
    3. The home that contains the interpreter (`sys.executable`). The
    Python of an installer or of a development home is in
    `MAST_HOME/python`, so its console scripts (for example
    `MAST_HOME/python/3.14.10/bin/mast-system`) work from any directory.

    Raise `MastHomeNotFound` if none of these gives a home.
    """
    home = os.environ.get("MAST_HOME")
    if home:
        return os.path.abspath(home)
    for start in (os.getcwd(), sys.executable):
        if not start:
            continue
        try:
            return find_from(start)
        except MastHomeNotFound:
            pass
    raise MastHomeNotFound(
        "MAST_HOME is not set and neither {} nor the interpreter {} is in "
        "a MAST home. {}".format(os.getcwd(), sys.executable, HELP))


def enter(home=None):
    """
    _function_: `mast.home.enter(home=None)`

    Set the `MAST_HOME` environment variable and change the current
    directory to the home, as the launchers do. Return the home.

    Call this before anything imports `mast.config`, because that
    module reads the home when it is imported.

    Parameters:

    * `home`: The home to enter. If it is not given, `find_home()`
    gives it.
    """
    home = os.path.abspath(home) if home else find_home()
    os.environ["MAST_HOME"] = home
    os.chdir(home)
    return home

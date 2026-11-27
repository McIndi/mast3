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
_module_: `mast._version`

`mast version`: the version of MAST and of each installed plugin. It
needs no MAST home.
"""
from mast import __version__

__all__ = ["program"]


def program(argv):
    """
    _function_: `mast._version.program(argv)`

    Print the version of core and of each plugin. Return 0.
    """
    import argparse
    from mast import plugins
    argparse.ArgumentParser(
        prog="mast version",
        description="Show the version of MAST and of each plugin.").parse_args(
            argv)
    print("McIndi's Automated Solutions Tool")
    print("Version {}".format(__version__))
    for name, manifest in sorted(plugins.manifests().items()):
        print("  {} ({}) {}".format(name, manifest.title, manifest.version))
    print("\nCopyright 2015-2026 McIndi Solutions, LLC")
    return 0

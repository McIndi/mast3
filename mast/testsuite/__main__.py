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
import os
import sys
import unittest
from mast import __version__
from mast.cli import Cli
from mast.cli import Params, Option
from mast.logging import make_logger

here = os.path.dirname(__file__)

unittest_dir = os.path.join(here, "unit")
unittest_suite = unittest.defaultTestLoader.discover(
    start_dir=unittest_dir,
    top_level_dir=unittest_dir)

integration_dir = os.path.join(here, "integration")
integration_suite = unittest.defaultTestLoader.discover(
    start_dir=integration_dir,
    top_level_dir=integration_dir)

regression_dir = os.path.join(here, "regression")
regression_suite = unittest.defaultTestLoader.discover(
    start_dir=regression_dir,
    top_level_dir=regression_dir)

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


def main(params: Main):
    """mast.testsuite

A testsuite for MAST for IBM DataPower. This module is executable
from mast, the following options can be specified:"""
    out_file = params.out_file
    unit = params.unit
    integration = params.integration
    regression = params.regression
    All = params.All
    version = params.version
    if version:
        print("mast.testsuite - version {}".format(__version__))
        sys.exit(0)
    suites = []
    if unit:
        suites.append(unittest_suite)
    if integration:
        suites.append(integration_suite)
    if regression:
        suites.append(regression_suite)
    if All:
        suites = [unittest_suite, integration_suite, regression_suite]

    suite = unittest.TestSuite(suites)
    if out_file == "stdout":
        unittest.TextTestRunner(stream=sys.stdout, verbosity=0).run(suite)
    else:
        with open(out_file, "w") as fp_out:
            unittest.TextTestRunner(stream=fp_out, verbosity=0).run(suite)


if __name__ == "__main__":
    cli = Cli(main=main, description=main.__doc__)
    try:
        cli.run()
    except SystemExit:
        pass
    except Exception:
        make_logger("error").exception("An unhandled exception occurred during execution")
        raise

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
Unittests for mast.util
"""
import contextlib
import io
import unittest
from unittest import mock

from mast import util


class TestRequireOptional(unittest.TestCase):

    def test_returns_a_module_that_is_installed(self):
        import json
        self.assertIs(json, util.require_optional("json"))

    def test_stops_with_instructions_when_the_module_is_missing(self):
        stderr = io.StringIO()
        logger = mock.Mock()
        with mock.patch("mast.logging.make_logger", return_value=logger), \
                mock.patch("sys.argv", ["contrib/some-script.py"]), \
                contextlib.redirect_stderr(stderr), \
                self.assertRaises(SystemExit) as raised:
            util.require_optional("no_such_library_for_mast")
        message = str(raised.exception.code)
        self.assertIn("some-script.py", message)
        self.assertIn("'no_such_library_for_mast'", message)
        self.assertIn("mast -m pip install no_such_library_for_mast", message)
        logger.error.assert_called_once_with(message)


if __name__ == "__main__":
    unittest.main()

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
Unittests for the log methods of mast.datapower.datapower.DataPower
"""
import unittest
from unittest import mock

from mast.datapower.datapower import DataPower


class TestDataPowerLogMethods(unittest.TestCase):

    def setUp(self):
        self.dp = DataPower("test", "user:secret")
        self.logger = mock.Mock()
        patcher = mock.patch.object(
            self.dp, "get_logger", return_value=self.logger)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_each_method_logs_at_its_own_level(self):
        self.dp.log_debug("one")
        self.logger.debug.assert_called_once()
        self.dp.log_info("two")
        self.logger.info.assert_called_once()
        self.dp.log_warn("three")
        self.logger.warning.assert_called_once()
        self.dp.log_error("four")
        self.logger.error.assert_called_once()
        self.dp.log_critical("five")
        self.logger.critical.assert_called_once()

    def test_messages_have_the_appliance_context(self):
        for name, level in (
                ("log_info", "info"),
                ("log_warn", "warning"),
                ("log_error", "error"),
                ("log_critical", "critical")):
            with self.subTest(method=name):
                getattr(self.dp, name)("the message")
                logged = getattr(self.logger, level).call_args[0][0]
                self.assertIn('"hostname": "test"', logged)
                self.assertIn('"message": "the message"', logged)

    def test_the_password_is_not_logged(self):
        self.dp.log_critical("the password is secret")
        logged = self.logger.critical.call_args[0][0]
        self.assertNotIn("secret", logged)
        self.assertIn("********", logged)


if __name__ == "__main__":
    unittest.main()

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
Unittests for mast.datapower.system
"""
import mast.datapower.system
from time import time
import unittest
import mock

test_zip = """UEsDBAoAAAAAAKt2m0jGNbk7BQAAAAUAAAAIABwAdGVzdC50eHRVVAkAA6EKIVehCiFXdXgLAAEE
6AMAAAToAwAAdGVzdApQSwECHgMKAAAAAACrdptIxjW5OwUAAAAFAAAACAAYAAAAAAABAAAAtIEA
AAAAdGVzdC50eHRVVAUAA6EKIVd1eAsAAQToAwAABOgDAABQSwUGAAAAAAEAAQBOAAAARwAAAAAA"""

@mock.patch("mast.datapower.system.system.enable_domain")
@mock.patch("mast.datapower.system.system.reboot_appliance")
@mock.patch("mast.datapower.system.system.disable_domain")
@mock.patch("mast.datapower.system.system.quiesce_appliance")
@mock.patch("mast.datapower.system.system.save_config")
@mock.patch("mast.datapower.system.system.clean_up")
@mock.patch("mast.datapower.system.system.datapower.environment.DataPower.is_reachable", return_value=True)
@mock.patch("mast.datapower.system.system.datapower.environment.DataPower.set_firmware")
@mock.patch("mast.datapower.system.system.datapower.environment.DataPower.ssh_connect")
@mock.patch("mast.datapower.system.system.datapower.environment.DataPower.ssh_issue_command")
@mock.patch("mast.datapower.system.system.datapower.environment.DataPower.send_request")
@mock.patch("mast.datapower.system.system.datapower.environment.DataPower.domains", new_callable=mock.PropertyMock, return_value=["test_domain"])
@mock.patch("mast.datapower.system.system.datapower.environment.Environment.perform_action")
@mock.patch("mast.datapower.system.system.get_normal_backup")
@mock.patch("mast.datapower.system.system.make_logger")
@mock.patch("mast.datapower.system.system.sleep")
class TestFirmwareUpgrade(unittest.TestCase):
    def setUp(self):
        self.start_time = time()

    def tearDown(self):
        self.time_taken = time() - self.start_time
        print("%.3f: %s" % (self.time_taken, self.id()))

    def test_firmware_upgrade_calls_functions_with_with_keyword_args(
            self,
            mock_sleep,
            mock_make_logger,
            mock_get_normal_backup,
            mock_perform_action,
            mock_domains,
            mock_send_request,
            mock_ssh_issue_command,
            mock_ssh_connect,
            mock_set_firmware,
            mock_is_reachable,
            mock_clean_up,
            mock_save_config,
            mock_quiesce_appliance,
            mock_disable_domain,
            mock_reboot_appliance,
            mock_enable_domain):
        """We had problems when changing the order of the arguments
        of a function when another function uses it. To overcome this, it is
        now a requirement that all function calls use keyword arguments.
        This test tests that `firmware_upgrade` calls `clean_up` with
        keyword arguments."""
        mast.datapower.system.firmware_upgrade(
            appliances=["test_1", "test_2"],
            credentials=["user:pass"])
        self.assertEqual(mock_clean_up.call_args[0], ())
        self.assertEqual(mock_get_normal_backup.call_args[0], ())
        self.assertEqual(mock_save_config.call_args[0], ())
        self.assertEqual(mock_quiesce_appliance.call_args[0], ())
        self.assertEqual(mock_disable_domain.call_args[0], ())
        self.assertEqual(mock_reboot_appliance.call_args[0], ())
        self.assertEqual(mock_enable_domain.call_args[0], ())



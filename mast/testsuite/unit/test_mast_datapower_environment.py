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
Unittests for mast.datapower.datapower.environment
"""
import unittest
from unittest import mock

from mast.datapower.datapower import environment

ENVIRONMENTS = {"lab": ["dp1", "dp2"], "prod": ["dp2", "dp3"]}


@mock.patch.object(environment, "environments", ENVIRONMENTS, create=True)
class TestEnvironmentLookups(unittest.TestCase):

    def test_get_appliances(self):
        self.assertEqual(["dp1", "dp2"], environment.get_appliances("lab"))
        self.assertIsNone(environment.get_appliances("nowhere"))

    def test_which_environments(self):
        self.assertEqual(["lab", "prod"], environment.which_environments("dp2"))
        self.assertEqual([], environment.which_environments("dp9"))

    def test_which_environment_returns_the_first_environment(self):
        self.assertEqual("lab", environment.which_environment("dp2"))
        self.assertIsNone(environment.which_environment("dp9"))

    def test_is_environment(self):
        self.assertTrue(environment.is_environment("lab"))
        self.assertFalse(environment.is_environment("dp1"))

    def test_get_environment(self):
        env = environment.get_environment("lab")
        self.assertEqual(["dp1", "dp2"], env.hostnames)

    def test_get_environment_raises_for_an_unknown_name(self):
        with self.assertRaises(ValueError):
            environment.get_environment("nowhere")


if __name__ == "__main__":
    unittest.main()

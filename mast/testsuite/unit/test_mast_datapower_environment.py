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

from mast import registry
from mast.datapower.datapower import DataPower, Environment, environment
from mast.testsuite.unit.test_mast_jobs import PostgresMixin, SqliteCase


class RegistryCase(SqliteCase):
    """The environments lab (dp1, dp2) and prod (dp2, dp3) in the
    appliance registry."""

    def setUp(self):
        super().setUp()
        for name in ("dp1", "dp2", "dp3"):
            registry.add(name, "{}.example.test".format(name))
        registry.env_add("lab", members=["dp1", "dp2"])
        registry.env_add("prod", production=True, members=["dp2", "dp3"])


class TestEnvironmentLookups(RegistryCase):

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


class TestDataPowerFromTheRegistry(RegistryCase):

    def test_a_registered_name_has_its_address_and_settings(self):
        registry.update("dp1", soma_port=5551, web_port=9091, ssh_port=2222,
                        soma_scheme="http", soma_uri="/mgmt",
                        retry_interval=3)
        dp = DataPower("dp1", "user:pass")
        self.assertTrue(dp.registered)
        self.assertEqual(dp.hostname, "dp1")
        self.assertEqual(dp._hostname, "dp1.example.test")
        self.assertEqual(dp.port, 5551)
        self.assertEqual(dp.web_port, 9091)
        self.assertEqual(dp.ssh_port, 2222)
        self.assertEqual(dp.scheme, "http")
        self.assertEqual(dp.uri, "/mgmt")
        self.assertEqual(dp.retry_interval, 3)
        self.assertEqual(dp.environment, "lab")

    def test_an_unregistered_name_is_its_own_address_with_the_defaults(self):
        dp = DataPower("other.example.test", "user:pass")
        self.assertFalse(dp.registered)
        self.assertEqual(dp._hostname, "other.example.test")
        self.assertEqual(int(dp.port), 5550)
        self.assertEqual(dp.environment, "-")

    def test_a_relative_spec_file_is_in_mast_home(self):
        import os
        from mast.config import MAST_HOME
        registry.update("dp1", soma_spec_file="etc/v10.6.0.11-C.xml")
        dp = DataPower("dp1", "user:pass")
        self.assertEqual(dp.test_case, os.path.join(
            MAST_HOME, "etc", "v10.6.0.11-C.xml"))

    def test_an_environment_becomes_its_appliances(self):
        env = Environment(["prod", "dp1"], ["user:pass"])
        self.assertEqual([a.hostname for a in env.appliances],
                         ["dp2", "dp3", "dp1"])
        self.assertEqual([a._hostname for a in env.appliances][0],
                         "dp2.example.test")
        self.assertEqual(env.appliances[0].environment, "prod")


class TestEnvironmentLookupsPostgres(PostgresMixin, TestEnvironmentLookups):
    pass


if __name__ == "__main__":
    unittest.main()

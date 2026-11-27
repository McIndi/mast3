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
_module_: `mast.datapower.plugin`

The manifest of the DataPower plugin: the entry point `mast_plugin`
names it. It imports nothing else of the plugin.

Until the plugin is its own distribution (mcindi-mast-datapower), it
has the version of MAST and works only with that version.
"""
from mast import __version__
from mast.plugins import (
    COLLECT_CRYPTO, TEST_CREDENTIALS, Attribute, Endpoint, Kind, Manifest)

__all__ = ["manifest", "DATAPOWER"]

# A port that is not set is the standard port of DataPower. Another
# setting that is not set takes the default of [global] in
# appliances.conf
DATAPOWER = Kind(
    "datapower", "IBM DataPower appliance",
    capabilities=(COLLECT_CRYPTO, TEST_CREDENTIALS),
    # SOMA and SSH both log in with a password
    credential_types=("password",),
    credential_test="datapower.check-credentials",
    attributes=(
        Attribute("soma_port", "port", default=5550,
                  help="The port of the XML management interface (SOMA)."),
        Attribute("web_port", "port", default=9090,
                  help="The port of the WebGUI."),
        Attribute("ssh_port", "port", default=22, help="The port of SSH."),
        Attribute("soma_scheme", choices=("https", "http"),
                  help="The scheme of SOMA. A stored credential is sent "
                       "only over https."),
        Attribute("soma_uri", help="The URI of SOMA."),
        Attribute("soma_spec_file",
                  help="The SOMA spec file, relative to MAST_HOME."),
        Attribute("retry_interval", "int",
                  help="The seconds between retries of a request."),
        Attribute("poll", "bool", default=False,
                  help="Collect the status of the appliance for the "
                       "Appliances page."),
    ),
    endpoints=(
        Endpoint("soma", "tls", "soma_port", "XML management interface"),
        Endpoint("ssh", "ssh", "ssh_port", "SSH"),
    ))

manifest = Manifest(
    provider="datapower",
    title="IBM DataPower",
    version=__version__,
    api=1,
    core="=={}".format(__version__),
    commands="mast.datapower.commands:cli",
    kinds=(DATAPOWER,),
    pages=("mast.datapower.status.page:page",
           "mast.datapower.crypto.page:page",
           "mast.datapower.ssh.page:page"))

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
import flask
from time import sleep
from mast.plugins.web import Plugin
from mast.timestamp import Timestamp
from mast.datapower import datapower
from pkg_resources import resource_string
from mast.xor import xordecode, xorencode
from mast.logging import make_logger, logged


PROVIDER_MAP = ({
    "CPUUsage.tenSeconds": datapower.STATUS_XPATH + '/CPUUsage/tenSeconds',
    "TCPSummary.established": datapower.STATUS_XPATH + '/TCPSummary/established',
    "MemoryStatus.Usage": datapower.STATUS_XPATH + '/MemoryStatus/Usage',
    "FilesystemStatus.FreeTemporary": datapower.STATUS_XPATH + 'FilesystemStatus/FreeTemporary',
    "FilesystemStatus.FreeEncrypted": datapower.STATUS_XPATH + 'FilesystemStatus/FreeEncrypted',
    "FilesystemStatus.FreeInternal": datapower.STATUS_XPATH + 'FilesystemStatus/FreeInternal',
    "SystemUsage.Load": datapower.STATUS_XPATH + 'SystemUsage/Load',
    "SystemUsage.WorkList": datapower.STATUS_XPATH + 'SystemUsage/WorkList'
    })

mast_home = os.environ["MAST_HOME"]


def get_data_file(f):
    return resource_string(__name__, 'docroot/{}'.format(f)).decode()


class WebPlugin(Plugin):
    def __init__(self):
        logger = make_logger("mast.status")
        global mast_home
        logger.debug("found MAST_HOME: {}".format(mast_home))
        self.route = self.status

        config_file = os.path.join(
            mast_home,
            "etc",
            "default",
            "status.conf")
        if not os.path.exists(config_file):
            logger.debug("Config file doesn't exist creating default config")
            with open(config_file, "w") as fout:
                fout.write(get_data_file("plugin.conf"))

    def css(self):
        return get_data_file("plugin.css")

    def js(self):
        return get_data_file("plugin.js")

    def html(self):
        return get_data_file("plugin.html")

    @logged("mast.datapower.status")
    def status(self):
        logger = make_logger("mast.datapower.status")

        t = Timestamp()
        check_hostname = "true" in flask.request.form.get(
            'check_hostname').lower()
        appliances = flask.request.form.getlist('appliances[]')
        credentials = [xordecode(
            _.encode(),
            key=xorencode(
                flask.request.cookies["9x4h/mmek/j.ahba.ckhafn"], key="_"))
                for _ in flask.request.form.getlist('credentials[]')]
        if not appliances:
            return flask.abort(404)

        env = datapower.Environment(
            appliances,
            credentials,
            check_hostname=check_hostname)

        providers = flask.request.form.getlist("providers[]")

        resp = {
            "appliances": appliances,
            "time": t.short}

        for provider in providers:
            _provider = provider.split(".")[0]
            resp[provider] = []
            for appliance in env.appliances:
                try:
                    _status = appliance.get_status(_provider)
                except datapower.AuthenticationFailure:
                    # This is to handle an intermittent authentication failure
                    # sometimes issued by the DataPower. We will sleep 2
                    # seconds and try again
                    sleep(2)
                    try:
                        return self.status()
                    except:
                        logger.exception(
                            "An unhandled exception occurred during execution")
                        raise
                except:
                    logger.exception(
                        "An unhandled exception occurred during execution")
                    raise
                metric = _status.xml.find(PROVIDER_MAP[provider]).text
                resp[provider].append(metric)
        return flask.jsonify(resp)

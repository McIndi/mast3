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
_module_: `mast.plugin_utils.params`

The parameters that the DataPower commands share, and the widgets that
tell the web GUI how to ask for a value.
"""
import os

from pydantic import model_validator

from mast.cli import Params, Option
from mast.datapower.datapower import Environment

__all__ = [
    "ApplianceOptions", "ApplianceParams", "SaveConfigParams",
    "APPLIANCES", "CREDENTIALS", "STORED_CREDENTIAL", "UPLOAD", "OUT_DIR", "OUT_FILE",
    "BACKUPS_DIR", "STATUS_PROVIDER", "OBJECT_CLASS", "configured", "timeout_option",
]

# Widgets
# =======
#
# A widget is the `widget` argument of `mast.cli.Option`. The command
# line ignores it. `mast.web.catalog` uses it to build the web form.

# The appliances and credentials that are selected at the top of the
# web GUI. The form does not ask for them again.
APPLIANCES = "appliances"
CREDENTIALS = "credentials"
# The name of a stored credential (see mast.credentials). The worker
# puts its user name and password in the credentials just before the
# command runs.
STORED_CREDENTIAL = "stored_credential"
# A file that the user uploads. The command receives the path of the
# uploaded file on the MAST host.
UPLOAD = "upload"
# A directory or file that the command writes. The web GUI chooses the
# path and gives the user a download link when the command finishes.
OUT_DIR = "out_dir"
OUT_FILE = "out_file"

# The default out_dir of the commands that take backups, or keep a copy
# of what they delete or change (relative to MAST_HOME). The rest write
# to tmp/ by default, which the daily retention empties of old files
# (tmp_days in [retention] of jobs.conf); this folder it leaves alone.
BACKUPS_DIR = os.path.join("var", "backups")
# A choice from the status providers, or from the object classes.
STATUS_PROVIDER = "status_provider"
OBJECT_CLASS = "object_class"


def configured(object_class):
    """
    _function_: `mast.plugin_utils.params.configured(object_class)`

    Return the widget for a choice from the objects of `object_class`
    that exist on every selected appliance. `configured("Domain")` is
    a choice of domains.
    """
    return "configured:" + object_class


def timeout_option(default=120):
    """
    _function_: `mast.plugin_utils.params.timeout_option(default=120)`

    Return the `timeout` option of `ApplianceParams` with another
    default, for a command that needs longer:

        :::python
        class SecureBackup(ApplianceParams):
            timeout: int = timeout_option(1200)
    """
    return Option(
        default, short="t",
        help="The timeout in seconds to wait for a response from an "
             "appliance for any single request. __NOTE__ Program "
             "execution may halt if a timeout is reached.")


class ApplianceOptions(Params):
    """
    _class_: `mast.plugin_utils.params.ApplianceOptions`

    The parameters that say which appliances a command talks to, and
    how. Most commands use `ApplianceParams`, which also makes the
    `Environment` from them.
    """
    appliances: list[str] = Option(
        default_factory=list, short="a", widget=APPLIANCES,
        target=True,
        help="The hostname(s), ip address(es), environment name(s) "
             "or appliance name(s) you would like to affect. "
             "Appliances and environments are kept in the "
             "registry; see `mast targets list` and "
             "`mast fleets list`.")
    credentials: list[str] = Option(
        default_factory=list, short="c", widget=CREDENTIALS, secret=True,
        help="The credentials to use for authenticating to the "
             "appliances, in the form `username:password`. Give either "
             "one set to use for all appliances or one set for each "
             "appliance, in the same order as the appliances. If you "
             "would prefer to not use plain-text passwords, you can use "
             "the output of `mast datapower xor <username:password>`.")
    credential: str | None = Option(
        None, widget=STORED_CREDENTIAL,
        help="The name of a stored credential to use in place of "
             "`--credentials` (see `mast credentials list`). It can be "
             "used only on the registered appliances that it is bound "
             "to, and a credential for collecting only with commands "
             "that only read. The password never leaves the MAST host's "
             "worker.")
    timeout: int = timeout_option()
    no_check_hostname: bool = Option(
        False, short="n",
        help="Turn off all checks of the TLS certificates of the "
             "appliances, not only of the hostname, so that anyone "
             "between MAST and an appliance can read the password. A "
             "pinned certificate (mast targets pin) is still "
             "checked. Not with a stored credential (`--credential`).")

    @model_validator(mode="after")
    def _one_kind_of_credentials(self):
        if self.credential and self.credentials:
            raise ValueError(
                "give either credentials or a stored credential "
                "(credential), not both")
        if self.credential and self.no_check_hostname:
            raise ValueError(
                "a stored credential is sent only to an appliance whose "
                "certificate is checked; no_check_hostname cannot be "
                "used with it")
        return self


class ApplianceParams(ApplianceOptions):
    """
    _class_: `mast.plugin_utils.params.ApplianceParams`

    The parameters of every command that talks to appliances.
    """

    def environment(self):
        """
        _method_: `mast.plugin_utils.params.ApplianceParams.environment(self)`

        Return the `mast.datapower.datapower.Environment` for the
        appliances and credentials in these parameters.
        """
        return Environment(
            self.appliances,
            self.credentials,
            timeout=self.timeout,
            check_hostname=not self.no_check_hostname)


class SaveConfigParams(ApplianceParams):
    """
    _class_: `mast.plugin_utils.params.SaveConfigParams`

    The parameters of a command that can save the configuration after
    it changes it.
    """
    save_config: bool = Option(
        False, short="s",
        help="If specified the configuration on the appliances will be "
             "saved")

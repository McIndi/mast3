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
from mast.cli import Params, Option
from mast.datapower.datapower import Environment

__all__ = [
    "ApplianceOptions", "ApplianceParams", "SaveConfigParams",
    "APPLIANCES", "CREDENTIALS", "UPLOAD", "OUT_DIR", "OUT_FILE",
    "STATUS_PROVIDER", "OBJECT_CLASS", "configured", "timeout_option",
]

# Widgets
# =======
#
# A widget is the `widget` argument of `mast.cli.Option`. The command
# line ignores it. `mast.plugin_utils.plugin_functions` uses it to build
# the web form.

# The appliances and credentials that are selected at the top of the
# web GUI. The form does not ask for them again.
APPLIANCES = "appliances"
CREDENTIALS = "credentials"
# A file that the user uploads. The command receives the path of the
# uploaded file on the MAST host.
UPLOAD = "upload"
# A directory or file that the command writes. The web GUI chooses the
# path and gives the user a download link when the command finishes.
OUT_DIR = "out_dir"
OUT_FILE = "out_file"
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
        help="The hostname(s), ip address(es), environment name(s) "
             "or alias(es) of the appliances you would like to affect. "
             "Environments are configured in `environments.conf` and "
             "aliases in `hosts.conf`, both located in "
             "`$MAST_HOME/etc/default`.")
    credentials: list[str] = Option(
        default_factory=list, short="c", widget=CREDENTIALS, secret=True,
        help="The credentials to use for authenticating to the "
             "appliances, in the form `username:password`. Give either "
             "one set to use for all appliances or one set for each "
             "appliance, in the same order as the appliances. If you "
             "would prefer to not use plain-text passwords, you can use "
             "the output of `mast-system xor <username:password>`.")
    timeout: int = timeout_option()
    no_check_hostname: bool = Option(
        False, short="n",
        help="If specified SSL verification will be turned off when "
             "sending commands to the appliances.")



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

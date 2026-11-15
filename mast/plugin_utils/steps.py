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
_module_: `mast.plugin_utils.steps`

The steps that many DataPower commands have in common. Each one
performs an action on every appliance and adds the responses to a
`mast.plugin_utils.results.Report`.
"""
from mast.cli import say
from mast.plugin_utils.results import ActionResults

__all__ = ["perform", "save_config"]


def perform(env, report, logger, action, suffix, **kwargs):
    """
    _function_: `mast.plugin_utils.steps.perform(env, report, logger,
    action, suffix, **kwargs)`

    Perform `action` on each appliance in `env` and add the responses
    to `report`. Returns the responses.
    """
    resp = env.perform_async_action(action, **kwargs)
    logger.debug("Responses received {}".format(str(resp)))
    report.add(ActionResults(resp, suffix=suffix))
    return resp


def save_config(env, report, logger, domain="default"):
    """
    _function_: `mast.plugin_utils.steps.save_config(env, report,
    logger, domain="default")`

    Save the configuration of `domain` on each appliance in `env` and
    add the responses to `report`.
    """
    msg = "Attempting to save config of {} domain on {}".format(
        domain, str(env.appliances))
    logger.info(msg)
    say(msg)
    return perform(env, report, logger, "SaveConfig", "save_config",
                   domain=domain)

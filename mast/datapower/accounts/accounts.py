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
mast accounts:

A set of tools for automating routine user/group
administration tasks associated with IBM DataPower
appliances.
"""
from mast.cli import Cli, Option, say
from mast.logging import make_logger, logged
from mast.plugin_utils.params import (
    ApplianceParams, SaveConfigParams, configured)
from mast.plugin_utils.results import ActionResults, CommonItems, Report
from mast.plugin_utils.steps import perform as _perform
from mast.plugin_utils.steps import save_config as _save_config

from mast.datapower.provider import cli as _provider  # noqa: E402

cli = _provider.section("accounts")


@logged("mast.datapower.accounts")
@cli.command('list-groups', effect="read", group='users/groups',
             exclusive=False)
def list_groups(params: ApplianceParams):
    """Display a list of all configured user groups on the specified
appliances, along with a list of the groups common to all
appliances."""
    logger = make_logger("mast.accounts")
    env = params.environment()

    groups = {}
    for appliance in env.appliances:
        groups[appliance.hostname] = appliance.groups
        logger.info(
            "Groups {} found for appliance {}".format(
                str(groups[appliance.hostname]), appliance.hostname))
    result = CommonItems("Groups", groups, env)
    logger.debug("Common Groups found {}".format(str(result.common)))
    return result


class AddGroup(SaveConfigParams):
    name: str = Option(short="N", help="The name of the group to add")
    access_policies: list[str] = Option(
        default_factory=list, short="A",
        help="An access policy of the group, in the form "
             "address/domain/resource?Access=permissions. Each part "
             "can be *. The permissions are r (read), w (write), "
             "a (add), d (delete) and x (execute), joined with +. "
             "For example, */*/*?Access=r reads everything in every "
             "domain, and */dev/*?Access=r+w+a+d+x gives full access "
             "to the domain dev.")


@logged("mast.datapower.accounts")
@cli.command('add-group', effect="change", group='users/groups')
def add_group(params: AddGroup):
    """Adds a user group to the specified appliances."""
    logger = make_logger("mast.accounts")
    env = params.environment()
    report = Report(env)

    msg = "Adding group {} to {}".format(params.name, str(env.appliances))
    logger.info(msg)
    say(msg)
    _perform(env, report, logger, 'add_group', "add_group",
             name=params.name, access_policies=params.access_policies)

    if params.save_config:
        _save_config(env, report, logger)
    return report


class DelGroup(SaveConfigParams):
    UserGroup: str = Option(
        "", short="U", widget=configured("UserGroup"),
        help="The name of the group to remove")


@logged("mast.datapower.accounts")
@cli.command('del-group', effect="change", group='users/groups')
def del_group(params: DelGroup):
    """Removes a user group from the specified appliances."""
    logger = make_logger("mast.accounts")
    env = params.environment()
    report = Report(env)

    msg = "Removing user group {} from {}".format(
        params.UserGroup, str(env.appliances))
    logger.info(msg)
    say(msg)
    _perform(env, report, logger, 'del_group', "del_group",
             group=params.UserGroup)

    if params.save_config:
        _save_config(env, report, logger)
    return report


@logged("mast.datapower.accounts")
@cli.command('list-users', effect="read", group='users/groups',
             exclusive=False)
def list_users(params: ApplianceParams):
    """Lists the users on the specified appliances as well as a list
of users common to all appliances."""
    logger = make_logger("mast.accounts")
    env = params.environment()

    logger.info("Attempting to list users for {}".format(str(env.appliances)))
    users = {}
    for appliance in env.appliances:
        users[appliance.hostname] = appliance.users
        logger.debug(
            "Found users for {}: {}".format(
                appliance.hostname, str(users[appliance.hostname])))
    result = CommonItems("Users", users, env)
    logger.debug(
        "Users common to {}: {}".format(
            str(env.appliances), str(result.common)))
    return result


class AddUser(SaveConfigParams):
    username: str = Option(short="u", help="The name of the user to add")
    password: str = Option(
        "", short="p", secret=True,
        help="The initial password for the user")
    supress_force_password_change: bool = Option(
        False, short="S",
        help="If specified, the user will not have to change the "
             "password at the first login")
    privileged: bool = Option(
        False, short="P",
        help="Whether the user will be a privileged user. You cannot "
             "specify both privileged and a group.")
    group: str | None = Option(
        None, short="g", help="The group to which to add the user")


@logged("mast.datapower.accounts")
@cli.command('add-user', effect="change", group='users/groups')
def add_user(params: AddUser):
    """Adds a user to the specified appliances.

**NOTE**: You cannot specify both privileged and a group.
It will fail to add the group"""
    logger = make_logger("mast.accounts")
    env = params.environment()
    report = Report(env)

    msg = "Attempting to add user {} to {}".format(
        params.username, str(env.appliances))
    logger.info(msg)
    say(msg)
    _perform(
        env, report, logger, 'add_user', "add_user",
        username=params.username,
        password=params.password,
        privileged=params.privileged,
        user_group=params.group,
        supress_force_password_change=params.supress_force_password_change)

    if params.save_config:
        _save_config(env, report, logger)
    return report


class UserParams(SaveConfigParams):
    """The parameters of a command that changes one existing user."""
    User: str = Option(
        "", short="U", widget=configured("User"),
        help="The name of the user")


@logged("mast.datapower.accounts")
@cli.command('del-user', effect="change", group='users/groups')
def del_user(params: UserParams):
    """Removes a user from the specified appliances."""
    logger = make_logger("mast.accounts")
    env = params.environment()
    report = Report(env)

    msg = "Attempting to delete user {} from {}".format(
        params.User, str(env.appliances))
    logger.info(msg)
    say(msg)
    _perform(env, report, logger, 'remove_user', "remove_user",
             username=params.User)

    if params.save_config:
        _save_config(env, report, logger)
    return report


class ChangePassword(UserParams):
    password: str = Option(
        "", short="p", secret=True,
        help="The new password for the specified user. NOTE: You may "
             "need to place quotations around the argument passed if "
             "using special characters as these might be incorrectly "
             "interpreted by your shell.")


@logged("mast.datapower.accounts")
@cli.command('change-password', effect="change", group='users/groups')
def change_password(params: ChangePassword):
    """Changes the specified user's password to the specified password."""
    logger = make_logger("mast.accounts")
    env = params.environment()
    report = Report(env)

    msg = "Attempting to change password for {} on {}".format(
        params.User, str(env.appliances))
    logger.info(msg)
    say(msg)
    _perform(env, report, logger, 'change_password', "change_password",
             username=params.User, password=params.password)

    if params.save_config:
        _save_config(env, report, logger)
    return report


@logged("mast.datapower.accounts")
@cli.command('force-change-password', effect="change", group='users/groups')
def force_change_password(params: UserParams):
    """Forces a user to change their password on their next login."""
    logger = make_logger("mast.accounts")
    env = params.environment()
    report = Report(env)

    msg = "Attempting to force {} to change password on {}".format(
        params.User, str(env.appliances))
    logger.info(msg)
    say(msg)
    _perform(env, report, logger, 'UserForcePasswordChange',
             "force_password_change", User=params.User)

    if params.save_config:
        _save_config(env, report, logger)
    return report


@logged("mast.datapower.accounts")
@cli.command('list-rbm-fallback', effect="read", group='users/groups',
             exclusive=False)
def list_rbm_fallback_users(params: ApplianceParams):
    """Lists the current RBM Fallback Users for the specified appliances,
as well as the fallback users which are common to all appliances."""
    logger = make_logger("mast.accounts")
    env = params.environment()

    users = {}
    for appliance in env.appliances:
        logger.info(
            "Attempting to list RBM Fallback users on {}".format(
                appliance.hostname))
        users[appliance.hostname] = appliance.fallback_users
        logger.debug(
            "RBM Fallback users for {}: {}".format(
                appliance.hostname, str(users[appliance.hostname])))
    result = CommonItems("RBM Fallback Users", users, env)
    logger.debug(
        "RBM Fallback usesrs common to {}: {}".format(
            str(env.appliances), str(result.common)))
    return result


@logged("mast.datapower.accounts")
@cli.command('add-rbm-fallback', effect="change", group='users/groups')
def add_rbm_fallback(params: UserParams):
    """Adds a user to the the RBM Fallback users."""
    logger = make_logger("mast.accounts")
    env = params.environment()
    report = Report(env)

    msg = "Attempting to add {} as an RBM Fallback user to {}".format(
        params.User, str(env.appliances))
    logger.info(msg)
    say(msg)
    _perform(env, report, logger, 'add_rbm_fallback', "add_rbm_fallback",
             user=params.User)

    if params.save_config:
        _save_config(env, report, logger)
    return report


@logged("mast.datapower.accounts")
@cli.command('del-rbm-fallback', effect="change", group='users/groups')
def del_rbm_fallback(params: UserParams):
    """Removes a user from the the RBM Fallback users."""
    logger = make_logger("mast.accounts")
    env = params.environment()
    report = Report(env)

    msg = "Attempting to remove {} from RBM Fallback users on {}".format(
        params.User, str(env.appliances))
    logger.info(msg)
    say(msg)
    _perform(env, report, logger, 'del_rbm_fallback', "del_rbm_fallback",
             username=params.User)

    if params.save_config:
        _save_config(env, report, logger)
    return report

#
# ~#~#~#~#~#~#~#



#
# ~#~#~#~#~#~#~#

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
from importlib.resources import files
from mast.plugins.web import Plugin
from mast.logging import make_logger, logged
from mast.plugin_utils.params import ApplianceParams, configured
from mast.plugin_utils.results import ActionResults, CommonItems, Report
from functools import partial, update_wrapper
import mast.plugin_utils.plugin_functions as pf

cli = Cli()


class SaveConfigParams(ApplianceParams):
    """The parameters of a command that can save the configuration
    after it changes it."""
    save_config: bool = Option(
        False, short="s",
        help="If specified the configuration on the appliances will be "
             "saved")


def _perform(env, report, logger, action, suffix, **kwargs):
    """Perform action on each appliance in env and add the responses
    to report."""
    resp = env.perform_async_action(action, **kwargs)
    logger.debug("Responses received {}".format(str(resp)))
    report.add(ActionResults(resp, suffix=suffix))


def _save_config(env, report, logger):
    """Save the configuration of the default domain on each appliance
    in env and add the responses to report."""
    msg = "Attempting to save config of default domain on {}".format(
        str(env.appliances))
    logger.info(msg)
    say(msg)
    _perform(env, report, logger, 'SaveConfig', "save_config",
             domain='default')


@logged("mast.datapower.accounts")
@cli.command('list-groups', category='users/groups')
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
        help="The access policies which will be associated with this "
             "group")


@logged("mast.datapower.accounts")
@cli.command('add-group', category='users/groups')
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
@cli.command('del-group', category='users/groups')
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
@cli.command('list-users', category='users/groups')
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
@cli.command('add-user', category='users/groups')
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
@cli.command('del-user', category='users/groups')
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
@cli.command('change-password', category='users/groups')
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
@cli.command('force-change-password', category='users/groups')
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
@cli.command('list-rbm-fallback', category='users/groups')
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
@cli.command('add-rbm-fallback', category='users/groups')
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
@cli.command('del-rbm-fallback', category='users/groups')
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

# ~#~#~#~#~#~#~#
# Caches
# ======
#
# These functions are meant to be used to flush the caches that DataPower
# maintains.
#
# Current Commands:
# ----------------
#
# FlushAAACache(PolicyName)
# FlushLDAPPoolCache(XMLManager)
# FlushRBMCache()


class FlushAAACache(ApplianceParams):
    Domain: str = Option(
        "", short="D", widget=configured("Domain"),
        help="The domain where the aaa_policy resides")
    aaa_policy: str = Option(
        "", short="A",
        help="The AAAPolicy whose cache you would like to flush")


@logged("mast.datapower.accounts")
@cli.command('flush-aaa-cache', category='caches')
def flush_aaa_cache(params: FlushAAACache):
    """Flushes the AAA Cache of the specified aaa_policy in the
specified Domain."""
    logger = make_logger("mast.accounts")
    env = params.environment()
    msg = "Attempting to flush AAA cache on {}".format(str(env.appliances))
    logger.info(msg)
    say(msg)

    kwargs = {"PolicyName": params.aaa_policy, 'domain': params.Domain}
    responses = env.perform_action('FlushAAACache', **kwargs)
    logger.debug("Responses received {}".format(str(responses)))
    return ActionResults(responses, env, suffix="flush_aaa_cache")


class FlushLDAPPoolCache(ApplianceParams):
    Domain: str = Option(
        "", short="D", widget=configured("Domain"),
        help="The domain which has the xml_manager whose cache you "
             "would like to flush.")
    xml_manager: str = Option(
        "", short="x",
        help="The XMLManager whose cache you would like to flush")


@logged("mast.datapower.accounts")
@cli.command('flush-ldap-pool-cache', category='caches')
def flush_ldap_pool_cache(params: FlushLDAPPoolCache):
    """Flushes the LDAP Pool Cache for the specified xml_manager
in the specified domain."""
    logger = make_logger("mast.accounts")
    env = params.environment()
    msg = "Attempting to flush LDAP Pool cache on {}".format(
        str(env.appliances))
    logger.info(msg)
    say(msg)

    kwargs = {"XMLManager": params.xml_manager, 'domain': params.Domain}
    responses = env.perform_action('FlushLDAPPoolCache', **kwargs)
    logger.debug("Responses received {}".format(str(responses)))
    return ActionResults(responses, env, suffix="flush_ldap_pool_cache")


class FlushRBMCache(ApplianceParams):
    Domain: str = Option(
        "", short="D", widget=configured("Domain"),
        help="The domain for which to flush the RBM Cache")


@logged("mast.datapower.accounts")
@cli.command('flush-rbm-cache', category='caches')
def flush_rbm_cache(params: FlushRBMCache):
    """Flush the RBM Cache in the specified Domain"""
    logger = make_logger("mast.accounts")
    env = params.environment()
    msg = "Attempting to flush RBM cache on {}".format(str(env.appliances))
    logger.info(msg)
    say(msg)

    responses = env.perform_action('FlushRBMCache', **{'domain': params.Domain})
    logger.debug("Responses received {}".format(str(responses)))
    return ActionResults(responses, env, suffix="flush_rbm_cache")

#
# ~#~#~#~#~#~#~#


def get_data_file(f):
    return files(__package__).joinpath('docroot').joinpath(f).read_text(encoding='utf-8')


class WebPlugin(Plugin):
    def __init__(self):
        self.route = partial(pf.handle, "accounts")
        self.route.__name__ = "accounts"
        self.html = partial(pf.html, "mast.datapower.accounts")
        update_wrapper(self.html, pf.html)

    def css(self):
        return get_data_file("plugin.css")

    def js(self):
        return get_data_file("plugin.js")


if __name__ == '__main__':
    cli.run()

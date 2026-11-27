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
from mast.util import _s
from mast.xor import xordecode
from mast import registry
from .DataPower import DataPower, STATUS_XPATH


def initialize_environments():
    """Kept for the callers of older versions. The environments are in
    the appliance registry (`mast.registry`), which is read each time."""
    return None


def get_appliances(env_name):
    """Returns a list of the names of the appliances in the environment
    env_name, or None if env_name is not an environment."""
    return registry.members(env_name)


def which_environments(hostname):
    """returns a list of environments to which hostname belongs."""
    return registry.which_fleets(hostname)


def which_environment(hostname):
    """Returns the first environment configured to which hostname belongs,
    returns None if hostname doesn't belong to a configured environment."""
    found = which_environments(hostname)
    return found[0] if found else None


def is_environment(name):
    """Returns True if name is an environment of the registry, False
    otherwise"""
    return registry.is_fleet(name)


def get_environment(name, credentials=None):
    """Factory function for creating an environment by name vs a list of
    hostnames."""
    if not is_environment(name):
        raise ValueError('Environment %s does not exist' % (name))
    hostnames = get_appliances(name)
    return Environment(hostnames, credentials)


class Environment(object):
    """Represents an arbitrary grouping of DataPower appliances"""
    def __init__(self, hostnames, credentials=None, timeout=120,
        check_hostname=True, tls=None):
        """Initializes the Environment with hostnames. If credentials are
        provided then initialize will be called to initialize the DataPower
        appliances.

        `tls` says how the TLS certificates are checked, see
        `mast.datapower.datapower.DataPower`. `check_hostname` False is
        the same as `tls="none"`."""
        self.hostnames = hostnames
        self.timeout = timeout
        if tls is None and not check_hostname:
            tls = "none"
        self.tls = tls
        self.check_hostname = tls != "none"
        if credentials is not None:
            self.credentials = credentials
            self.initialize()
        self.perform_async_action = self.perform_action

    def initialize(self):
        """Initializes a DataPower object for each hostname/ip in
        self.hostnames and loads them into self.appliances."""
        if not hasattr(self, 'credentials'):
            raise ValueError("Credentials were not supplied")
        if isinstance(self.credentials, str):  # lint:ok
            self.credentials = [self.credentials]
        if isinstance(self.hostnames, str):
            self.hostnames = [self.hostnames]
        if len(self.credentials) == 1:
            self.credentials = self.credentials * len(self.hostnames)

        self.appliances = []
        for index, hostname in enumerate(self.hostnames):
            if _s(':') not in _s(self.credentials[index]):
                self.credentials[index] = xordecode(self.credentials[index])
                if _s(':') not in _s(self.credentials[index]):
                    raise ValueError("Invalid credentials provided")
            if is_environment(hostname):
                _appliances = get_appliances(hostname)
                for _appliance in _appliances:
                    self.appliances.append(
                        DataPower(_appliance, self.credentials[index],
                            environment=hostname, tls=self.tls))
            else:
                self.appliances.append(
                    DataPower(hostname, self.credentials[index],
                              tls=self.tls))
        for appliance in self.appliances:
            appliance.request.set_timeout(self.timeout)

    def perform_action(self, func, **kwargs):
        """Calls func with kwargs for each appliance in the environment"""
        if not hasattr(self, 'appliances') or not self.appliances:
            raise IndexError("appliances not defined in environment")
        responses = {}
        for appliance in self.appliances:
            if not hasattr(appliance, func):
                raise ValueError(
                    "Method %s does not exist in DataPower class" % (func))
            responses[appliance.hostname] = getattr(appliance, func)(**kwargs)
        return responses

    def common_config(self, _class):
        """Find configuration objects across the environment which
        have the same name and object class. This method does not
        compare the configuration just the fact that an object of said
        type exists with the same name across the environment.
        """
        kwargs = {'provider': 'ObjectStatus'}
        responses = self.perform_action("get_status", **kwargs)

        xpath = STATUS_XPATH + "ObjectStatus"
        sets = []
        for host, response in list(responses.items()):
            try:
                sets.append(
                    set([
                        _.find('Name').text
                        for _ in response.xml.findall(xpath)
                        if _.find("Class").text == _class]))
            except AttributeError:
                sets.append(set([]))
        return sets[0].intersection(*sets[1:])

###############################################################################
# TODO: Fix this, right now performing async actions causes errors on windows
#       and does not behave consistently across platforms.
#
#    def perform_async_action(self, func, **kwargs):
#        if not hasattr(self, 'appliances') or not self.appliances:
#            raise IndexError("appliances not defined in environment")
#        out_queue = Queue()
#        threads = []
#        for appliance in self.appliances:
#            if not hasattr(appliance, func):
#                raise ValueError(
#                    "Method %s does not exist in DataPower class"%(func))
#            _func = getattr(appliance, func)
#            t = Thread(
#                target=self._call_method,
#                args=(_func, appliance.hostname, out_queue),
#                kwargs=kwargs)
#            threads.append(t)
#            t.start()

#        responses = {}
#        for thread in threads:
#            host, resp = out_queue.get()
#            responses[host] = resp

#        for thread in threads:
#            thread.join()

#        return responses

#    def _call_method(self, func, hostname, out_queue, **kwargs):
#        out_queue.put((hostname, func(**kwargs)))
#

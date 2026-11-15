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
mast network:

A set of tools for automating routine network administration
tasks associated with IBM DataPower appliances.
"""
from typing import Literal
from importlib.resources import files
import urllib.request, urllib.error, urllib.parse
from pydantic import field_validator
from mast.cli import Cli, Option, Table, say
from mast.plugins.web import Plugin
import mast.datapower.datapower as datapower
from mast.logging import make_logger, logged
from mast.plugin_utils.params import (
    ApplianceParams, SaveConfigParams, configured)
from mast.plugin_utils.results import ActionResults, Report, history
from mast.plugin_utils.steps import perform, save_config
from functools import partial, update_wrapper
import mast.plugin_utils.plugin_functions as pf


cli = Cli()


def _change(params, action, description, **kwargs):
    """Perform action, which changes the configuration, on each
    appliance in params, then save the configuration if params asks
    for that. Return the report."""
    logger = make_logger("mast.network")
    env = params.environment()
    report = Report(env)

    msg = "Attempting to {} on {}".format(description, str(env.appliances))
    logger.info(msg)
    say(msg)
    perform(env, report, logger, action, action, **kwargs)

    if params.save_config:
        save_config(env, report, logger)
    return report


def _flush(params, action, **kwargs):
    """Perform action, which flushes a cache, on each appliance in
    params. Return the responses."""
    logger = make_logger("mast.network")
    env = params.environment()
    msg = "Attempting {} on {}".format(action, str(env.appliances))
    logger.info(msg)
    say(msg)
    responses = env.perform_action(action, **kwargs)
    logger.debug("Responses received {}".format(str(responses)))
    return ActionResults(responses, env)


# network tests
# =============
#
# These functions are meant to check the network functionality and
# connectivity of the specified appliances.
#
# current commands
# ----------------
# check-connectivity - Will check the connectivity from localhost to the
#                      xml mgmt, web mgmt and CLI
# tcp-connection-test - Will check the connectivity from the appliance to a
#                       given host/port pair


@logged("mast.datapower.network")
@cli.command('check-connectivity', category='network tests')
def check_connectivity(params: ApplianceParams):
    """Check the connectivity of the three mgmt interfaces (xml, web and cli)
on the specified appliances."""
    logger = make_logger("mast.network")
    env = params.environment()
    logger.info(
        "Attempting to check connectivity of {}".format(str(env.appliances)))

    rows = []
    for appliance in env.appliances:
        xml = str(appliance.check_xml_mgmt())
        logger.debug("connectivity to xml mgmt interface: {}".format(xml))
        web = str(appliance.check_web_mgmt())
        logger.debug("connectivity to Web GUI: {}".format(web))
        _cli = str(appliance.check_cli_mgmt())
        logger.debug("connectivity to cli mgmt interface: {}".format(_cli))
        rows.append((appliance.hostname, xml, web, _cli))
    return Table(("Appliance", "XML", "Web", "CLI"), rows, history(env))


class TCPConnectionTest(ApplianceParams):
    remote_hosts: list[str] = Option(
        default_factory=list, short="r",
        help="The remote hosts to test for connectivity")
    remote_ports: list[str] = Option(
        default_factory=list, short="R",
        help="The ports on the remote hosts to test")


@logged("mast.datapower.network")
@cli.command('tcp-connection-test', category='network tests')
def tcp_connection_test(params: TCPConnectionTest):
    """Perform a TCP Connection Test from each appliance to each remote
host at each remote port."""
    logger = make_logger("mast.network")
    env = params.environment()
    logger.info(
        "Performing TCP Connection Test from {} to {} on ports {}".format(
            str(env.appliances),
            str(params.remote_hosts),
            str(params.remote_ports)))

    rows = []
    for appliance in env.appliances:
        for host in params.remote_hosts:
            for port in params.remote_ports:
                resp = appliance.TCPConnectionTest(
                    RemoteHost=host,
                    RemotePort=port)
                success = bool(resp)
                logger.debug(
                    "Response from {} for test to {}:{}: {}".format(
                        appliance.hostname, host, port, success))
                rows.append((appliance.hostname, host, port, success))
    return Table(
        ("Appliance", "Remote Host", "Remote Port", "Success"),
        rows, history(env))
#
# ~#~#~#~#~#~#~#

# ~#~#~#~#~#~#~#
# Network Configuration
# =====================
#
# These functions are meant to ease the network configuration of multiple
# DataPower appliances.
#
# current commands
# ----------------
# add-host-alias
# del-host-alias
# add-secondary-address
# del-secondary-address
# add-static-host
# del-static-host
# add-static-route
# del-static-route
#

@logged("mast.datapower.network")
@cli.command("display-routing-table", category="network config")
def display_routing_table(params: ApplianceParams):
    """Display the routing table for the specified appliances

__NOTE__: This will try the RoutingStatus3 status provider and
it will fall back to RoutingStatus2 if the appliance doesn't
support RoutingStatus3."""
    logger = make_logger("mast.network")
    env = params.environment()
    logger.info(
        "Attempting to retrieve routing table from {}".format(
            str(env.appliances)))

    # try RoutingStatus3 first
    try:
        logger.debug("Attempting RoutingStatus3")
        resp = env.perform_action(
            "get_status",
            domain="default",
            provider="RoutingStatus3")
        xpath = datapower.STATUS_XPATH + "RoutingStatus3"
    except urllib.error.HTTPError:
        logger.warning(
            "RoutingStatus3 unavailable, falling back to RoutingStatus2")
        resp = env.perform_action(
            "get_status",
            domain="default",
            provider="RoutingStatus2")
        xpath = datapower.STATUS_XPATH + "RoutingStatus2"
    logger.debug("Response received: {}".format(resp))

    header = []
    rows = []
    for host, response in resp.items():
        for route in response.xml.findall(xpath):
            if not header:
                header = ["Appliance"] + [child.tag for child in route]
            rows.append([host] + [child.text or "" for child in route])
    return Table(header or ["Appliance"], rows, history(env))


class EthernetInterfaceResults(ActionResults):
    """The configuration of an ethernet interface on each appliance.
    The web GUI shows one row for each setting."""

    def html(self):
        rows = []
        for host, response in self.responses.items():
            config = response.xml.find(datapower.CONFIG_XPATH)
            if config is None:
                continue
            for index, child in enumerate(config):
                if not index:
                    rows.append((host, config.get("name") or "", "", ""))
                if not list(child):
                    rows.append(("", "", child.tag, child.text or ""))
                for node in child:
                    rows.append((
                        "", "", "{}.{}".format(child.tag, node.tag),
                        node.text or ""))
        return Table(
            ("Appliance", "EthernetInterface", "Key", "Value"), rows).html()


class DisplayEthernetInterface(ApplianceParams):
    EthernetInterface: str = Option(
        "", short="E", widget=configured("EthernetInterface"),
        help="The EthernetInterface to examine")
    persisted: bool = Option(
        True,
        help="Give `--no-persisted` to get the running configuration. "
             "Otherwise the persisted configuration is returned.")


@logged("mast.datapower.network")
@cli.command('display-ethernet-interface', category='network config')
def display_ethernet_interface(params: DisplayEthernetInterface):
    """This will display the details of the specified ethernet interface
on the specified appliances."""
    logger = make_logger("mast.network")
    env = params.environment()
    logger.info(
        "Attempting to Retrieve EthernetInterface configuration for "
        "{} {}".format(
            str(env.appliances), params.EthernetInterface))

    resp = env.perform_action(
        "get_config",
        _class="EthernetInterface",
        name=params.EthernetInterface,
        persisted=params.persisted)
    logger.debug("Response received: {}".format(str(resp)))
    return EthernetInterfaceResults(resp, env)


@logged("mast.datapower.network")
@cli.command('list-host-aliases', category='network config')
def list_host_aliases(params: ApplianceParams):
    """Lists the host aliases of the specified appliances as well as
the host aliases common to all specified appliances."""
    logger = make_logger("mast.network")
    env = params.environment()
    logger.info(
        "Attempting to find host aliases for {}".format(
            str(env.appliances)))

    resp = env.perform_action('get_host_aliases')
    logger.debug("Received Responses: {}".format(str(resp)))

    rows = []
    sets = []
    for host, aliases in resp.items():
        sets.append(set(aliases))
        rows.extend((host,) + tuple(alias) for alias in aliases)
    common = sorted(sets[0].intersection(*sets[1:])) if sets else []
    logger.info("Host Aliases common to {}: {}".format(
        str(env.appliances),
        str(common)))
    rows.extend(("common",) + tuple(alias) for alias in common)
    return Table(
        ("Appliance", "Host Alias", "IPAddress", "AdminState"),
        rows, history(env))


class AddHostAlias(SaveConfigParams):
    name: str = Option(short="N", help="The name of the host alias")
    ip: str = Option(short="i", help="The IP address for the host alias")
    admin_state: Literal["enabled", "disabled"] = Option(
        "enabled", short="A", help="The admin state for the host alias")


@logged("mast.datapower.network")
@cli.command('add-host-alias', category='network config')
def add_host_alias(params: AddHostAlias):
    """Adds a host alias to the specified appliances."""
    return _change(
        params, 'add_host_alias',
        "add host alias {} {}".format(params.name, params.ip),
        name=params.name, ip=params.ip, admin_state=params.admin_state)


class DelHostAlias(SaveConfigParams):
    HostAlias: str = Option(
        "", short="H", widget=configured("HostAlias"),
        help="The name of the host alias to remove")


@logged("mast.datapower.network")
@cli.command('del-host-alias', category='network config')
def del_host_alias(params: DelHostAlias):
    """Removes a host alias from the specified appliances."""
    return _change(
        params, 'del_host_alias',
        "remove host alias {}".format(params.HostAlias),
        name=params.HostAlias)


class ListSecondaryAddresses(ApplianceParams):
    EthernetInterface: str = Option(
        "", short="E", widget=configured("EthernetInterface"),
        help="The ethernet interface to examine for secondary addresses")


@logged("mast.datapower.network")
@cli.command('list-secondary-addresses', category='network config')
def list_secondary_addresses(params: ListSecondaryAddresses):
    """This will list the secondary IP Addresses on the specified
Ethernet Interface for the specified appliances."""
    env = params.environment()
    resp = env.perform_action(
        'get_secondary_addresses', interface=params.EthernetInterface)
    return Table(
        ("Appliance", "Secondary Address"),
        [(host, address)
         for host, addresses in resp.items() for address in addresses],
        history(env))


class AddSecondaryAddress(SaveConfigParams):
    EthernetInterface: str = Option(
        "", short="E", widget=configured("EthernetInterface"),
        help="The ethernet interface to which to add the secondary "
             "address")
    secondary_address: str = Option(
        short="S",
        help="The secondary IP address to add to the ethernet interface")


@logged("mast.datapower.network")
@cli.command('add-secondary-address', category='network config')
def add_secondary_address(params: AddSecondaryAddress):
    """Adds a secondary IP address to the specified appliances on the
specified ethernet interface"""
    return _change(
        params, 'add_secondary_address',
        "add secondary address {} to {}".format(
            params.secondary_address, params.EthernetInterface),
        ethernet_interface=params.EthernetInterface,
        secondary_address=params.secondary_address)


class DelSecondaryAddress(SaveConfigParams):
    EthernetInterface: str = Option(
        "", short="E", widget=configured("EthernetInterface"),
        help="The ethernet interface to remove the secondary address "
             "from")
    secondary_address: str = Option(
        short="S",
        help="The secondary IP address to remove from the ethernet "
             "interface")


@logged("mast.datapower.network")
@cli.command('del-secondary-address', category='network config')
def del_secondary_address(params: DelSecondaryAddress):
    """Removes a secondary IP address from the specified appliances on the
specified ethernet interface"""
    return _change(
        params, 'del_secondary_address',
        "remove secondary address {} from {}".format(
            params.secondary_address, params.EthernetInterface),
        ethernet_interface=params.EthernetInterface,
        secondary_address=params.secondary_address)


@logged("mast.datapower.network")
@cli.command('list-static-hosts', category='network config')
def list_static_hosts(params: ApplianceParams):
    """This will list the static hosts on the specified appliances."""
    env = params.environment()
    resp = env.perform_async_action('get_static_hosts')
    return Table(
        ("Appliance", "Hostname", "IP Address"),
        [(host,) + tuple(item)
         for host, items in resp.items() for item in items],
        history(env))


class AddStaticHost(SaveConfigParams):
    hostname: str = Option(
        short="H", help="The hostname of the static host")
    ip: str = Option(short="i", help="The IP address of the static host")


@logged("mast.datapower.network")
@cli.command('add-static-host', category='network config')
def add_static_host(params: AddStaticHost):
    """Adds a static host to the specified appliances"""
    return _change(
        params, 'add_static_host',
        "add static host {} {}".format(params.hostname, params.ip),
        hostname=params.hostname, ip=params.ip)


class DelStaticHost(SaveConfigParams):
    hostname: str = Option(
        short="H", help="The hostname of the static host")


@logged("mast.datapower.network")
@cli.command('del-static-host', category='network config')
def del_static_host(params: DelStaticHost):
    """Removes a static host from the specified appliances"""
    return _change(
        params, 'del_static_host',
        "remove static host {}".format(params.hostname),
        hostname=params.hostname)


class ListStaticRoutes(ApplianceParams):
    EthernetInterface: str = Option(
        "", short="E", widget=configured("EthernetInterface"),
        help="The EthernetInterface to examine for static routes")


@logged("mast.datapower.network")
@cli.command('list-static-routes', category='network config')
def list_static_routes(params: ListStaticRoutes):
    """This will list all of the static routes on the specified
EthernetInterface on the specified appliances."""
    env = params.environment()
    resp = env.perform_action(
        'get_static_routes', interface=params.EthernetInterface)
    return Table(
        ("Appliance", "Destination", "Gateway", "Metric"),
        [(host,) + tuple(item)
         for host, items in resp.items() for item in items],
        history(env))


class AddStaticRoute(SaveConfigParams):
    EthernetInterface: str = Option(
        "", short="E", widget=configured("EthernetInterface"),
        help="The ethernet interface to add the static route to")
    destination: str = Option(
        short="d", help="The destination for the static route")
    gateway: str = Option(
        short="g", help="The gateway to use for this static route")
    metric: int = Option(
        short="m",
        help="The metric (priority) of this static route, from 0 to 255")

    @field_validator("metric")
    @classmethod
    def _metric_in_range(cls, metric):
        if not 0 <= metric <= 255:
            raise ValueError("must be a number from 0 to 255")
        return metric


@logged("mast.datapower.network")
@cli.command('add-static-route', category='network config')
def add_static_route(params: AddStaticRoute):
    """Adds a static route to the specified appliance on the specified
ethernet interface"""
    return _change(
        params, 'add_static_route',
        "add static route to {} through {} on {}".format(
            params.destination, params.gateway, params.EthernetInterface),
        ethernet_interface=params.EthernetInterface,
        destination=params.destination,
        gateway=params.gateway,
        metric=str(params.metric))


class DelStaticRoute(SaveConfigParams):
    EthernetInterface: str = Option(
        "", short="E", widget=configured("EthernetInterface"),
        help="The ethernet interface to remove the static route from")
    destination: str = Option(
        short="d",
        help="The destination for the static route to be removed")


@logged("mast.datapower.network")
@cli.command('del-static-route', category='network config')
def del_static_route(params: DelStaticRoute):
    """Removes a static route from the specified appliance on the specified
ethernet interface"""
    return _change(
        params, 'del_static_route',
        "remove static route to {} from {}".format(
            params.destination, params.EthernetInterface),
        ethernet_interface=params.EthernetInterface,
        destination=params.destination)
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
# FlushArpCache()
# FlushDNSCache()
# FlushNDCache()
# FlushPDPCache(XACMLPDP)
#

@logged("mast.datapower.network")
@cli.command('flush-arp-cache', category='caches')
def flush_arp_cache(params: ApplianceParams):
    """This will flush the ARP Cache on the specified appliances."""
    return _flush(params, 'FlushArpCache')


@logged("mast.datapower.network")
@cli.command('flush-dns-cache', category='caches')
def flush_dns_cache(params: ApplianceParams):
    """This will flush the DNS Cache for the specified appliances."""
    return _flush(params, 'FlushDNSCache')


@logged("mast.datapower.network")
@cli.command('flush-nd-cache', category='caches')
def flush_nd_cache(params: ApplianceParams):
    """This will flush the ND cache on the specified appliances."""
    return _flush(params, 'FlushNDCache')


class FlushPDPCache(ApplianceParams):
    XACMLPDP: str = Option(
        "", short="X", widget=configured("XACMLPDP"),
        help="The XACMLPDP for which to flush the PDP cache")


@logged("mast.datapower.network")
@cli.command('flush-pdp-cache', category='caches')
def flush_pdp_cache(params: FlushPDPCache):
    """This will flush the PDP cache on the specified appliances
for the specified XACMLPDP."""
    return _flush(params, 'FlushPDPCache', XACMLPDP=params.XACMLPDP)


def get_data_file(f):
    return files(__package__).joinpath('docroot').joinpath(f).read_text(encoding='utf-8')


class WebPlugin(Plugin):
    def __init__(self):
        self.route = partial(pf.handle, "network")
        self.route.__name__ = "network"
        self.html = partial(pf.html, "mast.datapower.network")
        update_wrapper(self.html, pf.html)

    def css(self):
        return get_data_file('plugin.css')

    def js(self):
        return get_data_file('plugin.js')

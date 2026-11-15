"""
tcp_mon.py

Monitors established TCP connections to services on a DataPower
appliance.

Options:

  -h, --help: show this help message and exit
  -a, --appliances: A space seperated list of hostnames
        or IP Addresses of IBM DataPower appliances you
        wish to query
  -c, --credentials: A space seperated list of credentials
        (<username>:<password>) to use to authenticate to
        the appliances (one for all or one for each)
  -t, --timeout: The timeout (in seconds) to wait for a
        response from the appliance
  -n, --no_check_hostname: If specified the hostname of the
        appliance will not be verified against the SSL Certificate
        presented
  -i, --interval: Time (in seconds) to wait between polls
        If interval is zero, this script will query once and
        exit
  -d, --domain_filter: A regex used to filter connections to
        a certain domain
  -C, --class_filter: A regex used to filter connections to
        a certain object class
  -o, --object_filter: A regex used to filter connections to
        a particular object (based on object name)
  -s, --state_filter: A regex used to filter connections based
        on their state
  -r, --remote_ip_filter: A regex used to filter connections based
        on the remote IP Address
  -R, --remote_port_filter: A regex used to filter connections based
        on the remote port
  -l, --local_ip_filter: A regex used to filter connections based
        on the local IP Address
  -L, --local_port_filter: A regex used to filter connections based
        on the local port
  --clear_screen: Boolean indicating whether or not to
        clear the screen between outputs
"""

from mast.datapower.datapower import Environment, STATUS_XPATH
import re
import os
import sys
import mast.cli as cli
from mast.cli import Params, Option
import xml.etree.cElementTree as etree
from time import sleep
from mast.timestamp import Timestamp
from functools import partial

clear = partial(os.system, 'cls' if os.name == 'nt' else 'clear')

def get_connections(env, clear_screen, class_filter, object_filter,
                domain_filter, state_filter, remote_ip_filter,
                remote_port_filter, local_ip_filter, local_port_filter):
    if clear_screen:
        clear()
    else:
        print("\n\n")

    for appliance in env.appliances:
        header = "LocalIP LocalPort RemoteIP RemotePort State Domain ServiceClass ServiceName".split()
        print(("\n", appliance.hostname, "-", Timestamp(), "\n", "=" * 133))
        print(("{0: <16} {1: <9} {2: <16} {3: <11} {4: <15} {5: <15} {6: <30} {7: <30}".format(*header)))
        print(("-" * 133))
        matching_values = []
        tcp_table = appliance.get_status("TCPTable")
        for node in tcp_table.xml.findall(STATUS_XPATH):
            cls = node.find("serviceClass").text or "-"
            name = node.find("serviceName").text or "-"
            domain = node.find("serviceDomain").text or "-"
            state = node.find("state").text or "-"
            remote_ip = node.find("remoteIP").text or "-"
            remote_port = node.find("remotePort").text or "-"
            local_ip = node.find("localIP").text or "-"
            local_port = node.find("localPort").text or "-"
            values = [local_ip, local_port, remote_ip, remote_port, state, domain, cls, name]

            if class_filter.match(cls) and object_filter.match(name) and domain_filter.match(domain) and state_filter.match(state) and remote_ip_filter.match(remote_ip) and remote_port_filter.match(remote_port) and local_ip_filter.match(local_ip) and local_port_filter.match(local_port):
                matching_values.append(values)

        for value in matching_values:
            print(("{0: <16} {1: <9} {2: <16} {3: <11} {4: <15} {5: <15} {6: <30} {7: <30}".format(*value)))



class Main(Params):
    appliances: list[str] = Option(
        default_factory=list, short="a",
        help='The hostname(s), ip address(es), environment name(s) or '
             'alias(es) of the appliances you would like to affect.')
    credentials: list[str] = Option(
        default_factory=list, short="c", secret=True,
        help='The credentials to use for authenticating to the '
             'appliances, in the form `username:password`. Give either '
             'one set to use for all appliances or one set for each '
             'appliance.')
    timeout: int = Option(
        120, short="t",
        help='The timeout in seconds to wait for a response from an '
             'appliance for any single request.')
    no_check_hostname: bool = Option(
        False, short="n",
        help='If specified SSL verification will be turned off when '
             'sending commands to the appliances.')
    interval: int = Option(0, short="i")
    domain_filter: str = Option(".*", short="d")
    class_filter: str = Option(".*", short="C")
    object_filter: str = Option(".*", short="o")
    state_filter: str = Option(".*", short="s")
    remote_ip_filter: str = Option(".*", short="r")
    remote_port_filter: str = Option(".*", short="R")
    local_ip_filter: str = Option(".*", short="l")
    local_port_filter: str = Option(".*", short="L")
    clear_screen: bool = Option(False)


def main(params: Main):
    appliances = params.appliances
    credentials = params.credentials
    timeout = params.timeout
    no_check_hostname = params.no_check_hostname
    interval = params.interval
    domain_filter = params.domain_filter
    class_filter = params.class_filter
    object_filter = params.object_filter
    state_filter = params.state_filter
    remote_ip_filter = params.remote_ip_filter
    remote_port_filter = params.remote_port_filter
    local_ip_filter = params.local_ip_filter
    local_port_filter = params.local_port_filter
    clear_screen = params.clear_screen

    check_hostname = not no_check_hostname
    class_filter = re.compile(class_filter)
    object_filter = re.compile(object_filter)
    domain_filter = re.compile(domain_filter)
    state_filter = re.compile(state_filter)
    remote_ip_filter = re.compile(remote_ip_filter)
    remote_port_filter = re.compile(remote_port_filter)
    local_ip_filter = re.compile(local_ip_filter)
    local_port_filter = re.compile(local_port_filter)

    env = Environment(
        appliances,
        credentials,
        timeout,
        check_hostname=check_hostname)

    if interval == 0:
        get_connections(
            env,
            clear_screen,
            class_filter,
            object_filter,
            domain_filter,
            state_filter,
            remote_ip_filter,
            remote_port_filter,
            local_ip_filter,
            local_port_filter)
        sys.exit(0)

    while True:
        try:
            get_connections(
                env,
                clear_screen,
                class_filter,
                object_filter,
                domain_filter,
                state_filter,
                remote_ip_filter,
                remote_port_filter,
                local_ip_filter,
                local_port_filter)
            sleep(interval)
        except KeyboardInterrupt:
            break

if __name__ == "__main__":
    cli = cli.Cli(main=main, description=__doc__)
    cli.run()

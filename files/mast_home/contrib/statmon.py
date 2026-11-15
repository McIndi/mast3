# -*- coding: utf-8 -*-
import re
import os
import sys
from time import sleep
from mast.cli import Cli
from mast.cli import Params, Option
from functools import partial
from mast.datapower import datapower
from mast.logging import make_logger
from mast.timestamp import Timestamp
from mast.datapower.datapower import Environment

logger = make_logger("mast.statmon")

clear = partial(os.system, 'cls' if os.name == 'nt' else 'clear')


def recurse_status(prefix, elem, row, header_row):
    for child in elem.findall(".//*"):
        logger.info("Found child {}".format(child.tag))
        if child.tag not in header_row:
            logger.debug("Appending {} to header row".format(child.tag))
            header_row.append(child.tag)
        if len(child):
            _prefix = "{}/{}".format(prefix, child.tag)
            logger.info("child {} has children, recursing...".format(_prefix))
            recurse_status(_prefix, child, row, header_row)
        else:
            logger.info("Adding {} -> {}".format(child.tag, child.text))
            row.insert(header_row.index(child.tag), child.text)


def display_rows(header_row, rows, grep, provider):
    _format_string = ""
    for index, field in enumerate(header_row):
        _ = [field] + [str(x[index]) if len(x) >= index else " " for x in rows]
        length = len(max(_, key=len)) + 3
        _format_string += "{%s: <%s}" % (index, length)
    clear()
    print((Timestamp()))
    print(provider)
    print(("\n", _format_string.format(*header_row)))
    print(('-' * len(_format_string.format(*header_row))))
    for row in rows:
        for pattern in grep:
            if not pattern.search(_format_string.format(*row)):
                break
        else:
            print((_format_string.format(*row)))


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
    provider: str = Option("", short="p")
    domains: list[str] = Option(default_factory=list, short="d")
    interval: int = Option(0, short="i")
    grep: list[str] = Option(default_factory=list, short="g")
    ignore_case: bool = Option(False, short="I")


def main(params: Main):
    appliances = params.appliances
    credentials = params.credentials
    timeout = params.timeout
    no_check_hostname = params.no_check_hostname
    provider = params.provider
    domains = params.domains
    interval = params.interval
    grep = params.grep
    ignore_case = params.ignore_case

    flags = re.IGNORECASE if ignore_case else 0
    grep = [re.compile(x, flags) for x in grep] if grep else []
    if not provider:
        msg = "provider must be provided"
        logger.error(msg)
        print(msg)
        sys.exit(1)

    check_hostname = not no_check_hostname
    env = Environment(
        appliances,
        credentials,
        timeout,
        check_hostname=check_hostname)
    if interval:
        while True:
            print_table(env, provider, domains, grep)
            sleep(interval)
    else:
        print_table(env, provider, domains, grep)


def print_table(env, provider, domains, grep):
    header_row = ["hostname", "domain"]

    rows = []
    for appliance in env.appliances:
        _domains = domains
        if "all-domains" in domains:
            _domains = appliance.domains

        for domain in _domains:
            status = appliance.get_status(provider, domain=domain)
            for node in status.xml.findall(datapower.STATUS_XPATH):
                row = [appliance.hostname, domain]
                recurse_status("", node, row, header_row)
                rows.append(row)
    display_rows(header_row, rows, grep, provider)

if __name__ == "__main__":
    try:
        cli = Cli(main=main, description=__doc__)
        cli.run()
    except KeyboardInterrupt:
        sys.exit(0)
    except SystemExit:
        raise
    except Exception:
        make_logger("error").exception(
            "An unhandled exception occurred during execution")
        raise

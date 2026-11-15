"""Remove files from DataPower using glob patterns
"""

from mast.datapower import datapower
from mast.logging import make_logger
from mast.pprint import pprint_xml_str, pprint_xml
from mast.util import _b
from collections import defaultdict
from mast.cli import Cli
from mast.cli import Params, Option
from fnmatch import fnmatch
from lxml import etree
import os
import re

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
    domains: list[str] = Option(default_factory=list, short="d")
    paths: list[str] = Option(default_factory=list, short="p")
    force: bool = Option(False, short="f")
    dry_run: bool = Option(False, short="D")


def main(params: Main):
    appliances = params.appliances
    credentials = params.credentials
    timeout = params.timeout
    no_check_hostname = params.no_check_hostname
    domains = params.domains
    paths = params.paths
    force = params.force
    dry_run = params.dry_run
    check_hostname = not no_check_hostname
    env = datapower.Environment(appliances,
                                credentials,
                                timeout=timeout,
                                check_hostname=check_hostname)
    for appliance in env.appliances:
        _domains = domains
        if "all-domains" in _domains:
            _domains = appliance.domains
        print((appliance.hostname))
        for domain in _domains:
            print(("\t{}".format(domain)))
            filestore_dict = defaultdict(list)
            for path in paths:
                location = path.split(":")[0] + ":"
                if location not in filestore_dict:
                    filestore = appliance.get_filestore(domain, location)
                    filestore_dict[location] = filestore.xml
                for directory in filestore_dict[location].xpath(".//directory"):
                    # print(directory.get("name"))
                    if fnmatch(directory.get("name"), path):
                        print(("\t\t{}".format(directory.get("name"))))
                        if len(directory) and not force:
                            print("\t\t\tDirectory not empty, use `--force` to remove this directory")
                            continue
                        if dry_run:
                            continue
                        resp = appliance.RemoveDir(
                            domain=domain,
                            Dir=directory.get("name"),
                        )
                        # print(resp)
                        # resp = etree.fromstring(resp.text)
                        msg = " ".join(resp.xml.itertext())
                        print(("\t\t\t{}".format(msg)))
                        continue
                    for node in directory.xpath("./file"):
                        name = "/".join((node.getparent().get("name"), node.get("name")))
                        if fnmatch(name, path):
                            print(("\t\t{}".format(name)))
                            if dry_run:
                                continue
                            resp = appliance.DeleteFile(
                                domain=domain,
                                File=name
                            )
                            resp = resp.xml
                            msg = " ".join(resp.itertext())
                            print(("\t\t\t{}".format(msg)))
if __name__ == "__main__":
    cli = Cli(main=main, description=__doc__)
    try:
        cli.run()
    except SystemExit:
        pass
    except Exception:
        make_logger("error").exception("An unhandled exception occurred")
        raise

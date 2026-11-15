import os
import openpyxl
from time import sleep
from mast.cli import Cli
from mast.cli import Params, Option
from mast.logging import make_logger
from mast.datapower import datapower
from mast.timestamp import Timestamp
import xml.etree.cElementTree as etree

t = Timestamp()

default_out_file = os.path.join(os.environ["MAST_HOME"],
                                "tmp",
                                "file-sizes.xlsx")
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
    out_file: str = Option(default_out_file, short="o")
    delay: float = Option(0.5, short="d")


def main(params: Main):
    appliances = params.appliances
    credentials = params.credentials
    timeout = params.timeout
    no_check_hostname = params.no_check_hostname
    out_file = params.out_file
    delay = params.delay
    logger = make_logger("mast.file-sizes")
    locations = ["cert:",
                 "chkpoints:",
                 "config:",
                 "export:",
                 "image:",
                 "local:",
                 "logstore:",
                 "logtemp:",
                 "pubcert:",
                 "sharedcert:",
                 "store:",
                 "tasktemplates:",
                 "temporary:"]
    check_hostname = not no_check_hostname
    env = datapower.Environment(appliances,
                                credentials,
                                timeout,
                                check_hostname=check_hostname)
    root_node = etree.fromstring("<filestores />")

    header = ("appliance",
              "domain",
              "directory",
              "filename",
              "size",
              "modified")

    rows = [header]
    for appliance in env.appliances:
        print((appliance.hostname))
        appliance_node = etree.fromstring(
            '<filestores appliance="{}" />'.format(appliance.hostname))
        for domain in appliance.domains:
            print(("\t{}".format(domain)))
            for location in locations:
                sleep(delay)
                print(("\t\t{}".format(location)))
                filestore = appliance.get_filestore(domain=domain,
                                                    location=location)
                _location = filestore.xml.find(datapower.FILESTORE_XPATH)
                if _location is None:
                    continue
                if _location.findall("./file") is not None:
                    for _file in _location.findall("./file"):
                        dir_name = _location.get("name")
                        filename = _file.get("name")
                        print(("\t\t\t{}".format(filename)))
                        size = _file.find("size").text
                        modified = _file.find("modified").text

                        rows.append((appliance.hostname,
                                     domain,
                                     dir_name,
                                     filename,
                                     size,
                                     modified))
                for directory in _location.findall(".//directory"):
                    dir_name = directory.get("name")
                    print(("\t\t\t{}".format(dir_name)))
                    for _file in directory.findall(".//file"):
                        filename = _file.get("name")
                        print(("\t\t\t\t{}".format(filename)))
                        size = _file.find("size").text
                        modified = _file.find("modified").text
                        rows.append((appliance.hostname,
                                     domain,
                                     dir_name,
                                     filename,
                                     size,
                                     modified))
    wb = openpyxl.Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    wb.save(out_file)

if __name__ == "__main__":
    try:
        cli = Cli(main=main)
        cli.run()
    except Exception:
        make_logger("error").exception("An unhandled exception occured.")
        raise

from mast.datapower import datapower
import mast.cli as cli
from mast.cli import Params, Option
import os

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
    base_dir: str = Option(".", short="b")


def main(params: Main):
    appliances = params.appliances
    credentials = params.credentials
    timeout = params.timeout
    no_check_hostname = params.no_check_hostname
    base_dir = params.base_dir
    check_hostname = not no_check_hostname
    env = datapower.Environment(
        hostnames=appliances,
        credentials=credentials,
        timeout=timeout,
        check_hostname=check_hostname)
    for appliance in env.appliances:
        _dir = os.path.join(base_dir, appliance.hostname)
        if not os.path.exists(_dir):
            os.makedirs(_dir)
        for domain in appliance.domains:
            if domain == "default":
                remote_name = "config:///autoconfig.cfg"
                local_name = os.path.join(_dir, "autoconfig.cfg")
                with open(local_name, "w") as fout:
                    contents = appliance.getfile(domain=domain, filename=remote_name)
                    fout.write(contents)
            else:
                remote_name = "config:///{}.cfg".format(domain)
                local_name = os.path.join(_dir, "{}.cfg".format(domain))
                with open(local_name, "w") as fout:
                    contents = appliance.getfile(domain=domain, filename=remote_name)
                    fout.write(contents)


if __name__ == "__main__":
    interface = cli.Cli(main=main)
    interface.run()

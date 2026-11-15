from mast.datapower import datapower
from mast.logging import make_logger
from mast.cli import Cli
from mast.cli import Params, Option

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
    domains: list[str] = Option(default_factory=list, short="d")
    no_check_hostname: bool = Option(
        False, short="n",
        help='If specified SSL verification will be turned off when '
             'sending commands to the appliances.')
    timeout: int = Option(
        120, short="t",
        help='The timeout in seconds to wait for a response from an '
             'appliance for any single request.')


def main(params: Main):
    appliances = params.appliances
    credentials = params.credentials
    domains = params.domains
    no_check_hostname = params.no_check_hostname
    timeout = params.timeout
    logger = make_logger("session_mon")
    check_hostname = not no_check_hostname

    env = datapower.Environment(
        appliances,
        credentials,
        timeout,
        check_hostname=check_hostname)
    for appliance in env.appliances:
        print((appliance.hostname))
        _domains = domains
        if "all-domains" in domains:
            _domains = appliance.domains
        for domain in _domains:
            print(("\t{}".format(domain)))
            config = appliance.get_config(
                _class="WSRRSubscription",
                domain=domain,
                persisted=False,
            )
            objs = config.xml.findall(datapower.CONFIG_XPATH)
            if not objs:
                continue
            for obj in objs:
                print(("\t\t{}".format(obj.get("name"))))
                resp = appliance.WsrrSynchronize(domain=domain, WSRRSubscription=obj.get("name"))
                print(("\t\t\t" + "\n\t\t\t{}".join(resp.xml.find(".//{http://www.datapower.com/schemas/management}result").itertext())))

if __name__ == "__main__":
    cli = Cli(main=main)
    try:
        cli.run()
    except SystemExit:
        pass
    except Exception:
        make_logger("error").exception("An unhandled exception occurred")
        raise

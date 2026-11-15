from mast.pprint import pprint, pprint_xml_str, pprint_xml
from mast.logging import make_logger
from mast.datapower import datapower
from lxml import etree
import mast.cli as cli
from mast.cli import Params, Option
import os

nsmap = {
    "env": "http://schemas.xmlsoap.org/soap/envelope/",
    "dp":"http://www.datapower.com/schemas/management"
}

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
    dry_run: bool = Option(False, short="d")
    save_config: bool = Option(False, short="s")


def main(params: Main):
    appliances = params.appliances
    credentials = params.credentials
    timeout = params.timeout
    no_check_hostname = params.no_check_hostname
    dry_run = params.dry_run
    save_config = params.save_config
    check_hostname = not no_check_hostname
    env = datapower.Environment(
        hostnames=appliances,
        credentials=credentials,
        timeout=timeout,
        check_hostname=check_hostname)
    allaterror = etree.fromstring('<LogEvents><Class class="LogLabel">all</Class><Priority>error</Priority></LogEvents>')
    for appliance in env.appliances:
        print((appliance.hostname))
        for domain in appliance.domains:
            changed = False
            print(("\t{}".format(domain)))
            config = appliance.get_config("LogTarget", "default-log", domain=domain, persisted=False)
            config = etree.fromstring(str(config))
            #config = config.xpath(r"/env:Envelope/env:Body/dp:response/dp:config/LogTarget", nsmap=nsmap)
            config = config.xpath(r'/*[local-name()="Envelope"]/*[local-name()="Body"]/*[local-name()="response"]/*[local-name()="config"]/*[local-name()="LogTarget"]')[0]
            if len(config.xpath('./LogEvents[not(./Class/text()="all") or not(./Priority/text()="error")]')):
                changed = True
                #pprint_xml(config)
                for nodeset in config.xpath(r'./*[local-name()="LogEvents"]'):
                    config.remove(nodeset)
                config.append(allaterror)
                #pprint_xml_str(etree.tostring(config))
                appliance.request.clear()
                request = appliance.request.request(domain=domain).set_config
                request.append(config)
                for nodeset in config.xpath(r'.//*'):
                    if "read-only" in nodeset.attrib:
                        nodeset.attrib.pop("read-only")
                #print(appliance.request)
                if not dry_run:
                    print((appliance.send_request(boolean=True)))
                else:
                    pprint_xml(allaterror)
                print(" ")
            if save_config and not dry_run and changed:
                appliance.SaveConfig(domain=domain)

if __name__ == "__main__":
    interface = cli.Cli(main=main)
    try:
        interface.run()
    except SystemExit:
        pass
    except Exception:
        make_logger("error").exception("Sorry, an unhandled exception has occurred.")
        raise

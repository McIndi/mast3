import json
import urllib3
import requests
from mast.cli import Cli
from mast.cli import Params, Option
from mast.logging import make_logger
from mast.datapower import datapower

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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
    timeout: float = Option(
        3.0, short="t",
        help='The timeout in seconds to wait for a response from an '
             'appliance for any single request.')
    no_check_hostname: bool = Option(
        False, short="n",
        help='If specified SSL verification will be turned off when '
             'sending commands to the appliances.')
    domains: list[str] = Option(default_factory=list, short="d")
    providers: list[str] = Option(default_factory=list, short="p")


def main(params: Main):
    appliances = params.appliances
    credentials = params.credentials
    timeout = params.timeout
    no_check_hostname = params.no_check_hostname
    domains = params.domains
    providers = params.providers
    logger = make_logger(__name__)
    check_hostname = not no_check_hostname
    env = datapower.Environment(
        appliances,
        credentials,
        timeout,
        check_hostname=check_hostname
    )
    for appliance in env.appliances:
        rest_url = "https://{}:{}".format(appliance._hostname, 5551)
        appliance.session = requests.Session()
        _domains = domains # Can we support glob and/or regex?
        if "all-domains" in _domains:
            _url = "{}/mgmt/domains/config/".format(rest_url)
            username, password = appliance.credentials.split(":", 1)
            try:
                domains_config = appliance.session.get(_url, verify=check_hostname, auth=(username, password), timeout=timeout).json()
            except requests.exceptions.ConnectionError:
                continue
            _domains = [d["name"] for d in domains_config["domain"]]
        if "app-domains" in _domains:
            _url = "{}/mgmt/domains/config/".format(rest_url)
            username, password = appliance.credentials.split(":", 1)
            try:
                domains_config = appliance.session.get(_url, verify=check_hostname, auth=(username, password), timeout=timeout).json()
            except requests.exceptions.ConnectionError:
                continue
            _domains = [d["name"] for d in domains_config["domain"] if d["name"] != "default"]

        for domain in _domains:
            for provider in providers:
                rest_uri = "/mgmt/status/{}/{}".format(domain, provider)
                _url = "{}{}".format(rest_url, rest_uri)
                username, password = appliance.credentials.split(":", 1)
                try:
                    status = appliance.session.get(_url, verify=check_hostname, auth=(username, password)).json()[provider]
                except KeyError:
                    print(("*** Error occured when calling {} ***".format(_url)))
                    continue
                if isinstance(status, list):
                    for _status in status:
                        _status.update(hostname=appliance.hostname, domain=domain, provider=provider, uri=rest_uri)
                        print((json.dumps(_status)))
                else:
                    status.update(hostname=appliance.hostname, domain=domain, provider=provider, uri=rest_uri)
                    print((json.dumps(status)))


if __name__ == "__main__":
    cli = Cli(main=main)
    try:
        cli.run()
    except SystemExit:
        pass
    except Exception:
        make_logger("error").exception("An unhandled exception occurred.")
        raise

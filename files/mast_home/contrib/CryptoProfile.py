from mast.datapower import datapower
from mast.logging import make_logger
from mast.cli import Cli
from mast.cli import Params, Option
from lxml import etree
from fnmatch import fnmatch

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
    exclude: list[str] = Option(default_factory=list, short="e")
    dry_run: bool = Option(False, short="D")
    save_config: bool = Option(False, short="s")


def main(params: Main):
    appliances = params.appliances
    credentials = params.credentials
    timeout = params.timeout
    no_check_hostname = params.no_check_hostname
    domains = params.domains
    exclude = params.exclude
    dry_run = params.dry_run
    save_config = params.save_config
    check_hostname = not no_check_hostname
    if exclude is None:
        exclude = []
    env = datapower.Environment(
        appliances,
        credentials,
        timeout=timeout,
        check_hostname=check_hostname
    )
    for appliance in env.appliances:
        print(appliance.hostname)
        _domains = domains
        if "all-domains" in domains:
            _domains = appliance.domains
        for domain in _domains:
            print(f"\t{domain}")
            print(f"\t\tCryptoProfile")
            config = appliance.get_config(
                _class="CryptoProfile",
                domain=domain,
                persisted=False,
            )
            objs = config.xml.findall(datapower.CONFIG_XPATH)
            objs  =  [obj for obj in objs if obj.get("external") != "true"]
            for obj in objs:
                name = obj.get("name")
                skip = False
                for pattern in exclude:
                    if fnmatch(name, pattern):
                        print(f"\t\t\tCryptoProfile '{name}' matched exclude pattern '{pattern}', skipping...")
                        skip = True
                if skip:
                    continue
                print(f"\t\t\t{name}")
                appliance.request.clear()
                request = appliance.request.request
                request.set("domain", domain)
                modify_config = etree.SubElement(request , f"{{{datapower.MGMT_NAMESPACE}}}modify-config")
                class_node = etree.SubElement(modify_config, "CryptoProfile")
                class_node.set("name", name)
                ssl_options = etree.SubElement(class_node, "SSLOptions")
                disable_ssl_v_3 = etree.SubElement(ssl_options, "Disable-SSLv3")
                disable_ssl_v_3.text = "on"
                disable_tls_v_1 = etree.SubElement(ssl_options, "Disable-TLSv1")
                disable_tls_v_1.text = "on"
                disable_tls_v_1_d_1 = etree.SubElement(ssl_options, "Disable-TLSv1d1")
                disable_tls_v_1_d_1.text = "on"
                disable_tls_v_1_d_2 = etree.SubElement(ssl_options, "Disable-TLSv1d2")
                disable_tls_v_1_d_2.text = "off"

                if dry_run:
                    print(appliance.request)
                else:
                    resp = appliance.send_request(boolean=True)
            if save_config:
                appliance.SaveConfig(domain=domain)
            
if __name__ == "__main__":
    cli = Cli(main=main, description=__doc__)
    try:
        cli.run()
    except SystemExit:
        pass
    except Exception:
        make_logger("error").exception("An unhandled exception occurred")
        raise

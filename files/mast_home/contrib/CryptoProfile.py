from mast.datapower import datapower
from mast.logging import make_logger
from mast.cli import Cli
from lxml import etree
from fnmatch import fnmatch

def main(
    appliances=[],
    credentials=[],
    timeout=120,
    no_check_hostname=False,
    domains=[],
    exclude=[],
    dry_run=False,
    save_config=False
):
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
    except:
        make_logger("error").exception("An unhandled exception occurred")
        raise

import os
import openpyxl
from lxml import etree
from mast.cli import Cli
from mast.cli import Params, Option
from mast.datapower import datapower
from mast.logging import make_logger
from mast.datapower.datapower import MGMT_NAMESPACE

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
    out_file: str = Option("tmp/conformance-validation.xlsx", short="o")


def main(params: Main):
    appliances = params.appliances
    credentials = params.credentials
    timeout = params.timeout
    no_check_hostname = params.no_check_hostname
    domains = params.domains
    out_file = params.out_file
    check_hostname = not no_check_hostname
    env = datapower.Environment(appliances,
                                credentials,
                                timeout=timeout,
                                check_hostname=check_hostname)
    profiles = [
        "dp-wsi-bp.xsl",
        "dp-cfg-bp.xsl",
        "dp-wsi-bsp-1.0.xsl",
    ]
    fields = [
        "domain",
        "profile",
        "severity",
        "type",
        "specification",
        "object-type",
        "object-name",
        "parameter-name",
        "permitted-setting",
        "actual-setting",
        "details"
    ]
    wb = openpyxl.Workbook()
    ws = wb.active
    wb.remove_sheet(ws)
    for appliance in env.appliances:
        ws = wb.create_sheet()
        ws.title = appliance.hostname
        ws.append(fields)
        print((appliance.hostname))
        _domains = domains
        if "all-domains" in domains:
            _domains = appliance.domains
        for domain in _domains:
            print(("\t", domain))
            for profile in profiles:
                print(("\t\t", profile))
                appliance.request.clear()
                req = appliance.request.request
                req.set('domain', domain)
                gcr = etree.SubElement(req, f'{{{MGMT_NAMESPACE}}}get-conformance-report')
                gcr.set('profile', profile)
                resp = appliance.send_request()
                reports = resp.xml.findall(".//Report")
                for report in reports:
                    ws.append([
                        domain,
                        profile,
                        report.get("severity", ""),
                        report.get("type", ""),
                        report.get("specification", ""),
                        report.find('Location').get("object-type", ""),
                        report.find('Location').get("object-name", ""),
                        getattr(report.find('ParameterName'), "text", ""),
                        getattr(report.find('PermittedSetting'), "text", ""),
                        getattr(report.find('ActualSetting'), "text", ""),
                        getattr(report.find('Details'), "text", ""),
                    ])
    wb.save(out_file)

    print("Done")



if __name__ == "__main__":
    cli = Cli(main=main)
    try:
        cli.run()
    except SystemExit:
        pass
    except Exception:
        make_logger("conformance").exception("An unhandled exception occurred")
        raise

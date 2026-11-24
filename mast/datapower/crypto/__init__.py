# This file is part of McIndi's Automated Solutions Tool (MAST).
#
# MAST is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 3
# as published by the Free Software Foundation.
#
# MAST is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with MAST.  If not, see <https://www.gnu.org/licenses/>.
#
# Copyright 2015-2024, McIndi Solutions, All rights reserved.
import os
import re
import csv
import sys
import OpenSSL
import openpyxl
from .utils import *
from time import sleep
from mast.cli import Cli, Option, Table, say
from datetime import datetime
from mast.logging import make_logger
from mast.timestamp import Timestamp
import xml.etree.cElementTree as etree
import mast.datapower.datapower as datapower
from mast.plugin_utils.params import ApplianceParams, OUT_DIR, OUT_FILE
from mast.plugin_utils.results import Report, SeeDownload, history
from dateutil import parser, tz, relativedelta
from mast import __version__

cli = Cli(namespace="crypto")


class CertAudit(ApplianceParams):
    domains: list[str] = Option(
        default_factory=list, short="d",
        help="The domains to audit, to audit all domains, provide "
             "`all-domains`")
    out_file: str = Option(
        "tmp/cert-audit.xlsx", short="o", widget=OUT_FILE,
        help="The spreadsheet to output, use either relative or "
             "absolute path. The file should end in `.xlsx` or `.csv`")
    delay: float = Option(
        0.5, short="D",
        help="The amount of time in seconds to wait between auditing each "
             "certificate. If you are experiencing intermitten "
             "`AuthenticationFailure`s, it is a good idea to increase "
             "this parameter.")
    date_time_format: str = Option(
        "%A, %B %d, %Y, %X",
        help="The format for date-timestamps. Refer to [this document]"
             "(https://docs.python.org/3/library/time.html#time.strftime) "
             "for information on using this parameter")
    localtime: bool = Option(
        False, short="l",
        help="If specified, the date-timestamps will be output in local "
             "time instead of UTC.")
    days_only: bool = Option(
        False,
        help="If specified, only the number of days (floored) will be "
             "reported in the `time-since-expiration` and "
             "`time-until-expiration` columns.")


@cli.command("cert-audit", category="certificates")
def cert_audit(params: CertAudit):
    """Perform an audit of all CryptoCertificate objects which are
up and enabled for the specified appliances and domains.

Output:

A spreadsheet. The web GUI also shows a table."""
    domains = params.domains
    out_file = params.out_file
    delay = params.delay
    date_time_format = params.date_time_format
    localtime = params.localtime
    days_only = params.days_only
    logger = make_logger("cert-audit")
    if out_file is None:
        logger.error("Must specify out file")
        say("Must specify out_file")
        sys.exit(2)
    fname, extension = os.path.splitext(out_file)
    say(f"Found filename: {fname}, extension: {extension}")
    if extension not in [".csv", ".xlsx"]:
        raise ValueError("out_file must be either csv or xlsx")

    if not os.path.exists(os.path.dirname(out_file)):
        os.makedirs(os.path.dirname(out_file))
    env = params.environment()

    header_row = [
        "appliance",
        "domain",
        "certificate-object",
        "password-alias",
        "filename",
        "serial-number",
        "subject",
        "sans",
        "signature_algorithm",
        "not_before",
        "not_after",
        "issuer",
        "is-expired",
        "time-since-expiration",
        "time-until-expiration",
    ]
    rows = [header_row]
    for appliance in env.appliances:
        logger.info("Checking appliance {}".format(appliance.hostname))
        say(appliance.hostname)
        _domains = domains
        if "all-domains" in domains:
            _domains = appliance.domains
        for domain in _domains:
            logger.info("In domain {}".format(domain))
            say("\t", domain)
            config = appliance.get_config("CryptoCertificate", domain=domain, persisted=False)
            certs = [x for x in config.xml.findall(datapower.CONFIG_XPATH)]

            # Filter out disabled objects because the results won't change,
            # but we will perform less network traffic
            certs = [x for x in certs if x.find("mAdminState").text == "enabled"]

            for cert in certs:
                logger.info("Exporting cert {}".format(cert))
                try:
                    filename = cert.find("Filename").text
                except Exception:
                    rows.append([appliance.hostname, domain, name])
                    say(f"Skipping cert: {cert}")
                    logger.exception(f"An exception has occurred. The certificate {cert.get('name')} is being skipped and the error is being ignored")
                    continue
                name = cert.get("name")
                # _filename = name
                password_alias = cert.find("Alias")
                if password_alias is not None:
                    password_alias = password_alias.text
                say("\t\t", name)
                row = [appliance.hostname, domain, name, password_alias, filename]

                try:
                    details = appliance.get_certificate_details(
                            domain=domain,
                            certificate_name=name,
                        )
                except Exception as exception:
                    logger.exception(f"An exception has occurred. The certificate {name} is being skipped and the error is being ignored")
                    say(f"Skipping Cert: {name}, exception: {exception}")
                    rows.append(row)
                    continue
                try:
                    subject = details.xml.xpath(r"//*[local-name()='CertificateDetails']/*[local-name()='Subject']/text()")[0]
                    issuer = details.xml.xpath(r"//*[local-name()='CertificateDetails']/*[local-name()='Issuer']/text()")[0]
                    serial_number = details.xml.xpath(r"//*[local-name()='CertificateDetails']/*[local-name()='SerialNumber']/text()")[0]
                    signature_algorithm = details.xml.xpath(r"//*[local-name()='CertificateDetails']/*[local-name()='SignatureAlgorithm']/text()")[0]
                    notBefore = details.xml.xpath(r"//*[local-name()='CertificateDetails']/*[local-name()='NotBefore']/text()")[0]
                    notAfter = details.xml.xpath(r"//*[local-name()='CertificateDetails']/*[local-name()='NotAfter']/text()")[0]
                except Exception:
                    logger.exception(f"Could not parse details for cert {name}, received response {details}, skipping.")
                    say(f"Skipping Cert: {name}, details: {details}")
                    rows.append(row)
                    continue
                try:
                    sans = ',\r\n'.join(details.xml.xpath(r"//*[local-name()='CertificateDetails']/*[local-name()='Extensions']/*[local-name()='Extension' and @name='subjectAltName']/*[local-name()='item']/text()"))
                except Exception:
                    logger.exception(f"Could not parse SANs for cert {name}, received response {details}, skipping.")
                    say(f"Skipping Cert: {name}")
                    rows.append(row)
                    continue
                local_tz = tz.tzlocal()
                utc_tz = tz.tzutc()
                notBefore_utc = parser.parse(notBefore)
                notBefore_local = notBefore_utc.astimezone(local_tz)

                notAfter_utc = parser.parse(notAfter)
                notAfter_local = notAfter_utc.astimezone(local_tz)
                if localtime:
                    notAfter = notAfter_local.strftime(date_time_format)
                    notBefore = notBefore_local.strftime(date_time_format)
                else:
                    notAfter = notAfter_utc.strftime(date_time_format)
                    notBefore = notBefore_utc.strftime(date_time_format)

                is_expired = notAfter_utc <= datetime.now().astimezone(utc_tz)
                if is_expired:
                    time_since_expiration = datetime.utcnow().replace(tzinfo=utc_tz) - notAfter_utc
                    if days_only:
                        time_since_expiration = time_since_expiration.days
                    else:
                        time_since_expiration = str(time_since_expiration)
                    time_until_expiration = 0
                else:
                    time_until_expiration = notAfter_utc - datetime.utcnow().replace(tzinfo=utc_tz)
                    if days_only:
                        time_until_expiration = time_until_expiration.days
                    else:
                        time_until_expiration = str(time_until_expiration)
                    time_since_expiration = 0
                row.extend(
                    [
                        str(serial_number),
                        subject,
                        sans,
                        signature_algorithm,
                        notBefore,
                        notAfter,
                        issuer,
                        str(is_expired),
                        time_since_expiration,
                        time_until_expiration
                    ]
                )
                rows.append(row)
                sleep(delay)

    if extension.lower() == ".csv":
        with open(out_file, "w", newline="") as fout:
            writer = csv.writer(fout)
            writer.writerows(rows)
    elif extension.lower() == ".xlsx":
        wb = openpyxl.Workbook()
        ws = wb.active
        for row in rows:
            try:
                ws.append(row)
            except Exception:
                say("Error Adding certificate: '{}'".format(row))
        wb.save(out_file)
    else:
        # This should not happen
        say("ERROR: out_file must have an xlsx or csv file extension.")
        raise ValueError("ERROR: out_file must have an xlsx or csv file extension.")
    # wb.save(out_file)
    say("\n\nCertificate Report (available at {}):".format(os.path.abspath(out_file)))
    # The terminal gets the name of the spreadsheet, not the table
    report = Report(env)
    report.add(Table(rows[0], rows[1:]), show=False)
    return report


class CertFileAudit(ApplianceParams):
    out_file: str = Option(
        os.path.join("tmp", "cert-file-audit.xlsx"), short="o",
        widget=OUT_FILE,
        help="The excel spreadsheet to output, use either relative or "
             "absolute path. The file should end in `.xlsx`")


@cli.command("cert-file-audit", category="certificates")
def cert_file_audit(params: CertFileAudit):
    """Perform an audit of all files which reside in `cert:`, `pubcert:`
and `sharedcert:` on the specified appliances.

Output:

A table and an excel spreadsheet."""
    out_file = params.out_file
    logger = make_logger("cert-file-audit")
    if out_file is None:
        logger.error("Must specify out file")
        say("Must specify out_file")
        sys.exit(2)
    if not os.path.exists(os.path.dirname(out_file)):
        os.makedirs(os.path.dirname(out_file))
    locations = ["cert:", "pubcert:", "sharedcert:"]
    env = params.environment()

    header_row = ["appliance",
                  "domain",
                  "directory",
                  "filename",
                  "size",
                  "modified"]
    rows = [header_row]

    for appliance in env.appliances:
        say(appliance.hostname)
        domain = "default"

        for location in locations:
            say("\t{}".format(location))
            filestore = appliance.get_filestore(domain=domain,
                                                location=location)
            _location = filestore.xml.find(datapower.FILESTORE_XPATH)
            if _location is None:
                continue
            if _location.findall("./file") is not None:
                for _file in _location.findall("./file"):
                    dir_name = _location.get("name")
                    filename = _file.get("name")
                    say("\t\t{}".format(filename))
                    size = _file.find("size").text
                    modified = _file.find("modified").text
                    rows.append([appliance.hostname,
                                 domain,
                                 dir_name,
                                 filename,
                                 size,
                                 modified])
            for directory in _location.findall(".//directory"):
                dir_name = directory.get("name")
                say("\t\t{}".format(dir_name))
                for _file in directory.findall(".//file"):
                    filename = _file.get("name")
                    say("\t\t\t{}".format(filename))
                    size = _file.find("size").text
                    modified = _file.find("modified").text

                    rows.append([appliance.hostname,
                                 domain,
                                 dir_name,
                                 filename,
                                 size,
                                 modified])
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "CertFileAudit"
    for row in rows:
        ws.append(row)
    wb.save(out_file)
    return Table(rows[0], rows[1:], history(env))


class ExportCerts(ApplianceParams):
    domains: list[str] = Option(
        default_factory=list, short="d",
        help="The domains to export from, to export from all domains, provide "
             "`all-domains`")
    out_dir: str = Option(
        "tmp", short="o", widget=OUT_DIR,
        help="The directory to which to download the certificates.")
    delay: float = Option(
        0.5, short="D",
        help="The amount of time in seconds to wait between exporting each "
             "certificate. If you are experiencing intermitten "
             "`AuthenticationFailure`s, it is a good idea to increase "
             "this parameter.")


@cli.command("export-certs", category="certificates")
def export_certs(params: ExportCerts):
    """Export all CryptoCertificate objects which are up and enabled
from the specified domains on the specified appliances in PEM format
and download them to `out-dir`

Output:

Downloaded files"""
    domains = params.domains
    out_dir = params.out_dir
    logger = make_logger("export-certs")
    env = params.environment()

    for appliance in env.appliances:
        logger.info("Checking appliance {}".format(appliance.hostname))
        say(appliance.hostname)

        _domains = domains
        if "all-domains" in domains:
            _domains = appliance.domains

        for domain in _domains:
            logger.info("In domain {}".format(domain))
            say("\t", domain)

            # Get a list of all certificates in this domain
            config = appliance.get_config("CryptoCertificate", domain=domain)
            certs = [x for x in config.xml.findall(datapower.CONFIG_XPATH)]

            # Filter out disabled objects because the results won't change,
            # but we will perform less network traffic
            certs = [x for x in certs if x.find("mAdminState").text == "enabled"]
            if not certs:
                continue

            # Create a directory structure $out_dir/hostname/domain
            dir_name = os.path.join(out_dir, appliance.hostname, domain)
            if not os.path.exists(dir_name):
                os.makedirs(dir_name)

            for cert in certs:
                logger.info("Exporting cert {}".format(cert))

                # Get filename as it will appear locally
                filename = cert.find("Filename").text
                out_file = re.sub(r":[/]*", "/", filename)
                out_file = out_file.split("/")
                out_file = os.path.join(dir_name, *out_file)

                # extract directory name as it will appear locally
                _out_dir = out_file.split(os.path.sep)[:-1]
                _out_dir = os.path.join(*_out_dir)
                # Create the directory if it doesn't exist
                if not os.path.exists(_out_dir):
                    os.makedirs(_out_dir)

                name = cert.get("name")
                say("\t\t", name)
                export = appliance.CryptoExport(domain=domain,
                                                ObjectType="cert",
                                                ObjectName=name,
                                                OutputFilename=name)
                logger.info("Finished exporting cert {}".format(cert))
                try:
                    logger.info(
                        "Retrieving file temporary:///{}".format(name))
                    cert = appliance.getfile(domain,
                                             "temporary:///{}".format(name))
                    logger.info(
                        "Finished retrieving file temporary:///{}".format(
                            name))
                    logger.info(
                        "Attempting to delete file temporary:///{}".format(
                            name))
                    appliance.DeleteFile(domain=domain,
                                         File="temporary:///{}".format(name))
                    logger.info(
                        "Finished deleting file temporary:///{}".format(name))
                except Exception:
                    logger.exception("An unhandled exception has occurred")
                    say("SKIPPING CERT")
                    continue
                cert = etree.fromstring(cert)
                with open(out_file, "w") as fout:
                    _contents = insert_newlines(cert.find("certificate").text)
                    contents = "{}\n{}\n{}\n".format(
                        "-----BEGIN CERTIFICATE-----",
                        _contents,
                        "-----END CERTIFICATE-----")
                    fout.write(contents)
    return SeeDownload(
        [appliance.hostname for appliance in env.appliances],
        env, suffix="export-certs")


if __name__ == "__main__":
    cli.run()


# The crypto inventory: its commands join this cli
from . import export, inventory, posture, probe  # noqa: E402,F401

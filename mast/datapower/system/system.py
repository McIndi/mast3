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
"""
mast system:

A set of tools for automating routine system-administration
tasks associated with IBM DataPower appliances.
"""
import os
import gzip
import base64
import shutil
import zipfile
from mast.cli import Cli, Params, Option, Result, Table, Text, say
from time import time, sleep
from mast.xor import xorencode
from mast.logging import make_logger
from mast.timestamp import Timestamp
from mast.datapower import datapower
from mast.plugin_utils.params import (
    ApplianceParams, SaveConfigParams, BACKUPS_DIR, OBJECT_CLASS, OUT_DIR,
    OUT_FILE, STATUS_PROVIDER, UPLOAD, configured, timeout_option)
from mast.plugin_utils.results import (
    ActionResults, CommonItems, Report, SeeDownload, StatusResults, history)
from mast.plugin_utils.steps import perform
from mast.plugin_utils.steps import save_config as save_default_domain
from mast.datapower.backups import get_normal_backup

from mast.datapower.provider import cli as _provider  # noqa: E402

cli = _provider.section("system")

from mast.config import MAST_HOME


def _pmr_create_dirs(appliances, out_dir, timestamp):
    for appliance in appliances:
        _dir = os.path.join(
            out_dir,
            appliance.hostname,
            timestamp)
        os.makedirs(_dir)


def _pmr_get_error_report_settings(appliances):
    results = {}
    for appliance in appliances:
        config = appliance.get_config("ErrorReportSettings")
        results[appliance.hostname] = config
    return results


def _pmr_conditionally_save_internal_state(appliances, ers, timestamp):
    xpath = datapower.CONFIG_XPATH + "/ErrorReportSettings/InternalState"
    for appliance in appliances:
        internal_state = ers[appliance.hostname].xml.find(xpath).text
        internal_state = True if (internal_state == "on") else False
        if not internal_state:
            appliance.SaveInternalState()


def _pmr_generate_error_reports(appliances):
    for appliance in appliances:
        appliance.ErrorReport()


def _pmr_backup_all_domains(appliances, out_dir, timestamp):
    for appliance in appliances:
        filename = os.path.join(
            out_dir,
            appliance.hostname,
            timestamp)
        filename = os.path.join(
            filename,
            '%s-%s-all-domains.zip' % (
                timestamp,
                appliance.hostname))
        with open(filename, 'wb') as fout:
            fout.write(appliance.get_normal_backup())


def _pmr_query_status_providers(appliances, out_dir, timestamp):
    global MAST_HOME
    filename = os.path.join(MAST_HOME, 'etc', 'statusProviders.txt')
    with open(filename, 'r') as fin:
        default_providers = [_.strip() for _ in fin.readlines()]
    filename = os.path.join('etc', 'statusProviders-applicationDomains.txt')
    with open(filename, 'r') as fin:
        application_providers = [_.strip() for _ in fin.readlines()]
    for appliance in appliances:
        for domain in appliance.domains:
            providers = application_providers
            if domain == 'default':
                providers = default_providers
            filename = 'pmrinfo-%s-%s-%s.xml' % (
                appliance.hostname, domain, timestamp)
            filename = os.path.join(
                out_dir,
                appliance.hostname,
                timestamp,
                filename)
            with open(filename, 'w') as fout:
                msg = "<pmrInfo-{}-{}>{}".format(
                    appliance.hostname, domain, os.linesep)
                fout.write(msg)
                for provider in providers:
                    fout.write('<{}>{}'.format(provider, os.linesep))
                    try:
                        status = appliance.get_status(
                            provider, domain=domain).pretty
                        fout.write(status)
                    except Exception:
                        fout.write("Failed to retrieve status!")
                    fout.write('</{}>{}'.format(provider, os.linesep))
                    fout.write(os.linesep)
                fout.write('</pmrInfo-{}-{}>{}'.format(
                    appliance.hostname, domain, os.linesep))


def _pmr_download_error_reports(appliances, out_dir, ers, timestamp):
    protocol_xpath = datapower.CONFIG_XPATH + "/ErrorReportSettings/Protocol"
    raid_path_xpath = datapower.CONFIG_XPATH + "/ErrorReportSettings/RaidPath"

    for appliance in appliances:
        protocol = ers[appliance.hostname].xml.find(protocol_xpath).text

        if protocol == 'temporary':
            path = 'temporary:'
            filestore = appliance.get_filestore('default', path)
            _dir = filestore.xml.find('.//location[@name="%s"]' % (path))

        elif protocol == 'raid':
            try:
                path = ers[appliance.hostname].xml.find(raid_path_xpath).text
            except AttributeError:
                path = ''
            path = "{}/{}".format(appliance.raid_directory, path)
            if path.endswith('/'):
                path = path[:-1]
            filestore = appliance.get_filestore('default', 'local:')
            _dir = filestore.xml.find('.//directory[@name="%s"]' % (path))

        else:
            appliance.log_warn(''.join(
                    ('\tThe failure notification looks like it is set for ',
                     protocol,
                     ', which we do not currently support. Failing back',
                     'to temporary:...\n')))
            path = 'temporary:'
            filestore = appliance.get_filestore('default', path)
            _dir = filestore.xml.find('.//location[@name="%s"]' % (path))

        if not _dir:
            appliance.log_warn("There were no error reports found.")
            return
        files = []
        for node in _dir.findall('.//*'):
            if node.tag == "file":
                if 'error-report' in node.get('name'):
                    files.append(node.get('name'))
        for file in files:
            fqp = '%s/%s' % (path, file)
            filename = '%s-%s' % (appliance.hostname, file)
            filename = os.path.join(
                out_dir,
                appliance.hostname,
                timestamp,
                filename)
            with open(filename, 'wb') as fout:
                fout.write(appliance.getfile('default', fqp))


def _pmr_cleanup(appliances, out_dir, timestamp):
    for appliance in appliances:
        zip_filename = '{}-{}-PMR_INFO.zip'.format(
            timestamp, appliance.hostname)
        zip_filename = os.path.join(out_dir, zip_filename)
        z = zipfile.ZipFile(zip_filename, 'w', zipfile.ZIP_DEFLATED)
        _zipdir(os.path.join(out_dir, appliance.hostname, timestamp), z)
        shutil.rmtree(
            os.path.abspath(
                os.path.join(out_dir, appliance.hostname, timestamp)))


def _zipdir(path, z):
    for root, dirs, files in os.walk(path):
        for file in files:
            z.write(os.path.join(root, file), os.path.basename(file))


def _verify_zip(zip_file):
    if isinstance(zip_file, str):
        try:
            zip_file = zipfile.ZipFile(zip_file, 'r')
        except zipfile.BadZipfile:
            return False
    if zip_file.testzip() is None:
        # if testzip returns None then there were no errors
        return True
    return False


def _action(params, action, suffix, description, **kwargs):
    """Perform action on each appliance in params and return the
    responses. description says what the action does, for the log and
    for the user."""
    logger = make_logger("mast.system")
    env = params.environment()
    msg = "Attempting to {} on {}".format(description, str(env.appliances))
    logger.info(msg)
    say(msg)
    responses = env.perform_action(action, **kwargs)
    logger.debug("Responses received: {}".format(str(responses)))
    return ActionResults(responses, env, suffix=suffix)


def _each_domain(env, domains, action, skip_default=False):
    """Call action(appliance, domain) for each appliance in env and
    each of domains. "all-domains" stands for every domain of the
    appliance, without the default domain if skip_default is True.
    Return the responses, with "hostname-domain" as the key."""
    responses = {}
    for appliance in env.appliances:
        _domains = list(domains)
        if "all-domains" in _domains:
            _domains = [
                domain for domain in appliance.domains
                if not (skip_default and domain == "default")]
        for domain in _domains:
            key = "{}-{}".format(appliance.hostname, domain)
            responses[key] = action(appliance, domain)
    return responses


def _domains_option(help):
    return Option(
        default_factory=list, short="D", widget=configured("Domain"),
        help=help + " `all-domains` stands for every domain of the "
                    "appliance.")


class Xor(Params):
    string: str = Option(
        "", short="s", help="The string to xor and base64 encode")


# Not queued: the string is a credential, and the result can be
# decoded
@cli.command('xor', effect="read", targets=(), group='utilities', queued=False)
def xor(params: Xor):
    """This will xor encode and base64 encode the given string
for suitable use in passing credentials to MAST CLI commands.
This is a useful utility for scripting multiple MAST CLI commands
since your credentials will not be in plain text.

**PLEASE NOTE THAT THIS IS OBFUSCATION AT BEST, SO DON'T LEAN
TOO HEAVILY ON THIS SECURITY**"""
    return Text(xorencode(params.string))


class CredentialChecks(Table):
    """The result of check-credentials: one row for each appliance. The
    columns are named in mast.credentials, which reads them."""
    def __init__(self, rows):
        super().__init__(
            ["appliance", "works", "firmware", "can change", "problem"],
            rows)

    def outcomes(self):
        return [row[1] == "yes" for row in self.rows]


def _check_credentials(appliance):
    """Return the row of check-credentials for one appliance."""
    try:
        status = appliance.get_status("FirmwareVersion")
        found = status.xml.find(".//Version")
    except Exception as exc:
        return [appliance.hostname, "no", "", "",
                "{}: {}".format(type(exc).__name__, exc)]
    if found is None or not (found.text or "").strip():
        # A user that an admin just made must change its password
        # before DataPower gives it any data
        return [appliance.hostname, "no", "", "",
                "The appliance gave no data for this user. A new user "
                "must change its password first (log in once), and the "
                "user must be allowed to read the status."]
    firmware = found.text.strip()
    try:
        # Ping of the appliance itself changes nothing, but it is an
        # action, so a user that may only read gets "Permission Denied"
        response = appliance.do_action("Ping", RemoteHost="127.0.0.1")
        text = getattr(response, "text", "") or ""
    except Exception as exc:
        return [appliance.hostname, "yes", firmware, "",
                "Could not find out whether the user can change the "
                "appliance. {}: {}".format(type(exc).__name__, exc)]
    can_change = "no" if "permission denied" in text.lower() else "yes"
    return [appliance.hostname, "yes", firmware, can_change, ""]


@cli.command('check-credentials', effect="read", group='utilities',
             exclusive=False)
def check_credentials(params: ApplianceParams):
    """Checks the credentials on each appliance: whether they can log in
and read the firmware version, and whether they can change the
appliance. To find that out, it asks the appliance to ping itself, an
action that changes nothing: a user that may only read is refused.

`mast credentials test` uses this to test a stored credential."""
    env = params.environment()
    rows = []
    for appliance in env.appliances:
        say("Checking the credentials on {}".format(appliance.hostname))
        rows.append(_check_credentials(appliance))
    return CredentialChecks(rows)


class ConnectivityChecks(Table):
    """The result of test-connectivity: one row for each appliance."""
    def __init__(self, rows):
        super().__init__(
            ["appliance", "soma", "firmware", "ssh", "problem"], rows)

    def outcomes(self):
        return [row[1] == "yes" and row[3] == "yes" for row in self.rows]


def _test_connectivity(appliance, timeout):
    """The row of test-connectivity for one appliance: can MAST log in
    to its XML management interface (SOMA), and to its SSH?"""
    problems = []
    soma, firmware = "no", ""
    try:
        status = appliance.get_status("FirmwareVersion")
        found = status.xml.find(".//Version")
        if found is not None and (found.text or "").strip():
            soma, firmware = "yes", found.text.strip()
        else:
            problems.append("SOMA: the appliance gave no data for this user "
                            "(a new user must change its password first)")
    except datapower.AuthenticationFailure:
        problems.append("SOMA: the user name or the password is wrong, or "
                        "the account is locked")
    except Exception as exc:
        problems.append("SOMA: {}: {}".format(type(exc).__name__, exc))
    ssh = "no"
    try:
        banner = appliance.ssh_connect(port=appliance.ssh_port,
                                       timeout=timeout) or ""
        try:
            if "DataPower" in banner or banner.rstrip().endswith("#"):
                ssh = "yes"
            else:
                problems.append("SSH: no DataPower prompt after the login")
        finally:
            appliance.ssh_disconnect()
    except datapower.AuthenticationFailure:
        problems.append("SSH: the user name or the password is wrong, or "
                        "the account is locked")
    except Exception as exc:
        problems.append("SSH: {}: {}".format(type(exc).__name__, exc))
    return [appliance.hostname, soma, firmware, ssh,
            "; ".join(p.splitlines()[0][:200] for p in problems)]


@cli.command('test-connectivity', effect="read", group='utilities',
             exclusive=False,
             passive=True)
def test_connectivity(params: ApplianceParams):
    """Tests whether MAST can log in to each appliance, with these
credentials: to its XML management interface (SOMA, which reads the
firmware version) and to its SSH (which logs in and out). It changes
nothing. With a stored credential, SSH needs a pinned host key.

The Test button of the target bar of the web UI runs this."""
    import contextvars
    from concurrent.futures import ThreadPoolExecutor
    env = params.environment()
    say("Testing {} appliance(s)".format(len(env.appliances)))
    with ThreadPoolExecutor(max_workers=8) as pool:
        # Each thread runs in a copy of this context: with a stored
        # credential, the job runs inside stored_credential(), which
        # refuses an SSH host key that is not pinned
        futures = [pool.submit(contextvars.copy_context().run,
                               _test_connectivity, a, params.timeout)
                   for a in env.appliances]
        rows = [f.result() for f in futures]
    return ConnectivityChecks(rows)


# ~#~#~#~#~#~#~#
# Caches
# ======
#
# These functions are meant to be used to flush the caches that DataPower
# maintains.
#
# Current Commands:
# ----------------
#
# FlushAAACache(PolicyName)
# FlushArpCache()
# FlushDNSCache()
# FlushDocumentCache(XMLManager)
# FlushLDAPPoolCache(XMLManager)
# FlushNDCache()
# FlushNSSCache(ZosNSSClient)
# FlushPDPCache(XACMLPDP)
# FlushRBMCache()
# FlushStylesheetCache(XMLManager)
#

class FlushAAACache(ApplianceParams):
    Domain: str = Option(
        "", short="D", widget=configured("Domain"),
        help="The domain where the specified AAAPolicy resides")
    aaa_policy: str = Option(
        "", short="A",
        help="The AAAPolicy whose cache you would like to flush")


@cli.command('flush-aaa-cache', effect="change", group='caches')
def flush_aaa_cache(params: FlushAAACache):
    """This will flush the AAA Cache for the specified AAAPolicy
in the specified Domain on the specified appliances."""
    return _action(
        params, 'FlushAAACache', "flush_aaa_cache",
        "flush the AAA cache of {} in the {} domain".format(
            params.aaa_policy, params.Domain),
        PolicyName=params.aaa_policy, domain=params.Domain)


@cli.command('flush-arp-cache', effect="change", group='caches')
def flush_arp_cache(params: ApplianceParams):
    """This will flush the ARP cache on the specified appliances."""
    return _action(
        params, 'FlushArpCache', "flush_arp_cache", "flush the ARP cache")


@cli.command('flush-dns-cache', effect="change", group='caches')
def flush_dns_cache(params: ApplianceParams):
    """This will flush the DNS cache on the specified appliances."""
    return _action(
        params, 'FlushDNSCache', "flush_dns_cache", "flush the DNS cache")


class XMLManagerParams(ApplianceParams):
    Domain: str = Option(
        "", short="D", widget=configured("Domain"),
        help="The domain where xml_manager resides")
    xml_manager: str = Option(
        "", short="x",
        help="The XMLManager whose cache you would like to flush")


@cli.command('flush-document-cache', effect="change", group='caches')
def flush_document_cache(params: XMLManagerParams):
    """This will flush the Document cache for the specified
xml_manager in the specified domain on the specified appliances."""
    return _action(
        params, 'FlushDocumentCache', "flush_document_cache",
        "flush the document cache of {} in the {} domain".format(
            params.xml_manager, params.Domain),
        XMLManager=params.xml_manager, domain=params.Domain)


@cli.command('flush-ldap-pool-cache', effect="change", group='caches')
def flush_ldap_pool_cache(params: XMLManagerParams):
    """This will flush the LDAP Pool Cache for the specified
xml_manager in the specified domain on the specified appliances"""
    return _action(
        params, 'FlushLDAPPoolCache', "flush_ldap_pool_cache",
        "flush the LDAP pool cache of {} in the {} domain".format(
            params.xml_manager, params.Domain),
        XMLManager=params.xml_manager, domain=params.Domain)


@cli.command('flush-nd-cache', effect="change", group='caches')
def flush_nd_cache(params: ApplianceParams):
    """This will flush the ND cache for the specified appliances."""
    return _action(
        params, 'FlushNDCache', "flush_nd_cache", "flush the ND cache")


class FlushNSSCache(ApplianceParams):
    Domain: str = Option(
        "", short="D", widget=configured("Domain"),
        help="The domain where zos_nss_client resides")
    zos_nss_client: str = Option(
        "", short="z",
        help="The ZOSNSSClient whose cache you would like to flush")


@cli.command('flush-nss-cache', effect="change", group='caches')
def flush_nss_cache(params: FlushNSSCache):
    """This will flush the NSS cache for the specified ZOSNSSClient
in the specified domain for the specified appliance."""
    return _action(
        params, 'FlushNSSCache', "flush_nss_cache",
        "flush the NSS cache of {} in the {} domain".format(
            params.zos_nss_client, params.Domain),
        ZosNSSClient=params.zos_nss_client, domain=params.Domain)


class FlushPDPCache(ApplianceParams):
    XACMLPDP: str = Option(
        "", short="X", widget=configured("XACMLPDP"),
        help="The XACMLPDP object whose cache you would like to flush")


@cli.command('flush-pdp-cache', effect="change", group='caches')
def flush_pdp_cache(params: FlushPDPCache):
    """This will flush the PDP cache for the specified XACMLPDP
for the specified appliances."""
    return _action(
        params, 'FlushPDPCache', "flush_pdp_cache",
        "flush the PDP cache of {}".format(params.XACMLPDP),
        XACMLPDP=params.XACMLPDP)


class FlushRBMCache(ApplianceParams):
    Domain: str = Option(
        "", short="D", widget=configured("Domain"),
        help="The domain whose RBM cache you would like to flush")


@cli.command('flush-rbm-cache', effect="change", group='caches')
def flush_rbm_cache(params: FlushRBMCache):
    """This will flush the RBM cache in the specified domain for
the specified appliances."""
    return _action(
        params, 'FlushRBMCache', "flush_rbm_cache",
        "flush the RBM cache in the {} domain".format(params.Domain),
        domain=params.Domain)


@cli.command('flush-stylesheet-cache', effect="change", group='caches')
def flush_stylesheet_cache(params: XMLManagerParams):
    """This will flush the stylesheet cache for the specified xml_manager
in the specified Domain on the specified appliances."""
    return _action(
        params, 'FlushStylesheetCache', "flush_stylesheet_cache",
        "flush the stylesheet cache of {} in the {} domain".format(
            params.xml_manager, params.Domain),
        XMLManager=params.xml_manager, domain=params.Domain)
#
# ~#~#~#~#~#~#~#

# ~#~#~#~#~#~#~#
# configuration
# =============
#
# These functions are meant to be used to affect the confiuration of the
# DataPower appliances.
#
# current commands:
# ----------------
#
# save - save the current configuration of the specified domains
#

class SaveConfig(ApplianceParams):
    Domain: list[str] = Option(
        default_factory=lambda: ["default"], short="D",
        widget=configured("Domain"),
        help="The domains to save (default: default). `all-domains` "
             "stands for every domain of the appliance.")


@cli.command('save', effect="change", group='configuration')
def save_config(params: SaveConfig):
    """Saves the current running configuration in the given domain(s)"""
    logger = make_logger("mast.system")
    env = params.environment()

    def save(appliance, domain):
        msg = "Attempting to save configuration of {} on {}".format(
            domain, appliance.hostname)
        logger.info(msg)
        say(msg)
        resp = appliance.SaveConfig(domain=domain)
        logger.debug("Response received: {}".format(resp))
        return resp

    return ActionResults(
        _each_domain(env, params.Domain, save), env, suffix="save_config")


class QuiesceService(ApplianceParams):
    type: str = Option("", short="T", help="The type of service to quiesce")
    name: str = Option("", short="N", help="The name of the service to quiesce")
    Domain: str = Option(
        "", short="D", widget=configured("Domain"),
        help="The domain in which the service resides")
    quiesce_timeout: int = Option(
        60, short="q",
        help="This is the amount of time (in seconds) the appliance "
             "should wait before forcing the quiesce (**Must be at "
             "least 60**)")


@cli.command("quiesce-service", effect="change", group="configuration")
def quiesce_service(params: QuiesceService):
    """This will quiesce a service in the specified domain on the specified
appliances."""
    result = _action(
        params, "ServiceQuiesce", "quiesce_service",
        "quiesce service {} in {}".format(params.name, params.Domain),
        type=params.type, name=params.name,
        timeout=params.quiesce_timeout, domain=params.Domain)
    sleep(params.quiesce_timeout)
    return result


class UnquiesceService(ApplianceParams):
    Domain: str = Option(
        "", short="D", widget=configured("Domain"),
        help="The domain in which the service resides")
    type: str = Option(
        "", short="T", help="The type of the service to unquiesce")
    name: str = Option(
        "", short="N", help="The name of the service to unquiesce")
    quiesce_timeout: int = Option(
        120, short="q",
        help="Not used. It is accepted so that existing scripts "
             "continue to work.")


@cli.command("unquiesce-service", effect="change", group="configuration")
def unquiesce_service(params: UnquiesceService):
    """This will unquiesce a service in the specified domain on the specified
appliances."""
    return _action(
        params, "ServiceUnquiesce", "unquiesce_service",
        "unquiesce service {} in {}".format(params.name, params.Domain),
        type=params.type, name=params.name, domain=params.Domain)

#
# ~#~#~#~#~#~#~#

# ~#~#~#~#~#~#~#
# domains
# =======
#
# These functions are meant to be used to affect the domains
# of the DataPower appliances.
#
# current commands:
# ----------------
# list-domains - Shows the domains of the specified appliances
# add-domain - adds a domain to the specified appliances
# del-domain - removes a domain from the specified appliances
# quiesce-domain - quiesce the specified domain
# unquiesce-domain - unquiesce the specified domain
# disable-domain - set the admin-state to disabled for the specified domain
# enable-domain - set the admin-state to enabled for the specified domain
#

@cli.command('list-domains', effect="read", group='domains', exclusive=False)
def list_domains(params: ApplianceParams):
    """Lists the domains on the specified appliances as well as all common
domains."""
    logger = make_logger("mast.system")
    env = params.environment()

    domains = {}
    for appliance in env.appliances:
        logger.info("Attempting to retrieve a list of domains for {}".format(
            str(appliance)))
        domains[appliance.hostname] = sorted(appliance.domains)
        logger.debug("Domains for {} found: {}".format(
            str(appliance), str(domains[appliance.hostname])))
    result = CommonItems("Domains", domains, env)
    logger.info("domains common to {}: {}".format(
        str(env.appliances), str(result.common)))
    return result


def _change_domain(params, action, description, name):
    """Add or remove the domain called name on each appliance in
    params, then save the default domain if params asks for that."""
    logger = make_logger("mast.system")
    env = params.environment()
    report = Report(env)

    msg = "Attempting to {} on {}".format(description, str(env.appliances))
    logger.info(msg)
    say(msg)
    perform(env, report, logger, action, action, name=name)

    if params.save_config:
        save_default_domain(env, report, logger)
    return report


class AddDomain(SaveConfigParams):
    domain_name: str = Option(
        short="d", help="The name of the domain to add")


@cli.command('add-domain', effect="change", group='domains')
def add_domain(params: AddDomain):
    """Adds a domain to the specified appliances"""
    return _change_domain(
        params, 'add_domain',
        "add domain {}".format(params.domain_name), params.domain_name)


class DelDomain(SaveConfigParams):
    Domain: str = Option(
        "", short="D", widget=configured("Domain"),
        help="The name of the domain to remove")


@cli.command('del-domain', effect="change", group='domains')
def del_domain(params: DelDomain):
    """Removes a domain from the specified appliances"""
    return _change_domain(
        params, 'del_domain',
        "remove domain {}".format(params.Domain), params.Domain)


class QuiesceDomain(ApplianceParams):
    Domain: list[str] = _domains_option("The domains to quiesce.")
    quiesce_timeout: int = Option(
        60, short="q", help="The timeout before quiescing the domain")


@cli.command('quiesce-domain', effect="change", group='domains')
def quiesce_domain(params: QuiesceDomain):
    """Quiesces a domain on the specified appliances"""
    logger = make_logger("mast.system")
    env = params.environment()

    def quiesce(appliance, domain):
        msg = "Attempting to quiesce domain {} on {}".format(
            domain, appliance.hostname)
        logger.info(msg)
        say(msg)
        resp = appliance.DomainQuiesce(
            name=domain, timeout=str(params.quiesce_timeout), domain=domain)
        logger.debug("Response received: {}".format(str(resp)))
        sleep(params.quiesce_timeout)
        return resp

    return ActionResults(
        _each_domain(env, params.Domain, quiesce), env,
        suffix="DomainQuiesce")


class DomainsParams(ApplianceParams):
    Domain: list[str] = _domains_option("The domains to affect.")


@cli.command('unquiesce-domain', effect="change", group='domains')
def unquiesce_domain(params: DomainsParams):
    """Unquiesces a domain on the specified appliances"""
    logger = make_logger("mast.system")
    env = params.environment()

    def unquiesce(appliance, domain):
        msg = "Attempting to unquiesce domain {} on {}".format(
            domain, appliance.hostname)
        logger.info(msg)
        say(msg)
        resp = appliance.DomainUnquiesce(name=domain)
        logger.debug("Response received: {}".format(str(resp)))
        return resp

    return ActionResults(
        _each_domain(env, params.Domain, unquiesce), env,
        suffix="unquiesce_domain")


class AdminStateParams(SaveConfigParams):
    Domain: list[str] = Option(
        default_factory=list, short="D", widget=configured("Domain"),
        help="The domains to affect. `all-domains` stands for every "
             "domain of the appliance except the default domain.")


def _admin_state(params, method, verb):
    """Enable or disable the domains in params. method is the name of
    the method of the appliance and verb is "enable" or "disable"."""
    logger = make_logger("mast.system")
    env = params.environment()
    report = Report(env)

    def change(appliance, domain):
        msg = "Attempting to {} domain {} on {}".format(
            verb, domain, appliance.hostname)
        logger.info(msg)
        say(msg)
        resp = getattr(appliance, method)(domain)
        logger.debug("Response received: {}".format(str(resp)))
        return resp

    def save(appliance, domain):
        msg = "Attempting to save configuration of {} on {}".format(
            domain, appliance.hostname)
        logger.info(msg)
        say(msg)
        resp = appliance.SaveConfig(domain=domain)
        logger.debug("Response received: {}".format(str(resp)))
        return resp

    report.add(ActionResults(
        _each_domain(env, params.Domain, change, skip_default=True),
        suffix=method))
    if params.save_config:
        report.add(ActionResults(
            _each_domain(env, params.Domain, save), suffix="save_config"))
    return report


@cli.command('disable-domain', effect="change", group='domains')
def disable_domain(params: AdminStateParams):
    """Disables a domain on the specified appliances"""
    return _admin_state(params, "disable_domain", "disable")


@cli.command('enable-domain', effect="change", group='domains')
def enable_domain(params: AdminStateParams):
    """Enables a domain on the specified appliances"""
    return _admin_state(params, "enable_domain", "enable")


@cli.command('restart-domain', effect="change", group='domains')
def restart_domain(params: DomainsParams):
    """Restarts the specified domains on the specified appliances"""
    logger = make_logger("mast.system")
    env = params.environment()

    def restart(appliance, domain):
        msg = "Attempting to restart domain {} on {}".format(
            domain, appliance.hostname)
        logger.info(msg)
        say(msg)
        resp = appliance.RestartDomain(Domain=domain)
        logger.debug("Response received: {}".format(str(resp)))
        return resp

    return ActionResults(
        _each_domain(env, params.Domain, restart), env,
        suffix="restart_domain")

#
# ~#~#~#~#~#~#~#

# ~#~#~#~#~#~#~#
# appliances
# ==========
#
# These functions are meant to affect the DataPower appliances
# as a whole.
#
# current commands
# ----------------
# quiesce-appliance - Quiesce the specified DataPower appliances
# unquiesce-appliance - Unquiesce the specified DataPower appliances
# reboot-appliance - Reboot the specified appliance.
# shutdown-appliance - Shutdown the specified appliance.

class QuiesceAppliance(ApplianceParams):
    quiesce_timeout: int = Option(
        60, short="q", help="The timeout before quiescing the appliance")


@cli.command('quiesce-appliance', effect="change", group='appliances')
def quiesce_appliance(params: QuiesceAppliance):
    """Quiesce the specified appliances"""
    result = _action(
        params, 'QuiesceDP', "quiesce_dp", "quiesce the appliance",
        timeout=str(params.quiesce_timeout))
    sleep(params.quiesce_timeout)
    return result


@cli.command('unquiesce-appliance', effect="change", group='appliances')
def unquiesce_appliance(params: ApplianceParams):
    """Unquiesce the specified appliances"""
    return _action(
        params, 'UnquiesceDP', "unquiesce_dp", "unquiesce the appliance")


class RebootAppliance(ApplianceParams):
    delay: int = Option(10, short="d", help="The delay before rebooting")
    wait: int = Option(
        1200, short="w",
        help="The amount of time to wait for each appliance to come "
             "back up")


@cli.command('reboot-appliance', effect="change", group='appliances')
def reboot_appliance(params: RebootAppliance):
    """Reboot the specified appliances (equivalent to shutdown-reboot)

The appliances are rebooted one at a time. If an appliance does not
come back up in time, the appliances after it are not rebooted."""
    logger = make_logger("mast.system")
    env = params.environment()

    responses = {}
    for appliance in env.appliances:
        msg = "Attempting to reboot {}".format(appliance.hostname)
        logger.info(msg)
        say(msg)
        resp = appliance.Shutdown(Mode="reboot", Delay=str(params.delay))
        responses[appliance.hostname] = resp
        logger.debug("Response received: {}".format(str(resp)))
        sleep(params.delay)
        start = time()
        while True:
            sleep(5)
            if appliance.is_reachable():
                logger.info("Appliance is back up, moving on")
                break
            if (time() - start) > params.wait:
                logger.error(
                    "{} failed to respond within the specified time".format(
                        appliance.hostname))
                raise RuntimeError(
                    "Appliance "
                    "{} did not respond within specified time {}".format(
                        appliance.hostname, str(params.wait)))
    return ActionResults(responses, env, suffix="reboot_appliance")


class ShutdownAppliance(ApplianceParams):
    delay: int = Option(10, short="d", help="The delay before shutting down")


@cli.command('shutdown-appliance', effect="change", group='appliances')
def shutdown_appliance(params: ShutdownAppliance):
    """Shutdown the specified appliances(equivalent to shutdown-halt)"""
    return _action(
        params, 'Shutdown', "shutdown-appliance", "shut down",
        Mode='halt', Delay=str(params.delay))


class ReloadResults(ActionResults):
    """The responses to a reload. The response of an appliance that
    did not come back up is False."""

    def text(self):
        lines = []
        for host, response in self.responses.items():
            lines.append("{}\n{}".format(host, "=" * len(host)))
            if response is False:
                lines.append("Appliance did not come back up")
            else:
                lines.append(str(response))
        return "\n".join(lines)


class ReloadAppliance(ApplianceParams):
    delay: int = Option(10, short="d", help="The delay before reloading")
    wait: int = Option(
        180, short="w",
        help="The amount of time to wait for the appliance to come "
             "back up")


@cli.command('reload-appliance', effect="change", group='appliances')
def reload_appliance(params: ReloadAppliance):
    """Reload the specified appliances (equivalent to shutdown-reload)"""
    logger = make_logger("mast.system")
    env = params.environment()

    resp = {}
    for appliance in env.appliances:
        msg = "Attempting to reload {}".format(appliance.hostname)
        logger.info(msg)
        say(msg)
        resp[appliance.hostname] = appliance.Shutdown(
            Mode='reload', Delay=str(params.delay))
        logger.debug("Response received: {}".format(resp[appliance.hostname]))
        sleep(params.delay)
        start = time()
        while True:
            if appliance.is_reachable():
                logger.info("Appliance {} is back online".format(
                    appliance.hostname))
                break
            sleep(3)
            if (time() - start) > params.wait:
                msg = "appliance {} ".format(appliance.hostname)
                msg += "failed to come back online within the "
                msg += "specified timeout"
                logger.warning(msg)
                resp[appliance.hostname] = False
                break
    return ReloadResults(resp, env, suffix="reload_appliance")


class FirmwareUpgrade(ApplianceParams):
    timeout: int = timeout_option(1200)
    file_in: str = Option(
        short="f", widget=UPLOAD,
        help="The patch (upgrade script usually `*.scrypt4`, "
             "`*.scrypt3` etc...)")
    accept_license: bool = Option(
        False, short="A",
        help="Whether to accept the license of the new firmware (You "
             "**MUST** specify this or the upgrade will not work)")
    out_dir: str = Option(
        BACKUPS_DIR, short="o", widget=OUT_DIR,
        help="The directory in which to save the backups that are "
             "taken before the upgrade")
    quiesce_timeout: int = Option(
        120, short="q", help="The timeout before quiescing the appliance")
    reboot_delay: int = Option(
        5, short="r", help="The delay before rebooting")
    reboot_wait: int = Option(
        1200, short="R",
        help="The amount of time to wait for an appliance to come back "
             "up")
    boot_delete: bool = Option(
        True,
        help="Give `--no-boot-delete` to skip the `boot delete` that "
             "is otherwise performed before upgrading the firmware")
    no_cleanup: bool = Option(
        False, short="N", help="Do not clean up the filesystem")
    no_backup: bool = Option(
        False, help="Do not take an all-domains backup")
    no_save_config: bool = Option(
        False, help="Do not save the configuration")
    no_quiesce_appliances: bool = Option(
        False, help="Do not quiesce the appliance")
    no_disable_domains: bool = Option(
        False, help="Do not disable the application domains")
    no_reboot: bool = Option(
        False, help="Do not reboot the appliance before the upgrade")
    no_set_firmware: bool = Option(
        False, help="Do not upload and apply the firmware image")
    no_enable_domains: bool = Option(
        False, help="Do not enable the application domains afterwards")


@cli.command('firmware-upgrade', effect="change", group='appliances')
def firmware_upgrade(params: FirmwareUpgrade):
    """This will attempt to upgrade the firmware of the specified
appliances.

The appliances are upgraded one at a time. For each appliance the
steps are: clean up the filesystem, `boot delete`, take an all-domains
backup, clean up again, save the configuration, quiesce the appliance,
disable the application domains, save the configuration, reboot, apply
the firmware, wait for the appliance, enable the application domains
and save the configuration. Each step can be turned off."""
    logger = make_logger("mast.system")
    env = params.environment()
    report = Report(env)
    logger.info("Attempting to upgrade the firmware of {}".format(
        str(env.appliances)))

    for appliance in env.appliances:
        # The parameters that every step needs to reach this appliance
        one = dict(
            appliances=appliance.hostname,
            credentials=appliance.credentials,
            timeout=params.timeout,
            no_check_hostname=params.no_check_hostname)

        def step(message):
            logger.info("{}: {}".format(appliance.hostname, message))
            say("{}: {}".format(appliance.hostname, message))

        def clean():
            step("Cleaning up the filesystem")
            report.add(clean_up(
                Domain="default",
                checkpoints=True,
                export=True,
                logtemp=True,
                logstore=True,
                error_reports=True,
                recursive=True,
                backup_files=True,
                out_dir=params.out_dir,
                **one))

        def save():
            step("Saving the configuration of all domains")
            report.add(save_config(Domain=["all-domains"], **one))

        def application_domains():
            return [d for d in appliance.domains if d != "default"]

        if not params.no_cleanup:
            clean()

        if params.boot_delete:
            step("Attempting to perform boot delete")
            appliance.ssh_connect()
            r = appliance.ssh_issue_command("co")
            r += appliance.ssh_issue_command("flash")
            r += appliance.ssh_issue_command("boot delete")
            r += appliance.ssh_issue_command("exit")
            r += appliance.ssh_issue_command("exit")
            r += appliance.ssh_issue_command("exit")
            say(r)
            logger.debug("Responses received: {}".format(str(r)))

        if not params.no_backup:
            step("Getting an all-domains backup")
            report.add(get_normal_backup(
                Domain="all-domains",
                comment="pre-firmware_upgrade_backup",
                out_dir=params.out_dir,
                **one))

        if not params.no_cleanup:
            clean()

        if not params.no_save_config:
            save()

        if not params.no_quiesce_appliances:
            step("Quiescing the appliance")
            report.add(quiesce_appliance(
                quiesce_timeout=params.quiesce_timeout, **one))
            sleep(params.quiesce_timeout)

        if not params.no_disable_domains:
            for domain in application_domains():
                step("Disabling domain {}".format(domain))
                report.add(disable_domain(
                    Domain=[domain], save_config=False, **one))

        if not params.no_save_config:
            save()

        if not params.no_reboot:
            step("Rebooting the appliance")
            report.add(reboot_appliance(
                delay=params.reboot_delay, wait=params.reboot_wait, **one))

        if not params.no_set_firmware:
            step("Attempting to update the firmware")
            _out = appliance.set_firmware(
                params.file_in,
                params.accept_license,
                params.timeout)
            report.add(StatusResults(
                {appliance.hostname: "Succeeded" if _out else "Failed"},
                suffix="set_firmware"))

        sleep(60)

        step("Waiting for the appliance to come back online")
        start = time()
        while not appliance.is_reachable():
            if time() - start > params.timeout:
                msg = (
                    "{} did not come back up within {} seconds. "
                    "Aborting the remaining firmware upgrades!".format(
                        appliance.hostname, params.timeout))
                logger.error(msg)
                raise RuntimeError(msg)
            sleep(5)
        logger.info("Appliance is back up.")

        # TODO: verify version

        if not params.no_enable_domains:
            for domain in application_domains():
                step("Enabling domain {}".format(domain))
                report.add(enable_domain(
                    Domain=[domain], save_config=False, **one))

        if not params.no_save_config:
            save()

    return report
#
# ~#~#~#~#~#~#~#

# ~#~#~#~#~#~#~#
# file management
# ===============
#
# These functions are meant to facilitate common filesystem related tasks
# such as setting a file, removing a file, fetching a file or getting a file
# to/from the specified appliances
#

class OutDirParams(ApplianceParams):
    out_dir: str = Option(
        "tmp", short="o", widget=OUT_DIR,
        help="The directory in which to save the files that this "
             "command writes")


def _write_listings(params, action, name, **kwargs):
    """Perform action, which returns a directory listing, on each
    appliance in params and write each listing to a file in out_dir."""
    logger = make_logger("mast.system")
    t = Timestamp()
    env = params.environment()
    logger.info("Attempting {} on {}".format(action, str(env.appliances)))
    resp = env.perform_action(action, **kwargs)
    logger.debug("Response received: {}".format(resp))

    out_dir = os.path.join(params.out_dir, t.timestamp)
    os.makedirs(out_dir)

    for host, r in resp.items():
        filename = os.path.join(out_dir, "{}-{}.xml".format(host, name))
        msg = "Writing directory listing of {} to {}".format(host, filename)
        logger.info(msg)
        say(msg)
        # pretty is bytes
        with open(filename, 'wb') as fout:
            fout.write(r.pretty)
    return SeeDownload(resp, env, suffix=action)


@cli.command('get-encrypted-filesystem', effect="read",
             group='file management')
def get_encrypted_filesystem(params: OutDirParams):
    """This will get a directory listing of all locations within the
encrypted filesystem. The output will be in the form of xml files saved
into `-o, --out-dir`."""
    return _write_listings(
        params, "get_encrypted_filesystem", "encrypted-filesystem")


@cli.command('get-temporary-filesystem', effect="read",
             group='file management')
def get_temporary_filesystem(params: OutDirParams):
    """This will get a directory listing of all locations within the
temporary filesystem. The output will be in the form of xml files saved
into `-o, --out-dir`."""
    return _write_listings(
        params, "get_temporary_filesystem", "temporary-filesystem")


class GetFilestore(ApplianceParams):
    Domain: str = Option(
        "", short="D", widget=configured("Domain"),
        help="The domain from which to get the filestore")
    location: str = Option(
        "local:", short="l",
        help="The location within the DataPower filesystem (`cert:`, "
             "`local:`, etc.)")
    out_dir: str = Option(
        "tmp", short="o", widget=OUT_DIR,
        help="The directory in which to output the results of the "
             "filesystem audit")


@cli.command('get-filestore', effect="read", group='file management')
def get_filestore(params: GetFilestore):
    """This will get the directory listing of the specified location.
The output will be in the form of xml files saved into `-o, --out-dir`."""
    return _write_listings(
        params, "get_filestore", "get-filestore",
        domain=params.Domain, location=params.location)


def _succeeded(responses, env, suffix=None, good="Success", bad="Failed"):
    return StatusResults(
        {host: good if response else bad
         for host, response in responses.items()},
        env, suffix=suffix)


class CopyFile(ApplianceParams):
    Domain: str = Option(
        "", short="D", widget=configured("Domain"),
        help="The domain for both src and dst")
    src: str = Option(
        "", short="s",
        help="The path to the source file (on the appliance(s))")
    dst: str = Option(
        "", short="d",
        help="The destination of the copied file (on the appliance(s))")
    overwrite: bool = Option(
        True,
        help="Give `--no-overwrite` to leave dst as it is if it "
             "already exists")


@cli.command('copy-file', effect="change", group='file management')
def copy_file(params: CopyFile):
    """Copies a file from src to dst (both src and dst are on the appliance)
optionally overwriting dst."""
    logger = make_logger("mast.system")
    env = params.environment()

    resp = {}
    for appliance in env.appliances:
        logger.info("Attempting to copy file on {} from {} to {}".format(
            appliance.hostname, params.src, params.dst))
        fin = appliance.getfile(domain=params.Domain, filename=params.src)
        fout = base64.encodebytes(fin)
        resp[appliance.hostname] = appliance._set_file(
            fout, params.dst, params.Domain, params.overwrite)
        logger.debug("Response received: {}".format(resp[appliance.hostname]))
    return _succeeded(resp, env)


class SetFile(ApplianceParams):
    file_in: str = Option(
        short="f", widget=UPLOAD,
        help="The path and filename of the file to upload")
    destination: str = Option(
        short="d",
        help="If a location or directory is provided filename will be "
             "as it appears on the local machine, if a path and "
             "filename is provided filename will be as provided")
    Domain: str = Option(
        'default', short="D", widget=configured("Domain"),
        help="The domain to which to upload the file")
    overwrite: bool = Option(
        True,
        help="Give `--no-overwrite` to fail rather than overwrite the "
             "destination if it exists")


@cli.command('set-file', effect="change", group='file management')
def set_file(params: SetFile):
    """Uploads a file to the specified appliances"""
    env = params.environment()
    resp = env.perform_async_action(
        'set_file',
        file_in=params.file_in,
        file_out=params.destination,
        domain=params.Domain,
        overwrite=params.overwrite)
    return _succeeded(resp, env, suffix="set_file", good="Succeeded")


class GetFile(ApplianceParams):
    location: str = Option(
        short="l",
        help="The location of the file (on DataPower) you would like "
             "to get")
    Domain: str = Option(
        'default', short="D", widget=configured("Domain"),
        help="The domain from which to get the file")
    out_dir: str = Option(
        'tmp', short="o", widget=OUT_DIR,
        help="The directory you would like to save the file to")


@cli.command('get-file', effect="read", group='file management')
def get_file(params: GetFile):
    """Retrieves a file from the specified appliances"""
    t = Timestamp()
    env = params.environment()
    responses = env.perform_async_action(
        'getfile', domain=params.Domain, filename=params.location)

    os.makedirs(params.out_dir, exist_ok=True)

    for hostname, fin in responses.items():
        filename = params.location.split('/')[-1]
        filename = os.path.join(
            params.out_dir,
            '%s-%s-%s' % (hostname, t.timestamp, filename))
        with open(filename, 'wb') as fout:
            fout.write(fin)
        say("Wrote {} from {} to {}".format(
            params.location, hostname, filename))
    return SeeDownload(responses, env, suffix="get_file")


class DeleteFile(ApplianceParams):
    Domain: str = Option(
        "", short="D", widget=configured("Domain"),
        help="The domain from which to delete the file")
    filename: str = Option(
        "", short="f",
        help="The filename of the file to delete **Must include the "
             "full path**")
    backup: bool = Option(
        False, short="b",
        help="If specified, the file will be backed up to the "
             "directory specified as `-o, --out-dir`")
    out_dir: str = Option(
        BACKUPS_DIR, short="o", widget=OUT_DIR,
        help="If `-b, --backup` is specified, this is where to save "
             "the backup of the file to.")


@cli.command('del-file', effect="change", group="file management")
def delete_file(params: DeleteFile):
    """Deletes a file from the specified appliance(s) optionally backing
up the file locally."""
    env = params.environment()
    if params.backup:
        resp = {}
        for appliance in env.appliances:
            _out_dir = os.path.join(params.out_dir, appliance.hostname)
            os.makedirs(_out_dir, exist_ok=True)
            resp[appliance.hostname] = appliance.del_file(
                filename=params.filename, domain=params.Domain,
                backup=True, local_dir=_out_dir)
    else:
        resp = env.perform_action(
            "del_file", filename=params.filename, domain=params.Domain)
    return _succeeded(resp, env, bad="Error")


@cli.command('generate-error-report', effect="change", group='error-reports')
def generate_error_report(params: ApplianceParams):
    """This will generate an error report on the specified
appliance(s)."""
    env = params.environment()
    report = Report(env)
    responses = {}
    for appliance in env.appliances:
        msg = "Generating Error report on {}".format(appliance.hostname)
        say(msg)
        appliance.log_info(msg)
        resp = appliance.ErrorReport()
        responses[appliance.hostname] = resp
        if resp:
            msg = "Successfully generated error report on {}".format(
                appliance.hostname)
            say("\t", msg)
            appliance.log_info(msg)
        else:
            msg = "An error occurred generating error report on {}: \n{}".format(
                appliance.hostname, resp)
            say("\t", msg)
            appliance.log_error(msg)
    report.add(ActionResults(responses), show=False)
    return report


class GetErrorReports(ApplianceParams):
    out_dir: str = Option(
        "tmp", short="o", widget=OUT_DIR,
        help="The directory to save the error reports in")
    decompress: bool = Option(
        False, short="d",
        help='If specified, the error reports will be decompressed '
             'from the ".gz" encoding')


@cli.command('get-error-reports', effect="read", group='error-reports')
def get_error_reports(params: GetErrorReports):
    """This will attempt to retireve any error reports from the
currently configured location on the specified appliance(s)."""
    env = params.environment()
    out_dir = params.out_dir

    for appliance in env.appliances:
        os.makedirs(os.path.join(out_dir, appliance.hostname), exist_ok=True)
    ers = _pmr_get_error_report_settings(env.appliances)
    _pmr_download_error_reports(env.appliances, out_dir, ers, "")
    if params.decompress:
        filenames = []
        for _dir in os.listdir(out_dir):
            filenames.extend(
                os.path.join(out_dir, _dir, x)
                for x in os.listdir(os.path.join(out_dir, _dir)))
        for filename in filenames:
            if not filename.endswith(".gz"):
                continue
            new_filename = filename[:-len(".gz")]
            say("Decompress: {} -> {}".format(filename, new_filename))
            with gzip.open(filename, "rb") as fin, \
                    open(new_filename, "wb") as fout:
                shutil.copyfileobj(fin, fout)
            os.remove(os.path.abspath(filename))
            say("\tDone")
    return SeeDownload(
        [appliance.hostname for appliance in env.appliances],
        env, suffix="get_error_reports")


class CopyDirectory(ApplianceParams):
    location: str = Option(
        "", short="l",
        help="The filesystem and/or directory name to copy.")
    out_dir: str = Option(
        "tmp", short="o", widget=OUT_DIR,
        help="The local directory into which to copy the files from "
             "the appliance(s)")
    Domain: str = Option(
        "", short="D", widget=configured("Domain"),
        help="The domain from which to copy the files")
    recursive: bool = Option(
        False, short="r",
        help="If specified, files will be copied recursively from the "
             "directory specified by `-l, --location`")


@cli.command('copy-directory', effect="read", group='file management')
def copy_directory(params: CopyDirectory):
    """This will get all of the files from a directory on the appliances
in the specified domain."""
    t = Timestamp()
    env = params.environment()

    for appliance in env.appliances:
        _out_dir = os.path.join(
            params.out_dir, t.timestamp, appliance.hostname)
        os.makedirs(_out_dir, exist_ok=True)
        appliance.copy_directory(
            params.location, _out_dir, params.Domain,
            recursive=params.recursive)
        say("Copied {} from {} to {}".format(
            params.location, appliance.hostname, _out_dir))
    return SeeDownload(
        [appliance.hostname for appliance in env.appliances],
        env, suffix="copy_directory")


class CreateDir(ApplianceParams):
    Domain: str = Option(
        "default", short="D", widget=configured("Domain"),
        help="The domain in which to create the directory")
    directory: str = Option(
        "", short="d",
        help="The directory to create. While the location needs to "
             "exists, parent directories do not, will act like "
             "`mkdir -p`")


@cli.command("create-dir", effect="change", group="file management")
def create_dir(params: CreateDir):
    """Creates a directory in the specified domain. **NOTE** the
parent directory does not need to exist it will act like `mkdir -p`."""
    return _action(
        params, 'CreateDir', "create-directory",
        "create directory {} in {}".format(params.directory, params.Domain),
        domain=params.Domain, Dir=params.directory)
#
# ~#~#~#~#~#~#~#

# ~#~#~#~#~#~#~#
# auditing
# ========
#
# These functions are meant to help with accountability. They invlove
# log files as well as object auditing and gathering information necessary
# in order to submit a PMR
#
# current commands
# ----------------
# fetch-logs - Retrieves a copy of all log files on the appliance
# get-pmr-info - Retrieves the information necessary to submit a PMR
# object-audit - Retrieves a diff of the running and persisted configurations

@cli.command('fetch-logs', effect="read", group='auditing')
def fetch_logs(params: OutDirParams):
    """Fetch all log files from the specified appliance(s) storing them in
out-dir."""
    env = params.environment()
    resp = env.perform_async_action('get_all_logs', log_dir=params.out_dir)
    say("Saved the logs of {} in {}".format(
        str(env.appliances), params.out_dir))
    return SeeDownload(resp, env, suffix="fetch_logs")


@cli.command('get-pmr-info', effect="change", group='auditing')
def get_pmr_info(params: OutDirParams):
    """Get all posible troubleshooting information from the
specified appliances.

Implementation:

This script will perform the following actions in this order:

1. Create a directory structure on the local machine under `-o, --out-dir`
to store the artifacts
2. Retrieve the appliance(s) ErrorReportSettings
3. If the appliance(s) are not configured to save internal state on error
report generation, the internal state will be saved
4. An error report will be generated
5. A backup of all domains will be taken
6. A number of StatusProviders will be queried in both the default
domain and each application domain. See `$MAST_HOME/etc/statusProviders.txt`
and `$MAST_HOME/etc/statusProviders-applicationDomains.txt` to see which
providers will be queried.
7. All error reports present on the appliance(s) will be copied
8. A zipfile will be created per appliance containing all the artifacts
generated by this script and the files will be cleaned up (locally)."""
    env = params.environment()
    out_dir = params.out_dir

    t = Timestamp()
    _pmr_create_dirs(env.appliances, out_dir, t.timestamp)
    ers = _pmr_get_error_report_settings(env.appliances)
    _pmr_conditionally_save_internal_state(env.appliances, ers, t.timestamp)
    _pmr_generate_error_reports(env.appliances)
    _pmr_backup_all_domains(env.appliances, out_dir, t.timestamp)
    _pmr_query_status_providers(env.appliances, out_dir, t.timestamp)
    _pmr_download_error_reports(env.appliances, out_dir, ers, t.timestamp)
    _pmr_cleanup(env.appliances, out_dir, t.timestamp)
    say("Saved the PMR information of {} in {}".format(
        str(env.appliances), out_dir))
    return SeeDownload(
        [appliance.hostname for appliance in env.appliances],
        env, suffix="get_pmr_info")


@cli.command('object-audit', effect="read", group='auditing')
def objects_audit(params: OutDirParams):
    """Get a "diff" of the current and persisted configuration
from all-domains."""
    t = Timestamp()
    env = params.environment()
    results = env.perform_async_action('object_audit')

    for hostname, audit in results.items():
        filename = os.path.join(
            params.out_dir, hostname, 'object_audit', t.timestamp)
        os.makedirs(filename)
        filename = os.path.join(filename, 'object-audit.xml')
        with open(filename, 'w') as fout:
            fout.write(audit)
        say("Wrote the object audit of {} to {}".format(hostname, filename))
    return SeeDownload(results, env, suffix="object_audit")


def _leaves(element, prefix=""):
    """Yield (name, text) for each element below element that has no
    children. The name is the path to it, joined with dots."""
    for child in element:
        if len(child):
            for leaf in _leaves(child, prefix + child.tag + "."):
                yield leaf
        else:
            yield prefix + child.tag, child.text or ""


class XMLResults(Result):
    """
    The status or configuration that each appliance returned. The
    command line prints the XML. The web GUI shows one row for each
    value.

    * `kind`: "status" or "config"
    * `machine`: If True, the XML is printed with the whitespace
    collapsed
    """
    def __init__(self, kind, env=None, machine=False, suffix=None):
        self.kind = kind
        self.env = env
        self.machine = machine
        self.suffix = suffix
        self.sections = []

    def add(self, responses, label, timestamp):
        for hostname, response in responses.items():
            self.sections.append((hostname, label, timestamp, response))

    @property
    def history(self):
        return history(self.env) if self.env is not None else ""

    def text(self):
        return "".join(
            "\n\n{} - {} - {}\n\n{}\n".format(
                hostname, label, timestamp,
                repr(response) if self.machine else str(response))
            for hostname, label, timestamp, response in self.sections)

    def html(self):
        return self.table().html()

    def data(self):
        return self.table().data()

    def table(self):
        config = self.kind == "config"
        if config:
            header = ["Appliance", "ObjectClass", "ObjectName", "Key", "Value"]
            xpath = datapower.CONFIG_XPATH
        else:
            header = ["Appliance", "Provider", "Key", "Value"]
            xpath = datapower.STATUS_XPATH
        rows = []
        for hostname, label, timestamp, response in self.sections:
            if self.suffix:
                hostname = "{}-{}".format(hostname, self.suffix)
            for item in response.xml.findall(xpath):
                first = [hostname, item.tag]
                if config:
                    first.append(item.get("name") or "")
                rows.append(first)
                for name, value in _leaves(item):
                    rows.append([""] * len(first) + [name, value])
        return Table(header, rows)


def _deliver(result, out_file, env):
    """Return result so that it is printed, or write it to out_file
    if there is one."""
    if out_file is None:
        return result
    directory = os.path.dirname(out_file)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(out_file, "w") as fout:
        fout.write(result.text())
    say("Wrote the results to {}".format(out_file))
    report = Report()
    report.add(result, show=False)
    return report


class GetStatus(ApplianceParams):
    StatusProvider: list[str] = Option(
        default_factory=list, short="S", widget=STATUS_PROVIDER,
        help="Status providers to query.")
    Domain: str = Option(
        'default', short="D", widget=configured("Domain"),
        help="The domain from which to query the status providers")
    out_file: str | None = Option(
        None, short="o", widget=OUT_FILE,
        help="The file to write the results to. Without it the "
             "results are printed.")
    machine: bool = Option(
        False, short="m",
        help="If specified, the xml will be written with whitespace "
             "collapsed and newlines removed")


@cli.command('get-status', effect="read", group='auditing', exclusive=False)
def get_status(params: GetStatus):
    """This will query the status of the specified appliances in
in the specified Domain for the specified StatusProviders."""
    env = params.environment()
    result = XMLResults(
        "status", env, machine=params.machine, suffix="get_status")
    for provider in params.StatusProvider:
        t = Timestamp()
        result.add(
            env.perform_async_action(
                'get_status', provider=provider, domain=params.Domain),
            provider, t.timestamp)
    return _deliver(result, params.out_file, env)


class GetConfig(ApplianceParams):
    ObjectClass: str = Option(
        "", short="O", widget=OBJECT_CLASS,
        help="The class of the object whose config you wish to get")
    obj_name: str | None = Option(
        None, short="o",
        help="If given, the configuration will be gotten for that "
             "object, otherwise, all objects of class ObjectClass will "
             "be provided")
    recursive: bool = Option(
        False, short="r",
        help="If specified, the configuration will be retrieved "
             "recursively")
    persisted: bool = Option(
        False, short="p",
        help="If specified, the persisted configuration will be "
             "retrieved as opposed to the running configuration")
    Domain: str = Option(
        'default', short="D", widget=configured("Domain"),
        help="The domain from which to get the configuration")
    out_file: str | None = Option(
        None, widget=OUT_FILE,
        help="The file to write the results to. Without it the "
             "results are printed.")
    machine: bool = Option(
        False, short="m",
        help="If specified, the xml will be written with whitespace "
             "collapsed and newlines removed")


@cli.command('get-config', effect="read", group='auditing', exclusive=False)
def get_config(params: GetConfig):
    """This will get the config of obj_name from the specified
domain on the specified appliances."""
    t = Timestamp()
    env = params.environment()
    result = XMLResults(
        "config", env, machine=params.machine, suffix="get_config")
    result.add(
        env.perform_async_action(
            'get_config',
            _class=params.ObjectClass,
            name=params.obj_name,
            recursive=params.recursive,
            persisted=params.persisted,
            domain=params.Domain),
        params.ObjectClass, t.timestamp)
    return _deliver(result, params.out_file, env)


class ListObjects(ApplianceParams):
    ObjectClass: str = Option(
        short="O", widget=OBJECT_CLASS,
        help="The class of the objects, for example Domain or "
             "XMLFirewallService")
    Domain: str = Option(
        'default', short="D", widget=configured("Domain"),
        help="The domain whose objects to list")


# The columns of the result of list-objects. The web UI reads the first
# two to fill its lists (mast.web.api.choices).
OBJECTS_HEADER = ("Name", "On every appliance", "Appliances")


@cli.command('list-objects', effect="read", group='auditing', exclusive=False)
def list_objects(params: ListObjects):
    """Lists the objects of one class on the specified appliances, and
whether every appliance has each one. The web UI fills its lists of
objects (the domains, for example) with this command."""
    env = params.environment()
    found = {}
    for appliance in env.appliances:
        response = appliance.get_status("ObjectStatus", domain=params.Domain)
        names = set()
        for node in response.xml.findall(
                datapower.STATUS_XPATH + "ObjectStatus"):
            _class, name = node.find("Class"), node.find("Name")
            if _class is not None and name is not None \
                    and _class.text == params.ObjectClass:
                names.add(name.text)
        found[appliance.hostname] = names
    every = set.intersection(*found.values()) if found else set()
    rows = [(name, "yes" if name in every else "no",
             ", ".join(host for host in found if name in found[host]))
            for name in sorted(set().union(*found.values()))]
    return Table(OBJECTS_HEADER, rows, history=history(env))
#
# ~#~#~#~#~#~#~#

# ~#~#~#~#~#~#~#
# maintenance
# ===========
#
# These functions are meant to perform routine maintenance on the specified
# appliances
#
# current commands
# ----------------
# clean-up - Cleans the filesystem.

class CleanUp(ApplianceParams):
    Domain: str = Option(
        'default', short="D", widget=configured("Domain"),
        help="The domain whose filesystem you would like to clean up")
    checkpoints: bool = Option(
        False, short="C",
        help="If specified, all checkpoints will be removed from the "
             "domain")
    export: bool = Option(
        False, short="e",
        help="If specified all exports will be removed from the domain")
    logtemp: bool = Option(
        False, short="l",
        help="If specified, all files in `logtemp:` will be removed "
             "from the domain")
    logstore: bool = Option(
        False, short="L",
        help="If specified, all files in `logstore:` will be removed")
    error_reports: bool = Option(
        False, short="E",
        help="If specified, all error reports will be removed from the "
             "appliance(s)")
    recursive: bool = Option(
        False, short="r",
        help="If specified, directories will be cleaned recursively")
    backup_files: bool = Option(
        False, short="b",
        help="If specified, files will be backed up before deleting")
    out_dir: str = Option(
        BACKUPS_DIR, short="o", widget=OUT_DIR,
        help="The directory to save backed up files")


@cli.command('clean-up', effect="change", group='maintenance')
def clean_up(params: CleanUp):
    """This will clean up the specified appliances filesystem optionally
taking copies of the files as backups."""
    env = params.environment()

    t = Timestamp()
    dirs = []
    if params.checkpoints:
        dirs.append('chkpoints:/')
    if params.export:
        dirs.append('export:/')
    if params.logtemp:
        dirs.append('logtemp:/')
    if params.logstore:
        dirs.append('logstore:/')

    rows = []
    for appliance in env.appliances:
        for _dir in dirs:
            _clean_dir(
                appliance,
                _dir,
                params.Domain,
                params.recursive,
                params.backup_files,
                t.timestamp,
                params.out_dir)
            rows.append((appliance.hostname, _dir, "Cleaned"))
        if params.error_reports:
            _clean_error_reports(
                appliance, params.Domain,
                params.backup_files, t.timestamp,
                params.out_dir)
            rows.append((appliance.hostname, "ErrorReports", "Cleaned"))
    return Table(["Appliance", "Location", "Action"], rows, history(env))


def _clean_dir(appliance, _dir, domain, recursive, backup, timestamp, out_dir):
    if backup:
        local_dir = os.path.sep.join(
            os.path.sep.join(_dir.split(':/')).split('/'))
        local_dir = os.path.join(
            out_dir,
            appliance.hostname,
            timestamp,
            domain,
            local_dir)
        os.makedirs(local_dir, exist_ok=True)
    # if not recursive don't include_directories
    files = appliance.ls(_dir, domain=domain, include_directories=recursive)
    for file in files:
        if "diag-log" in file and "." not in file:
            continue
        if ':/' in file:
            _clean_dir(
                appliance,
                file.rstrip("/"),
                domain,
                recursive,
                backup,
                timestamp,
                out_dir)
        else:
            filename = '{}/{}'.format(_dir, file)
            if backup:
                fout = open(os.path.join(local_dir, file), 'wb')
                contents = appliance.getfile(domain, filename)
                fout.write(contents)
                fout.close
            appliance.DeleteFile(domain=domain, File=filename)


def _clean_error_reports(appliance, domain, backup, timestamp, out_dir):
    protocol_xpath = datapower.CONFIG_XPATH + "/ErrorReportSettings/Protocol"
    raid_path_xpath = datapower.CONFIG_XPATH + "/ErrorReportSettings/RaidPath"

    if backup:
        local_dir = os.path.join(
            out_dir,
            appliance.hostname,
            timestamp,
            domain,
            'temporary')
        os.makedirs(local_dir, exist_ok=True)
    ers = appliance.get_config("ErrorReportSettings")
    protocol = ers.xml.find(protocol_xpath).text

    if protocol == 'temporary':
        path = 'temporary:'
        filestore = appliance.get_filestore('default', path)
        _dir = filestore.xml.find('.//location[@name="%s"]' % (path))

    elif protocol == 'raid':
        try:
            path = ers.xml.find(raid_path_xpath).text
        except AttributeError:
            path = ''
        path = "{}/{}".format(appliance.raid_directory, path)
        path = path.rstrip("/")

        filestore = appliance.get_filestore('default', 'local:')
        _dir = filestore.xml.find('.//directory[@name="%s"]' % (path))

    else:
        appliance.log_warn(''.join(
                ('\tThe failure notification looks like it is set for ',
                 protocol,
                 ', which we do not currently support. Failing back',
                 'to temporary:...\n')))
        path = 'temporary:'
        filestore = appliance.get_filestore('default', path)
        _dir = filestore.xml.find('.//location[@name="%s"]' % (path))

    if not len(_dir):
        appliance.log_warn("There were no error reports found.")
        return

    files = []
    for node in _dir.findall('.//*'):
        if node.tag == "file" and 'error-report' in node.get('name'):
            files.append(node.get('name'))

    for f in files:
        fqp = '%s/%s' % (path, f)
        filename = '%s-%s' % (appliance.hostname, f)
        if backup:
            local_dir = os.path.join(
                out_dir,
                appliance.hostname,
                timestamp,
                domain,
                path.replace(":", "").replace("/", os.path.sep))
            if not os.path.exists(local_dir):
                os.makedirs(local_dir)
            filename = os.path.join(local_dir, filename)
            with open(filename, 'wb') as fout:
                fout.write(appliance.getfile('default', fqp))
        appliance.DeleteFile(domain="default", File=fqp)

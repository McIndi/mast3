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
mast deployment

A set of utilities which can simplify, automate and audit
your DataPower service deployments and migrations.
"""
import os
import subprocess
from importlib.resources import files
from time import sleep
from typing import Literal
from pydantic import model_validator
from mast.cli import Cli, Option, Table, say
from mast.datapower.deployment.git_deploy import git_deploy
from mast.plugin_utils.params import (
    ApplianceParams, OUT_DIR, UPLOAD, configured, timeout_option)
from mast.plugin_utils.results import Report, SeeDownload, StatusResults
from mast.plugins.web import Plugin
from mast.timestamp import Timestamp
from mast.logging import make_logger, logged
from functools import partial, update_wrapper
import mast.plugin_utils.plugin_functions as pf

cli = Cli(namespace="deployment")


def system_call(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        shell=False):
    """
    # system_call

    helper function to shell out commands. This should be platform
    agnostic.
    """
    stderr = subprocess.STDOUT
    pipe = subprocess.Popen(
        command,
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        shell=shell)
    stdout, stderr = pipe.communicate()
    return stdout, stderr


class SetFile(ApplianceParams):
    file_in: str = Option(
        short="f", widget=UPLOAD,
        help="The path and filename of the file to upload")
    destination: str = Option(
        short="d",
        help="Should be the path and filename of the file once "
             "uploaded to the DataPower **NOTE: file_out should "
             "contain the filename ie. local:/test.txt**")
    Domain: str = Option(
        'default', short="D", widget=configured("Domain"),
        help="The domain to which to upload the file")
    overwrite: bool = Option(
        True,
        help="If specified this program will exit with an error "
             "rather than overwrite a file")


@logged('mast.datapower.deployment')
@cli.command('set-file', category='file management')
def set_file(params: SetFile):
    """Uploads a file to the specified appliances"""
    env = params.environment()
    kwargs = {
        'file_in': params.file_in,
        'file_out': params.destination,
        'domain': params.Domain,
        'overwrite': params.overwrite}
    resp = env.perform_async_action('set_file', **kwargs)
    return StatusResults(
        {host: "Succeeded" if r else "Failed" for host, r in resp.items()},
        env, suffix="set_file")


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


@logged('mast.datapower.deployment')
@cli.command('get-file', category='file management')
def get_file(params: GetFile):
    """Retrieves a file from the specified appliances"""
    t = Timestamp()
    env = params.environment()
    kwargs = {'domain': params.Domain, 'filename': params.location}
    responses = env.perform_async_action('getfile', **kwargs)

    os.makedirs(params.out_dir, exist_ok=True)

    for hostname, fin in list(responses.items()):
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
        help="The name of the file (on DataPower) you would like to "
             "delete")
    backup: bool = Option(
        False, short="b",
        help="Whether to backup the file before deleting")
    out_dir: str = Option(
        "tmp", short="o", widget=OUT_DIR,
        help="The directory you would like to save the file to")


@logged('mast.datapower.deployment')
@cli.command('del-file', category="file management")
def delete_file(params: DeleteFile):
    """Deletes a file from the specified appliances"""
    env = params.environment()
    if params.backup:
        resp = {}
        for appliance in env.appliances:
            _out_dir = os.path.join(params.out_dir, appliance.hostname)
            if not os.path.exists(_out_dir):
                os.makedirs(_out_dir)
            resp[appliance.hostname] = appliance.del_file(
                filename=params.filename, domain=params.Domain,
                backup=True, local_dir=_out_dir)
    else:
        resp = env.perform_action(
            "del_file", filename=params.filename, domain=params.Domain)
    return StatusResults(
        {host: "Success" if r else "Error" for host, r in resp.items()}, env)


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
    error_reports: bool = Option(
        False, short="E",
        help="If specified, all error reports will be removed from the "
             "appliance(s)")
    recursive: bool = Option(
        False, short="r",
        help="If specified, directories will be cleaned recursively")
    logtemp: bool = Option(
        False, short="l",
        help="If specified, all files in `logtemp:` will be removed "
             "from the domain")
    logstore: bool = Option(
        False, short="L",
        help="If specified, all files in `logstore:` will be removed")
    backup_files: bool = Option(
        True,
        help="If specified, files will not be backed up before "
             "deleting")
    out_dir: str = Option(
        'tmp', short="o", widget=OUT_DIR,
        help="The directory to save backed up files")


@logged('mast.datapower.deployment')
@cli.command('clean-up', category='maintenance')
def clean_up(params: CleanUp):
    """This will clean up the specified appliances filesystem optionally
(defaults to True) taking copies of the files as backups."""
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
        rows.append((appliance.hostname, ))
        for _dir in dirs:
            _clean_dir(
                appliance,
                _dir,
                params.Domain,
                params.recursive,
                params.backup_files,
                t.timestamp,
                params.out_dir)
            rows.append(("", _dir, "Cleaned"))
        if params.error_reports:
            _clean_error_reports(
                appliance, params.Domain,
                params.backup_files, t.timestamp,
                params.out_dir)
            rows.append(("", "ErrorReports", "Cleaned"))
    report = Report(env)
    report.add(Table(["Appliance", "Location", "Action"], rows))
    return report


@logged('mast.datapower.deployment')
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
        os.makedirs(local_dir)
    # if not recursive don't include_directories
    files = appliance.ls(_dir, domain=domain, include_directories=recursive)
    for file in files:
        if "diag-log" in file and "." not in file:
            continue
        if ':/' in file:
            _clean_dir(
                appliance,
                file,
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


@logged('mast.datapower.deployment')
def _clean_error_reports(appliance, domain, backup, timestamp, out_dir):
    if backup:
        local_dir = os.path.join(
            out_dir,
            appliance.hostname,
            timestamp,
            domain,
            'temporary')
        os.makedirs(local_dir)
    files = appliance.ls(
        'temporary:/',
        domain=domain,
        include_directories=False)
    files = [f for f in files if 'error-report' in f]
    for _file in files:
        filename = 'temporary:/{}'.format(_file)
        if backup:
            fout = open(os.path.join(local_dir, _file), 'wb')
            contents = appliance.getfile(domain, filename)
            fout.write(contents)
            fout.close
        appliance.DeleteFile(domain=domain, File=filename)


class Predeploy(ApplianceParams):
    out_dir: str = Option(
        "tmp", short="o", widget=OUT_DIR,
        help="The directory (local) where you would like to store the "
             "artifacts generated by this script")
    Domain: str = Option(
        "", short="D", widget=configured("Domain"),
        help="This is the app domain to backup and the app domain in "
             "which to set a checkpoint")
    comment: str = Option(
        "", short="C",
        help="This is shared among other actions. The comment is used "
             "to build the name of the checkpoint.")
    predeploy_command: str | None = Option(
        None, short="p",
        help='This command will be "shelled out" to the machine '
             "running MAST after performing the backups and "
             "checkpoints. Use this parameter to pull from version "
             "control or similar operations.")
    CryptoCertificate: str = Option(
        "", widget=configured("CryptoCertificate"),
        help="The CryptoCertificate with which to encrypt the secure "
             "backup")
    secure_backup_destination: str = Option(
        "local:/raid0", short="s",
        help="The destination (on the DataPower) where the secure "
             "backup will be stored")
    backup_default: bool = Option(
        True,
        help="If specified, the default domain will not be backed up")
    backup_all: bool = Option(
        True, help="If specified, all-domains will not be backed up")
    do_secure_backup: bool = Option(
        False, short="d",
        help="Whether to retrieve a secure backup. This needs "
             "--CryptoCertificate")
    do_normal_backup: bool = Option(
        True, help="If specified, no normal backups will be retrieved")
    set_checkpoints: bool = Option(
        True, help="If specified, no checkpoints will be set")
    include_iscsi: bool = Option(
        False, short="i",
        help="Whether to include the iscsi volume in the secure backup")
    include_raid: bool = Option(
        False, short="I",
        help="Whether to include the raid volume in the secure backup")
    remove_secure_backup: bool = Option(
        True,
        help="If specified, the secure backup will not be removed from "
             "the appliance after verifying your local copy.")
    default_checkpoint: bool = Option(
        True,
        help="If specified, no checkpoint will be created in the "
             "default domain")
    remove_oldest_checkpoint: bool = Option(
        True,
        help="If specified, the oldest checkpoint will not be removed "
             "from the domain when the maximum number of checkpoints "
             "has been reached.")
    allow_shell: bool = Option(
        False, short="A",
        help="Whether to allocate a shell for the execution of the "
             "predeploy-command")

    @model_validator(mode="after")
    def _secure_backup_needs_a_certificate(self):
        if self.do_secure_backup and not self.CryptoCertificate:
            raise ValueError(
                "CryptoCertificate must be specified in order to "
                "perform a secure backup")
        return self


@logged('mast.datapower.deployment')
@cli.command('predeploy', category='deployment')
def predeploy(params: Predeploy):
    """Perform routine pre-deployment actions. Everything is optional, but if
you wish to perform an action, you must provide the necessary arguments.

Here is what is possible (will be done in this order):

1. Secure Backup
    * The following parameters apply:
        * `-d, --do-secure-backup`
        * `-o, --out-dir`
        * `--CryptoCertificate`
        * `-s, --secure-backup-destination`
        * `-i, --include-iscsi`
        * `-I, --include-raid`
        * `--no-remove-secure-backup`
2. Normal Backups
    * The following parameters apply:
        * `--no-do-normal-backup`
        * `-o, --out-dir`
        * `-D, --Domain`
        * `--no-backup-default`
        * `--no-backup-all`
        * `-C, --comment`
3. Checkpoints
    * The following parameters apply:
        * `--no-set-checkpoints`
        * `-D, --Domain`
        * `-C, --comment`
        * `--no-default-checkpoint`
        * `--no-remove-oldest-checkpoint`"""
    from mast.datapower.backups import set_checkpoint
    from mast.datapower.backups import get_normal_backup, get_secure_backup

    logger = make_logger('mast.datapower.deployment')
    env = params.environment()
    # Each step is the result of another command, with its own history
    report = Report()

    # Loop through appliances so we will only affect one appliance at a time
    for appliance in env.appliances:

        # Secure Backup
        if params.do_secure_backup:
            logger.info("Starting Secure Backup for {}".format(
                appliance.hostname))

            report.add(get_secure_backup(
                appliances=appliance.hostname,
                credentials=appliance.credentials,
                timeout=params.timeout,
                out_dir=params.out_dir,
                CryptoCertificate=params.CryptoCertificate,
                destination=params.secure_backup_destination,
                include_iscsi=params.include_iscsi,
                include_raid=params.include_raid,
                remove=params.remove_secure_backup,
                quiesce_before=False,
                unquiesce_after=False,
                no_check_hostname=params.no_check_hostname))

            logger.info("Finished Secure Backup for {}".format(
                appliance.hostname))

        # Normal backups
        if params.do_normal_backup:
            logger.info(
                "Pre-Deployment backups started at {}".format(
                    str(Timestamp())))

            domains = [params.Domain]
            if params.backup_default:
                domains.append("default")
            if params.backup_all:
                domains.append("all-domains")

            report.add(get_normal_backup(
                appliances=[appliance.hostname],
                credentials=params.credentials,
                timeout=params.timeout,
                no_check_hostname=params.no_check_hostname,
                Domain=domains,
                comment=params.comment,
                out_dir=params.out_dir))

            logger.info(
                "Pre-Deployment backups finished at {}".format(
                    str(Timestamp())))

        # Checkpoints
        if params.set_checkpoints:
            logger.info(
                "Pre-Deployment checkpoints started at {}".format(
                    str(Timestamp())))

            domains = [params.Domain]
            if params.default_checkpoint:
                domains.append("default")

            report.add(set_checkpoint(
                appliances=[appliance.hostname],
                credentials=params.credentials,
                timeout=params.timeout,
                no_check_hostname=params.no_check_hostname,
                Domain=domains,
                comment=params.comment,
                remove_oldest=params.remove_oldest_checkpoint))

            logger.info(
                "Pre-Deployment checkpoints finished at {}".format(
                    str(Timestamp())))

    if params.predeploy_command:
        logger.info(
            "Pre-Deployment command '{}' found. Executing at {}".format(
                params.predeploy_command, str(Timestamp())))

        out, err = system_call(
            command=params.predeploy_command, shell=params.allow_shell)
        out = str(out)
        err = str(err)

        logger.info(
            "finished executing Pre-Deployment command '{}' at {}., output: {}".format(
                params.predeploy_command, str(Timestamp()), ";".join([out, err])))
        say("Finished running pre-deploy command. output: {}".format(
            ";".join([out, err])))
        report.add(StatusResults({
            "predeploy command": "{}\n\nout: {}\n\nerr: {}".format(
                params.predeploy_command, out, err)}), show=False)

    return report


class Deploy(ApplianceParams):
    timeout: int = timeout_option(180)
    Domain: list[str] = Option(
        default_factory=list, short="D", widget=configured("Domain"),
        help="The domain to import the configuration into, also the "
             "domain to quiesce if `--no-quiesce-domain` is not "
             "specified.")
    file_in: str = Option(
        short="f", widget=UPLOAD,
        help="This is the configuration file that you are importing. "
             "It must be in the format specified by the format "
             "parameter.")
    deployment_policy: str = Option(
        "", short="d",
        help="The deployment policy to apply to the import, must "
             "already exist on the appliance")
    dry_run: bool = Option(
        False,
        help="If specified, a dry-run will be performed instead of an "
             "actual import")
    overwrite_files: bool = Option(
        True,
        help="If specified, no files will be overwritten during the "
             "import")
    overwrite_objects: bool = Option(
        True,
        help="If specified, no objects will be overwritten during "
             "import")
    rewrite_local_ip: bool = Option(
        True,
        help="If specified local ip addresses will not be rewritten on "
             "import")
    object_audit: bool = Option(
        True,
        help="If specified, an object audit will not be performed. An "
             "object audit is a diff between the running and persisted "
             "configuration")
    out_dir: str = Option(
        'tmp', short="o", widget=OUT_DIR,
        help="This is where to place the artifacts generated by this "
             "script")
    format: Literal["ZIP", "XML"] = Option(
        'ZIP', short="F", help="The format of the configuration file.")
    quiesce_domain: bool = Option(
        True,
        help="If specified, the domain will not be quiesced prior to "
             "the deployment.")
    quiesce_appliance: bool = Option(
        False, short="q",
        help="If specified, the appliance will be quiesced prior to "
             "the deployment")
    quiesce_timeout: int = Option(
        120, short="Q",
        help="This is the amount of time for the appliance to wait "
             "before beginning the quiescence procedure.")


@logged('mast.datapower.deployment')
@cli.command('deploy', category='deployment')
def deploy(params: Deploy):
    """Perform a deployment/migration of a service/object to an IBM DataPower
appliance. This script will try to perform the deployment/migration in a
manner consistent with best practices.

The appliances are deployed to one at a time. For each appliance the
steps are: quiesce the domain, quiesce the appliance, import the
configuration, unquiesce the domain, unquiesce the appliance and take
an object audit."""
    import mast.datapower.system as system
    from mast.datapower.developer import _import

    logger = make_logger('mast.datapower.deployment')
    env = params.environment()
    # Each step is the result of another command, with its own history
    report = Report()

    for appliance in env.appliances:
        appliance.log_info("Deployment started on {}".format(
            appliance.hostname))
        # The parameters that every step needs to reach this appliance
        one = dict(
            appliances=[appliance.hostname],
            credentials=[appliance.credentials],
            timeout=params.timeout,
            no_check_hostname=params.no_check_hostname)

        # Quiesce Domain
        if params.quiesce_domain:
            logger.info(
                "Quiescing domain {} before deployment at {}".format(
                    params.Domain, str(Timestamp())))

            report.add(system.quiesce_domain(
                Domain=params.Domain,
                quiesce_timeout=params.quiesce_timeout,
                **one))

            logger.info(
                "Finished quiescing domain {} before deployment at {}".format(
                    params.Domain, str(Timestamp())))

            sleep(params.quiesce_timeout)

        # Quiesce Appliance
        if params.quiesce_appliance:
            logger.info(
                "Quiescing appliances before deployment at {}".format(
                    str(Timestamp())))

            report.add(system.quiesce_appliance(
                quiesce_timeout=params.quiesce_timeout, **one))

            logger.info(
                "Finished quiescing appliances before deployment at {}".format(
                    str(Timestamp())))

            sleep(params.quiesce_timeout)

        appliance.log_info("Attempting to import configuration at '{}'".format(
            str(Timestamp())))

        # import configuration
        report.add(_import(
            Domain=params.Domain,
            file_in=params.file_in,
            deployment_policy=params.deployment_policy,
            dry_run=params.dry_run,
            overwrite_files=params.overwrite_files,
            overwrite_objects=params.overwrite_objects,
            rewrite_local_ip=params.rewrite_local_ip,
            source_type=params.format,
            out_dir=params.out_dir,
            **one))

        appliance.log_info("Finished importing configuration at {}".format(
            str(Timestamp())))

        # unquiesce domain
        if params.quiesce_domain:
            appliance.log_info("Attempting to unquiesce domain")
            report.add(system.unquiesce_domain(Domain=params.Domain, **one))
            appliance.log_info("Finished unquiescing domain")

        # unquiesce appliance
        if params.quiesce_appliance:
            logger.info(
                "Unquiescing appliances after deployment at {}".format(
                    str(Timestamp())))

            report.add(system.unquiesce_appliance(**one))

            logger.info(
                "Finished unquiescing appliances after deployment at "
                "{}".format(str(Timestamp())))

        if params.object_audit:
            appliance.log_info(
                "Post-Deployment Object audit started at {}".format(
                    str(Timestamp())))

            report.add(system.objects_audit(out_dir=params.out_dir, **one))

            appliance.log_info(
                "Post-Deployment Object audit finished at {}".format(
                    str(Timestamp())))
    return report


class Postdeploy(ApplianceParams):
    Domain: str = Option(
        "", short="D", widget=configured("Domain"),
        help="The domain which will be unquiesced and persisted.")
    unquiesce_domain: bool = Option(
        True,
        help="If specified, this script will not attempt to unquiesce "
             "the domain")
    unquiesce_appliance: bool = Option(
        False, short="u",
        help="If specified, this script will attempt to unquiesce the "
             "appliance")
    postdeploy_command: str | None = Option(
        None, short="p",
        help='This command will be "shelled out" to the machine '
             "running MAST after unquiescing and saving the "
             "configuration, use this parameter to clean up VCS "
             "artifacts and/or perform check-outs of your deployed "
             "services or similar operations")
    save_config: bool = Option(
        True,
        help="If specified, the configuration will not be saved in the "
             "application domain")


@logged('mast.datapower.deployment')
@cli.command('postdeploy', category='deployment')
def postdeploy(params: Postdeploy):
    """This is a simple script which will allow you to unquiesce
your domain or appliances after you quiesce them for a deployment.
Also this will allow you to save the config.

__WARNING__: `--postdeploy-command` is "shelled out" to your operating
system. For this reason PLEASE be sure to run this script (and the
MAST Web GUI server) as a user with appropriate permissions.

DO NOT RUN AS ROOT!!!"""
    import mast.datapower.system as system
    logger = make_logger('mast.datapower.deployment')
    env = params.environment()
    # Each step is the result of another command, with its own history
    report = Report()

    for appliance in env.appliances:
        one = dict(
            appliances=[appliance.hostname],
            credentials=[appliance.credentials],
            timeout=params.timeout,
            no_check_hostname=params.no_check_hostname)

        if params.unquiesce_appliance:
            appliance.log_info("Attempting to unquiesce appliance")
            report.add(system.unquiesce_appliance(**one))
            appliance.log_info("Finished Unquiescing appliance")
            say("Finished unquiescing appliance")

        if params.unquiesce_domain:
            appliance.log_info("Attempting to unquiesce domain")
            report.add(system.unquiesce_domain(Domain=params.Domain, **one))
            appliance.log_info("Finished Unquiescing domain")
            say("Finished unquiescing domain")

        if params.save_config:
            appliance.log_info(
                "Attempting to save configuration after deployment")
            report.add(system.save_config(Domain=params.Domain, **one))
            appliance.log_info(
                "Finished saving configuration after deployment")
            say("Finished saving the configuration")

    if params.postdeploy_command:
        logger.info(
            "Post-Deployment command '{}' found. Executing at {}".format(
                params.postdeploy_command, str(Timestamp())))

        out, err = system_call(command=params.postdeploy_command)
        out = str(out)
        err = str(err)

        logger.info(
            "finished executing Post-Deployment command '{}' at {}., output: {}".format(
                params.postdeploy_command, str(Timestamp()),
                ";".join([out, err])))
        say("Finished running post-deploy command. output: {}".format(
            ";".join([out, err])))
        report.add(StatusResults({
            "postdeploy command": "{}\n\nout: {}\n\nerr: {}".format(
                params.postdeploy_command, out, err)}), show=False)
    return report


def get_data_file(f):
    return files(__package__).joinpath('docroot').joinpath(f).read_text(encoding='utf-8')


git_deploy = cli.command('git-deploy', category='deployment')(git_deploy)

def _result_lines(response):
    """Return the lines of text in the result of a response."""
    return [line for line in response.xml.find(
        ".//{http://www.datapower.com/schemas/management}result"
    ).itertext()]


def _password_map_alias(params, method, action, message, **kwargs):
    """Call method on each appliance to add or delete a password map
    alias, then save the configuration if the parameters ask for it."""
    logger = make_logger('mast.datapower.deployment')
    env = params.environment()

    output = {}

    def record(appliance, step, response):
        logger.info(repr(response))
        lines = _result_lines(response)
        output["{}-{}".format(appliance.hostname, step)] = "\n".join(lines)
        say("\t\t{}".format("\n\t\t".join(lines).strip()))

    for appliance in env.appliances:
        say(appliance.hostname)
        say("\t" + message)
        response = getattr(appliance, method)(
            domain=params.Domain, AliasName=params.alias_name, **kwargs)
        record(appliance, action, response)
        if params.save_config and response:
            say("\tSaving Configuration")
            record(appliance, "SaveConfig",
                   appliance.SaveConfig(domain=params.Domain))
    report = Report(env)
    report.add(StatusResults(output), show=False)
    return report


class DelPasswordMapAlias(ApplianceParams):
    Domain: str = Option(
        "", short="D", widget=configured("Domain"),
        help="The domain of the password map alias")
    alias_name: str = Option(
        "", short="A", help="The name of the password map alias")
    save_config: bool = Option(
        True,
        help="If specified, the configuration of the domain will not "
             "be saved")


class AddPasswordMapAlias(DelPasswordMapAlias):
    password: str = Option(
        "", short="p", secret=True,
        help="The password to use for the password map alias")


@logged('mast.datapower.deployment')
@cli.command('add-password-map-alias', category='password map aliases')
def add_password_map_alias(params: AddPasswordMapAlias):
    """create a password map alias on the specified appliances"""
    return _password_map_alias(
        params, "AddPasswordMap", "AddPasswordMapAlias",
        "Attempting to add Password Map Alias", Password=params.password)


@logged('mast.datapower.deployment')
@cli.command('del-password-map-alias', category='password map aliases')
def del_password_map_alias(params: DelPasswordMapAlias):
    """delete a password map alias on the specified appliances"""
    return _password_map_alias(
        params, "DeletePasswordMap", "DeletePasswordMapAlias",
        "Attempting to remove Password Map Alias")


class WebPlugin(Plugin):
    def __init__(self):
        self.route = partial(pf.handle, "deployment")
        self.route.__name__ = "deployment"
        self.html = partial(pf.html, "mast.datapower.deployment")
        update_wrapper(self.html, pf.html)

    def css(self):
        return get_data_file('plugin.css')

    def js(self):
        return get_data_file('plugin.js')


if __name__ == '__main__':
    cli.run()

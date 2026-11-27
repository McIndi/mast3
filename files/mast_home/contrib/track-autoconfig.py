# -*- coding: utf-8 -*-
from dulwich.repo import NotGitRepository
from pygments import highlight
from pygments.lexers import DiffLexer
from pygments.formatters import TerminalFormatter
import mast.pprint as pprint
from dulwich import porcelain as git
from mast.timestamp import Timestamp
from mast.datapower import datapower
from mast.logging import make_logger
from io import StringIO
from mast.cli import Cli
from mast.cli import Params, Option
import colorama
import os

colorama.init()

def pull_autoconfig(appliances=[],
                    credentials=[],
                    timeout=120,
                    no_check_hostname=False,
                    base_dir="."):
    """
    _function_: `pull_autoconfig(appliances=[], credentials=[], timeout=120, no_check_hostname=False, base_dir=".")`

    Description:

    For each appliance loop through each domain and copy the `.cfg`
    file to `base_dir/<hostname>/.`

    Usage:

        :::python
        >>> pull_autoconfig(appliances=["hostname"],
        ...                 credentials=["user:pass"],
        ...                 base_dir="tmp")

    Parameters:

    * `appliances` - The hostname(s), ip addresse(s), environment name(s)
    or alias(es) of the appliances you would like to affect. For details
    on configuring environments please see the comments in
    `environments.conf` located in `$MAST_HOME/etc/default`. For details
    on configuring aliases please see the comments in `hosts.conf` located
    in `$MAST_HOME/etc/default`.
    * `credentials`: The credentials to use for authenticating to the
    appliances. Should be either one set to use for all appliances
    or one set for each appliance. Credentials should be in the form
    `username:password` and should be provided in a space-seperated list
    if multiple are provided. If you would prefer to not use plain-text
    passwords, you can use the output of
    `$ mast datapower xor <username:password>`.
    * `timeout`: The timeout in seconds to wait for a response from
    an appliance for any single request. __NOTE__ Program execution may
    halt if a timeout is reached.
    * `no-check-hostname`: If specified SSL verification will be turned
    off when sending commands to the appliances.
    * `base_dir`: The base directory where to store the downloaded
    files. Files will actually be stored in a subdirectory of `base_dir`
    named after the hostname in the form of `base_dir/<hostname>`
    """
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


default_base_dir = os.path.join(os.environ["MAST_HOME"], "tmp", "config")
default_comment = "Update {}".format(Timestamp().friendly)
class Main(Params):
    appliances: list[str] = Option(
        default_factory=list, short="a",
        help='- The hostname(s), ip addresse(s), environment name(s) or '
             'alias(es) of the appliances you would like to affect. For '
             'details on configuring environments please see the '
             'comments in `environments.conf` located in '
             '`$MAST_HOME/etc/default`. For details on configuring '
             'aliases please see the comments in `hosts.conf` located in'
             ' `$MAST_HOME/etc/default`.')
    credentials: list[str] = Option(
        default_factory=list, short="c", secret=True,
        help='The credentials to use for authenticating to the '
             'appliances. Should be either one set to use for all '
             'appliances or one set for each appliance. Credentials '
             'should be in the form `username:password` and should be '
             'provided in a space-seperated list if multiple are '
             'provided. If you would prefer to not use plain-text '
             'passwords, you can use the output of `$ mast datapower xor '
             '<username:password>`.')
    timeout: int = Option(
        120, short="t",
        help='The timeout in seconds to wait for a response from an '
             'appliance for any single request. __NOTE__ Program '
             'execution may halt if a timeout is reached.')
    no_check_hostname: bool = Option(
        False, short="n",
        help='If specified SSL verification will be turned off when '
             'sending commands to the appliances.')
    base_dir: str = Option(
        default_base_dir, short="b",
        help='The base directory where to store the downloaded files. '
             'Files will actually be stored in a subdirectory of '
             '`base_dir` named after the hostname in the form of '
             '`base_dir/<hostname>`')
    comment: str = Option(default_comment, short="C")
    page: bool = Option(
        False, short="p",
        help='If specified, page the output when too long to display at '
             'once')
    no_highlight_diff: bool = Option(
        False, short="N",
        help='If specified, the output of the diff will be syntax-'
             'highlighted')


def main(params: Main):
    """track_autoconfig.py

Description:

Store persisted domain configuration in a local git repository
for auditing purposes.

Usage:

    :::bash
    $ mast contrib/track_autoconfig.py --appliances <HOSTNAMES> --credentials <USER:PASS> --base-dir tmp/config"""
    appliances = params.appliances
    credentials = params.credentials
    timeout = params.timeout
    no_check_hostname = params.no_check_hostname
    base_dir = params.base_dir
    comment = params.comment
    page = params.page
    no_highlight_diff = params.no_highlight_diff
    base_dir = os.path.abspath(base_dir)
    if not os.path.exists(base_dir):
        os.makedirs(base_dir)
    try:
        repo = git.Repo(base_dir)
        first_sha1 = repo.head()
        print(first_sha1)
    except NotGitRepository:
        print("Initializing git repository")
        git.init(base_dir)
        git.add(base_dir)
        first_sha1 = git.commit(base_dir, message="Initial Commit")
        print(first_sha1)
    except KeyError:
        git.add(base_dir)
        git.commit(base_dir, message="Initial Commit")
    print()
    pull_autoconfig(appliances=appliances,
                    credentials=credentials,
                    timeout=timeout,
                    no_check_hostname=no_check_hostname,
                    base_dir=base_dir)
    git.add(base_dir)
    print((git.status(base_dir)))
    second_sha1 = git.commit(base_dir, message=comment)
    print(second_sha1)
    print("\n\nDIFF\n\n")

    tmp = StringIO()
    git.show(base_dir, outstream=tmp)
    tmp.seek(0)

    if no_highlight_diff:
        out = tmp.read()
    else:
        out = highlight(tmp.read(), DiffLexer(), TerminalFormatter())

    if page:
        pprint.page(out)
    else:
        print(out)

if __name__ == "__main__":
    cli = Cli(main=main, description=main.__doc__)
    try:
        cli.run()
    except SystemExit:
        pass
    except Exception:
        make_logger("error").exception("An unhandled exception occurred!")
        raise

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
from mast.datapower.datapower import Environment
from mast.logging import make_logger
from getpass import getpass
from mast.cli import Params, Option
from mast.datapower.provider import cli as _provider
import sys
import os
import atexit

cli = _provider.section("terminal")


def _history():
    """Keep the lines typed in ~/.mast_ssh_history, from one session to
    the next. Only an interactive session does this, not the import."""
    try:
        import readline
    except ImportError:
        import pyreadline as readline  # lint:ok
    histfile = os.path.join(os.path.expanduser("~"), '.mast_ssh_history')
    try:
        readline.read_history_file(histfile)
    except IOError:
        pass
    atexit.register(readline.write_history_file, histfile)

PASSWORD_CHANGE_PROMPTS = [
    "Please enter new password: ",
    "Please re-enter new password to confirm: ",
]
PASSWORD_PROMPTS = ["Password: "] + PASSWORD_CHANGE_PROMPTS

class Input(object):
    """This is a class representing the ssh command prompt. This is a
    very simple CLI. It has a queue (python list) named commands which
    can be added to. If next() is called and this list is empty then
    the user is prompted for the next command"""
    def __init__(self, prompt):
        """Initialization method"""
        self.commands = []
        self.prompt = prompt

    def send(self, command):
        """Adds a command to the queue (the python list) commands"""
        self.commands.append(command)

    def __next__(self):
        """This is provided so that this object can function as
        an iterator."""
        if self.prompt in PASSWORD_CHANGE_PROMPTS:
            return getpass(self.prompt)

        try:
            return self.commands.pop(0)
        except IndexError:
            if self.prompt in PASSWORD_PROMPTS:
                return getpass(self.prompt)
            return input(self.prompt)

    def __iter__(self):
        """return self as an iterator"""
        return self


def initialize_appliances(env, domain='default', timeout=120):
    """This initiates an ssh session, extracts the prompt and
    displays the initial output."""
    responses = []
    for appliance in env.appliances:
        _resp = appliance.ssh_connect(domain=domain, timeout=timeout)
        # Sanitize password from output
        password = appliance.credentials.split(":", 1)[1]
        _resp = _resp.replace(password, "*"*8)
        responses.append(_resp)
    output = format_output(responses, env)
    prompt = output.splitlines()[-1:][0] + ' '
    output = '\n'.join(output.splitlines()[:-1]) + '\n'
    display_output(output)
    return prompt


def issue_command(command, env, timeout=60, secret=False):
    """This method issues a command to the DataPower's CLI. A `secret`
    line (typed at a password prompt) is logged as ********."""
    responses = []
    for appliance in env.appliances:
        resp = appliance.ssh_issue_command(command, secret=secret)
        responses.append(resp)
    return responses


def compare(array):
    """Return True if all responses from the DataPowers are the same,
    False otherwise."""
    return array.count(array[0]) == len(array)


def format_output(array, env):
    """Format the output based on the output of compare(array). If all of
    the appliances responded with the same output, then it will be printed
    only once (This is to make it appear as if you are speaking to one
    appliance). Otherwise, if any of the output is different then every
    response is printed individually prepended wiht the appliance's name/ip."""
    seperator = '\n\n{}\n----\n\n'
    if compare(array):
        return array[0]
    response = ''
    for index, appliance in enumerate(env.appliances):
        response += seperator.format(appliance.hostname) + array[index]
    return response + '\n\n> '


def display_output(string):
    """This outputs string to stdout and immediately flushes stdout. This is
    used to prevent the print statment from printing a newline each time it
    is invoked."""
    sys.stdout.write(string)
    sys.stdout.flush()


class SSH(Params):
    appliances: list[str] = Option(
        default_factory=list, short="a", target=True,
        help="The hostname(s), ip addresse(s), environment name(s) or "
             "appliance name(s) you would like to affect. "
             "Appliances and environments are kept in the "
             "registry; see `mast targets list` and "
             "`mast fleets list`.")
    credentials: list[str] = Option(
        default_factory=list, short="c", secret=True,
        help="The credentials to use for authenticating to the "
             "appliances, in the form `username:password`. Give either "
             "one set to use for all appliances or one set for each "
             "appliance. If you would prefer to not use plain-text "
             "passwords, you can use the output of "
             "`mast datapower xor <username:password>`.")
    domain: str = Option("default", short="d", help="The domain to log into")
    input_file: str = Option(
        "", short="i",
        help="If provided, it should point to a text file which "
             "contains cli commands (one-per-line) which will be "
             "executed in order on each appliance")
    timeout: int = Option(
        120, short="t",
        help="The timeout in seconds to wait for a response from an "
             "appliance for any single request. __NOTE__ Program "
             "execution may halt if a timeout is reached.")
    credential: str | None = Option(
        None,
        help="The name of a stored credential of purpose change, in "
             "place of `--credentials` (see `mast credentials list`). "
             "Every appliance must be registered, bound to it, and have "
             "a pinned SSH host key. The session is recorded as a job "
             "(ssh.terminal), with each line and each use of the "
             "credential.")


class _Recorder(object):
    """The job of a session with a stored credential: it records the
    use of the credential on each appliance, and each line (a line
    typed at a password prompt as ********). It is kept with heartbeats
    until the session ends."""

    def __init__(self, credential, appliances):
        import threading
        from mast import jobs, registry
        from mast.credentials import store
        from mast.jobs import config as jobs_config
        from mast.jobs.auth import LOCAL
        from mast.jobs.runner import new_worker_id
        names = [a.name for a in registry.resolve(appliances)]
        found = store.check_use(credential, names, LOCAL,
                                command="datapower.terminal", effect="change")
        if found.purpose != store.CHANGE:
            raise store.Forbidden(
                "The credential {} is not for changes (purpose "
                "change).".format(credential))
        unpinned = [a.name for a in registry.resolve(names)
                    if not a.ssh_host_key]
        if unpinned:
            raise store.Forbidden(
                "A stored credential is never sent over SSH to an appliance "
                "whose host key is not pinned: {}. Pin it with "
                "mast targets pin -n NAME --ssh.".format(", ".join(unpinned)))
        self.jobs = jobs
        self.worker_id = new_worker_id()
        self.job_id = jobs.submit(
            "datapower.terminal", {"appliances": names, "credential": credential},
            principal=LOCAL, reserved_for=self.worker_id, passive=True)
        jobs.claim(self.worker_id, job_id=self.job_id)
        self.names = names
        self.passwords = []
        self.credentials = []
        for name in names:
            username, password = store._open(credential, name, self.job_id,
                                             LOCAL.name)
            self.passwords.append(password)
            self.credentials.append("{}:{}".format(username, password))
        self._stop = threading.Event()
        interval = max(1.0, jobs_config.lease_seconds() / 3.0)
        threading.Thread(target=self._beat, args=(interval,),
                         daemon=True).start()

    def _beat(self, interval):
        while not self._stop.wait(interval):
            try:
                self.jobs.heartbeat(self.job_id, self.worker_id)
            except Exception:
                pass

    def line(self, text, secret=False):
        if secret:
            text = "********"
        for password in self.passwords:
            text = text.replace(password, "********")
        try:
            self.jobs.add_event(self.job_id, "> " + text.rstrip("\n"),
                                worker_id=self.worker_id)
        except Exception:
            pass

    def finish(self):
        self._stop.set()
        try:
            self.jobs.finish(self.job_id, self.worker_id, "succeeded",
                             text="A mast datapower ssh session on {}.".format(
                                 ", ".join(self.names)))
        except Exception:
            pass


@cli.command("ssh", effect="change", group="terminal", queued=False,
             interactive=True)
def main(params: SSH):
    """An interactive SSH session with one or more DataPower appliances:
each line typed goes to every appliance.

__IMPORTANT__: This will not work for other types of machines, this is
because since the DataPower doesn't follow the SSH spec exactly,
special care was taken so we would properly handle their implementation.
The steps which were taken to achieve this handling prevent it from
being reliably used for other machine types"""
    from mast.cli.results import stored_credential
    _history()
    if params.credential and params.credentials:
        raise SystemExit("Give either credentials or a stored credential "
                         "(credential), not both.")
    if not params.credential:
        return _main(params, Environment(params.appliances,
                                         params.credentials), None)
    recorder = _Recorder(params.credential, params.appliances)
    try:
        with stored_credential(params.credential):
            return _main(params, Environment(recorder.names,
                                             recorder.credentials), recorder)
    finally:
        recorder.finish()


def _main(params, env, recorder):
    domain = params.domain
    input_file = params.input_file
    timeout = params.timeout
    log = make_logger("ssh")
    prompt = initialize_appliances(env, domain, timeout=timeout)
    log.info(f"Found prompt: {prompt}")
    global _input
    _input = Input(prompt)
    if input_file:
        log.info(f"Found input_file: {input_file}")
        display_output(f"Found input_file: {input_file}")
        if not os.path.isfile(input_file):
            display_output("input_file must be a file containing cli commands to issue")
            sys.exit(-1)
        with open(input_file, "r") as fin:
            for command in fin:
                log.info(f"Found command: {command}")
                if recorder is not None:
                    recorder.line(command)
                output = issue_command(command, env, timeout=timeout)
                output = format_output(output, env)
                log.info(f"Found output: {output}")
                prompt = output.splitlines()[-1:][0] + ' '
                log.debug(f"Found prompt: {prompt}")
                _input.prompt = prompt
                # output = '\n'.join(output.splitlines()[:-1]) + '\n'
                display_output(output)
                if ('Goodbye' in prompt) or ('Goodbye' in output):
                    display_output('Goodbye')
                    sys.exit(0)
    for command in _input:
        # A line typed at a password prompt is not logged
        secret = _input.prompt in PASSWORD_PROMPTS
        log.info("Found command: {}".format("********" if secret else command))
        if recorder is not None:
            recorder.line(command, secret=secret)
        output = issue_command(command, env, timeout=timeout, secret=secret)
        output = format_output(output, env)
        log.info(f"Found output: {output}")
        prompt = output.splitlines()[-1:][0] + ' '
        _input.prompt = prompt
        output = '\n'.join(output.splitlines()[:-1]) + '\n'
        display_output(output)
        if ('Goodbye' in prompt) or ('Goodbye' in output):
            display_output('Goodbye')
            break
    raise SystemExit


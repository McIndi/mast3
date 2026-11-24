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
# Copyright 2015-2026, McIndi Solutions, All rights reserved.
"""
_module_: `mast.datapower.ssh.sessions`

The sessions of the Terminal page: SSH to the DataPower CLI of several
appliances at once. One line goes to all of them (or some), and their
answers sit side by side.

A `TerminalSession` lives in the web server process (`SessionManager`).
It belongs to the user who opened it: every call checks the owner, and
another user gets the same answer as for a session that does not
exist. It is closed when:

* nobody used it for `idle_minutes` (`[ssh]` in `ssh.conf`, 15), or it
is `max_hours` old (8);
* its owner logs out, or their session epoch changes (a new password,
a disable);
* an admin cancels its job on the Jobs page;
* the owner closes it, or the web server stops.

One web server process for each MAST home is the supported setup: a
second one would not see the sessions of the first.

**Each session is a job** (`ssh.terminal`), for the record. The web
server submits it reserved for itself and takes it at once, and keeps
it with heartbeats while the session is open: a web server that dies
leaves the job `lost` after one lease. Each line is a job event
(`> line`), and so is each appliance's answer (`[appliance] output`).
The job ends with the whole transcript as its result. The job is
passive and not exclusive: an open terminal does not hold up a
deployment, and what is typed in it can run alongside a job on the same
appliance.

**Secrets:** the password of the login, and every line typed while an
appliance asks for a password, are replaced by `********` in the job
events, the result and the transcripts. Such a line is never logged:
the shell here does not write the lines it sends to the log.
"""
import re
import secrets
import threading
import time
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import datetime, timezone

from mast.logging import make_logger

__all__ = ["SessionManager", "TerminalSession", "Pane", "Line",
           "DataPowerShell", "Settings", "settings", "NoSuchSession",
           "TooManySessions", "COMMAND", "is_password_prompt", "clean"]

COMMAND = "ssh.terminal"
SECRET = "********"
# How long a request waits for the answers of a line before it returns
# with the ones that are done (the others are fetched later)
WAIT_SECONDS = 2.0

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(\x07|\x1b\\)"
                   r"|\x1b[@-Z\\-_]")
_PASSWORD_PROMPT = re.compile(
    r"(password|passphrase)[^\n]*:\s*$|enter password\s*$", re.I)


def _log():
    return make_logger("mast.datapower.ssh")


def clean(text):
    """`text` without ANSI escape codes and carriage returns: the page
    shows them as text, it does not render them."""
    return _ANSI.sub("", text or "").replace("\r", "")


def is_password_prompt(output):
    """True if the last line of `output` asks for a password."""
    lines = [line for line in (output or "").splitlines() if line.strip()]
    return bool(lines) and bool(_PASSWORD_PROMPT.search(lines[-1].strip()))


@dataclass
class Settings(object):
    idle_minutes: int = 15
    max_hours: int = 8
    max_sessions_per_user: int = 3
    max_appliances_per_session: int = 20
    command_timeout: int = 120
    connect_timeout: int = 30


def settings():
    """The `Settings` of `[ssh]` in `ssh.conf`. A value that is not a
    whole number above 0 is ignored (the default stays)."""
    from mast.config import get_config
    config = get_config("ssh.conf")
    found = Settings()
    for name in vars(found):
        if not config.has_option("ssh", name):
            continue
        try:
            value = int(config.get("ssh", name))
            if value < 1:
                raise ValueError(value)
        except ValueError:
            _log().warning("ssh.conf: [ssh] {} is not a whole number above "
                           "0; using {}".format(name, getattr(found, name)))
            continue
        setattr(found, name, value)
    return found


class NoSuchSession(KeyError):
    """No open session with this id belongs to this user."""


class TooManySessions(ValueError):
    """The user has `max_sessions_per_user` sessions open."""


# The shell of one appliance
# ==========================

class DataPowerShell(object):
    """
    _class_: `mast.datapower.ssh.sessions.DataPowerShell`

    The DataPower CLI of one appliance over SSH, with the checks of
    `mast.datapower.datapower.DataPower.ssh_connect` (the pinned host
    key of the registry). `run` sends a line and reads the answer
    without logging either, so that a password typed at a prompt is not
    in a log.
    """

    def __init__(self, name, username, password):
        from mast.datapower.datapower import DataPower
        self.name = name
        self.warnings = []
        self._dp = DataPower(name, "{}:{}".format(username, password))
        # A host key that is not checked is said in the pane, not on the
        # standard output of the web server
        self._dp._warn_host_key = self.warnings.append

    def connect(self, domain, timeout):
        """Log in. Returns the banner and the first prompt."""
        return self._dp.ssh_connect(domain=domain, timeout=timeout)

    def _channel(self):
        return getattr(self._dp, "_ssh_conn", None)

    def connected(self):
        client = getattr(self._dp, "_ssh", None)
        transport = client.get_transport() if client is not None else None
        return bool(transport and transport.is_active())

    def run(self, line, timeout):
        """Send `line` and return what the appliance answers, up to its
        next prompt. Raises `TimeoutError` after `timeout` seconds."""
        channel = self._channel()
        if channel is None or not self.connected():
            raise ConnectionError("not connected")
        channel.sendall(line + "\n")
        deadline = time.monotonic() + timeout
        output = ""
        while True:
            if channel.recv_ready():
                output += channel.recv(4096).decode("utf-8", "replace")
                continue
            if output and _finished(output):
                # A last look: the prompt may not be the end
                time.sleep(0.05)
                if not channel.recv_ready():
                    return output
                continue
            if channel.closed or not self.connected():
                return output
            if time.monotonic() > deadline:
                raise TimeoutError(
                    "no prompt after {} seconds".format(timeout))
            time.sleep(0.05)

    def interrupt(self):
        """Send Ctrl-C."""
        channel = self._channel()
        if channel is not None:
            channel.send("\x03")

    def close(self):
        client = getattr(self._dp, "_ssh", None)
        if client is not None:
            client.close()


def _finished(output):
    """True if `output` ends with a prompt of the DataPower CLI (the
    tests of `DataPower.ssh_finished_command`)."""
    lines = output.replace("\r", "").splitlines()
    last = lines[-1].strip() if lines else ""
    lower = output.strip().lower()
    return (last.endswith("#") or "Goodbye" in output
            or "nter new password" in output
            or bool(re.match(r".*?\[y/n\]", last))
            or lower.endswith("login:") or lower.endswith("password:")
            or lower.endswith("domain (? for all):")
            or is_password_prompt(output))


# A session
# =========

@dataclass
class Pane(object):
    """One appliance of a session."""
    name: str
    shell: object = None
    connected: bool = False
    error: str | None = None
    banner: str = ""
    warnings: list = field(default_factory=list)
    password_prompt: bool = False
    busy: bool = False
    # The transcript, already without secrets
    transcript: list = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def json(self):
        return {"name": self.name, "connected": self.connected,
                "error": self.error, "banner": self.banner,
                "warnings": list(self.warnings),
                "password_prompt": self.password_prompt, "busy": self.busy}


@dataclass
class Line(object):
    """One line sent to some panes, and their answers."""
    number: int
    text: str
    appliances: list
    results: dict = field(default_factory=dict)
    done: threading.Condition = field(default_factory=threading.Condition)

    def finished(self):
        return all(r["done"] for r in self.results.values())

    def json(self, appliance=None):
        results = {name: dict(result) for name, result in self.results.items()
                   if appliance is None or name == appliance}
        return {"number": self.number, "line": self.text,
                "appliances": list(self.appliances), "results": results,
                "finished": all(r["done"] for r in results.values())}


def _now():
    return datetime.now(timezone.utc)


class TerminalSession(object):
    """
    _class_: `mast.datapower.ssh.sessions.TerminalSession`

    An open terminal: its id (made here), its owner (user id, name and
    session epoch), its panes, its lines, and its job.
    """

    def __init__(self, owner, job_id, names, clock):
        self.id = secrets.token_urlsafe(32)
        self.user_id = owner.id
        self.user_name = owner.name
        self.epoch = owner.session_epoch
        self.job_id = job_id
        self.panes = {name: Pane(name) for name in names}
        self.lines = []
        self.clock = clock
        self.created = clock()
        self.last_used = self.created
        self.opened_at = _now()
        self.closed = False
        self.lock = threading.Lock()
        self._secrets = set()
        self._redactor = None
        # The stored credential (its name), and who opened it as a
        # principal of the queue, for a reconnect
        self.credential = None
        self.principal = None

    # Secrets
    def add_secret(self, value):
        if value:
            with self.lock:
                self._secrets.add(value)
                self._redactor = None

    def redact(self, text):
        from mast.jobs.secrets import Redactor
        with self.lock:
            if self._redactor is None:
                self._redactor = Redactor(self._secrets)
            redactor = self._redactor
        text = redactor(text or "")
        # Redactor marks a secret with its own word; the terminal shows
        # the stars that a password field shows
        from mast.jobs.secrets import REDACTED
        return text.replace(REDACTED, SECRET)

    def touch(self):
        self.last_used = self.clock()

    def json(self):
        return {"id": self.id, "job_id": self.job_id,
                "opened_at": self.opened_at.isoformat(),
                "appliances": [p.json() for p in self.panes.values()],
                "lines": len(self.lines)}

    def transcript(self, appliance=None):
        """The transcript of one appliance, or of all of them."""
        names = [appliance] if appliance else list(self.panes)
        parts = []
        for name in names:
            pane = self.panes[name]
            if len(names) > 1:
                parts.append("===== {} =====\n".format(name))
            parts.append("".join(pane.transcript))
            if len(names) > 1:
                parts.append("\n")
        return "".join(parts)


# The sessions of the web server
# ==============================

class SessionManager(object):
    """
    _class_: `mast.datapower.ssh.sessions.SessionManager`

    The open sessions of this process. `shell_factory(name, username,
    password)` makes the shell of one appliance (a `DataPowerShell` by
    default; the tests give a fake). `clock` is `time.monotonic` by
    default.
    """

    def __init__(self, shell_factory=None, clock=None, settings_=None,
                 sweep_seconds=10.0, worker_id=None):
        self.shell_factory = shell_factory or DataPowerShell
        self.clock = clock or time.monotonic
        self._settings = settings_
        self.sweep_seconds = sweep_seconds
        self._worker_id = worker_id
        self._sessions = {}
        self._lock = threading.Lock()
        self._sweeper = None
        self._stop = threading.Event()

    @property
    def settings(self):
        if self._settings is None:
            self._settings = settings()
        return self._settings

    @property
    def worker_id(self):
        if self._worker_id is None:
            from mast.jobs.runner import new_worker_id
            self._worker_id = new_worker_id()
        return self._worker_id

    # Finding a session
    def get(self, session_id, user):
        """The open session `session_id` of `user`. Raises
        `NoSuchSession` for an unknown id and for another user's."""
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None or session.closed or session.user_id != user.id:
            raise NoSuchSession(session_id)
        session.touch()
        return session

    def list(self, user):
        with self._lock:
            return [s for s in self._sessions.values()
                    if s.user_id == user.id and not s.closed]

    def by_job(self, job_id):
        with self._lock:
            for session in self._sessions.values():
                if session.job_id == job_id:
                    return session
        return None

    # Opening one
    def open(self, user, principal, names, credentials=None,
             credential=None, domain="default"):
        """
        Open a session of `user` (a `mast.auth.users.UserInfo`; the
        job is submitted as `principal`) to the appliances `names`,
        with `credentials` (`["user:password"]`, one for all or one
        for each) or the stored credential `credential`. The page
        checks who may use what before it calls this. Each appliance
        connects in parallel; one that fails does not stop the others.
        Returns the `TerminalSession`. Raises `TooManySessions`.
        """
        from mast import jobs
        if len(self.list(user)) >= self.settings.max_sessions_per_user:
            raise TooManySessions(
                "You have {} terminal sessions open, the most that one user "
                "may have. Close one first.".format(
                    self.settings.max_sessions_per_user))
        params = {"appliances": list(names)}
        if credential:
            params["credential"] = credential
        job_id = jobs.submit(COMMAND, params, principal=principal,
                             reserved_for=self.worker_id, passive=True)
        claimed = jobs.claim(self.worker_id, job_id=job_id)
        if claimed != job_id:
            # Cannot happen for a passive job reserved for us; do not
            # leave it queued
            jobs.cancel(job_id, principal)
            raise RuntimeError("The job of the terminal could not start.")
        session = TerminalSession(user, job_id, names, self.clock)
        session.credential = credential or None
        session.principal = principal
        with self._lock:
            self._sessions[session.id] = session
        logins = self._logins(session, names, credentials, credential,
                              principal)
        self._event(session, "Opened by {} on {}".format(
            user.name, ", ".join(names)))
        threads = [threading.Thread(
            target=self._connect,
            args=(session, session.panes[name], logins[name], domain,
                  bool(credential)), daemon=True) for name in names]
        for thread in threads:
            thread.start()
        limit = self.settings.connect_timeout + 5
        started = time.monotonic()
        for thread in threads:
            thread.join(max(0.1, limit - (time.monotonic() - started)))
        for pane in session.panes.values():
            if not pane.connected and pane.error is None:
                pane.error = "The login did not finish in {} seconds.".format(
                    limit)
        self._start_sweeper()
        return session

    def _logins(self, session, names, credentials, credential, principal):
        """`{name: (username, password)}`, and the passwords become
        secrets of the session. A stored credential is opened here, and
        each appliance's use of it is recorded with the job."""
        found = {}
        if credential:
            from mast.credentials import store
            for name in names:
                found[name] = store._open(credential, name, session.job_id,
                                          principal.name)
        else:
            credentials = list(credentials or [])
            for index, name in enumerate(names):
                text = credentials[index if len(credentials) > 1 else 0]
                username, _, password = text.partition(":")
                found[name] = (username, password)
        for username, password in found.values():
            session.add_secret(password)
        return found

    def _connect(self, session, pane, login, domain, stored):
        from mast.cli.results import stored_credential
        username, password = login
        try:
            # With a stored credential, a connection that does not check
            # the host key is refused (mast.datapower.datapower.tls)
            with stored_credential(True) if stored else nullcontext():
                pane.shell = self.shell_factory(pane.name, username, password)
                banner = pane.shell.connect(domain,
                                            self.settings.connect_timeout)
            pane.warnings = list(getattr(pane.shell, "warnings", []))
            pane.banner = session.redact(clean(banner))
            pane.connected = True
            pane.password_prompt = is_password_prompt(pane.banner)
            pane.transcript.append(pane.banner)
            self._event(session, "[{}] connected".format(pane.name))
        except Exception as exc:
            pane.connected = False
            pane.error = session.redact(_error(exc))
            self._event(session, "[{}] not connected: {}".format(
                pane.name, pane.error))
            self._close_shell(pane)

    def reconnect(self, session_id, user, name, credentials=None,
                  domain="default"):
        """Connect one appliance of the session again. A session with a
        stored credential uses it again; one with typed credentials
        needs them again (`credentials`)."""
        session = self.get(session_id, user)
        if name not in session.panes:
            raise NoSuchSession(name)
        pane = session.panes[name]
        if pane.busy:
            raise ValueError("{} is still running a line.".format(name))
        credential = session.credential
        login = self._logins(session, [name], credentials, credential,
                             session.principal)[name]
        self._close_shell(pane)
        pane.error = None
        pane.connected = False
        pane.password_prompt = False
        self._connect(session, pane, login, domain, bool(credential))
        return pane

    # Lines
    def send(self, session_id, user, text, names=None, wait=None):
        """
        Send the line `text` to the appliances `names` of the session
        (all of its connected ones if None), each in its own thread.
        Waits up to `wait` seconds (`WAIT_SECONDS`) and returns the `Line`: the answers
        of the appliances that are done, and `done: False` for the
        others (`line()` gets them later).

        If one of them asks for a password, the line is a secret: it is
        recorded only as `********`.
        """
        session = self.get(session_id, user)
        names = list(session.panes) if names is None else list(names)
        unknown = [n for n in names if n not in session.panes]
        if unknown:
            raise ValueError("Not in this session: {}".format(
                ", ".join(unknown)))
        targets = [session.panes[n] for n in names]
        secret = any(p.password_prompt for p in targets if p.connected)
        if secret:
            session.add_secret(text)
        shown = SECRET if secret else session.redact(text)
        with session.lock:
            line = Line(len(session.lines) + 1, shown, names)
            session.lines.append(line)
        self._event(session, "> {}".format(shown))
        for pane in targets:
            if not pane.connected:
                line.results[pane.name] = {
                    "done": True, "output": "",
                    "error": pane.error or "not connected"}
            elif pane.busy:
                line.results[pane.name] = {
                    "done": True, "output": "",
                    "error": "still running the line before; wait for it, "
                             "or send Ctrl-C"}
            else:
                line.results[pane.name] = {"done": False, "output": "",
                                           "error": None}
                pane.busy = True
                threading.Thread(target=self._run, args=(
                    session, pane, line, text), daemon=True).start()
        with line.done:
            line.done.wait_for(line.finished, timeout=WAIT_SECONDS
                               if wait is None else wait)
        return line

    def _run(self, session, pane, line, text):
        result = line.results[pane.name]
        output, error = "", None
        try:
            output = pane.shell.run(text, self.settings.command_timeout)
        except Exception as exc:
            error = _error(exc)
        output = session.redact(clean(output))
        with pane.lock:
            pane.transcript.append(output)
            if error:
                pane.transcript.append("\n[{}]\n".format(error))
            pane.password_prompt = is_password_prompt(output)
            if not pane.shell.connected():
                pane.connected = False
                pane.error = error or "the appliance closed the session"
            pane.busy = False
        self._event(session, "[{}] {}".format(
            pane.name, output if not error else "{}\n{}".format(output, error)))
        with line.done:
            result.update(done=True, output=output,
                          error=session.redact(error) if error else None,
                          password_prompt=pane.password_prompt,
                          connected=pane.connected)
            line.done.notify_all()

    def line(self, session_id, user, number, appliance=None):
        """The `Line` `number` (from 1) of the session."""
        session = self.get(session_id, user)
        if not 1 <= number <= len(session.lines):
            raise NoSuchSession(number)
        if appliance is not None and appliance not in session.panes:
            raise NoSuchSession(appliance)
        return session.lines[number - 1]

    def interrupt(self, session_id, user, names=None):
        """Send Ctrl-C to the appliances `names` that are running a
        line. Returns their names."""
        session = self.get(session_id, user)
        sent = []
        for name, pane in session.panes.items():
            if (names is None or name in names) and pane.busy and pane.shell:
                try:
                    pane.shell.interrupt()
                    sent.append(name)
                except Exception as exc:
                    _log().warning("Ctrl-C to {}: {}".format(name, exc))
        if sent:
            self._event(session, "> ^C ({})".format(", ".join(sent)))
        return sent

    # Closing
    def close(self, session_id, user=None, status="succeeded",
              reason="closed by its owner"):
        """Close a session: disconnect, and end its job with the
        transcript. With `user`, only that user's session (or any if
        `user` is an admin)."""
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None or session.closed:
            raise NoSuchSession(session_id)
        if user is not None and session.user_id != user.id \
                and getattr(user, "role", None) != "admin":
            raise NoSuchSession(session_id)
        self._close(session, status, reason)

    def _close(self, session, status, reason, finish=True):
        from mast import jobs
        with session.lock:
            if session.closed:
                return
            session.closed = True
        for pane in session.panes.values():
            if pane.shell is not None and pane.busy:
                try:
                    pane.shell.interrupt()
                except Exception:
                    pass
            self._close_shell(pane)
            pane.connected = False
        with self._lock:
            self._sessions.pop(session.id, None)
        if not finish:
            return
        self._event(session, "Closed: {}".format(reason))
        try:
            jobs.finish(session.job_id, self.worker_id, status,
                        text=session.transcript())
        except Exception:
            _log().exception("Could not finish the job {} of a terminal "
                             "session".format(session.job_id))
        _log().info("The terminal session of {} (job {}) closed: {}".format(
            session.user_name, session.job_id, reason))

    def _close_shell(self, pane):
        if pane.shell is not None:
            try:
                pane.shell.close()
            except Exception:
                pass

    def close_all(self, reason="the web server stopped"):
        with self._lock:
            open_ = list(self._sessions.values())
        for session in open_:
            self._close(session, "succeeded", reason)
        self._stop.set()

    # The sweeper
    def sweep(self):
        """Close the sessions that expired, whose owner logged out or
        changed, or whose job was cancelled; keep the jobs of the
        others."""
        from mast import jobs
        from mast.auth import users
        now = self.clock()
        idle = self.settings.idle_minutes * 60
        longest = self.settings.max_hours * 3600
        with self._lock:
            open_ = list(self._sessions.values())
        for session in open_:
            if session.closed:
                continue
            if now - session.created > longest:
                self._close(session, "succeeded", "open for {} hours".format(
                    self.settings.max_hours))
                continue
            busy = any(p.busy for p in session.panes.values())
            if not busy and now - session.last_used > idle:
                self._close(session, "succeeded", "idle for {} minutes".format(
                    self.settings.idle_minutes))
                continue
            owner = users.get_by_id(session.user_id)
            if owner is None or owner.disabled or \
                    owner.session_epoch != session.epoch:
                self._close(session, "succeeded",
                            "its owner logged out or was changed")
                continue
            beat = jobs.heartbeat(session.job_id, self.worker_id)
            if not beat.owned:
                # Declared lost: the job is no longer ours to finish
                self._close(session, "succeeded", "its job was lost",
                            finish=False)
            elif beat.cancel_requested:
                self._close(session, "cancelled",
                            "its job was cancelled on the Jobs page")

    def _start_sweeper(self):
        with self._lock:
            if self._sweeper is not None and self._sweeper.is_alive():
                return
            self._stop.clear()
            self._sweeper = threading.Thread(target=self._sweep_forever,
                                             name="mast-terminal-sweeper",
                                             daemon=True)
            self._sweeper.start()

    def _sweep_forever(self):
        while not self._stop.wait(self.sweep_seconds):
            try:
                self.sweep()
            except Exception:
                _log().exception("The sweep of the terminal sessions failed")
            with self._lock:
                if not self._sessions:
                    self._sweeper = None
                    return

    def _event(self, session, message):
        from mast import jobs
        try:
            jobs.add_event(session.job_id, message, worker_id=self.worker_id)
        except Exception:
            # The job was lost: the sweeper closes the session
            pass


def _error(exc):
    text = "{}: {}".format(type(exc).__name__, exc).strip()
    return text.splitlines()[0][:300] if text else type(exc).__name__

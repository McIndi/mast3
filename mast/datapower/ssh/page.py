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
_module_: `mast.datapower.ssh.page`

The Terminal page of the web UI: the DataPower CLI of several
appliances at once (`mast.datapower.ssh.sessions`). A terminal can
change anything, so it follows the rules for changes: operators and
admins only. An operator types a user name and a password; an admin may
also use a stored credential of purpose `change`, on appliances whose
SSH host key is pinned.

Its script (`static/terminal.js`) uses these routes:

* `GET /api/v1/ssh/sessions`: the user's open sessions (a reload
attaches to them again).
* `POST /api/v1/ssh/sessions` `{appliances, credentials | credential,
domain}`: open a session; each appliance connects in parallel.
* `GET /api/v1/ssh/sessions/<id>`: one session, with the transcript of
each appliance.
* `POST /api/v1/ssh/sessions/<id>/lines` `{line, appliances}`: send a
line. `200` with every answer, or `202` when some appliance is still
running after 2 seconds; then `GET .../lines/<n>` (`?appliance=`).
* `POST /api/v1/ssh/sessions/<id>/interrupt` `{appliances}`: Ctrl-C.
* `POST /api/v1/ssh/sessions/<id>/reconnect` `{appliance,
credentials}`.
* `GET /api/v1/ssh/sessions/<id>/transcript?appliance=`: a download.
* `DELETE /api/v1/ssh/sessions/<id>`: close it, and end its job.

A session of another user is answered as one that does not exist (404).
"""
import atexit

import flask

from mast.jobs.auth import Forbidden
from mast.web.page import Page

from .sessions import (COMMAND, NoSuchSession, SessionManager,
                       TooManySessions)

__all__ = ["page", "MANAGER"]

MANAGER = SessionManager()
atexit.register(MANAGER.close_all)

blueprint = flask.Blueprint(
    "terminal_page", __name__, template_folder="templates",
    static_folder="static", static_url_path="/static/terminal")


def _user():
    return flask.g.mast_user


def _error(status, message):
    return flask.jsonify({"error": message}), status


def _body():
    body = flask.request.get_json(silent=True)
    return body if isinstance(body, dict) else {}


def _names(value):
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()]


def _credentials(value):
    """`["user:password"]` as typed: a password may start or end with
    a space."""
    if not isinstance(value, list):
        return []
    return [v for v in value if isinstance(v, str) and v]


@blueprint.get("/terminal")
def terminal_page():
    """
    _function_: `mast.datapower.ssh.page.terminal_page()`

    The Terminal page.

    Exposed at: `/terminal` via GET
    """
    return flask.render_template(
        "terminal.html",
        may_store=_may_use_stored())


def _may_use_stored():
    """Whether a rule of the user lets them use a stored credential, on
    some targets (each use is checked when the session opens)."""
    from mast import permissions
    return any(match.rule.path.startswith("mast/credentials/")
               and "x" in match.rule.allow and match.rule.scope
               for match in permissions.rules_of(_user().name))


@blueprint.get("/api/v1/ssh/sessions")
def sessions():
    """
    _function_: `mast.datapower.ssh.page.sessions()`

    The user's open sessions.

    Exposed at: `/api/v1/ssh/sessions` via GET
    """
    return flask.jsonify({"sessions": [
        s.json() for s in MANAGER.list(_user())]})


def _targets(names):
    """The registered appliances or hostnames that `names` stand for
    (an environment stands for its appliances)."""
    from mast import registry
    try:
        return registry.resolve(names)
    except registry.UnknownName as exc:
        raise ValueError(str(exc)) from None


def _check_stored(name, targets, who):
    """A `change` credential that `who` may use, bound to every
    appliance, and a pinned SSH host key on each: else `Forbidden`."""
    from mast.credentials import store
    credential = store.check_use(name, [t.name for t in targets], who,
                                 command=COMMAND, effect="change")
    if credential.purpose != store.CHANGE:
        raise Forbidden("The credential {} is not for changes (purpose "
                        "change).".format(name))
    unpinned = [t.name for t in targets if not _ssh_pinned(t)]
    if unpinned:
        raise Forbidden(
            "A stored credential is never sent over SSH to an appliance "
            "whose host key is not pinned: {}. Pin it in Admin → "
            "Appliances.".format(", ".join(unpinned)))


@blueprint.post("/api/v1/ssh/sessions")
def open_session():
    """
    _function_: `mast.datapower.ssh.page.open_session()`

    Open a session: `{appliances, credentials: ["user:password"] |
    credential: NAME, domain}`. Returns the session, with each
    appliance's state (`connected`, `error`, `banner`).

    Exposed at: `/api/v1/ssh/sessions` via POST
    """
    from mast.web.auth import principal
    body = _body()
    names = _names(body.get("appliances"))
    if not names:
        return _error(422, "Add the appliances to the target bar first.")
    try:
        targets = _targets(names)
    except ValueError as exc:
        return _error(422, str(exc))
    if not targets:
        return _error(422, "Add the appliances to the target bar first.")
    most = MANAGER.settings.max_appliances_per_session
    if len(targets) > most:
        return _error(422, "A session has at most {} appliances "
                           "(max_appliances_per_session in ssh.conf).".format(
                               most))
    credential = str(body.get("credential") or "").strip() or None
    credentials = _credentials(body.get("credentials"))
    domain = str(body.get("domain") or "default").strip() or "default"
    who = principal()
    if credential:
        try:
            _check_stored(credential, targets, who)
        except Forbidden as exc:
            return _error(403, str(exc))
    else:
        if not credentials or any(":" not in c for c in credentials) \
                or len(credentials) not in (1, len(targets)):
            return _error(422, "Type a user name and a password in the "
                               "target bar, or choose a stored credential.")
    try:
        session = MANAGER.open(_user(), who, [t.name for t in targets],
                               credentials=credentials, credential=credential,
                               domain=domain)
    except TooManySessions as exc:
        return _error(429, str(exc))
    except Forbidden as exc:
        return _error(403, str(exc))
    return flask.jsonify(session.json()), 201


def _session(session_id):
    return MANAGER.get(session_id, _user())


@blueprint.errorhandler(NoSuchSession)
def _no_session(exc):
    return _error(404, "There is no such terminal session open.")


@blueprint.get("/api/v1/ssh/sessions/<session_id>")
def one_session(session_id):
    """
    _function_: `mast.datapower.ssh.page.one_session(session_id)`

    One session, with the transcript of each appliance, so that a
    reloaded page shows what it showed.

    Exposed at: `/api/v1/ssh/sessions/<session_id>` via GET
    """
    session = _session(session_id)
    answer = session.json()
    for pane in answer["appliances"]:
        pane["transcript"] = session.transcript(pane["name"])
    return flask.jsonify(answer)


def _line_answer(line, appliance=None):
    answer = line.json(appliance)
    return flask.jsonify(answer), 200 if answer["finished"] else 202


@blueprint.post("/api/v1/ssh/sessions/<session_id>/lines")
def send_line(session_id):
    """
    _function_: `mast.datapower.ssh.page.send_line(session_id)`

    Send `{line, appliances}` (all the connected appliances if none).
    `200` with every answer, or `202` with those that are done after
    2 seconds; then ask `GET .../lines/<number>`.

    Exposed at: `/api/v1/ssh/sessions/<session_id>/lines` via POST
    """
    body = _body()
    text = body.get("line")
    if not isinstance(text, str) or "\n" in text or "\r" in text:
        return _error(422, "Send one line.")
    names = body.get("appliances")
    names = _names(names) if names is not None else None
    if names is not None and not names:
        return _error(422, "Choose the appliances for the line.")
    try:
        line = MANAGER.send(session_id, _user(), text, names)
    except ValueError as exc:
        return _error(422, str(exc))
    return _line_answer(line)


@blueprint.get("/api/v1/ssh/sessions/<session_id>/lines/<int:number>")
def get_line(session_id, number):
    """
    _function_: `mast.datapower.ssh.page.get_line(session_id, number)`

    A line and the answers that came (`?appliance=` for one).

    Exposed at: `/api/v1/ssh/sessions/<session_id>/lines/<number>` via GET
    """
    appliance = flask.request.args.get("appliance") or None
    line = MANAGER.line(session_id, _user(), number, appliance)
    return _line_answer(line, appliance)


@blueprint.post("/api/v1/ssh/sessions/<session_id>/interrupt")
def interrupt(session_id):
    """
    _function_: `mast.datapower.ssh.page.interrupt(session_id)`

    Send Ctrl-C to `{appliances}` (all) that are running a line.

    Exposed at: `/api/v1/ssh/sessions/<session_id>/interrupt` via POST
    """
    names = _body().get("appliances")
    sent = MANAGER.interrupt(session_id, _user(),
                             _names(names) if names is not None else None)
    return flask.jsonify({"interrupted": sent})


@blueprint.post("/api/v1/ssh/sessions/<session_id>/reconnect")
def reconnect(session_id):
    """
    _function_: `mast.datapower.ssh.page.reconnect(session_id)`

    Connect `{appliance}` again, with the stored credential of the
    session or with `credentials` (typed again).

    Exposed at: `/api/v1/ssh/sessions/<session_id>/reconnect` via POST
    """
    body = _body()
    session = _session(session_id)
    name = str(body.get("appliance") or "")
    credentials = _credentials(body.get("credentials"))
    if not session.credential and (
            not credentials or any(":" not in c for c in credentials)):
        return _error(422, "Type the user name and the password again in "
                           "the target bar.")
    try:
        pane = MANAGER.reconnect(session_id, _user(), name,
                                 credentials=credentials[:1],
                                 domain=str(body.get("domain") or "default"))
    except ValueError as exc:
        return _error(409, str(exc))
    answer = pane.json()
    answer["transcript"] = session.transcript(name)
    return flask.jsonify(answer)


@blueprint.get("/api/v1/ssh/sessions/<session_id>/transcript")
def transcript(session_id):
    """
    _function_: `mast.datapower.ssh.page.transcript(session_id)`

    The transcript of one appliance (`?appliance=`), or of all, as a
    text file. Secrets are `********` in it.

    Exposed at: `/api/v1/ssh/sessions/<session_id>/transcript` via GET
    """
    session = _session(session_id)
    appliance = flask.request.args.get("appliance") or None
    if appliance is not None and appliance not in session.panes:
        raise NoSuchSession(appliance)
    head = "# MAST terminal, job {}, opened {} by {}\n".format(
        session.job_id, session.opened_at.strftime("%Y-%m-%d %H:%M:%S UTC"),
        session.user_name)
    name = "terminal-{}{}.txt".format(
        session.opened_at.strftime("%Y%m%d-%H%M%S"),
        "-" + "".join(c if c.isalnum() or c in "-_." else "_"
                      for c in appliance) if appliance else "")
    response = flask.make_response(head + session.transcript(appliance))
    response.headers["Content-Type"] = "text/plain; charset=utf-8"
    response.headers["Content-Disposition"] = \
        'attachment; filename="{}"'.format(name)
    return response


@blueprint.delete("/api/v1/ssh/sessions/<session_id>")
def close(session_id):
    """
    _function_: `mast.datapower.ssh.page.close(session_id)`

    Close the session: disconnect every appliance, and end its job with
    the transcript.

    Exposed at: `/api/v1/ssh/sessions/<session_id>` via DELETE
    """
    session = _session(session_id)
    MANAGER.close(session.id, _user())
    return flask.jsonify({"closed": session.id, "job_id": session.job_id})


page = Page(name="terminal", title="Terminal", order=70, requires="datapower.terminal",
            blueprint=blueprint)


def _ssh_pinned(target):
    """True if the SSH endpoint of the target has a pinned host key."""
    trust = target.trust_for("ssh")
    return trust is not None and bool(trust.pin)

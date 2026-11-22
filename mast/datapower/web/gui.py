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
from io import StringIO, BytesIO
from importlib import metadata
from mast.timestamp import Timestamp
from mast.web import auth as web_auth
from mast.web import security
from mast.web.security import (
    CONTENT_SECURITY_POLICY, CSRF_FIELD, CSRF_HEADER, csrf_token, secret_key)
from mast.datapower import datapower
from mast.datapower.datapower import is_environment, get_appliances
from mast.xor import xordecode, xorencode
from mast.config import get_configs_dict, MAST_HOME
from urllib.parse import unquote
from mast.logging import make_logger
from mast import jobs
from mast.jobs import auth as jobs_auth
from mast.plugin_utils import plugin_functions
from markupsafe import Markup
import logging
import random
import re
from secrets import token_hex
from werkzeug.utils import secure_filename
import string
import flask
import json
import os
import getpass

config = get_configs_dict()["server.conf"]

static_dir = config["dirs"]["static"]
template_dir = config["dirs"]["template"]
if template_dir != os.path.abspath(template_dir):
    template_dir = os.path.join(MAST_HOME, template_dir)
plugin_dir = config["dirs"]["plugins"]
plugin_dir = os.path.join(MAST_HOME, plugin_dir)
upload_dir = config["dirs"]["upload"]
upload_dir = os.path.join(MAST_HOME, upload_dir)
static_path = config["paths"]["static"]
log_file = config["logging"]["file"]
log_level = int(config["logging"]["level"])
debug = bool(config["server"]["debug"])
port = int(config["server"]["port"])
host = config["server"]["host"]
max_file_upload_size = int(config["server"]["max_file_upload_size"])
threaded = bool(config["server"]["threaded"])
secure = bool(config["server"]["secure"])
# How long a browser keeps to HTTPS for this server after it saw the
# web GUI over TLS (HSTS). 0 tells browsers to forget it.
hsts_max_age = int(config["server"].get("hsts_max_age") or 31536000)

app = flask.Flask(
    __name__,
    static_folder=static_dir,
    static_url_path=static_path,
    template_folder=template_dir)


app.secret_key = secret_key(config["server"])
# The classic GUI shares the session cookie of the web UI (mast.web.app),
# which mounts it under /classic/: the same name, the path /, and the same
# flags. Over TLS the cookie is only sent over TLS (Secure), scripts cannot
# read it (HttpOnly), and the requests that other sites make in the
# background do not carry it (SameSite=Lax).
app.config["SESSION_COOKIE_PATH"] = "/"
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SECURE"] = secure
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
wzl = logging.getLogger("werkzeug")
app.root_path = os.environ["MAST_HOME"]
log_file = os.path.join(os.environ["MAST_HOME"], log_file)
file_handler = logging.FileHandler(log_file)
file_handler.setLevel(log_level)
wzl.addHandler(file_handler)

app.debug = debug


def initialize_plugins():
    """
    _function_: `mast.datapower.web.initialize_plugins()`

    Initializes all of the plugins for the MAST web GUI

    Logs to: `gui.log`

    Parameters:

    This function accepts no arguments.

    """
    logger = make_logger("gui")
    logger.debug("Running as user {}".format(getpass.getuser()))
    logger.debug("Running in directory {}".format(os.getcwd()))
    logger.debug("Attempting to retrieve list of web plugins")
    plugins_dict = {}
    entry_points = metadata.entry_points()
    if hasattr(entry_points, "select"):
        candidates = entry_points.select(group='mast_web_plugin')
    else:
        candidates = entry_points.get('mast_web_plugin', [])
    for ep in candidates:
        logger.debug("found plugin: {}".format(ep.name))
        try:
            plugins_dict.update({ep.name: ep.load()})
        except Exception:
            logger.exception(
                "An unhandled exception occurred during execution.")
            pass
    logger.info("Collected plugins {}".format(str(list(plugins_dict.keys()))))

    plugins = {
        'css': '',
        'html': '',
        'js': '',
        'tabs': ''}

    for name, cls in sorted(plugins_dict.items()):
        _plugin = cls()
        plugins[name] = _plugin

        try:
            plugins["html"] += Markup(_plugin.html())
            plugins["tabs"] += Markup(
                '<li><a href="#mast.datapower.{0}">{0}</a></li>'.format(name))
            plugins["css"] += Markup(_plugin.css())
            plugins["js"] += Markup(_plugin.js())
        except Exception:
            logger.exception(
                "An unhandled exception occured while attempting "
                "to gather the content for web plugin {}".format(name))
            raise
        # Route function
        try:
            app.add_url_rule(
                '/%s' % (name),
                view_func=_plugin.route,
                methods=["GET", "POST", "DELETE", "PUT"])
        except Exception:
            logger.exception(
                "An unhandled exception occured while attempting "
                "to assign a handler for web plugin {}".format(name))
            raise
    # print(Markup(plugins["js"]))
    return plugins

# The configuration files that /config/ does not serve
_PRIVATE_CONFIGS = frozenset(("xor.conf", "server.conf", "jobs.conf"))


@app.route('/config/<_file>')
def get_json_config(_file):
    """
    _function_: `mast.datapower.web.get_json_config(_file)`

    return merged confiuration from `$MAST_HOME/etc/default/_file`
    and `$MAST_HOME/etc/local/_file` in json format this is used to
    allow `mast_web` plugins to have configuration within MAST's
    unified configuration system.

    Exposed at: `/config/_file` via GET

    Parameters:

    * `_file`: The name of the configuration file to parse and return.
    """
    _file = _file if _file.endswith(".conf") else "{}.conf".format(_file)
    # These files can hold keys or the URL of the database, with its
    # password. Every user who logs in can read this route.
    if _file in _PRIVATE_CONFIGS:
        return flask.jsonify({})

    config = get_configs_dict()[_file]
    return flask.jsonify(config)


@app.route('/test/connectivity/<hostname>')
def check_connectivity(hostname):
    """
    _function_: `mast.datapower.web.check_connectivity(hostname)`

    This function will return a JSON response to a GET request containing the
    hostname and credentials (encrypted). The response will be a mapping
    of hostname to boolean value indicating whether or not we were able
    to reach the appliance using the hostname and credentials.

    Exposed at: `/test/connectivity` via GET

    Parameters:

    * `hostname`: The hostname of the appliance to connect to.
    """
    resp = {}
    credentials = flask.request.args.get("credentials")
    credentials = xordecode(
        credentials.encode(),
        key=xorencode(
            flask.request.cookies["9x4h/mmek/j.ahba.ckhafn"],
            key="_"
        )
    )
    check_hostname = flask.request.args.get("check_hostname", True)
    check_hostname = False if "false" in check_hostname else check_hostname
    appl = datapower.DataPower(
        hostname,
        credentials,
        check_hostname=check_hostname)
    resp["soma"] = appl.is_reachable()
    if "Authentication failure" in appl.last_response.decode():
        resp["soma"] = False
    try:
        _resp = appl.ssh_connect(port=appl.ssh_port)
        appl.ssh_disconnect()
        resp["ssh"] = 'DataPower' in _resp
    except Exception:
        resp["ssh"] = False
    return flask.jsonify(resp)


@app.route('/')
def index():
    """
    _function_: `mast.datapower.web.index()`

    Renders index.html

    Exposed at: `/` via GET

    Parameters:

    This function accepts no arguments.
    """
    global PLUGINS
    random.seed()
    csrf_token()
    ephemeral_session = random.randint(10000, 99999)
    resp = flask.make_response(
        flask.render_template(
            'index.html',
            ephemeral_session=ephemeral_session,
            plugins=app.PLUGINS))
    resp.set_cookie(
        "9x4h/mmek/j.ahba.ckhafn",
        ''.join(random.choice(
            string.ascii_lowercase + string.ascii_uppercase + string.digits)
            for _ in range(64)),
        secure=secure, samesite="Lax")
    return resp


@app.route('/plugins.js')
def plugins_js():
    """
    _function_: `mast.datapower.web.plugins_js()`

    The JavaScript of all the web plugins, which runs when the page is
    ready. It is a file, not a script in the page, so that the Content
    Security Policy can forbid inline scripts.

    Exposed at: `/plugins.js` via GET
    """
    body = "$(function(){{\n{}\n}});\n".format(app.PLUGINS["js"])
    return flask.Response(body, mimetype="text/javascript")


@app.route('/plugins.css')
def plugins_css():
    """
    _function_: `mast.datapower.web.plugins_css()`

    The style sheets of all the web plugins, as one file.

    Exposed at: `/plugins.css` via GET
    """
    return flask.Response(str(app.PLUGINS["css"]), mimetype="text/css")


@app.route('/upload', methods=["POST"])
def upload():
    """
    _function_: `mast.datapower.web.upload()`

    This handles uploads of files, stores them in a temporary directory,
    and returns the path and filename in JSON format

    Exposed at: `/upload` via POST

    Parameters:

    This function does not accept any arguments, but does require a
    POST body to be present in the request.
    """
    global upload_dir
    UPLOAD_DIRECTORY = upload_dir
    # A file is uploaded to be used by a command, which only a role
    # that may run commands can do
    try:
        jobs_auth.authorize(web_auth.principal(), "submit")
    except jobs.Forbidden:
        flask.abort(403)
    t = Timestamp()
    fin = flask.request.files["_file_in"]
    # A directory of its own for each upload, and only the last part of
    # the name that the browser sent, without "..": the name must not
    # put the file anywhere else
    filename = os.path.join(
        UPLOAD_DIRECTORY,
        "{}-{}".format(t.timestamp, token_hex(4)))
    os.makedirs(filename)
    filename = os.path.join(
        filename, secure_filename(fin.filename or "") or "upload")
    fin.save(filename)
    return flask.jsonify({"filename": filename})


_HISTORY_ID = re.compile(r"^[0-9]{14}-[0-9a-f]{32}\.log$")


@app.route('/download_history', methods=["POST"])
def download_history():
    """
    _function_: `mast.datapower.web.download_history()`

    This function is Allows users to download a history of the requests
    and responses made to the SOMA interface.

    Exposed at: `/download_history` via POST

    Parameters:

    This function accepts no arguments, but does require a POST body to
    be present in the request.
    """
    _id = flask.request.form.get("id") or ""
    # The id that plugin_functions._save_history made:
    # <timestamp>-<id of the job>.log. Anything else could name another
    # file (with "../"), for example a key file.
    if not _HISTORY_ID.match(_id):
        flask.abort(404)
    ts = _id.split("-")[0]
    filename = os.path.join(
        "var",
        "www",
        "static",
        "tmp",
        "request_history",
        ts,
        _id)
    return flask.send_file(
        filename,
        download_name=_id,
        as_attachment=True)


@app.route('/download', methods=["GET", "POST"])
def download():
    """
    _function_: `mast.datapower.web.download()`

    This function is used to allow users to download ssh-transcripts.

    This function will accept a form POST with two fields:

    1. `hostname`: The hostname of the appliance for which the transcript
    was generated. This is only used for creating the filename.
    2. `content`: This is the content of the transcript and will be returned
    as a download for the user.

    Exposed at: `/download` via POST

    Parameters:

    This function accepts no arguments, but it does require a POST body
    to be present in the request.
    """
    # This needs to be more universal. This is currently only useful for
    # ssh transcripts. This should allow any relevant file to be downloaded.
    if flask.request.method == "POST":
        t = Timestamp()
        hostname = flask.request.form.get("hostname")
        filename = '%s-%s-ssh-transcript.txt' % (t.timestamp, hostname)
        f = BytesIO()
        f.write(
            unquote(
                flask.request.form.get("content")
            ).replace(
                '\n',
                os.linesep
            ).replace(
                "+",
                " "
            ).encode(),
        )
        f.seek(0)
        return flask.send_file(
            f,
            download_name=filename,
            as_attachment=True)


@app.route('/jobs/<job_id>')
def job_state(job_id):
    """
    _function_: `mast.datapower.web.job_state(job_id)`

    Return the state of a job as JSON: its status, the lines of
    progress after the event `after`, and the html of its output when
    it is finished. The browser asks for this again and again after it
    submits a form.

    Exposed at: `/jobs/<job_id>` via GET

    Parameters:

    * `job_id`: The id of the job.
    """
    try:
        after = int(flask.request.args.get("after", 0))
        return flask.jsonify(plugin_functions.job_state(
            job_id, after, principal=web_auth.principal()))
    except (jobs.JobNotFound, ValueError):
        flask.abort(404)
    except jobs.Forbidden:
        flask.abort(403)


@app.route('/jobs/<job_id>/cancel', methods=["POST"])
def cancel_job(job_id):
    """
    _function_: `mast.datapower.web.cancel_job(job_id)`

    Ask to cancel a job, and return its state as JSON, as `job_state`
    does. A job that runs stops at its next step.

    Exposed at: `/jobs/<job_id>/cancel` via POST

    Parameters:

    * `job_id`: The id of the job.
    * `after`: The id of the last event that the browser has.
    """
    try:
        after = int(flask.request.values.get("after", 0))
        return flask.jsonify(plugin_functions.cancel_job(
            job_id, after, principal=web_auth.principal()))
    except (jobs.JobNotFound, ValueError):
        flask.abort(404)
    except jobs.Forbidden:
        flask.abort(403)


@app.route('/environments/<name>')
def list_environment(name):
    """
    _function_: `mast.datapower.web.list_environment(name)`

    Return a JSON response of the appliances in environment name.
    If name is not an environment then return name.

    exposed at: `/environments/<name>` via GET

    Parameters:

    * `name`: The name of the environment to list.
    """
    if not is_environment(name):
        return flask.jsonify({'appliances': [name]})
    return flask.jsonify({'appliances': get_appliances(name)})


@app.before_request
def log_access():
    """
    _function_: `mast.datapower.web.log_access()`

    This function logs information about each request received.

    Logs To: `mast.web.access.log`

    Parameters:

    This function accepts no arguments.

    """
    r = flask.request
    logger = make_logger("mast.web.access")
    logger.info("method: {}, url: {}, client: {}".format(
        r.method,
        r.url,
        r.remote_addr))
    logger.debug("data: {}, headers: {}".format(
        r.data,
        str(r.headers).replace("\n", "; ")))

# Every request needs the login session of the web UI (mast.web.auth),
# before anything else. Without one, the browser goes to /login.
app.before_request(web_auth.require_login())


@app.context_processor
def _template_functions():
    # mast_root is where the classic GUI is mounted (/classic), for the
    # links in its pages and its scripts
    root = flask.request.script_root if flask.has_request_context() else ""
    return {"csrf_token": csrf_token, "mast_root": root}


@app.before_request
def check_csrf_token():
    """
    _function_: `mast.datapower.web.check_csrf_token()`

    Refuse a request that can change something (POST, PUT, DELETE,
    PATCH) unless it carries the CSRF token of the session. Another
    site can make the browser send such a request, with the cookies of
    the web GUI, but it cannot read the token.

    Logs to: `gui.log`
    """
    request = flask.request
    if security.csrf_ok(request):
        return None
    make_logger("gui").warning(
        "Refused {} {} from {}: no valid CSRF token".format(
            request.method, request.path, request.remote_addr))
    return flask.Response(
        "This request has no valid CSRF token. Reload the web GUI and "
        "try again.\n", status=403, mimetype="text/plain")


@app.after_request
def security_headers(response):
    """
    _function_: `mast.datapower.web.security_headers(response)`

    Add the headers with which the browser defends the web GUI: the
    Content Security Policy, no guessing of content types, no referrer
    to other sites, and HSTS when the web GUI uses TLS (`secure`).
    """
    return security.add_security_headers(response, secure, hsts_max_age)


with app.app_context():
    flask.current_app.PLUGINS = initialize_plugins()

# The names of the web plugins, each of which is a route: /<name>
_PLUGIN_NAMES = frozenset(app.PLUGINS) - {"css", "html", "js", "tabs"}


@app.before_request
def check_plugin_role():
    """
    _function_: `mast.datapower.web.check_plugin_role()`

    Refuse (403) a request to a web plugin that is not a GET unless the
    user may run commands (`submit`). Such a request runs a command,
    and the `ssh` tab talks to the appliances without the job queue,
    which would not check the role.
    """
    request = flask.request
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return None
    if request.path.strip("/").split("/")[0] not in _PLUGIN_NAMES:
        return None
    try:
        jobs_auth.authorize(web_auth.principal(), "submit")
    except jobs.Forbidden:
        return flask.Response(
            "Your role may not run commands.\n", status=403,
            mimetype="text/plain")
    return None


def main():
    """
    _function_: `mast.datapower.web.main()`

    Serve the web UI, with this classic GUI under `/classic/`. See
    `mast.web.server.main`.
    """
    from mast.web.server import main as serve
    serve()


def stop_server():
    """
    _function_: `mast.datapower.web.stop_server()`

    Stop the web server. See `mast.web.server.stop_server`.
    """
    from mast.web.server import stop_server as stop
    stop()


if __name__ == "__main__":
    main()

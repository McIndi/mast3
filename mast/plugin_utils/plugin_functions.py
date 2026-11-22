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
This module is meant to consolidate the functionality of the following
plugins:

    1_system
    2_accounts
    3_backups
    4_developer
    5_network

This is possible since, even though the functionality between these plugins
is completely different, the structure is identical. In essence these plugins
dynamically create forms based on the function signatures of the functions
within the corresponding bin scripts.

The point of this is to recreate the functionality of the MAST CLI in the MAST
web GUI. So, I was able to consolidate all of the functionality of these
plugins into this single module.
"""
import re
import os
import threading
import traceback
import flask
import zipfile
import markdown
from markupsafe import Markup
from html import escape as html_escape
import html.entities as html_entities
from mast.config import get_config
from mast.datapower.datapower import Environment
from mast.xor import xordecode, xorencode
from mast.timestamp import Timestamp
from mast.logging import make_logger
from mast import jobs
from mast.jobs import auth as jobs_auth
from mast.jobs import client as jobs_client
from mast.jobs import registry as jobs_registry
from mast.cli import Result, captured
from mast.cli import params as cli_params
from mast.plugin_utils import choices
from mast.plugin_utils import params as widgets
from pydantic import ValidationError

from mast.plugin_utils.choices import OBJECT_STATUS_ARGS, STATUS_PROVIDERS

def _zipdir(path, z):
    """Create a zip file z of all files in path recursively"""
    for root, _, files in os.walk(path):
        for f in files:
            filename = os.path.join(
                *os.path.join(root, f).split(os.path.sep)[2:])
            z.write(os.path.join(root, f), filename)


def _has_files(path):
    """Return True if there is a file in path or below it."""
    return any(files for _, _, files in os.walk(path))


def get_module(plugin):
    """Return the imported objects which correspond to plugin.
    These are all from bin (which is a module itself)."""
    module = __import__("mast.datapower", globals(), locals(), [plugin], 0)
    return getattr(module, plugin)


def unescape(text):
    """Removes HTML or XML character references and entities from a text string.
    @param text The HTML (or XML) source text.
    @return The plain text, as a Unicode string, if necessary.

    This function was taken from:
    http://effbot.org/zone/re-sub.htm#unescape-html

    written by: Fredrik Lundh"""

    def fixup(m):
        text = m.group(0)
        if text[:2] == "&#":
            # character reference
            try:
                if text[:3] == "&#x":
                    return chr(int(text[3:-1], 16))
                else:
                    return chr(int(text[2:-1]))
            except ValueError:
                pass
        else:
            # named entity
            try:
                text = chr(html_entities.name2codepoint[text[1:-1]])
            except KeyError:
                pass
        return text  # leave as is
    return re.sub(r"&#?\w+;", fixup, text)



def html(plugin):
    """Return the html for plugin's tab"""
    htm = []
    module = get_module(plugin.replace("mast.datapower.", ""))
    last_category = ''
    command_list = module.cli.categories()
    for category in sorted(command_list):
        for command in command_list[category]:
            callable_name = command.key.replace('_', ' ')
            if category != last_category:
                htm.append(
                    flask.render_template(
                        'categorylabel.html', category=category))
            htm.append(
                flask.render_template(
                    'dynbutton.html', plugin=plugin, callable=callable_name))
            last_category = category
    return unescape(flask.render_template(
        'dynplugin.html', plugin=plugin, buttons=''.join(htm)))


def _get_command(plugin, fn_name):
    """Return the `mast.cli.Command` of plugin whose function is named
    fn_name."""
    return get_module(plugin).cli.find(fn_name)


def render_textbox(key, value):
    """Render a textbox for a dynamic form."""
    name = key
    label = key.replace('_', ' ')
    return flask.render_template(
        "textbox.html", name=name,
        label=label, value=value)


def render_password_box(key, value):
    """Render a textbox for a dynamic form."""
    name = key
    label = key.replace('_', ' ')
    return flask.render_template(
        "passwordbox.html", name=name,
        label=label, value=value)


def render_checkbox(key, checked=False):
    """Render a checkbox for a dynamic form."""
    name = key
    label = key.replace('_', ' ')
    checked = "checked=checked" if checked else ""
    return flask.render_template(
        "checkbox.html", name=name,
        label=label, checked=checked)


def render_multitext(key):
    """Render a multi-value textbox for a dynamic form."""
    _id = key
    label = key.replace('_', ' ')
    return flask.render_template("multitext.html", id=_id, label=label)


def render_file_upload(plugin, key):
    """Render our custom file upload form control for a dynamic form."""
    name = key
    label = key.replace('_', ' ')
    return flask.render_template(
        "fileupload.html", name=name,
        label=label, plugin=plugin)


def _model_form(plugin, command, env):
    """Return the controls of the form for a command. Each control comes from the type and the widget of one
    parameter."""
    textboxes = []
    checkboxes = []
    file_uploads = []
    selects = []
    for spec in cli_params.options(command.model):
        key, widget, many = spec.name, spec.widget or "", spec.kind == "list"
        if widget in (widgets.APPLIANCES, widgets.CREDENTIALS,
                      widgets.OUT_DIR, widgets.OUT_FILE):
            # The web GUI supplies these, see _model_values
            continue
        elif widget == widgets.STORED_CREDENTIAL:
            # Only the new web UI offers the stored credentials
            continue
        elif widget == widgets.UPLOAD:
            file_uploads.append(render_file_upload(plugin, key))
        elif widget in (widgets.STATUS_PROVIDER, widgets.OBJECT_CLASS) \
                or widget.startswith("configured:"):
            selects.append(_render_select(
                key, choices.choices(widget, env), many,
                disclaimer=widget.startswith("configured:")))
        elif spec.kind == "flag":
            checkboxes.append(render_checkbox(key, checked=bool(spec.default)))
        elif spec.choices:
            selects.append(_render_select(key, spec.choices, many))
        elif many:
            textboxes.append(render_multitext(key))
        elif spec.secret:
            textboxes.append(render_password_box(key, spec.default or ""))
        else:
            textboxes.append(render_textbox(
                key, "" if spec.default is None else spec.default))
    return textboxes + selects + file_uploads + checkboxes


def _render_select(key, options, many=False, disclaimer=False):
    if many:
        return flask.render_template(
            "multiselect.html", options=options, key=key,
            disclaimer=disclaimer)
    return flask.render_template(
        "dynselect.html", options=options, name=key, disclaimer=disclaimer)


def get_form(plugin, fn_name, appliances, credentials, no_check_hostname=True):
    """Return a form suitable for gathering arguments to function name"""
    check_hostname = not no_check_hostname
    command = _get_command(plugin, fn_name)
    env = Environment(appliances, credentials, check_hostname=check_hostname)
    forms = ['<div class="{0}Form"><div name="{1}">'.format(plugin, fn_name)]
    forms.append(flask.render_template(
        'formlabel.html', label=fn_name.replace('_', ' ')))
    forms.append('<a href="#" class="help">help</a>')
    forms.append('<div class="hidden help_content">{}</div>'.format(
        Markup(markdown.markdown(command.doc))))
    forms.extend(_model_form(plugin, command, env))
    forms.append(flask.render_template('submitbutton.html', plugin=plugin))
    forms.append('</div></div>')
    return '<br />\n'.join(forms)


def _save_history(hist, token):
    """Write the request history to a file for the history download and
    return the id of the file. `token` is the id of the job, so that
    two jobs never have the same file."""
    t = Timestamp()

    filename = os.path.join(
        "var", "www", "static", "tmp", "request_history", t.timestamp)
    os.makedirs(filename, exist_ok=True)
    _id = "{}-{}.log".format(str(t.timestamp), token)
    filename = os.path.join(filename, _id)
    with open(filename, 'wb') as fout:
        fout.write(hist.encode())
    return _id


def _model_values(command, form, t, token):
    """Gather the values for a command that declares its parameters
    with a model. Return a dict to build the model from. A value that
    the user left empty is left out, so that the model uses its
    default.

    `token` is the id of the job. It is part of the paths that the
    command writes to, so that two jobs never write to (and never
    offer for download) the same files."""
    name = command.key
    values = {}
    for spec in cli_params.options(command.model):
        key, widget = spec.name, spec.widget
        if widget == widgets.CREDENTIALS:
            values[key] = [
                xordecode(
                    _.encode(), key=xorencode(
                        flask.request.cookies["9x4h/mmek/j.ahba.ckhafn"], key="_"))
                for _ in form.getlist(key + '[]')]
        elif widget == widgets.OUT_DIR:
            values[key] = os.path.join(
                'tmp', 'web', name, "{}-{}".format(t.timestamp, token))
        elif widget == widgets.OUT_FILE:
            extension = os.path.splitext(str(spec.default or ""))[1]
            values[key] = "/".join((
                "tmp", "web", name,
                "{}-{}-{}{}".format(
                    t.timestamp, name, token[:8], extension)))
        elif spec.kind == "flag":
            values[key] = form.get(key) == 'true'
        elif spec.kind == "list":
            values[key] = form.getlist(key + '[]')
        elif form.get(key) not in (None, ""):
            values[key] = form.get(key)
    return values


def _check(command, values):
    """Return what is wrong with the values for a command, or `None`
    if the command can be queued."""
    appliances = [
        spec.name for spec in cli_params.options(command.model)
        if spec.widget == widgets.APPLIANCES]
    if appliances and not any(values.get(appliances[0], [])):
        return "Must select at least one appliance."
    try:
        command.model(**values)
    except ValidationError as exc:
        return "; ".join(cli_params.error_messages(
            command.model, exc, flags=False))
    return None


def call_method(plugin, form, principal=jobs.LOCAL):
    """Gather the arguments and function name from form, then put the
    command in the job queue as `principal`. Return the html of a box
    that the browser fills with the progress and the result of the job
    (see `job_state`). If the arguments are not valid, or the principal
    may not run commands, return the html of the problem and queue
    nothing."""
    t = Timestamp()
    name = form.get("callable")
    cli = get_module(plugin).cli
    command = cli.find(name)
    job_id = jobs.new_job_id()
    kwargs = _model_values(command, form, t, job_id)
    problem = _check(command, kwargs)
    if problem is not None:
        return _render_output(
            name, t, kwargs, Markup(html_escape(problem)),
            _save_history(problem, job_id), job_id)
    if not getattr(command, "queued", True):
        try:
            jobs_auth.authorize(principal, "submit", command=name)
        except jobs.Forbidden as exc:
            return _render_output(
                name, t, kwargs,
                Markup(html_escape(_queue_error_message(exc))),
                _save_history(str(exc), job_id), job_id)
        out, history = _call_now(command, kwargs)
        return _render_output(
            name, t, kwargs, out, _save_history(history, job_id), job_id)
    # The namespace is the name of the plugin, which is how a worker
    # finds the command
    namespace = getattr(cli, "namespace", None) or plugin
    try:
        jobs_registry.register(namespace, cli)
        jobs_client.start(
            jobs_registry.command_id(namespace, command), kwargs,
            principal=principal, job_id=job_id)
    except Exception as exc:
        # The browser shows this box. Without it, the user would see
        # nothing at all.
        make_logger("mast.plugin_functions").exception(
            "Could not queue {}".format(name))
        # The box shows no parameters, so the credentials in kwargs are
        # not shown
        return _render_output(
            name, t, kwargs, Markup(html_escape(_queue_error_message(exc))),
            _save_history(traceback.format_exc(), job_id), job_id)
    return flask.render_template(
        "pending.html", job_id=job_id, callable=name, timestamp=str(t))


def _queue_error_message(exc):
    """What the user sees when a command could not be queued."""
    if isinstance(exc, jobs.Forbidden):
        return "You are not allowed to run this command on these appliances."
    return ("Sorry, an unhandled exception occurred while performing "
            "action:\n\n\t {}".format(exc))


def _call_now(command, values):
    """Call a command that does not go through the job queue. Return
    the html of its result and its request history. Nothing is
    stored."""
    try:
        # The progress messages are for the terminal
        with captured():
            result = command.fn(command.model(**values))
    except Exception as exc:
        return Markup(html_escape(
            "Sorry, an unhandled exception occurred while "
            "performing action:\n\n\t {}".format(exc))), \
            traceback.format_exc()
    if isinstance(result, Result):
        return Markup(result.html()), result.history
    return Markup(""), ""


# One job is rendered by one thread at a time
_render_lock = threading.Lock()


def _rendered_path(job_id):
    return os.path.join("tmp", "web", "jobs", "{}.html".format(job_id))


def _finished_html(info):
    """Return the html of the output box of a finished job. The box
    is made once for each job and kept in a file, because making it
    writes the download and the history of the job."""
    path = _rendered_path(info.id)
    with _render_lock:
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as fin:
                return fin.read()
        result = jobs.result(info.id)
        history = result.history if result is not None else ""
        if info.status == jobs.SUCCEEDED:
            out = Markup(result.html if result is not None else "")
        elif result is not None and result.html:
            # The command ended, and its result says that some or all
            # of what it did failed
            out = Markup("<p><b>{}</b></p>".format(html_escape(
                info.error or info.status))) + Markup(result.html)
        elif info.status == jobs.FAILED:
            out = Markup(html_escape(
                "Sorry, an unhandled exception occurred while "
                "performing action:\n\n\t {}".format(info.error)))
        else:
            out = Markup(html_escape(
                info.error or "The job was {}.".format(info.status)))
            history = history or (info.error or info.status)
        command = jobs.resolve(info.command)
        rendered = str(_render_output(
            command.key, Timestamp(info.created_at.timestamp()),
            info.params, out, _save_history(history, info.id), info.id))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # Another process must not read half of the file
        partial = "{}.{}.tmp".format(path, os.getpid())
        with open(partial, "w", encoding="utf-8") as fout:
            fout.write(rendered)
        os.replace(partial, path)
        return rendered


def job_state(job_id, after=0, principal=jobs.LOCAL):
    """Return what the browser needs to show a job, as a dict for
    JSON:

    * `status` and `finished`: See `mast.jobs.JobInfo`.
    * `events`: The lines of progress after the event `after`.
    * `last_event`: The value of `after` for the next call.
    * `html`: The output box, if the job is finished.

    Raises `mast.jobs.JobNotFound`, or `mast.jobs.Forbidden` if
    `principal` may not read the job."""
    # A job whose worker died is declared lost here, so that the box
    # does not show it as running for ever
    info = jobs.reap_if_expired(jobs.get(job_id, principal), principal)
    events = jobs.events(job_id, after, principal)
    state = {
        "id": info.id,
        "status": info.status,
        "finished": info.finished,
        "cancel_requested": info.cancel_requested,
        "events": [event.message for event in events if event.kind == "say"],
        "last_event": events[-1].id if events else after,
    }
    if info.finished:
        state["html"] = _finished_html(info)
    return state


def cancel_job(job_id, after=0, principal=jobs.LOCAL):
    """Ask to cancel a job as `principal`, and return its state as
    `job_state` does, with the events after the event `after`."""
    jobs.cancel(job_id, principal)
    return job_state(job_id, after, principal)


def _render_output(name, t, kwargs, out, history_id, token):
    """Wrap the output of a command in html, with a link to download
    what the command wrote to out_dir or out_file. `token` is the id
    of the job, which keeps the download of one job apart from the
    download of another."""
    link = ""
    # Validation or execution may have failed before the command wrote
    # anything. A download is only offered if there is something in it.
    if 'out_dir' in kwargs and _has_files(kwargs['out_dir']):
        config = get_config("server.conf")
        static_dir = config.get('dirs', 'static')

        fname = ""
        for appliance in kwargs['appliances']:
            fname = "{}-{}".format(fname, appliance)
        fname = "{}-{}{}-{}.zip".format(
            t.timestamp, name, fname, token[:8])
        zip_filename = os.path.join(
            static_dir,
            'tmp',
            fname)
        zip_file = zipfile.ZipFile(zip_filename, 'w', zipfile.ZIP_DEFLATED)
        _zipdir(kwargs['out_dir'], zip_file)
        zip_file.close()
        #filename = '%s-%s.zip' % (t.timestamp, name)
        link = Markup(flask.render_template('link.html', filename=fname))
    if kwargs.get("out_file") and os.path.isfile(kwargs["out_file"]):
        import shutil
        config = get_config("server.conf")
        static_dir = config.get('dirs', 'static')
        dst = os.path.join(static_dir,
                           "tmp",
                           os.path.basename(kwargs["out_file"]))
        shutil.copyfile(kwargs["out_file"], dst)

        link = Markup(flask.render_template('link.html',
                                                  filename=os.path.basename(kwargs["out_file"])))
    out = flask.render_template(
        'output.html',
        output=out,
        callable=name,
        timestamp=str(t),
        history_id=history_id,
        link=link)
    return out


def handle(plugin):
    """main funcion which will be routed to the plugin's endpoint"""
    logger = make_logger("mast.plugin_functions")
    import urllib.request, urllib.parse, urllib.error
    if flask.request.method == 'GET':
        logger.info("GET Request received")
        name = flask.request.args.get('callable')
        logger.debug("name: {}".format(name))
        appliances = flask.request.args.getlist('appliances[]')
        logger.debug("appliances: {}".format(str(appliances)))
        credentials = [xordecode(urllib.parse.unquote(_).encode(), key=xorencode(
                        flask.request.cookies["9x4h/mmek/j.ahba.ckhafn"], key="_"))
                        for _ in flask.request.args.getlist('credentials[]')]
        logger.debug("getting form")
        try:
            form = get_form(plugin.replace("mast.", ""), name, appliances, credentials)
        except Exception:
            logger.exception("An unhandled exception occurred during execution.")
            raise
        logger.debug("Got form")
        return form
    elif flask.request.method == 'POST':
        logger.info("Received POST request for {}".format(plugin))
        try:
            return Markup(str(call_method(
                plugin, flask.request.form,
                principal=flask.g.get("mast_principal") or jobs.LOCAL)))
        except Exception:
            logger.exception("An unhandled exception occurred during processing of request.")
            raise

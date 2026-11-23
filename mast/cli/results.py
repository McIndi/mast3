# -*- coding: utf-8 -*-
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
_module_: `mast.cli.results`

What a command gives back, and how a command reports progress.

A command returns a `Result`. The command does not know who called
it: the command line prints the result as text, and the web GUI shows
it as HTML.

Most commands do not stop when one appliance refuses an action. They
do the action on the other appliances and show what happened on each
one. Such a result says how it went with `outcomes`, and the caller
reads `outcome`: `succeeded`, `partial` (some of it failed) or
`failed`. The command line turns this into its exit status and the
job queue into the status of the job.

A command reports progress with `say`. On the command line `say`
prints. The web GUI collects the messages with `captured`.

Code that is about to change something outside MAST calls
`checkpoint` first. Whoever runs the command can stop it there (see
`guarded`). The job queue stops a job in this way when the job was
cancelled, or when the worker lost it and another job can have the
appliances.
"""
import contextlib
import contextvars
import html

__all__ = ["Result", "Text", "Table", "say", "captured", "listening",
           "checkpoint", "guarded", "stored_credential",
           "using_stored_credential",
           "SUCCEEDED", "PARTIAL", "FAILED"]

# The values of Result.outcome. The job queue uses the same words for
# the status of a job.
SUCCEEDED = "succeeded"
PARTIAL = "partial"
FAILED = "failed"

_sink = contextvars.ContextVar("mast_cli_sink", default=None)
_guard = contextvars.ContextVar("mast_cli_guard", default=None)
_stored = contextvars.ContextVar("mast_cli_stored_credential", default=False)


def say(*parts):
    """
    _function_: `mast.cli.results.say(*parts)`

    Report progress to the user. The parts are joined with a space,
    as `print` does.
    """
    message = " ".join(str(part) for part in parts)
    sink = _sink.get()
    if sink is None:
        print(message)
    else:
        sink(message)


@contextlib.contextmanager
def listening(callback):
    """
    _function_: `mast.cli.results.listening(callback)`

    A context manager that gives each message from `say` to `callback`
    and does not print it. The job queue stores the messages this way.

        :::python
        with listening(store_message):
            result = command(params)
    """
    token = _sink.set(callback)
    try:
        yield
    finally:
        _sink.reset(token)


def checkpoint():
    """
    _function_: `mast.cli.results.checkpoint()`

    Say that the command is about to do its next step. Each request
    to an appliance calls this before it is sent. It does nothing
    unless the command runs inside `guarded`.
    """
    guard = _guard.get()
    if guard is not None:
        guard()


@contextlib.contextmanager
def guarded(callback):
    """
    _function_: `mast.cli.results.guarded(callback)`

    A context manager that calls `callback` at each `checkpoint` of
    the command. `callback` raises an exception to stop the command
    before its next step.
    """
    token = _guard.set(callback)
    try:
        yield
    finally:
        _guard.reset(token)


@contextlib.contextmanager
def stored_credential():
    """
    _function_: `mast.cli.results.stored_credential()`

    A context manager in which the command runs with a stored
    credential (see `mast.jobs.runner`). The code that connects to an
    appliance then refuses every connection that does not check who
    the appliance is (see `mast.datapower.datapower.tls`).
    """
    token = _stored.set(True)
    try:
        yield
    finally:
        _stored.reset(token)


def using_stored_credential():
    """
    _function_: `mast.cli.results.using_stored_credential()`

    True inside `stored_credential`.
    """
    return _stored.get()


@contextlib.contextmanager
def captured():
    """
    _function_: `mast.cli.results.captured()`

    A context manager that collects the messages from `say` in a list
    and does not print them.

        :::python
        with captured() as messages:
            result = command(params)
    """
    messages = []
    with listening(messages.append):
        yield messages


class Result(object):
    """
    _class_: `mast.cli.results.Result`

    The base class for what a command returns.

    * `text()`: The result for a terminal.
    * `html()`: The result for the web GUI.
    * `data()`: The result as JSON types, for the web UI to sort and
    filter, or None (the default) if it has no such form. A table is
    `{"kind": "table", "columns": [...], "rows": [[...], ...]}`, with
    a string in each cell. A result of several parts is
    `{"kind": "items", "items": [...]}`, where each item is a table or
    `{"kind": "text", "text": "..."}`.
    * `history`: The requests and responses that produced the result,
    for the web GUI's history download. May be empty.
    * `outcomes()`: A list with True or False for each thing that the
    command did (usually one action on one appliance): True if it
    worked. A result that does not know this returns an empty list.
    * `outcome`: `SUCCEEDED` if `outcomes()` has no False, `FAILED`
    if it has only False, otherwise `PARTIAL`.
    * `emit()`: Print the result. The command line calls this.
    """
    history = ""

    def outcomes(self):
        return []

    @property
    def outcome(self):
        outcomes = [bool(worked) for worked in self.outcomes()]
        if all(outcomes):
            return SUCCEEDED
        return PARTIAL if any(outcomes) else FAILED

    def text(self):
        return ""

    def html(self):
        return "<pre>{}</pre>".format(html.escape(self.text()))

    def data(self):
        return None

    def emit(self):
        text = self.text()
        if text:
            print(text)

    def __repr__(self):
        return "<{}>".format(type(self).__name__)


class Text(Result):
    """
    _class_: `mast.cli.results.Text(text, history="")`

    A result that is plain text.
    """
    def __init__(self, text, history=""):
        self._text = text
        self.history = history

    def text(self):
        return self._text


class Table(Result):
    """
    _class_: `mast.cli.results.Table(header, rows, history="")`

    A result that is a table. `header` is a sequence of column names
    and `rows` is a sequence of rows, each one a sequence of cells. A
    cell can have more than one line.
    """
    def __init__(self, header, rows, history=""):
        self.header = [str(cell) for cell in header]
        self.rows = [[str(cell) for cell in row] for row in rows]
        self.history = history

    def text(self):
        table = [self.header] + self.rows
        widths = [0] * max(len(row) for row in table)
        for row in table:
            for index, cell in enumerate(row):
                width = max(len(line) for line in cell.split("\n")) if cell else 0
                widths[index] = max(widths[index], width)
        lines = []
        for number, row in enumerate(table):
            cells = [cell.split("\n") for cell in row]
            for depth in range(max(len(cell) for cell in cells)):
                lines.append("  ".join(
                    (cell[depth] if depth < len(cell) else "").ljust(
                        widths[index])
                    for index, cell in enumerate(cells)).rstrip())
            if number == 0:
                lines.append("  ".join("-" * width for width in widths))
        return "\n".join(lines)

    def data(self):
        return {"kind": "table", "columns": list(self.header),
                "rows": [list(row) for row in self.rows]}

    def html(self):
        # The same markup as the results_table.html template.
        out = ['<table class="width-100">',
               '<tr class="results_table_header_row">']
        for cell in self.header:
            out.append('<td class="results_table_header_column">{}</td>'.format(
                html.escape(cell)))
        out.append("</tr>")
        for row in self.rows:
            out.append('<tr class="result_table_row">')
            for cell in row:
                out.append('<td class="result_table_cell">{}</td>'.format(
                    html.escape(cell).replace("\n", "<br />")))
            out.append("</tr>")
        out.append("</table>")
        return "\n".join(out)

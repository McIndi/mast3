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

A command reports progress with `say`. On the command line `say`
prints. The web GUI collects the messages with `captured`.
"""
import contextlib
import contextvars
import html

__all__ = ["Result", "Text", "Table", "say", "captured"]

_sink = contextvars.ContextVar("mast_cli_sink", default=None)


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
    token = _sink.set(messages.append)
    try:
        yield messages
    finally:
        _sink.reset(token)


class Result(object):
    """
    _class_: `mast.cli.results.Result`

    The base class for what a command returns.

    * `text()`: The result for a terminal.
    * `html()`: The result for the web GUI.
    * `history`: The requests and responses that produced the result,
    for the web GUI's history download. May be empty.
    * `emit()`: Print the result. The command line calls this.
    """
    history = ""

    def text(self):
        return ""

    def html(self):
        return "<pre>{}</pre>".format(html.escape(self.text()))

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
    and `rows` is a sequence of rows, each one a sequence of cells.
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
                    html.escape(cell)))
            out.append("</tr>")
        out.append("</table>")
        return "\n".join(out)

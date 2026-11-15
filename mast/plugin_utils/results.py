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
_module_: `mast.plugin_utils.results`

The results that the DataPower commands return. Each one is a
`mast.cli.Result`: the command line prints it as text and the web GUI
shows it as HTML.
"""
import os

from mast.cli import Result, Table, say
from mast.pprint import format_xml

__all__ = [
    "history", "ActionResults", "Report", "CommonItems", "SeeDownload",
    "ListResults", "StatusResults",
]


def history(env):
    """
    _function_: `mast.plugin_utils.results.history(env)`

    Return the requests and responses of every appliance in `env` as
    one string.
    """
    ret = []
    for appliance in env.appliances:
        ret.append(appliance.hostname)
        ret.append("=" * len(appliance.hostname))
        ret.append(appliance.history)
        ret.append(os.linesep)
    return os.linesep.join(ret)


def _label(host, suffix):
    return "{}-{}".format(host, suffix) if suffix else host


class _EnvironmentResult(Result):
    """A result that takes its history from an `Environment`. The
    history is only built when it is asked for, because the command
    line does not use it."""
    env = None

    @property
    def history(self):
        return history(self.env) if self.env is not None else ""


class ActionResults(_EnvironmentResult):
    """
    _class_: `mast.plugin_utils.results.ActionResults(responses, env=None, suffix=None)`

    The result of one action on each appliance. `responses` maps each
    hostname to the response from that appliance.

    The command line prints each response. The web GUI shows a table
    that says whether each appliance succeeded.
    """
    def __init__(self, responses, env=None, suffix=None):
        self.responses = responses
        self.env = env
        self.suffix = suffix

    def text(self):
        lines = []
        for host, response in self.responses.items():
            lines.append("{}\n{}".format(host, "=" * len(host)))
            lines.append(format_xml(response.xml))
        return "\n".join(lines)

    def html(self):
        return Table(
            ("Appliance", "Result"),
            [(_label(host, self.suffix),
              "Succeeded" if response else "Failed")
             for host, response in self.responses.items()]).html()


class Report(_EnvironmentResult):
    """
    _class_: `mast.plugin_utils.results.Report(env=None)`

    The result of a command that does several things in turn. The
    command adds the result of each step with `add`. The terminal gets
    each step when it is added, so the user sees progress. The web GUI
    shows all the steps when the command finishes.

    A step that the command already reported in its own words is added
    with `show=False`.

    A step can be the result of another command. The history of the
    report is the history of its own `env`, then of each step.
    """
    def __init__(self, env=None):
        self.env = env
        self.steps = []

    def add(self, result, show=True):
        self.steps.append(result)
        text = result.text() if show else ""
        if text:
            say(text)
        return result

    def html(self):
        return "".join(step.html() for step in self.steps)

    @property
    def history(self):
        own = history(self.env) if self.env is not None else ""
        return own + "".join(step.history for step in self.steps)


class CommonItems(_EnvironmentResult):
    """
    _class_: `mast.plugin_utils.results.CommonItems(title, items, env=None)`

    The items (users or groups, for example) on each appliance, and the
    items that every appliance has. `items` maps each hostname to a
    list of strings and `title` is the name of the items.
    """
    def __init__(self, title, items, env=None):
        self.title = title
        self.items = items
        self.env = env
        sets = [set(found) for found in items.values()]
        self.common = sorted(sets[0].intersection(*sets[1:])) if sets else []

    def text(self):
        lines = []
        for host, found in self.items.items():
            lines += ["", host, "=" * len(host)]
            lines += ["\t{}".format(item) for item in found]
        lines += ["", "Common", "======"]
        lines += ["\t{}".format(item) for item in self.common]
        return "\n".join(lines)

    def html(self):
        rows = [(host, "\n".join(found))
                for host, found in self.items.items()]
        rows.append(("All", "\n".join(self.common)))
        return Table(("Appliance", self.title), rows).html()


class SeeDownload(_EnvironmentResult):
    """
    _class_: `mast.plugin_utils.results.SeeDownload(hosts, env=None, suffix=None)`

    The result of a command that wrote files. The command reports each
    file with `mast.cli.say` as it writes it, so the command line has
    nothing more to print. The web GUI shows a table that points to
    the download link.
    """
    def __init__(self, hosts, env=None, suffix=None):
        self.hosts = list(hosts)
        self.env = env
        self.suffix = suffix

    def html(self):
        return Table(
            ("Appliance", "Result"),
            [(_label(host, self.suffix), "See Download")
             for host in self.hosts]).html()


class ListResults(_EnvironmentResult):
    """
    _class_: `mast.plugin_utils.results.ListResults(results, env=None,
    title="Result", suffix=None)`

    A list of items for each key. `results` maps each key (usually a
    hostname) to a list of strings. `title` is the heading of the
    items in the web GUI.
    """
    def __init__(self, results, env=None, title="Result", suffix=None):
        self.results = results
        self.env = env
        self.title = title
        self.suffix = suffix

    def text(self):
        lines = []
        for key, items in self.results.items():
            lines.append("{} \n {}".format(key, "-" * len(key)))
            lines.extend("\t{}".format(item) for item in items)
        return "\n".join(lines)

    def html(self):
        return Table(
            ("Appliance", self.title),
            [(_label(key, self.suffix), "\n".join(items))
             for key, items in self.results.items()]).html()


class StatusResults(_EnvironmentResult):
    """
    _class_: `mast.plugin_utils.results.StatusResults(results, env=None, suffix=None)`

    One word of status ("Verified", "Failed") for each key. `results`
    maps each key (usually a hostname) to its status.
    """
    def __init__(self, results, env=None, suffix=None):
        self.results = results
        self.env = env
        self.suffix = suffix

    def text(self):
        lines = []
        for key, status in self.results.items():
            lines += ["", key, "=" * len(key), str(status), ""]
        return "\n".join(lines)

    def html(self):
        return Table(
            ("Appliance", "Result"),
            [(_label(key, self.suffix), status)
             for key, status in self.results.items()]).html()

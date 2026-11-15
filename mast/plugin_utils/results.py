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

from mast.cli import Result, Table
from mast.pprint import pprint_xml

__all__ = ["history", "ActionResults", "SeeDownload", "ListResults"]


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

    def table(self):
        return Table(
            ("Appliance", "Result"),
            [(_label(host, self.suffix),
              "Succeeded" if response else "Failed")
             for host, response in self.responses.items()])

    def text(self):
        return self.table().text()

    def html(self):
        return self.table().html()

    def emit(self):
        for host, response in self.responses.items():
            print("{}\n{}".format(host, "=" * len(host)))
            pprint_xml(response.xml)


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
    _class_: `mast.plugin_utils.results.ListResults(results, env=None)`

    A list of items for each key. `results` maps each key (usually a
    hostname) to a list of strings.
    """
    def __init__(self, results, env=None):
        self.results = results
        self.env = env

    def text(self):
        lines = []
        for key, items in self.results.items():
            lines.append("{} \n {}".format(key, "-" * len(key)))
            lines.extend("\t{}".format(item) for item in items)
        return "\n".join(lines)

    def html(self):
        return Table(
            ("Appliance", "Result"),
            [(key, "\n".join(items))
             for key, items in self.results.items()]).html()

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
_module_: `mast.web.pages.commands`

The Commands page: choose a namespace, a category and a command, and
fill in a form that `static/js/forms.js` makes from the options of
the command (`GET /api/v1/commands`). Submitting it queues a job
(`POST /api/v1/jobs`) and opens the job on the Jobs page.
"""
import flask

from mast.web.page import Page

__all__ = ["page"]

blueprint = flask.Blueprint("commands", __name__)


@blueprint.get("/commands")
def commands():
    """
    _function_: `mast.web.pages.commands.commands()`

    The Commands page.

    Exposed at: `/commands` via GET
    """
    return flask.render_template("commands.html")


page = Page(name="commands", title="Commands", order=60,
            requires="submit", blueprint=blueprint)

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
_module_: `mast.web.pages.jobs`

The Jobs page: the jobs in the queue, newest first, with filters; one
job with its parameters, its progress, its result, its child jobs and
its downloads; and the workers. The page is HTML and a script
(`static/js/jobs.js`) that asks the JSON API (`mast.web.api`).
"""
import flask

from mast.web.page import Page

__all__ = ["page"]

blueprint = flask.Blueprint("jobs", __name__)


@blueprint.get("/jobs")
def jobs():
    """
    _function_: `mast.web.pages.jobs.jobs()`

    The list of jobs, and the workers.

    Exposed at: `/jobs` via GET
    """
    return flask.render_template("jobs.html")


@blueprint.get("/jobs/<job_id>")
def job(job_id):
    """
    _function_: `mast.web.pages.jobs.job(job_id)`

    One job. The script fills it in and follows it until it ends.

    Exposed at: `/jobs/<id>` via GET
    """
    return flask.render_template("job.html", job_id=job_id)


page = Page(name="jobs", title="Jobs", order=50, min_role="viewer",
            blueprint=blueprint)

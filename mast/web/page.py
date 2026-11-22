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
_module_: `mast.web.page`

The contract between the web UI and the packages that add pages to it.

A package adds a page with an entry point in the group `mast_web_page`
that names a `Page`:

    :::toml
    [project.entry-points.mast_web_page]
    jobs = "mast.web.pages.jobs:page"

The web UI registers the blueprint of each page, puts the page in its
navigation for the users whose role is at least `min_role`, and
refuses (403) every request to the blueprint from other users. The
blueprint has the templates, the static files and the routes of the
page, including its JSON routes under `/api/v1/<name>/...`.
"""
from dataclasses import dataclass, field
from importlib import metadata

import flask

from mast.auth import roles
from mast.logging import make_logger

__all__ = ["Page", "load_pages", "GROUP"]

GROUP = "mast_web_page"


@dataclass
class Page(object):
    """
    _class_: `mast.web.page.Page`

    One page of the web UI.

    * `name`: A short name, which is also the start of its path
    (`/<name>`).
    * `title`: The name in the navigation.
    * `order`: Its place in the navigation; lower comes first.
    * `min_role`: The lowest role that may see it (`viewer`,
    `operator` or `admin`).
    * `blueprint`: The `flask.Blueprint` with its routes.
    * `nav`: Its sub-pages, as `(title, path)` pairs.
    """
    name: str
    title: str
    order: int
    min_role: str
    blueprint: flask.Blueprint
    nav: tuple = field(default_factory=tuple)

    def __post_init__(self):
        self.blueprint.before_request(_guard(self))

    @property
    def path(self):
        return "/" + self.name


def _guard(page):
    """A before_request for the blueprint of `page`: only a user whose
    role is at least `page.min_role` may use it. The login check of the
    app comes first."""
    def check():
        user = flask.g.get("mast_user")
        if user is not None and roles.at_least(user.role, page.min_role):
            return None
        if flask.request.path.startswith("/api/"):
            return flask.jsonify({"error": "This needs the role {}.".format(
                page.min_role)}), 403
        flask.abort(403)
    return check


def load_pages():
    """
    _function_: `mast.web.page.load_pages()`

    Return the `Page` of every entry point in `mast_web_page`, by
    `order`. An entry point that does not load is logged and left out,
    so that one broken page does not stop the web UI.
    """
    pages = []
    logger = make_logger("gui")
    for ep in metadata.entry_points().select(group=GROUP):
        try:
            page = ep.load()
            if not isinstance(page, Page):
                page = page()
            if not isinstance(page, Page):
                raise TypeError("{} is not a mast.web.page.Page".format(
                    ep.value))
            if page.min_role not in roles.ROLES:
                raise ValueError("{}: min_role {!r} is not a role".format(
                    ep.value, page.min_role))
        except Exception:
            logger.exception("The page {} of the web UI did not load".format(
                ep.name))
            continue
        pages.append(page)
    # One distribution can be found twice (a source checkout and an
    # installed copy). A name is one page.
    unique = {}
    for page in pages:
        unique.setdefault(page.name, page)
    return sorted(unique.values(), key=lambda page: (page.order, page.name))

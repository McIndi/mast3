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

The contract between the web UI and the parts of MAST that add pages to
it.

The pages of core are in `CORE_PAGES`. A plugin names its pages in its
manifest (`mast.plugins.Manifest.pages`), as `module:attribute` of a
`Page`:

    :::python
    manifest = Manifest(..., pages=("mast.datapower.status.page:page",))

A page of a plugin is loaded only if the plugin is used: a plugin that
was left out has no pages. If a page of a plugin cannot be loaded, or
takes the name of another page, the web UI leaves the whole plugin out,
with its commands, and the Plugins page of Admin says why.

The web UI registers the blueprint of each page, puts the page in its
navigation for the users whose role is at least `min_role`, and
refuses (403) every request to the blueprint from other users. The
blueprint has the templates, the static files and the routes of the
page, including its JSON routes under `/api/v1/<name>/...`.
"""
from dataclasses import dataclass, field

import flask

from mast.auth import roles
from mast.logging import make_logger

__all__ = ["Page", "load_pages", "CORE_PAGES"]

# The pages of core, `module:attribute` of each Page
CORE_PAGES = (
    "mast.web.pages.jobs:page",
    "mast.web.pages.commands:page",
    "mast.web.pages.admin:page",
)


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
    app comes first. The static files of the page are for everyone, as
    those of the app are (`mast.web.auth.require_login`)."""
    def check():
        if (flask.request.endpoint or "").endswith(".static"):
            return None
        user = flask.g.get("mast_user")
        if user is not None and roles.at_least(user.role, page.min_role):
            return None
        if flask.request.path.startswith("/api/"):
            return flask.jsonify({"error": "This needs the role {}.".format(
                page.min_role)}), 403
        flask.abort(403)
    return check


class _NameTaken(ValueError):
    """A page of a plugin has the name of another page."""


def _load(target):
    """The `Page` that `target` (`module:attribute`) names. Raises."""
    # Imported here: mast.roots needs nothing, but the loading is the
    # same for the roots of the command line
    from mast import roots
    page = roots.load(target)
    if not isinstance(page, Page):
        page = page()
    if not isinstance(page, Page):
        raise TypeError("{} is not a mast.web.page.Page".format(target))
    if page.min_role not in roles.ROLES:
        raise ValueError("{}: min_role {!r} is not a role".format(
            target, page.min_role))
    return page


def load_pages():
    """
    _function_: `mast.web.page.load_pages()`

    Return the `Page`s of core and of each plugin that is used, by
    `order`. A page of core that does not load is logged and left out,
    so that one broken page does not stop the web UI. A plugin whose
    page does not load, or takes the name of another page, is left out
    whole (`mast.jobs.registry.leave_out`).
    """
    # Imported here: the registry needs the configuration of the home
    from mast import plugins
    from mast.jobs import registry
    logger = make_logger("gui")
    pages = {}
    for target in CORE_PAGES:
        try:
            page = _load(target)
        except Exception:
            logger.exception("The page {} of the web UI did not "
                             "load".format(target))
            continue
        pages[page.name] = page
    # Check the commands of every plugin first: a plugin with a fault
    # has no pages either
    registry.commands()
    for provider, manifest in sorted(plugins.manifests().items()):
        found = {}
        try:
            for target in manifest.pages:
                page = _load(target)
                if page.name in pages or page.name in found:
                    raise _NameTaken("the page {} takes the name {!r} of "
                                     "another page".format(target, page.name))
                found[page.name] = page
        except Exception as exc:
            logger.exception("The pages of the plugin {} did not "
                             "load".format(provider))
            registry.leave_out(
                provider, plugins.CONFLICT if isinstance(exc, _NameTaken)
                else plugins.BROKEN,
                "its pages cannot be loaded: {}".format(exc))
            continue
        pages.update(found)
    return sorted(pages.values(), key=lambda page: (page.order, page.name))

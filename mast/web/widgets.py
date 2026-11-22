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
_module_: `mast.web.widgets`

How the web UI asks for each widget of an option (`Option(widget=...)`).
This is the same map as `WIDGETS` in `static/js/forms.js`, which makes
the controls. The unit tests check that the two are the same, and that
every widget of every command is in the map, so that a new widget
cannot go without a control.

* `target-bar`: The target bar of the page fills it (the appliances,
the credentials).
* `file`: A file input; the file is uploaded first, and the job gets
the token of the upload.
* `hidden`: The server fills it (`out_dir`, `out_file`); the job page
offers the download.
* `choices`: A select, filled from `POST /api/v1/choices/<widget>`.

A key that ends with `:` is a prefix: `configured:` stands for
`configured:Domain`, `configured:User` and so on.

An option without a widget gets a control from its type: a checkbox
for a `bool`, a select for `choices`, a text area (one value per line)
for a `list`, a password input for a `secret`, a number input for an
`int` or a `float`, and a text input otherwise.
"""

__all__ = ["WIDGETS", "control"]

WIDGETS = {
    "appliances": "target-bar",
    "credentials": "target-bar",
    "upload": "file",
    "out_dir": "hidden",
    "out_file": "hidden",
    "status_provider": "choices",
    "object_class": "choices",
    "configured:": "choices",
}


def control(widget):
    """
    _function_: `mast.web.widgets.control(widget)`

    Return the control for `widget`, or None if it has none.
    """
    if not widget:
        return None
    if widget in WIDGETS:
        return WIDGETS[widget]
    if ":" in widget:
        return WIDGETS.get(widget.split(":", 1)[0] + ":")
    return None

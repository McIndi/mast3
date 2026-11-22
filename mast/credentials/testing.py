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
_module_: `mast.credentials.testing`

The test of a stored credential: a job of `mast.credentials.TEST_COMMAND`
with the credential, on one appliance, through the queue like any other
job. When it has ended, `record` reads its result and stores the
outcome with the credential (`last_tested_at`, `last_test_ok`,
`can_write`).
"""
from mast.credentials import TEST_COMMAND, store
from mast.jobs import queue
from mast.jobs.auth import LOCAL, authorize
from mast.jobs.models import SUCCEEDED

__all__ = ["params", "record", "Outcome", "NotATest"]


class NotATest(ValueError):
    """The job is not a test of that credential, or has not ended."""


class Outcome(object):
    """
    _class_: `mast.credentials.testing.Outcome`

    What a test found: `ok` (the credential works), `can_write` (True,
    False, or None if not known), `appliance`, `firmware` and
    `problem`.
    """
    def __init__(self, ok, can_write, appliance="", firmware="", problem=""):
        self.ok = ok
        self.can_write = can_write
        self.appliance = appliance
        self.firmware = firmware
        self.problem = problem

    def json(self):
        return {"ok": self.ok, "can_write": self.can_write,
                "appliance": self.appliance, "firmware": self.firmware,
                "problem": self.problem}


def params(name, appliance):
    """
    _function_: `mast.credentials.testing.params(name, appliance)`

    Return the parameters of the job that tests the credential `name`
    on `appliance`.
    """
    return {"appliances": [appliance], "credential": name}


def _outcome(info, found):
    if found is None or not found.data or found.data.get("kind") != "table":
        return Outcome(False, None, problem=info.error or
                       "The test did not report a result.")
    columns = found.data.get("columns") or []
    rows = found.data.get("rows") or []
    try:
        works = columns.index("works")
        change = columns.index("can change")
        appliance = columns.index("appliance")
        firmware = columns.index("firmware")
        problem = columns.index("problem")
    except ValueError:
        return Outcome(False, None, problem="The test reported a table "
                                            "that MAST does not know.")
    if not rows:
        return Outcome(False, None, problem="The test checked no appliance.")
    row = rows[0]
    can_write = {"yes": True, "no": False}.get(row[change])
    return Outcome(info.status == SUCCEEDED and row[works] == "yes",
                   can_write, appliance=row[appliance],
                   firmware=row[firmware], problem=row[problem])


def record(name, job_id, principal=LOCAL):
    """
    _function_: `mast.credentials.testing.record(name, job_id,
    principal=LOCAL)`

    Store the outcome of the test job `job_id` with the credential
    `name`, and return it as an `Outcome`. Raises `NotATest` if the
    job is not a test of this credential or has not ended.
    """
    authorize(principal, "manage-credential")
    info = queue.get(job_id, principal)
    if info.command != TEST_COMMAND or info.params.get("credential") != name:
        raise NotATest("The job {} is not a test of the credential "
                       "{}.".format(job_id, name))
    if not info.finished:
        raise NotATest("The job {} has not ended.".format(job_id))
    outcome = _outcome(info, queue.result(job_id, principal))
    store.record_test(name, outcome.ok, outcome.can_write,
                      principal=principal, appliance=outcome.appliance,
                      job_id=job_id)
    return outcome

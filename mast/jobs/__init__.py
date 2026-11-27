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
_module_: `mast.jobs`

A job queue for MAST commands, in a SQL database.

A job is one invocation of a command: the id of the command and its
parameters. A client puts a job in the queue and a worker runs it:

    :::python
    from mast import jobs

    job_id = jobs.submit("datapower.normal-backup", {
        "appliances": ["dp1"], "credentials": ["user:password"],
        "Domain": ["default"]})

    # In a worker
    worker = jobs.new_worker_id()
    jobs.run_next(worker)

    # The client follows the job
    for event in jobs.events(job_id):
        print(event.message)
    if jobs.get(job_id).finished:
        print(jobs.result(job_id).text)

The database is SQLite in `$MAST_HOME/var/jobs` unless `jobs.conf`
names another. See `mast.jobs.queue` for the rules of the queue.
"""
from mast.jobs.auth import LOCAL, Forbidden, Principal
from mast.jobs.models import (
    CANCELLED, FAILED, FINISHED, LOST, PARTIAL, QUEUED, RUNNING, SUCCEEDED)
from mast.jobs.queue import (
    EventInfo, Heartbeat, JobInfo, JobNotFound, NotOwner, NotQueued,
    ResultInfo,
    WorkerInfo, abandon, add_event, attach, cancel, claim, events, find, finish, get,
    heartbeat, list_jobs, load, new_job_id, reap, reap_if_expired, result, submit, worker_gone,
    worker_seen, workers)
from mast.jobs.registry import UnknownCommand, register, resolve
from mast.jobs.runner import new_worker_id, run_job, run_next

__all__ = [
    "new_job_id", "submit", "attach", "get", "find", "list_jobs", "events", "result", "cancel",
    "claim", "load", "heartbeat", "add_event", "finish", "abandon", "reap",
    "reap_if_expired", "worker_seen", "worker_gone", "workers",
    "new_worker_id", "run_job", "run_next",
    "register", "resolve",
    "JobInfo", "EventInfo", "ResultInfo", "Heartbeat", "WorkerInfo",
    "Principal", "LOCAL", "Forbidden",
    "JobNotFound", "NotOwner", "NotQueued", "UnknownCommand",
    "QUEUED", "RUNNING", "SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED",
    "LOST",
    "FINISHED",
]

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
_module_: `mast.jobs.cli`

`mast-jobs`: look at the job queue from the command line.

* `list`: The newest jobs.
* `show`: One job, with its progress and its result.
* `follow`: Show a job until it ends, as the command that submitted
it does.
* `cancel`: Cancel a job.
* `workers`: The workers that are alive.

Where a command takes the id of a job, the first characters of the id
are enough.
"""
import sys
from typing import Literal

from mast.cli import Cli, Option, Params, Table, Text
from mast.jobs import client, queue
from mast.jobs.models import (
    CANCELLED, FAILED, LOST, PARTIAL, QUEUED, RUNNING, SUCCEEDED)

__all__ = ["cli"]

cli = Cli(description="Look at the MAST job queue.")


def _when(time):
    return "" if time is None else time.astimezone().strftime(
        "%Y-%m-%d %H:%M:%S")


def _job(prefix):
    try:
        return queue.find(prefix)
    except queue.JobNotFound as exc:
        print("There is no job {}".format(exc), file=sys.stderr)
        sys.exit(1)


class JobParams(Params):
    job: str = Option(
        short="j", help="The id of the job, or the start of the id.")


class List(Params):
    status: list[Literal[
        QUEUED, RUNNING, SUCCEEDED, PARTIAL, FAILED, CANCELLED,
        LOST]] = Option(
        default_factory=list, short="s",
        help="Show only the jobs with this status.")
    limit: int = Option(20, short="n", help="How many jobs to show.")


@cli.command("list")
def list_jobs(params: List):
    """List the newest jobs, newest first."""
    jobs = queue.list_jobs(status=params.status or None, limit=params.limit)
    return Table(
        ("Job", "Status", "Command", "Submitted", "By", "Appliances"),
        [(job.id, job.status, job.command, _when(job.created_at),
          job.submitted_by, " ".join(job.targets)) for job in jobs])


class Show(JobParams):
    history: bool = Option(
        False, short="H",
        help="Also show the history of the job: the requests and "
             "responses, or the traceback of a failed job.")


@cli.command("show")
def show(params: Show):
    """Show one job: what it is, its progress and its result."""
    job_id = _job(params.job)
    info = queue.get(job_id)
    lines = [
        "Job:        {}".format(info.id),
        "Command:    {}".format(info.command),
        "Status:     {}".format(info.status)
        + (" (cancel requested)"
           if info.cancel_requested and not info.finished else ""),
        "Submitted:  {} by {}".format(
            _when(info.created_at), info.submitted_by),
        "Started:    {}".format(_when(info.started_at)),
        "Finished:   {}".format(_when(info.finished_at)),
        "Worker:     {}".format(info.worker_id or ""),
        "Appliances: {}".format(" ".join(info.targets)),
    ]
    if info.error:
        lines.append("Error:      {}".format(info.error))
    lines += ["", "Parameters:"]
    for name, value in info.params.items():
        if isinstance(value, dict):
            value = "(secret)"
        lines.append("  {}: {}".format(name, value))
    lines += ["", "Events:"]
    for event in queue.events(job_id):
        lines.append("  {}  {}".format(
            _when(event.created_at),
            event.message if event.kind == "say"
            else "[{}]".format(event.message)))
    result = queue.result(job_id)
    if result is not None and result.text:
        lines += ["", "Result:", result.text]
    if params.history and result is not None and result.history:
        lines += ["", "History:", result.history]
    return Text("\n".join(lines))


@cli.command("follow")
def follow(params: JobParams):
    """Show a job until it ends: its progress, then its result.

    Press Ctrl-C to cancel the job. Press it again to stop waiting."""
    status = client.follow(_job(params.job))
    if status:
        sys.exit(status)


@cli.command("cancel")
def cancel(params: JobParams):
    """Cancel a job.

    A job that waits is cancelled now. A job that runs stops at its
    next step, and what it did before that is not undone."""
    info = queue.cancel(_job(params.job))
    if info.finished:
        return Text("The job {} is {}.".format(info.id, info.status))
    return Text("Asked to cancel the job {}. It stops at its next "
                "step.".format(info.id))


@cli.command("workers")
def workers(params: Params):
    """List the workers that are alive."""
    found = queue.workers()
    if not found:
        return Text("No worker is running. Start one with mast-worker. "
                    "Without a worker, each command does its own job.")
    return Table(
        ("Worker", "Host", "PID", "Jobs at a time", "Started", "Last seen"),
        [(worker.id, worker.hostname, worker.pid, worker.concurrency,
          _when(worker.started_at), _when(worker.last_seen_at))
         for worker in found])

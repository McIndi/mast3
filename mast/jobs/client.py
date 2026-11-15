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
_module_: `mast.jobs.client`

What a program does to run a command as a job: put it in the queue,
show its progress, and show its result.

The MAST command line uses this (see `mast.cli.Cli`). The user sees
what a command printed before there was a queue: the progress and the
result on standard output. Messages about the queue go to standard
error.

If no worker is alive, the program does the job itself, so that an
installation with no worker still works. The job goes through the
queue all the same: it waits for its turn, and its progress and
result are stored.
"""
import sys
import time

from mast.jobs import config, queue, runner
from mast.jobs.models import CANCELLED, FAILED, QUEUED, SUCCEEDED

__all__ = ["run", "follow"]

# The exit status of a program that was stopped with Ctrl-C
INTERRUPTED = 130


def _out(message):
    print(message, flush=True)


def _note(message):
    print(message, file=sys.stderr, flush=True)


def run(command, params, detach=False, local=False, principal=queue.LOCAL):
    """
    _function_: `mast.jobs.client.run(command, params, detach=False,
    local=False, principal=LOCAL)`

    Put a job in the queue and, unless `detach` is given, show it
    until it ends (see `follow`). Return the exit status for the
    program.

    Parameters:

    * `command`: The id of the command, for example
    `backups.normal-backup`.
    * `params`: The parameters, see `mast.jobs.queue.submit`.
    * `detach`: If True, print the id of the job and return 0.
    * `local`: If True, do the job in this process, also if a worker
    is alive.
    * `principal`: Who submits the job.
    """
    if not detach:
        # With local, no worker may take the job before this process does
        worker_id = runner.new_worker_id()
        job_id = queue.submit(
            command, params, principal=principal, attached=True,
            reserved_for=worker_id if local else None)
        return follow(job_id, local=local, principal=principal,
                      worker_id=worker_id)
    job_id = queue.submit(command, params, principal=principal)
    _out(job_id)
    if not queue.workers():
        _note("No worker is running. The job waits until one starts "
              "(mast-worker).")
    _note("To see the job: mast-jobs show -j {}".format(job_id[:8]))
    return 0


def follow(job_id, local=False, poll_seconds=0.25, principal=queue.LOCAL,
           worker_id=None):
    """
    _function_: `mast.jobs.client.follow(job_id, local=False,
    poll_seconds=0.25, principal=LOCAL, worker_id=None)`

    Show a job until it ends: print its progress as it is reported,
    then its result. Return the exit status for the program: 0 if the
    job succeeded, 130 if the user stopped it, otherwise 1.

    While the job waits in the queue and no worker is alive (or
    `local` is True), this process takes the job and does it, as the
    worker `worker_id`.

    The first Ctrl-C asks to cancel the job, and the job is still
    shown until it ends. The second Ctrl-C returns at once. The job
    then continues without this program.
    """
    last_event = 0
    interrupted = False
    next_attach = 0
    told_waiting = False
    started = time.monotonic()
    while True:
        try:
            info = queue.get(job_id, principal)
            for event in queue.events(job_id, last_event, principal):
                last_event = event.id
                if event.kind == "say":
                    _out(event.message)
            if info.finished:
                break
            if info.status == QUEUED and time.monotonic() >= next_attach:
                queue.attach(job_id)
                next_attach = time.monotonic() + config.lease_seconds() / 3.0
            if info.status == QUEUED and (local or not queue.workers()):
                worker_id = worker_id or runner.new_worker_id()
                if queue.claim(worker_id, job_id=job_id):
                    try:
                        runner.run_job(job_id, worker_id, echo=_out)
                    finally:
                        # The progress was printed as it happened
                        shown = queue.events(job_id, last_event, principal)
                        last_event = shown[-1].id if shown else last_event
                    continue
            if (info.status == QUEUED and not told_waiting
                    and time.monotonic() - started > 2):
                told_waiting = True
                _note("The job {} waits in the queue. Press Ctrl-C to "
                      "cancel it.".format(job_id[:8]))
            time.sleep(poll_seconds)
        except KeyboardInterrupt:
            if interrupted:
                _note("The job {} continues. To see it: mast-jobs show "
                      "-j {}".format(job_id[:8], job_id[:8]))
                return INTERRUPTED
            interrupted = True
            if queue.cancel(job_id, principal).finished:
                continue
            _note("Asked to cancel the job {}. It stops at its next "
                  "step. Press Ctrl-C again to stop waiting for "
                  "it.".format(job_id[:8]))
    return _show_end(info, queue.result(job_id, principal), interrupted)


def _show_end(info, result, interrupted):
    """Print the result of a finished job. Return the exit status."""
    if info.status == SUCCEEDED:
        if result is not None and result.text:
            _out(result.text)
        return 0
    if info.status == FAILED and result is not None and result.history:
        # The traceback of the command
        _note(result.history.rstrip())
    _note("The job {} {}{}".format(
        info.id[:8], info.status, ": " + info.error if info.error else "."))
    if interrupted or info.status == CANCELLED:
        return INTERRUPTED
    return 1

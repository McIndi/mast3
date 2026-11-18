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
import threading
import time

from mast.jobs import config, queue, runner
from mast.jobs.models import CANCELLED, FAILED, QUEUED, SUCCEEDED
from mast.logging import make_logger

__all__ = ["run", "follow", "start"]

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
    job succeeded, 130 if the user stopped it, otherwise 1. A job that
    is `partial` (the command failed on some of the appliances) is
    also 1.

    While the job waits in the queue and no worker is alive (or
    `local` is True), this process takes the job and does it, as the
    worker `worker_id`.

    The first Ctrl-C asks to cancel the job, and the job is still
    shown until it ends. The second Ctrl-C returns at once. The job
    then continues without this program.
    """
    last_event = 0
    local_job = None
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
            if info.status == QUEUED and local_job is None \
                    and (local or not queue.workers()):
                worker_id = worker_id or runner.new_worker_id()
                candidate = _Local(job_id, worker_id)
                if candidate.try_start():
                    local_job = candidate
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
    if local_job is not None:
        # The job is stored. The thread only has to end.
        local_job.join()
    return _show_end(info, queue.result(job_id, principal), interrupted)


def _show_end(info, result, interrupted):
    """Print the result of a finished job. Return the exit status."""
    # A command that raised an exception has no result: its history
    # is the traceback
    returned = result is not None and bool(result.html)
    if returned and result.text:
        _out(result.text)
    if info.status == SUCCEEDED:
        return 0
    if info.status == FAILED and not returned \
            and result is not None and result.history:
        _note(result.history.rstrip())
    _note("The job {} {}{}".format(
        info.id[:8], info.status, ": " + info.error if info.error else "."))
    if interrupted or info.status == CANCELLED:
        return INTERRUPTED
    return 1


def start(command, params, principal=queue.LOCAL, job_id=None):
    """
    _function_: `mast.jobs.client.start(command, params,
    principal=LOCAL, job_id=None)`

    Put a job in the queue, make sure that it will be done, and return
    its id at once. The web GUI uses this. The caller then asks the
    queue for the progress and the result of the job.

    If a worker is alive, the worker does the job. If not, a thread of
    this process does it, so that a web server with no worker still
    works. That job belongs to this process: if the process stops
    before the job starts, the job is cancelled.

    `job_id` is the id for the job, see `mast.jobs.queue.submit`.
    """
    if queue.workers():
        return queue.submit(
            command, params, principal=principal, job_id=job_id)
    worker_id = runner.new_worker_id()
    job_id = queue.submit(
        command, params, principal=principal, attached=True,
        reserved_for=worker_id, job_id=job_id)
    threading.Thread(
        target=_do, args=(job_id, worker_id),
        name="mast-job-{}".format(job_id[:8]), daemon=True).start()
    return job_id


class _Local(object):
    """
    A job that this process runs itself, in a thread of its own.

    The thread does what a worker does: it runs the job with
    `mast.jobs.runner.run_job` and stores its progress and its result.
    The program shows the progress as it does for a job on a worker,
    from the queue.
    """
    def __init__(self, job_id, worker_id):
        self.job_id = job_id
        self.worker_id = worker_id
        self._thread = None

    def try_start(self):
        """Claim the job if it can start now, and start the thread.
        Return True if the thread runs."""
        if not queue.claim(self.worker_id, job_id=self.job_id):
            return False
        self._thread = threading.Thread(
            target=self._run, name="mast-job-{}".format(self.job_id[:8]),
            daemon=True)
        self._thread.start()
        return True

    def _run(self):
        try:
            runner.run_job(self.job_id, self.worker_id)
        except Exception:
            make_logger("mast.jobs").exception(
                "The job {} could not be completed in this process".format(
                    self.job_id))

    @property
    def running(self):
        """True while the thread runs."""
        return self._thread is not None and self._thread.is_alive()

    def join(self, timeout=None):
        """Wait until the thread ends."""
        if self._thread is not None:
            self._thread.join(timeout)

    def abandon(self, reason):
        """Declare the job lost, because this process stops before the
        job ends. Return False if the job is not running here any
        more."""
        return queue.abandon(self.job_id, self.worker_id, reason)


def _do(job_id, worker_id, poll_seconds=0.25):
    """Wait until the job can start, then run it."""
    logger = make_logger("mast.jobs")
    local = _Local(job_id, worker_id)
    next_attach = 0
    while True:
        try:
            if time.monotonic() >= next_attach:
                queue.attach(job_id)
                next_attach = time.monotonic() + config.lease_seconds() / 3.0
            if queue.get(job_id).status != QUEUED:
                # It was cancelled
                return
            if local.try_start():
                local.join()
                return
        except Exception:
            # The database can be away. Keep trying.
            logger.exception(
                "Could not start the job {} in this process".format(job_id))
        time.sleep(poll_seconds)

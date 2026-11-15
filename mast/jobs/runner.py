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
_module_: `mast.jobs.runner`

Run a job that was claimed from the queue.

This is the one place where a queued command is executed. A worker
process calls `run_next` in a loop. A program that submits a job and
has no worker to give it to can call `run_next` itself.
"""
import os
import socket
import threading
import traceback
import uuid

from mast.cli import Result, listening
from mast.jobs import config, queue
from mast.jobs.models import FAILED, SUCCEEDED
from mast.logging import make_logger

__all__ = ["new_worker_id", "run_job", "run_next"]


def new_worker_id():
    """
    _function_: `mast.jobs.runner.new_worker_id()`

    Return a name for a worker that no other worker has: the host, the
    process id and a random part.
    """
    return "{}:{}:{}".format(
        socket.gethostname(), os.getpid(), uuid.uuid4().hex[:8])


def _beat(job_id, worker_id, lease_seconds, stop):
    logger = make_logger("mast.jobs")
    while not stop.wait(lease_seconds / 3.0):
        try:
            if not queue.heartbeat(job_id, worker_id, lease_seconds).owned:
                logger.error(
                    "The job {} does not belong to the worker {} any "
                    "more".format(job_id, worker_id))
                return
        except Exception:
            # The database can be away for a moment. Try again at the
            # next beat.
            logger.exception(
                "The heartbeat for the job {} failed".format(job_id))


def _execute(job_id, worker_id):
    """Call the command of the job. Return the arguments for
    `queue.finish`."""
    try:
        command, values = queue.load(job_id, worker_id)
        with listening(lambda message: queue.add_event(job_id, message)):
            result = command.fn(command.model(**values))
    except SystemExit as exc:
        # Some commands report a problem and then exit
        if exc.code in (None, 0):
            return {"status": SUCCEEDED}
        return {"status": FAILED,
                "error": "The command exited with status {}".format(exc.code)}
    except Exception as exc:
        return {"status": FAILED,
                "error": "{}: {}".format(type(exc).__name__, exc),
                "history": traceback.format_exc()}
    if not isinstance(result, Result):
        return {"status": SUCCEEDED}
    try:
        return {"status": SUCCEEDED, "text": result.text(),
                "html": result.html(), "history": result.history}
    except Exception as exc:
        return {"status": FAILED,
                "error": "The command finished, but its result could not "
                         "be shown. {}: {}".format(type(exc).__name__, exc),
                "history": traceback.format_exc()}


def run_job(job_id, worker_id, lease_seconds=None):
    """
    _function_: `mast.jobs.runner.run_job(job_id, worker_id,
    lease_seconds=None)`

    Run a job that `worker_id` claimed, and store its progress and its
    result. Heartbeats are sent from another thread while the command
    runs. Return the status of the job, or `None` if the job was
    declared lost before its result could be stored.

    An exception from the command does not come out of this function.
    The job is `failed` and the traceback is in its history.
    """
    logger = make_logger("mast.jobs")
    if lease_seconds is None:
        lease_seconds = config.lease_seconds()
    stop = threading.Event()
    beat = threading.Thread(
        target=_beat, args=(job_id, worker_id, lease_seconds, stop),
        name="mast-jobs-heartbeat", daemon=True)
    beat.start()
    logger.info("The worker {} starts the job {}".format(worker_id, job_id))
    try:
        try:
            outcome = _execute(job_id, worker_id)
        except BaseException as exc:
            # Ctrl-C, for example. Record it, then let it continue.
            queue.finish(
                job_id, worker_id, FAILED,
                error="The job was interrupted ({})".format(
                    type(exc).__name__),
                history=traceback.format_exc())
            raise
    finally:
        stop.set()
        beat.join()
    stored = queue.finish(job_id, worker_id, **outcome)
    logger.info("The job {} ended: {}{}".format(
        job_id, outcome["status"], "" if stored else " (not stored, lost)"))
    return outcome["status"] if stored else None


def run_next(worker_id, lease_seconds=None):
    """
    _function_: `mast.jobs.runner.run_next(worker_id, lease_seconds=None)`

    Claim the next job and run it. Return the id of the job, or `None`
    if there was no job to run.
    """
    job_id = queue.claim(worker_id, lease_seconds)
    if job_id is not None:
        run_job(job_id, worker_id, lease_seconds)
    return job_id

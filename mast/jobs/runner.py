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
process (`mast.jobs.worker`) calls `run_next` in a loop. A program
that submits a job and has no worker to give it to can call `run_next`
itself.

A running job can be cancelled where its command reports progress
with `mast.cli.say`. Commands do that between their steps, so a job
stops between two steps and not in the middle of a request to an
appliance. A command that is silent for a long time runs to its end.

A job is also stopped before each request that it sends to an
appliance (`mast.cli.checkpoint`). A command that does one action on
several appliances reports nothing between them, and it must not go
on to the next appliance. That check does not ask the database: the
thread of the heartbeat also looks for a request to cancel the job
every `cancel_poll_seconds` (`jobs.conf`, one second by default), so a
cancel is seen there within about that time. A program that runs the
job itself can also tell it at once (`cancel` of `run_job`), as the
first Ctrl-C does.

A job that the worker lost is stopped in the same places. The worker
loses a job when its heartbeats do not reach the queue for longer
than the lease: the queue then declares the job `lost` and gives its
appliances to the next job. The worker knows when its lease ends
without asking the database, so it also stops a job when the database
is away. A request to an appliance that is in progress at that moment
is still completed.

What a command reports can repeat a secret parameter. The values of
the secret parameters are removed from the progress, the result and
the history before they are stored, see `mast.jobs.secrets.redactor`.

The status of a job that ran to its end comes from the result of the
command (`mast.cli.Result.outcome`): `succeeded`, `partial` if some
of what the command did failed, or `failed` if all of it failed. A
command that raised an exception is `failed` and has no result.
"""
import json
import os
import socket
import threading
import time
import traceback
import uuid

from mast.cli import Result, guarded, listening
from mast.jobs import config, queue, secrets
from mast.jobs.models import CANCELLED, FAILED, PARTIAL, SUCCEEDED
from mast.logging import make_logger

__all__ = ["new_worker_id", "run_job", "run_next", "JobCancelled",
           "JobLost"]


class JobCancelled(BaseException):
    """Raised inside a command, from `mast.cli.say` or before a
    request to an appliance, when somebody asked to cancel its job. It is not an `Exception`, so that a
    command that catches every `Exception` does not continue."""


class JobLost(BaseException):
    """Raised inside a command, from `mast.cli.say` or before a
    request to an appliance, when the worker does not have the job any
    more. Another job can have the
    appliances now, so the command must not do its next step."""


class _Lease(object):
    """What a worker knows about its hold on a job. `started_at` is
    the time (`time.monotonic()`) at which the worker asked for the
    job: the lease in the database began after it."""
    def __init__(self, seconds, started_at=None, cancel=None):
        if started_at is None:
            started_at = time.monotonic()
        self.seconds = seconds
        self._lost = threading.Event()
        self._good_until = started_at + seconds
        # Set when somebody asked to cancel the job: by the thread of the
        # heartbeat, or at once by the program that runs the job
        self._cancel = cancel if cancel is not None else threading.Event()

    @property
    def cancel_requested(self):
        return self._cancel.is_set()

    def request_cancel(self):
        self._cancel.set()

    def renewed(self, asked_at):
        """A heartbeat that was sent at `asked_at` was accepted."""
        self._good_until = asked_at + self.seconds

    def lose(self):
        self._lost.set()

    def held(self):
        return not self._lost.is_set() \
            and time.monotonic() < self._good_until


def new_worker_id():
    """
    _function_: `mast.jobs.runner.new_worker_id()`

    Return a name for a worker that no other worker has: the host, the
    process id and a random part.
    """
    return "{}:{}:{}".format(
        socket.gethostname(), os.getpid(), uuid.uuid4().hex[:8])


_LOST = (
    "The worker could not keep the job, because its heartbeats did not "
    "reach the queue for longer than the lease. The job was stopped. "
    "It was not completed, and it is not known how much of it was done.")


def _beat(job_id, worker_id, lease, stop, poll_seconds):
    """Keep the lease of the job: a heartbeat every third of the lease.
    Between the heartbeats, look every `poll_seconds` whether somebody
    asked to cancel the job, which only reads one value."""
    logger = make_logger("mast.jobs")
    every = lease.seconds / 3.0
    next_beat = time.monotonic() + every
    while True:
        now = time.monotonic()
        if stop.wait(max(0.0, min(poll_seconds, next_beat - now))):
            return
        asked_at = time.monotonic()
        if asked_at < next_beat:
            if lease.cancel_requested:
                continue
            try:
                if queue.cancel_requested(job_id):
                    lease.request_cancel()
            except Exception:
                # The heartbeat finds out if the database is away
                logger.debug("Could not ask whether the job {} was "
                             "cancelled".format(job_id), exc_info=True)
            continue
        next_beat = asked_at + every
        try:
            beat = queue.heartbeat(job_id, worker_id, lease.seconds)
        except Exception:
            # The database can be away for a moment. Try again at the
            # next beat. The lease ends if this continues.
            logger.exception(
                "The heartbeat for the job {} failed".format(job_id))
            continue
        if not beat.owned:
            lease.lose()
            logger.error(
                "The job {} does not belong to the worker {} any "
                "more".format(job_id, worker_id))
            return
        lease.renewed(asked_at)
        if beat.cancel_requested:
            lease.request_cancel()


def _execute(job_id, worker_id, lease, echo=None):
    """Call the command of the job. Return the arguments for
    `queue.finish`, or `None` if the worker lost the job."""
    redact = [secrets.Redactor(())]

    def store(message):
        message = redact[0](message)
        if not lease.held():
            raise JobLost()
        try:
            cancelled = queue.add_event(job_id, message, worker_id=worker_id)
        except queue.NotOwner:
            lease.lose()
            raise JobLost() from None
        if echo is not None:
            echo(message)
        if cancelled:
            raise JobCancelled()

    def guard():
        # Called before each request to an appliance, so it does not
        # ask the database
        if not lease.held():
            raise JobLost()
        if lease.cancel_requested:
            raise JobCancelled()

    try:
        outcome = _call(job_id, worker_id, store, guard, redact)
    except JobLost:
        return None
    clean = {"status": lambda value: value, "html": redact[0].html,
             "data": lambda value: secrets.redact_data(redact[0], value)}
    return {name: clean.get(name, redact[0])(value)
            for name, value in outcome.items()}


def _data(result):
    """Return `result.data()` as JSON types, or None. A result whose
    data cannot be stored as JSON has none: the web UI then shows its
    html."""
    data = result.data()
    if data is None:
        return None
    try:
        return json.loads(json.dumps(data))
    except (TypeError, ValueError):
        return None


def _call(job_id, worker_id, store, guard, redact):
    """Call the command and give each line of its progress to `store`.
    `guard` is called before each request to an appliance.
    Put the function that removes the secret parameters of the job in
    `redact[0]`. Return the arguments for `queue.finish`, not yet
    redacted."""
    try:
        command, values = queue.load(job_id, worker_id)
        redact[0] = secrets.redactor(command.model, values)
        with listening(store), guarded(guard):
            result = command.fn(command.model(**values))
    except JobCancelled:
        return {"status": CANCELLED,
                "error": "The job was cancelled while it ran. What it "
                         "did before that was not undone.",
                "history": traceback.format_exc()}
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
        outcomes = [bool(worked) for worked in result.outcomes()]
        status = result.outcome
        if status not in (SUCCEEDED, PARTIAL, FAILED):
            raise ValueError("{!r} is not an outcome".format(status))
        error = None
        if status != SUCCEEDED:
            error = "The command reported that {} of its {} results " \
                    "failed.".format(outcomes.count(False), len(outcomes))
        return {"status": status, "error": error, "text": result.text(),
                "html": result.html(), "history": result.history,
                "data": _data(result)}
    except Exception as exc:
        return {"status": FAILED,
                "error": "The command finished, but its result could not "
                         "be shown. {}: {}".format(type(exc).__name__, exc),
                "history": traceback.format_exc()}


def run_job(job_id, worker_id, lease_seconds=None, echo=None,
            claimed_at=None, cancel=None, cancel_poll_seconds=None):
    """
    _function_: `mast.jobs.runner.run_job(job_id, worker_id,
    lease_seconds=None, echo=None, claimed_at=None, cancel=None,
    cancel_poll_seconds=None)`

    Run a job that `worker_id` claimed, and store its progress and its
    result. Heartbeats are sent from another thread while the command
    runs. Return the status of the job, or `None` if the job was
    declared lost before its result could be stored. A job that is
    lost while its command runs is stopped at the next line of
    progress from the command.

    An exception from the command does not come out of this function.
    The job is `failed` and the traceback is in its history.

    `echo`, if given, is called with each line of progress from the
    command after the line is stored.

    `claimed_at` is the value of `time.monotonic()` just before the
    worker called `queue.claim`. The worker counts its lease from
    then, so that it stops the job no later than the queue gives the
    appliances to another job. Without it, the lease is counted from
    now, which is later than the queue counts it: the job could then
    send a request after it was lost. Always give it.

    `cancel`, if given, is a `threading.Event`. Setting it cancels the
    job at its next step at once, without the database: the program
    that runs the job does that when the user presses Ctrl-C. A request
    to cancel that is stored in the queue is seen within
    `cancel_poll_seconds` (default: `cancel_poll_seconds` in
    `jobs.conf`).
    """
    logger = make_logger("mast.jobs")
    if lease_seconds is None:
        lease_seconds = config.lease_seconds()
    if cancel_poll_seconds is None:
        cancel_poll_seconds = config.cancel_poll_seconds()
    lease = _Lease(lease_seconds, started_at=claimed_at, cancel=cancel)
    stop = threading.Event()
    beat = threading.Thread(
        target=_beat,
        args=(job_id, worker_id, lease, stop, cancel_poll_seconds),
        name="mast-jobs-heartbeat", daemon=True)
    beat.start()
    logger.info("The worker {} starts the job {}".format(worker_id, job_id))
    try:
        try:
            outcome = _execute(job_id, worker_id, lease, echo)
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
    if outcome is None:
        logger.error(
            "The worker {} lost the job {} and stopped it".format(
                worker_id, job_id))
        try:
            # The queue does not know yet if the database was away
            queue.abandon(job_id, worker_id, _LOST)
        except Exception:
            logger.exception(
                "Could not declare the job {} lost".format(job_id))
        return None
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
    # Before the claim: a lease that is counted from too early ends too
    # soon, never too late
    claimed_at = time.monotonic()
    job_id = queue.claim(worker_id, lease_seconds)
    if job_id is not None:
        run_job(job_id, worker_id, lease_seconds, claimed_at=claimed_at)
    return job_id

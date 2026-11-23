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
_module_: `mast.jobs.worker`

The worker: a process that takes jobs from the queue and runs them.

Start one with `mast-worker`. `mastd` also hosts one, unless
`jobs.conf` says otherwise. Any number of workers can use one
database. They can be started and stopped at any time.

A worker also runs the scheduler of the job queue
(`mast.jobs.schedules.QueueScheduler`), unless `[worker] schedules` of
`jobs.conf` is false or it drains: it queues the jobs of the schedules
when they are due and runs the daily retention (`mast.jobs.retention`).
Each run of a schedule is queued once, however many workers check it.

To stop a worker, send it SIGTERM or press Ctrl-C. It takes no more
jobs and stops when its running jobs end. A second signal stops it
now. The jobs that it was running become `lost`, because their
commands were cut off.
"""
import os
import signal
import socket
import threading
import time

from pydantic import field_validator
from sqlalchemy.engine import make_url

from mast.cli import Cli, Option, Params
from mast.jobs import config, queue, runner
from mast.jobs.models import QUEUED
from mast.logging import make_logger

__all__ = ["Worker", "Plugin", "main"]


class Worker(object):
    """
    _class_: `mast.jobs.worker.Worker(concurrency=None,
    poll_seconds=None, lease_seconds=None, drain=False, report=None,
    schedules=None)`

    Takes jobs from the queue and runs them, `concurrency` at a time,
    each in its own thread.

    Parameters:

    * `concurrency`: How many jobs to run at the same time.
    * `poll_seconds`: How long to wait before looking again when
    there was no job to take.
    * `lease_seconds`: See `mast.jobs.queue.claim`.
    * `drain`: If True, `run` returns when no job waits in the queue.
    * `report`: A function that is called with one line of text when
    something happens (a job starts, a job ends, the worker stops).
    * `schedules`: Whether to run the scheduler of the job queue while
    the worker runs. A worker that drains never runs it.

    The defaults of the first three and of `schedules` are in
    `jobs.conf`.
    """
    def __init__(self, concurrency=None, poll_seconds=None,
                 lease_seconds=None, drain=False, report=None,
                 schedules=None):
        self.id = runner.new_worker_id()
        self.concurrency = (
            config.concurrency() if concurrency is None else concurrency)
        if self.concurrency < 1:
            raise ValueError("concurrency must be 1 or more")
        self.poll_seconds = (
            config.poll_seconds() if poll_seconds is None else poll_seconds)
        self.lease_seconds = (
            config.lease_seconds() if lease_seconds is None else lease_seconds)
        self.drain = drain
        self.schedules = not drain and (
            config.worker_schedules() if schedules is None else schedules)
        self.scheduler = None
        self._report = report
        self._logger = make_logger("mast.jobs.worker")
        self._stop = threading.Event()
        self._abort = threading.Event()
        self._lock = threading.Lock()
        # The jobs that run now: the worker_id of the slot -> the job
        self._running = {}

    def _say(self, message):
        self._logger.info("{}: {}".format(self.id, message))
        if self._report is not None:
            self._report(message)

    @property
    def stopping(self):
        """True after `stop` or `abort`."""
        return self._stop.is_set()

    @property
    def aborted(self):
        """True after `abort`."""
        return self._abort.is_set()

    def running(self):
        """
        _method_: `mast.jobs.worker.Worker.running(self)`

        Return the ids of the jobs that this worker runs now.
        """
        with self._lock:
            return list(self._running.values())

    def stop(self):
        """
        _method_: `mast.jobs.worker.Worker.stop(self)`

        Take no more jobs and queue no more jobs of schedules. `run`
        returns when the running jobs end.
        """
        self._stop.set()
        self._stop_scheduler()

    def abort(self):
        """
        _method_: `mast.jobs.worker.Worker.abort(self)`

        Stop now. `run` declares the running jobs lost and returns.
        The threads of those jobs are not stopped, so the process
        must exit after `run` returns.
        """
        self._abort.set()
        self._stop.set()
        self._stop_scheduler()

    def _start_scheduler(self):
        # Imported here: a worker that does not run it does not need it
        try:
            from mast.jobs import retention, schedules
            maintenance = []
            if config.retention_schedule():
                maintenance.append(
                    (config.retention_schedule(), retention.daily))
            self.scheduler = schedules.QueueScheduler(maintenance=maintenance)
            self.scheduler.start()
            self._say("queues the jobs of the schedules when they are due")
        except Exception:
            self.scheduler = None
            self._logger.exception(
                "{}: could not start the scheduler of the job queue. This "
                "worker does not queue the jobs of the schedules.".format(
                    self.id))

    def _stop_scheduler(self):
        scheduler = self.scheduler
        if scheduler is not None:
            scheduler.stop()

    def _slot(self, slot_id):
        """The loop of one thread: claim a job, run it, again."""
        while not self._stop.is_set():
            try:
                # Taken before the claim, see runner.run_job
                claimed_at = time.monotonic()
                job_id = queue.claim(slot_id, self.lease_seconds)
                if job_id is None and self.drain and not queue.list_jobs(
                        status=QUEUED, limit=1):
                    return
            except Exception:
                # The database can be away. Keep trying.
                self._logger.exception(
                    "{}: could not look for a job".format(slot_id))
                job_id = None
            if job_id is None:
                self._stop.wait(self.poll_seconds)
                continue
            with self._lock:
                self._running[slot_id] = job_id
            self._say("job {} started".format(job_id))
            try:
                status = runner.run_job(
                    job_id, slot_id, self.lease_seconds,
                    claimed_at=claimed_at)
                self._say("job {} {}".format(job_id, status or "lost"))
            except Exception:
                self._logger.exception(
                    "{}: the job {} could not be completed".format(
                        slot_id, job_id))
                self._say("job {} could not be completed, see the "
                          "log".format(job_id))
            finally:
                with self._lock:
                    self._running.pop(slot_id, None)

    def _seen(self):
        try:
            queue.worker_seen(
                self.id, socket.gethostname(), os.getpid(), self.concurrency)
        except Exception:
            self._logger.exception(
                "{}: could not record that the worker is alive".format(
                    self.id))

    def _abandon(self):
        with self._lock:
            running = dict(self._running)
        for slot_id, job_id in running.items():
            try:
                if queue.abandon(
                        job_id, slot_id,
                        "The worker {} was stopped while the job ran. The "
                        "job was not completed, and it is not known how "
                        "much of it was done.".format(self.id)):
                    self._say("job {} lost".format(job_id))
            except Exception:
                self._logger.exception(
                    "{}: could not declare the job {} lost".format(
                        slot_id, job_id))

    def run(self):
        """
        _method_: `mast.jobs.worker.Worker.run(self)`

        Run jobs until `stop` or `abort` is called (or, with `drain`,
        until no job waits). Call this once.
        """
        self._seen()
        if self.schedules and not self._stop.is_set():
            self._start_scheduler()
        threads = [
            threading.Thread(
                target=self._slot, args=("{}/{}".format(self.id, number),),
                name="mast-worker-{}".format(number), daemon=True)
            for number in range(1, self.concurrency + 1)]
        for thread in threads:
            thread.start()
        told = False
        next_seen = time.monotonic() + self.lease_seconds / 3.0
        try:
            while any(thread.is_alive() for thread in threads):
                if self._abort.is_set():
                    self._abandon()
                    break
                if self._stop.is_set() and not told:
                    told = True
                    waiting = len(self.running())
                    if waiting:
                        self._say("stopping when {} running job(s) "
                                  "end".format(waiting))
                if time.monotonic() >= next_seen:
                    self._seen()
                    next_seen = time.monotonic() + self.lease_seconds / 3.0
                time.sleep(0.1)
        finally:
            self._stop_scheduler()
            try:
                queue.worker_gone(self.id)
            except Exception:
                self._logger.exception(
                    "{}: could not record that the worker stopped".format(
                        self.id))
            self._say("stopped")


class Plugin(threading.Thread):
    """
    _class_: `mast.jobs.worker.Plugin(threading.Thread)`

    A `mastd` plugin that hosts a worker, so that an installation
    that runs `mastd` needs no other process to run jobs and
    schedules. Set `mastd` to `false` in the `[worker]` section of
    `jobs.conf` to turn it off.
    """
    def __init__(self):
        super(Plugin, self).__init__()
        self.daemon = True
        self._stopped = threading.Event()
        self.worker = Worker() if config.mastd_worker() else None

    def stop(self):
        """
        _method_: `mast.jobs.worker.Plugin.stop(self)`

        Stop the worker when its running jobs end.
        """
        self._stopped.set()
        if self.worker is not None:
            self.worker.stop()

    def abort(self):
        """
        _method_: `mast.jobs.worker.Plugin.abort(self)`

        Stop the worker now. Its running jobs become lost.
        """
        self._stopped.set()
        if self.worker is not None:
            self.worker.abort()

    def run(self):
        """
        _method_: `mast.jobs.worker.Plugin.run(self)`

        Run the worker. If the worker is turned off, only wait, so
        that `mastd` does not start this plugin again each minute.
        """
        if self.worker is None:
            self._stopped.wait()
        else:
            self.worker.run()


class Main(Params):
    concurrency: int | None = Option(
        None, short="c",
        help="How many jobs to run at the same time. The default is "
             "`concurrency` in `jobs.conf`.")
    poll_seconds: float | None = Option(
        None, short="p",
        help="How many seconds to wait before looking again when there "
             "was no job to take. The default is `poll_seconds` in "
             "`jobs.conf`.")
    drain: bool = Option(
        False, short="d",
        help="If specified, stop when no job waits in the queue.")

    @field_validator("concurrency")
    @classmethod
    def _at_least_one(cls, value):
        if value is not None and value < 1:
            raise ValueError("must be 1 or more")
        return value

    @field_validator("poll_seconds")
    @classmethod
    def _positive(cls, value):
        if value is not None and value <= 0:
            raise ValueError("must be more than 0")
        return value


def _signals():
    names = ("SIGINT", "SIGTERM", "SIGBREAK")  # SIGBREAK is on Windows
    return [getattr(signal, name) for name in names if hasattr(signal, name)]


def main(params: Main):
    """Take jobs from the MAST job queue and run them, and queue the
    jobs of the schedules when they are due.

    Any number of workers can run at the same time. To stop a worker,
    press Ctrl-C (or send SIGTERM). It then takes no more jobs and
    stops when its running jobs end. Do that again to stop it now.
    The jobs that it was running then become "lost", and the exit
    status is 1.

    `schedules = false` in the `[worker]` section of `jobs.conf` makes
    it only run jobs. With `--drain` it never queues the jobs of the
    schedules."""
    worker = Worker(
        concurrency=params.concurrency, poll_seconds=params.poll_seconds,
        drain=params.drain, report=lambda message: print(message, flush=True))

    def on_signal(signum, frame):
        # Only set a flag here. The loop in Worker.run does the work.
        if worker.stopping:
            worker.abort()
        else:
            worker.stop()

    for signum in _signals():
        signal.signal(signum, on_signal)
    print("worker {} runs up to {} job(s) at a time from {}".format(
        worker.id, worker.concurrency,
        make_url(config.database_url()).render_as_string(hide_password=True)),
        flush=True)
    worker.run()
    if worker.aborted:
        raise SystemExit(1)


if __name__ == "__main__":
    import sys
    sys.argv[0] = "mast-worker"
    Cli(main=main).run()

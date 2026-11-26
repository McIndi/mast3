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

`mast jobs`: look at the job queue from the command line.

* `list`: The newest jobs.
* `show`: One job, with its progress and its result.
* `follow`: Show a job until it ends, as the command that submitted
it does.
* `cancel`: Cancel a job.
* `workers`: The workers that are alive.
* `downgrade-db`: Take the tables of the job database back to an older
revision, to go back to an older MAST.
* `prune`: Delete old jobs, uses of credentials, audit events, uploads
and logs now (see `mast.jobs.retention`).

`mast schedules`: the scheduled jobs (see `mast.jobs.schedules`):
`add`, `update`, `enable`, `disable`, `remove`, `list` and `run`.

Where a command takes the id of a job, the first characters of the id
are enough.
"""
import json
import sys
from typing import Literal

from mast.cli import Cli, Option, Params, Table, Text
from mast.roots import CORE
from mast.jobs import client, db, queue, retention, schedules
from mast.jobs.auth import Forbidden
from mast.jobs.models import (
    CANCELLED, FAILED, LOST, PARTIAL, QUEUED, RUNNING, SUCCEEDED)

__all__ = ["jobs_cli", "schedules_cli"]

jobs_cli = Cli(description="Look at the MAST job queue.", provider=CORE,
               prog="mast jobs", queued=False)
schedules_cli = Cli(description="Manage the schedules: commands that the "
                            "workers put in the job queue at set times.",
                    provider=CORE, prog="mast schedules", queued=False)


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


@jobs_cli.command("list", id="list-jobs")
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


@jobs_cli.command("show", id="show-job")
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


@jobs_cli.command("follow", id="follow-job")
def follow(params: JobParams):
    """Show a job until it ends: its progress, then its result.

    Press Ctrl-C to cancel the job. Press it again to stop waiting."""
    status = client.follow(_job(params.job))
    if status:
        sys.exit(status)


@jobs_cli.command("cancel", id="cancel-job")
def cancel(params: JobParams):
    """Cancel a job.

    A job that waits is cancelled now. A job that runs stops at its
    next step, and what it did before that is not undone."""
    info = queue.cancel(_job(params.job))
    if info.finished:
        return Text("The job {} is {}.".format(info.id, info.status))
    return Text("Asked to cancel the job {}. It stops at its next "
                "step.".format(info.id))


@jobs_cli.command("workers", id="list-workers")
def workers(params: Params):
    """List the workers that are alive."""
    found = queue.workers()
    if not found:
        return Text("No worker is running. Start one with mast worker. "
                    "Without a worker, each command does its own job.")
    return Table(
        ("Worker", "Host", "PID", "Jobs at a time", "Started", "Last seen"),
        [(worker.id, worker.hostname, worker.pid, worker.concurrency,
          _when(worker.started_at), _when(worker.last_seen_at))
         for worker in found])


class DowngradeDb(Params):
    revision: str = Option(
        short="r",
        help="The revision to go back to. 0001 is the tables of MAST "
             "3.15.2.")
    yes: bool = Option(
        False, short="y",
        help="Do it. Without this, only show what would change.")


@jobs_cli.command("downgrade-db", id="downgrade-db")
def downgrade_db(params: DowngradeDb):
    """Take the tables of the job database back to an older revision,
    to go back to an older MAST. The tables and columns of the newer
    revisions are dropped, with what they hold (for example the users
    of the web UI). The jobs stay.

    Stop every MAST process of this home first (mastd, mast web,
    mast worker), and after this start only the older MAST: a newer
    MAST brings the tables up to date again when it starts."""
    current = db.revision()
    if current is None:
        return Text("The job database has no tables.")
    if current == params.revision:
        return Text("The job database is at revision {} already.".format(
            current))
    if not params.yes:
        return Text(
            "The job database is at revision {}. With --yes, this takes "
            "it back to revision {}.".format(current, params.revision))
    db.downgrade(params.revision)
    return Text("The job database is now at revision {} (it was {}).".format(
        db.revision(), current))


# Schedules
# =========


def _schedule_do(change, *args, **kwargs):
    try:
        return change(*args, **kwargs)
    except (schedules.ScheduleError, Forbidden) as exc:
        print("mast jobs: {}".format(exc), file=sys.stderr)
        sys.exit(1)


def _parse_params(text, pairs):
    values = {}
    if text:
        try:
            values = json.loads(text)
        except ValueError as exc:
            print("mast jobs: --params is not JSON: {}".format(exc),
                  file=sys.stderr)
            sys.exit(1)
        if not isinstance(values, dict):
            print("mast jobs: --params is a JSON object", file=sys.stderr)
            sys.exit(1)
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key.strip():
            print("mast jobs: --set is NAME=VALUE: {!r}".format(pair),
                  file=sys.stderr)
            sys.exit(1)
        try:
            values[key.strip()] = json.loads(value)
        except ValueError:
            values[key.strip()] = value
    return values


def _show_schedule(info):
    lines = [
        "Schedule:  {}{}".format(info.name, "" if info.enabled else " (paused)"),
        "Command:   {}".format(info.command),
        "Cron:      {} (UTC)".format(info.cron),
        "Next runs: {}".format(", ".join(
            _when(t) for t in schedules.next_runs(info.cron, count=3))),
        "Made by:   {} at {}".format(info.created_by, _when(info.created_at)),
    ]
    if info.description:
        lines.append("About:     {}".format(info.description))
    if info.last_fired_at:
        lines.append("Last run:  {}{}".format(
            _when(info.last_fired_at),
            " job {}".format(info.last_job_id) if info.last_job_id else ""))
    if info.last_skip_reason:
        lines.append("Note:      {}".format(info.last_skip_reason))
    lines.append("Parameters:")
    for name, value in info.params.items():
        if value not in (None, "", [], False):
            lines.append("  {}: {}".format(name, value))
    return "\n".join(lines)


class ScheduleName(Params):
    name: str = Option(short="n", help="The name of the schedule.")


class ScheduleAdd(ScheduleName):
    command: str = Option(
        short="c", help="The id of the command, for example "
                        "system.list-domains (see the Commands page).")
    cron: str = Option(
        short="t", help="When it runs: five cron fields in UTC (minute hour "
                        "day-of-month month day-of-week), for example "
                        "'0 2 * * *', or @daily, @hourly...")
    params: str | None = Option(
        None, short="p", help="The parameters of the command as a JSON "
                              "object, for example '{\"appliances\": "
                              "[\"prod\"], \"credential\": \"collector\"}'.")
    set: list[str] = Option(
        default_factory=list, short="s",
        help="One parameter, NAME=VALUE. A VALUE that is JSON is read as "
             "JSON (appliances='[\"dp1\"]').")
    description: str = Option("", short="d", help="What it is for.")
    disabled: bool = Option(False, help="Add it paused.")


@schedules_cli.command("add", id="add-schedule")
def schedule_add(params: ScheduleAdd):
    """Add a schedule: a command that the workers put in the job queue
    when its cron expression is due. A command that talks to appliances needs
    a stored credential (credential), not credentials. Its jobs run as
    schedule:NAME with the role of who made it."""
    info = _schedule_do(
        schedules.add, params.name, params.command,
        _parse_params(params.params, params.set), params.cron,
        description=params.description, enabled=not params.disabled)
    return Text("Added\n" + _show_schedule(info))


class ScheduleUpdate(ScheduleName):
    cron: str | None = Option(None, short="t", help="A new cron expression.")
    params: str | None = Option(
        None, short="p", help="New parameters, as a JSON object. They "
                              "replace the old ones.")
    set: list[str] = Option(
        default_factory=list, short="s",
        help="Change one parameter, NAME=VALUE; the others stay.")
    description: str | None = Option(None, short="d", help="A new description.")


@schedules_cli.command("update", id="update-schedule")
def schedule_update(params: ScheduleUpdate):
    """Change the cron expression, the parameters or the description of
    a schedule."""
    new = None
    if params.params is not None or params.set:
        base = schedules.get(params.name).params if params.params is None else {}
        new = dict(base, **_parse_params(params.params, params.set))
    info = _schedule_do(schedules.update, params.name, cron_text=params.cron,
                        params=new, description=params.description)
    return Text("Updated\n" + _show_schedule(info))


@schedules_cli.command("enable", id="enable-schedule")
def schedule_enable(params: ScheduleName):
    """Enable a paused schedule. It is checked again first."""
    info = _schedule_do(schedules.update, params.name, enabled=True)
    return Text("Enabled\n" + _show_schedule(info))


@schedules_cli.command("disable", id="disable-schedule")
def schedule_disable(params: ScheduleName):
    """Pause a schedule. Its jobs that are queued or running go on."""
    info = _schedule_do(schedules.update, params.name, enabled=False)
    return Text("Paused\n" + _show_schedule(info))


class ScheduleRemove(ScheduleName):
    yes: bool = Option(False, short="y", help="Do not ask to confirm.")


@schedules_cli.command("remove", id="remove-schedule")
def schedule_remove(params: ScheduleRemove):
    """Remove a schedule. Its jobs stay."""
    if not params.yes:
        answer = input("Remove the schedule {}? [y/N] ".format(params.name))
        if answer.strip().lower() not in ("y", "yes"):
            return Text("Nothing was removed.")
    _schedule_do(schedules.remove, params.name)
    return Text("Removed the schedule {}.".format(params.name))


@schedules_cli.command("list", id="list-schedules")
def schedule_list(params: Params):
    """List the schedules."""
    found = schedules.list_schedules()
    if not found:
        return Text("There are no schedules. Add one with mast schedules "
                    "add, or in the web UI under Admin -> Schedules.")
    rows = []
    for info in found:
        upcoming = schedules.next_runs(info.cron, count=1)
        rows.append((info.name, "yes" if info.enabled else "paused",
                     info.cron, info.command,
                     _when(upcoming[0]) if upcoming else "",
                     _when(info.last_fired_at), info.last_job_id or "",
                     info.last_skip_reason or ""))
    return Table(("Name", "Enabled", "Cron (UTC)", "Command", "Next run",
                  "Last run", "Last job", "Note"), rows)


@schedules_cli.command("run", id="run-schedule")
def schedule_run(params: ScheduleName):
    """Queue the job of a schedule now, whatever its cron expression
    says. Use mast jobs follow to see it."""
    job_id = _schedule_do(schedules.run_now, params.name)
    return Text("Queued the job {} of the schedule {}.".format(
        job_id, params.name))


# Retention
# =========


class Prune(Params):
    dry_run: bool = Option(
        False, short="d", help="Only count what would be deleted.")


@jobs_cli.command("prune", id="prune-jobs")
def prune(params: Prune):
    """Delete the jobs, uses of credentials, audit events, uploads, log
    files, files in tmp/ and status of the appliances that are older
    than [retention] of jobs.conf says. The workers do this each day."""
    counts = retention.prune(dry_run=params.dry_run)
    what = {
        "jobs_days": "jobs", "scheduled_jobs_days": "succeeded scheduled jobs",
        "scheduled_failed_days": "other scheduled jobs",
        "credential_uses_days": "uses of credentials",
        "audit_events_days": "audit events", "uploads_days": "uploads",
        "logs_days": "log files", "tmp_days": "files in tmp",
        "status_samples_days": "status samples",
        "status_hourly_days": "hourly status",
        "status_daily_days": "daily status",
        "crypto_snapshots_days": "crypto inventory snapshots"}
    return Table(("What", "Would delete" if params.dry_run else "Deleted"),
                 [(what[rule], count) for rule, count in counts.items()])

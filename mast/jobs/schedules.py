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
_module_: `mast.jobs.schedules`

Scheduled jobs: a command, its parameters and a cron expression (five
fields, in UTC). When the expression is due, the scheduler of `mastd`
(`QueueScheduler`) puts a job in the queue, as the principal
`schedule:<name>` with the role of the admin who made the schedule.

* A schedule holds no secret. A command that talks to appliances uses a
stored credential (`credential`), which is checked when the schedule is
saved and again for each job.
* **Coalescing:** if the job of the last run still waits or runs, the
run is skipped and `last_skip_reason` says why.
* If the principal that made the schedule is removed, disabled or may
no longer submit jobs, the schedule is paused (`enabled` false).
* **One firing:** each run is claimed in a transaction that compares
`last_fired_at`, so two `mastd` on one database do not both submit it.

`mast-jobs schedule ...` and Admin -> Schedules manage them (the
action `manage-schedule`, admins).
"""
import re
import threading
import time
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from mast import cron
from mast.cli import params as cli_params
from mast.jobs import db, queue, registry
from mast.jobs.auth import LOCAL, Forbidden, Principal, authorize
from mast.jobs.models import QUEUED, RUNNING, Job, Schedule
from mast.logging import make_logger

__all__ = [
    "ScheduleInfo", "ScheduleError", "UnknownSchedule", "ScheduleTaken",
    "add", "update", "remove", "get", "list_schedules", "run_now",
    "fire_due", "next_runs", "QueueScheduler", "PRINCIPAL_PREFIX",
]

PRINCIPAL_PREFIX = "schedule:"
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_SECRET_WIDGET = "credentials"


class ScheduleError(ValueError):
    """A schedule was refused. The message says why."""


class UnknownSchedule(ScheduleError, LookupError):
    """There is no schedule with this name."""


class ScheduleTaken(ScheduleError):
    """A schedule has this name already."""


class ScheduleInfo(BaseModel):
    """
    _class_: `mast.jobs.schedules.ScheduleInfo`

    A schedule, as the functions of this module return it.
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    command: str
    params: dict
    cron: str
    enabled: bool
    description: str
    created_by: str
    created_at: datetime
    updated_at: datetime
    last_fired_at: datetime | None
    last_job_id: str | None
    last_skip_reason: str | None


def _now():
    return datetime.now(timezone.utc)


def _logger():
    return make_logger("mast.schedules")


def _require(session, name):
    found = session.scalars(select(Schedule).where(Schedule.name == name)).first()
    if found is None:
        raise UnknownSchedule("There is no schedule {}.".format(name))
    return found


def principal_for(created_by):
    """
    _function_: `mast.jobs.schedules.principal_for(created_by)`

    Return `(role, None)` for the principal that made a schedule, or
    `(None, reason)` if it may no longer submit jobs. An account of the
    operating system (`os:<account>`, the command line) is an admin.
    """
    if created_by.startswith("os:"):
        return LOCAL.role, None
    # Imported here: the users are needed only for this check
    from mast.auth import roles, users
    try:
        user = users.get(created_by)
    except users.UserNotFound:
        return None, "the user {} who made it no longer exists".format(created_by)
    if user.disabled:
        return None, "the user {} who made it is disabled".format(created_by)
    if not roles.allowed(user.role, "submit"):
        return None, "the user {} who made it may not submit jobs (role {})".format(
            created_by, user.role)
    return user.role, None


def _check(command, params, principal, name):
    """Return the parameters of `command` as JSON types, after the
    checks of a schedule. Raises `ScheduleError` or `Forbidden`."""
    try:
        cmd = registry.resolve(command)
    except Exception:
        raise ScheduleError("There is no command {}.".format(command))
    if not getattr(cmd, "queued", True):
        raise ScheduleError("{} does not run as a job.".format(command))
    try:
        model = cmd.model(**(params or {}))
    except Exception as exc:
        raise ScheduleError("The parameters of {} are not valid: {}".format(
            command, exc))
    specs = cli_params.options(cmd.model)
    widgets = {spec.widget for spec in specs}
    for spec in specs:
        value = getattr(model, spec.name, None)
        if spec.secret and value:
            raise ScheduleError(
                "A schedule cannot hold a password ({}). Store the "
                "credential with mast-credentials and give its name "
                "(credential).".format(spec.name))
    field, credential = queue.stored_credential(cmd.model, model)
    if _SECRET_WIDGET in widgets and not credential:
        raise ScheduleError(
            "{} talks to appliances: a schedule of it needs a stored "
            "credential (credential).".format(command))
    targets = queue._targets(cmd.model, model)
    role = principal.role
    as_schedule = Principal(PRINCIPAL_PREFIX + name, role)
    # What the jobs of the schedule would be refused is a problem of the
    # schedule, not of who asks for it
    try:
        authorize(as_schedule, "submit", command=command, targets=targets)
        if credential:
            from mast.credentials import store
            store.check_use(credential, queue.appliance_names(cmd.model, model),
                            as_schedule, command=command,
                            exclusive=getattr(cmd, "exclusive", True))
    except Forbidden as exc:
        raise ScheduleError(str(exc))
    return model.model_dump(mode="json")


def _check_name(name):
    if not isinstance(name, str) or not _NAME.match(name):
        raise ScheduleError(
            "A schedule name has 1 to 64 letters, digits, '_', '.' or '-', "
            "and starts with a letter or a digit: {!r}".format(name))
    return name


def _info(row):
    return ScheduleInfo.model_validate(row)


def add(name, command, params, cron_text, principal=LOCAL, description="",
        enabled=True):
    """
    _function_: `mast.jobs.schedules.add(name, command, params, cron_text,
    principal=LOCAL, description="", enabled=True)`

    Add a schedule and return its `ScheduleInfo`. `principal` must be
    allowed `manage-schedule`; its jobs run with the role of
    `principal`.
    """
    authorize(principal, "manage-schedule")
    _check_name(name)
    try:
        cron.parse_schedule(cron_text)
    except ValueError as exc:
        raise ScheduleError(str(exc))
    values = _check(command, params, principal, name)
    now = _now()
    with db.session() as session:
        if session.scalars(select(Schedule.id).where(Schedule.name == name)).first():
            raise ScheduleTaken("There is a schedule {} already.".format(name))
        row = Schedule(
            name=name, command=command, params=values,
            cron=" ".join(str(cron_text).split()),
            enabled=bool(enabled), description=description or "",
            created_by=principal.name, created_at=now, updated_at=now)
        session.add(row)
        session.flush()
        info = _info(row)
    _logger().info("{} added the schedule {}: {} at '{}'".format(
        principal.name, name, command, info.cron))
    return info


def update(name, principal=LOCAL, cron_text=None, params=None,
           description=None, enabled=None):
    """
    _function_: `mast.jobs.schedules.update(name, principal=LOCAL,
    cron_text=None, params=None, description=None, enabled=None)`

    Change a schedule: its cron expression, its parameters (they
    replace the old ones), its description, or whether it is enabled.
    Enabling it checks it again, as the creator may have lost a right
    since it was paused.
    """
    authorize(principal, "manage-schedule")
    with db.session(write=False) as session:
        current = _info(_require(session, name))
    changes = {}
    if cron_text is not None:
        try:
            cron.parse_schedule(cron_text)
        except ValueError as exc:
            raise ScheduleError(str(exc))
        changes["cron"] = " ".join(str(cron_text).split())
    if params is not None or enabled:
        role, reason = principal_for(current.created_by)
        if role is None:
            raise ScheduleError("The schedule {} cannot run: {}. Remove it and "
                                "add it again.".format(name, reason))
        creator = Principal(current.created_by, role)
        changes["params"] = _check(
            current.command, current.params if params is None else params,
            creator, name)
    if description is not None:
        changes["description"] = description
    if enabled is not None:
        changes["enabled"] = bool(enabled)
        if enabled:
            changes["last_skip_reason"] = None
    with db.session() as session:
        row = _require(session, name)
        for key, value in changes.items():
            setattr(row, key, value)
        row.updated_at = _now()
        session.flush()
        info = _info(row)
    _logger().info("{} changed the schedule {} ({})".format(
        principal.name, name, ", ".join(sorted(changes)) or "nothing"))
    return info


def remove(name, principal=LOCAL):
    """
    _function_: `mast.jobs.schedules.remove(name, principal=LOCAL)`

    Remove a schedule. Its jobs stay.
    """
    authorize(principal, "manage-schedule")
    with db.session() as session:
        session.delete(_require(session, name))
    _logger().info("{} removed the schedule {}".format(principal.name, name))


def get(name, principal=LOCAL):
    """
    _function_: `mast.jobs.schedules.get(name, principal=LOCAL)`

    Return the `ScheduleInfo` of a schedule.
    """
    authorize(principal, "read")
    with db.session(write=False) as session:
        return _info(_require(session, name))


def list_schedules(principal=LOCAL):
    """
    _function_: `mast.jobs.schedules.list_schedules(principal=LOCAL)`

    Return every schedule, by name.
    """
    authorize(principal, "read")
    with db.session(write=False) as session:
        return [_info(row) for row in session.scalars(
            select(Schedule).order_by(Schedule.name))]


def next_runs(cron_text, count=5, now=None):
    """
    _function_: `mast.jobs.schedules.next_runs(cron_text, count=5, now=None)`

    Return the next `count` times (UTC) at which `cron_text` is due.
    Raises `ScheduleError` for an expression that is not valid.
    """
    try:
        return cron.next_runs(cron.parse_schedule(cron_text), now or _now(), count)
    except ValueError as exc:
        raise ScheduleError(str(exc))


# Firing
# ======


def _claim(schedule_id, due_at, force=False):
    """Mark the schedule as fired for the minute `due_at`, unless it was
    fired for that minute or a later one already (by another mastd).
    Return the `ScheduleInfo`, or None if another process has it."""
    with db.session() as session:
        connection = session.connection()
        # One writer at a time: SQLite starts each writing transaction
        # with BEGIN IMMEDIATE; PostgreSQL locks the row
        db.lock_queue(connection)
        query = select(Schedule).where(Schedule.id == schedule_id)
        if connection.dialect.name == "postgresql":
            query = query.with_for_update()
        row = session.scalars(query).first()
        if row is None or (not force and not row.enabled):
            return None
        if not force and row.last_fired_at is not None \
                and row.last_fired_at >= due_at:
            return None
        row.last_fired_at = due_at
        session.flush()
        return _info(row)


def _record(schedule_id, job_id=None, reason=None, pause=False):
    with db.session() as session:
        row = session.get(Schedule, schedule_id)
        if row is None:
            return
        if job_id is not None:
            row.last_job_id = job_id
        row.last_skip_reason = reason
        if pause:
            row.enabled = False


def _fire(info, force=False):
    """Coalesce, check the creator, and submit the job of `info`.
    Return `(job_id, None)` or `(None, reason)`."""
    logger = _logger()
    if info.last_job_id:
        try:
            last = queue.get(info.last_job_id)
        except queue.JobNotFound:
            last = None
        if last is not None and last.status in (QUEUED, RUNNING):
            reason = "The job {} of the last run is still {}; this run is " \
                     "skipped.".format(last.id[:8], last.status)
            logger.warning("Schedule {}: {}".format(info.name, reason))
            _record(info.id, reason=reason)
            return None, reason
    role, problem = principal_for(info.created_by)
    if role is None:
        reason = "Paused: {}.".format(problem)
        logger.warning("Schedule {}: {}".format(info.name, reason))
        _record(info.id, reason=reason, pause=True)
        return None, reason
    principal = Principal(PRINCIPAL_PREFIX + info.name, role)
    try:
        job_id = queue.submit(info.command, info.params, principal=principal,
                              schedule_id=info.id)
    except Exception as exc:
        reason = "The job was not queued: {}: {}".format(type(exc).__name__, exc)
        logger.error("Schedule {}: {}".format(info.name, reason))
        _record(info.id, reason=reason)
        return None, reason
    logger.info("Schedule {} queued the job {}{}".format(
        info.name, job_id, " (run now)" if force else ""))
    _record(info.id, job_id=job_id)
    return job_id, None


def run_now(name, principal=LOCAL):
    """
    _function_: `mast.jobs.schedules.run_now(name, principal=LOCAL)`

    Queue the job of a schedule now, whatever its cron expression says,
    and return its id. A paused schedule runs too. The rules of a run
    still apply: `ScheduleError` says why no job was queued.
    """
    authorize(principal, "manage-schedule")
    with db.session(write=False) as session:
        schedule_id = _require(session, name).id
    info = _claim(schedule_id, _now(), force=True)
    job_id, reason = _fire(info, force=True)
    if job_id is None:
        raise ScheduleError(reason)
    return job_id


def fire_due(minutes):
    """
    _function_: `mast.jobs.schedules.fire_due(minutes)`

    Queue the job of each enabled schedule that is due in one of
    `minutes` (UTC `datetime`s at the start of a minute), once, even if
    it is due in several of them. Return `{name: job_id or reason}`.
    """
    logger = _logger()
    with db.session(write=False) as session:
        rows = [_info(row) for row in session.scalars(
            select(Schedule).where(Schedule.enabled.is_(True))
            .order_by(Schedule.name))]
    fired = {}
    for info in rows:
        try:
            expression = cron.parse_schedule(info.cron)
        except ValueError as exc:
            logger.error("Schedule {}: {}".format(info.name, exc))
            continue
        due = [minute for minute in minutes if cron.is_due(expression, minute)]
        if not due:
            continue
        claimed = _claim(info.id, max(due))
        if claimed is None:
            continue
        job_id, reason = _fire(claimed)
        fired[info.name] = job_id or reason
    return fired


def _minute_start(when):
    return when.astimezone(timezone.utc).replace(second=0, microsecond=0)


class QueueScheduler(threading.Thread):
    """
    _class_: `mast.jobs.schedules.QueueScheduler(clock=time.time, maintenance=None)`

    The thread of `mastd` (started by the cron plugin) that wakes at the
    start of each minute and queues the jobs of the schedules that are
    due (`fire_due`). After a gap (the server was busy or suspended),
    it looks at the missed minutes too, up to `CATCH_UP_MINUTES`, and a
    schedule fires once for all of them.

    `maintenance` is a list of `(cron expression, function)`: built-in
    tasks, such as the daily retention (`mast.jobs.retention`), that
    run in their own thread when they are due.
    """
    CATCH_UP_MINUTES = 60

    def __init__(self, clock=time.time, maintenance=None):
        super().__init__(name="mast-queue-scheduler", daemon=True)
        self.clock = clock
        self.maintenance = [(cron.parse_schedule(text), fn)
                            for text, fn in (maintenance or [])]
        self._stopping = threading.Event()
        self._last = None
        self._tasks = {}

    def stop(self):
        self._stopping.set()

    def check(self, now=None):
        """Fire what is due between the last check and `now` (a UTC
        `datetime`). Return what `fire_due` returned."""
        logger = _logger()
        minute = _minute_start(now or datetime.fromtimestamp(self.clock(), timezone.utc))
        if self._last is None:
            minutes = [minute]
        elif minute <= self._last:
            return {}
        elif minute - self._last > timedelta(minutes=self.CATCH_UP_MINUTES):
            logger.warning("The scheduler did not check for {} minutes. Those "
                           "runs are skipped.".format(
                               int((minute - self._last).total_seconds() // 60) - 1))
            minutes = [minute]
        else:
            count = int((minute - self._last).total_seconds() // 60)
            minutes = [self._last + timedelta(minutes=n) for n in range(1, count + 1)]
        self._last = minute
        for expression, fn in self.maintenance:
            if any(cron.is_due(expression, m) for m in minutes):
                self._start(fn)
        return fire_due(minutes)

    def _start(self, fn):
        name = getattr(fn, "__name__", "task")
        thread = self._tasks.get(name)
        if thread is not None and thread.is_alive():
            _logger().warning("{} still runs; it is not started again".format(name))
            return
        def run():
            try:
                fn()
            except Exception:
                _logger().exception("{} failed".format(name))
        thread = threading.Thread(target=run, name="mast-" + name, daemon=True)
        self._tasks[name] = thread
        thread.start()

    def run(self):
        while not self._stopping.is_set():
            try:
                self.check()
            except Exception:
                _logger().exception(
                    "Could not check the schedules. Trying again next minute.")
            delay = 60.0 - self.clock() % 60 + 0.05
            self._stopping.wait(min(max(delay, 0.05), 60.05))

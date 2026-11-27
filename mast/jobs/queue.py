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
_module_: `mast.jobs.queue`

The operations of the job queue.

A client calls `submit`, then follows the job with `get`, `events` and
`result`. A worker calls `claim`, then `load`, then `heartbeat` while
the command runs, then `finish`. `mast.jobs.runner` does the worker's
part.

The rules that keep workers out of each other's way:

* Only one worker gets a job. `claim` is one transaction, and only
one process at a time can be in it.
* A job waits while another job that started before it, or that was
submitted before it, uses one of the same appliances. Two jobs that
are both not exclusive can share an appliance.
* A passive job (`submit(..., passive=True)`, only for a command that
only reads) neither waits for other jobs nor holds them up. The status
polls are passive.
* One exception: while no worker is alive, a job that nobody waits
for (submitted without `attached`) does not hold up the jobs behind
it. Only a worker runs such a job, so it would hold them up until a
worker starts. A program that does its own job does not have to wait
for it. The job keeps its place and runs when a worker starts.
* A worker keeps its job with `heartbeat`. If the worker is silent
for longer than the lease, the job becomes `lost`. A lost job is not
run again, because nobody knows how much of it was done. The worker
stops a job that it lost at the next line of progress (see
`mast.jobs.runner`).
* A command can name other things that it must not share, for
example a directory. They are locked with the appliances of the job.
* A program that waits for its job says so with `attach` while the
job is queued. If the program goes away, the job is cancelled, so
that it does not run when nobody waits for it and does not hold up
the jobs behind it.

All times are UTC, from the clock of the process that writes them.
Workers on more than one host need synchronized clocks.
"""
import uuid
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, ConfigDict
from sqlalchemy import and_, delete, false, func, or_, select, true, update
from sqlalchemy.orm import aliased

from mast.jobs import config, db, registry, secrets
from mast.jobs.auth import LOCAL, authorize
from mast.jobs.models import (
    CANCELLED, FINISHED, LOST, QUEUED, RUNNING, SCHEDULED, Job, JobEvent,
    JobResult, JobTarget, Worker)

__all__ = [
    "new_job_id", "submit", "attach", "get", "find", "list_jobs", "count_jobs", "events", "result",
    "cancel", "reap_if_expired",
    "claim", "load", "heartbeat", "cancel_requested", "add_event", "finish",
    "abandon", "reap",
    "worker_seen", "worker_gone", "workers",
    "appliance_names", "stored_credential", "STORED_CREDENTIAL_WIDGET",
    "NotPassive", "TargetRefused",
    "JobInfo", "EventInfo", "ResultInfo", "Heartbeat", "WorkerInfo",
    "JobNotFound", "NotOwner", "NotQueued",
]

# The widget of the parameter that names a stored credential, see
# mast.plugin_utils.params.STORED_CREDENTIAL
STORED_CREDENTIAL_WIDGET = "stored_credential"

# A target that is not an appliance starts with this. No address of
# an appliance does.
_RESOURCE = "resource:"


class JobNotFound(Exception):
    """There is no job with that id."""


class NotQueued(Exception):
    """The command never runs as a job, see `mast.cli.Command.queued`."""


class NotOwner(Exception):
    """The job is not running, or another worker has it."""


class JobInfo(BaseModel):
    """
    _class_: `mast.jobs.queue.JobInfo`

    A job, as the queue reports it. `params` has no secret values.
    `targets` is the list of appliances that the job talks to, then
    the other things that the job locks, as `resource:<name>`.
    """
    model_config = ConfigDict(from_attributes=True)

    id: str
    command: str
    params: dict
    status: str
    exclusive: bool
    submitted_by: str
    parent_id: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    worker_id: str | None
    lease_expires_at: datetime | None
    cancel_requested: bool
    error: str | None
    passive: bool = False
    schedule_id: int | None = None
    targets: list[str] = []

    @property
    def finished(self):
        """True if the job will not change again."""
        return self.status in FINISHED


class EventInfo(BaseModel):
    """
    _class_: `mast.jobs.queue.EventInfo`

    One event of a job. `kind` is `"say"` for a line of progress from
    the command and `"state"` for a change of status.
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    kind: str
    message: str


class ResultInfo(BaseModel):
    """
    _class_: `mast.jobs.queue.ResultInfo`

    What a finished job returned: `text` for a terminal, `html` for
    the web GUI, `history` (the requests and responses, or the
    traceback of a failed job), and `data`, the result as JSON types
    (see `mast.cli.Result.data`), or None.
    """
    model_config = ConfigDict(from_attributes=True)

    text: str
    html: str
    history: str
    data: dict | None = None


class Heartbeat(BaseModel):
    """
    _class_: `mast.jobs.queue.Heartbeat`

    The answer to a heartbeat. `owned` is False if the worker lost the
    job. `cancel_requested` is True if somebody asked to cancel it.
    """
    owned: bool
    cancel_requested: bool = False


class WorkerInfo(BaseModel):
    """
    _class_: `mast.jobs.queue.WorkerInfo`

    A worker process. `id` is its name, and `concurrency` is how many
    jobs it runs at the same time.
    """
    model_config = ConfigDict(from_attributes=True)

    id: str
    hostname: str
    pid: int
    concurrency: int
    started_at: datetime
    last_seen_at: datetime


def _now():
    return datetime.now(timezone.utc)


def _lease(now, lease_seconds):
    if lease_seconds is None:
        lease_seconds = config.lease_seconds()
    return now + timedelta(seconds=lease_seconds)


def _event(session, job_id, kind, message, now=None):
    session.add(JobEvent(
        job_id=job_id, created_at=now or _now(), kind=kind, message=message))


def _info(session, job):
    info = JobInfo.model_validate(job)
    info.params = secrets.scrub(job.params)
    info.targets = list(session.scalars(
        select(JobTarget.appliance)
        .where(JobTarget.job_id == job.id).order_by(JobTarget.id)))
    return info


def _require(session, job_id):
    job = session.get(Job, job_id)
    if job is None:
        raise JobNotFound(job_id)
    return job


def _targets(cmd, params):
    """Return the names of the targets that a command with these
    parameters acts on, lower case, in order: a fleet becomes its
    targets. Raises `TargetRefused` for a name that is not a registered
    target or fleet, and for a target of a kind that the command does
    not accept, before anything is queued."""
    names = appliance_names(cmd.model, params)
    if not names:
        return []
    # Imported here because it reads the registry, which a program that
    # only reads the queue does not need
    from mast import registry
    try:
        found = registry.resolve(names)
    except registry.UnknownName as exc:
        raise TargetRefused(str(exc)) from None
    wanted = tuple(getattr(cmd, "targets", ()) or ())
    needs = tuple(getattr(cmd, "needs", ()) or ())
    if wanted or needs:
        for target in found:
            if not registry.accepts(target, wanted, needs):
                raise TargetRefused(
                    "{} is of the kind {}, and {} accepts {}.".format(
                        target.name, " + ".join(registry.kinds_of(target)),
                        getattr(cmd, "id", None) or "the command",
                        " or ".join(wanted) if wanted else
                        "targets that can " + ", ".join(needs)))
    targets = []
    for target in found:
        name = target.name.strip().lower()
        if name not in targets:
            targets.append(name)
    return targets


def appliance_names(model, params):
    """
    _function_: `mast.jobs.queue.appliance_names(model, params)`

    Return the names of appliances and environments in `params` (an
    instance of `model`, or a dict), as they were given.
    """
    from mast.cli import params as cli_params
    names = []
    for spec in cli_params.options(model):
        if spec.target:
            value = params.get(spec.name) if isinstance(params, dict) \
                else getattr(params, spec.name)
            if value:
                names.extend([value] if isinstance(value, str) else value)
    return names


def stored_credential(model, params):
    """
    _function_: `mast.jobs.queue.stored_credential(model, params)`

    Return `(field, name)`: the parameter of `model` that names a
    stored credential, and the name in `params` (an instance of
    `model`, or a dict). Both are None if the command has no such
    parameter; the name is None if it was not given.
    """
    from mast.cli import params as cli_params
    for spec in cli_params.options(model):
        if spec.widget == STORED_CREDENTIAL_WIDGET:
            value = params.get(spec.name) if isinstance(params, dict) \
                else getattr(params, spec.name)
            return spec.name, value or None
    return None, None


def _locks(command, params):
    """Return the targets for what a command locks besides its
    appliances, see `mast.cli.Command.locks`."""
    locks = getattr(command, "locks", None) or ()
    if callable(locks):
        locks = locks(params)
    targets = []
    for name in locks:
        target = _RESOURCE + str(name)
        if target not in targets:
            targets.append(target)
    return targets


def new_job_id():
    """
    _function_: `mast.jobs.queue.new_job_id()`

    Return an id for a new job. A program that must know the id
    before the job exists (to name the files of the job after it, for
    example) gives the id to `submit`.
    """
    return uuid.uuid4().hex


class TargetRefused(ValueError):
    """
    _class_: `mast.jobs.queue.TargetRefused`

    A job that was not queued because of its targets: a name that is
    not a registered target or fleet, or a target of a kind that the
    command does not accept. The message says which.
    """


class NotPassive(ValueError):
    """A job of a command that can change an appliance cannot be
    passive."""


def submit(command, params=None, principal=LOCAL, exclusive=None,
           parent_id=None, reserved_for=None, attached=False, job_id=None,
           passive=False, schedule_id=None):
    """
    _function_: `mast.jobs.queue.submit(command, params=None,
    principal=LOCAL, exclusive=None, parent_id=None, reserved_for=None,
    attached=False, job_id=None, passive=False, schedule_id=None)`

    Put a job in the queue and return its id.

    Parameters:

    * `command`: The id of the command, for example
    `backups.normal-backup`.
    * `params`: The parameters, as an instance of the command's
    `Params` model or as a `dict`. They are validated now, so a job
    with bad parameters is never queued (`pydantic.ValidationError`).
    A command that never runs as a job is refused (`NotQueued`).
    * `principal`: Who submits the job.
    * `exclusive`: If True, the job does not share its appliances with
    a running job. The default is the command's own `exclusive`
    attribute, or True if it has none.
    * `parent_id`: The id of the job that makes this job.
    * `reserved_for`: If given, only the worker with this id can
    claim the job.
    * `attached`: If True, the caller waits for the job and must call
    `attach` several times in each lease while the job is queued. A
    queued job whose caller is silent is cancelled.
    * `job_id`: The id for the job, from `new_job_id`. The default is
    a new id.
    * `passive`: If True, the job neither waits for other jobs on its
    appliances nor holds them up. Only for a command that only reads
    (`exclusive=False`); otherwise `NotPassive`. The status polls are
    passive, so that a deployment does not delay them, nor they it.
    Its appliances are still recorded. The jobs of a command declared
    `passive` (`mast.cli.Command`) are passive anyway, unless
    `exclusive` is True.
    * `schedule_id`: The id of the schedule that submits the job.

    A job with a stored credential (the parameter with the widget
    `stored_credential`) is queued only if `principal` may use that
    credential on the appliances of the job with this command, see
    `mast.credentials.store.check_use`. Otherwise `Forbidden` says why.
    Only the name of the credential is stored with the job.
    """
    cmd = registry.resolve(command)
    if not getattr(cmd, "queued", True):
        raise NotQueued(command)
    if not isinstance(params, cmd.model):
        params = cmd.model(**(params or {}))
    targets = _targets(cmd, params)
    authorize(principal, "submit", command=command, targets=targets)
    _, credential = stored_credential(cmd.model, params)
    if credential:
        # Imported here: a program that only reads the queue does not
        # need the credential store
        from mast.credentials import store
        store.check_use(
            credential, appliance_names(cmd.model, params), principal,
            command=command, effect=getattr(cmd, "effect", None))
    if exclusive is None:
        exclusive = getattr(cmd, "exclusive", True)
    # A command can make all of its jobs passive (Command.passive)
    passive = passive or (getattr(cmd, "passive", False) and not exclusive)
    if passive and (exclusive or getattr(cmd, "exclusive", True)):
        raise NotPassive(
            "{} can change the appliances, so its jobs cannot be "
            "passive".format(command))
    locked = targets + _locks(cmd, params)
    now = _now()
    job_id = job_id or new_job_id()
    with db.session() as session:
        session.add(Job(
            id=job_id,
            command=command,
            params=secrets.seal(cmd.model, params.model_dump(mode="json")),
            status=QUEUED,
            exclusive=bool(exclusive),
            submitted_by=principal.name,
            parent_id=parent_id,
            created_at=now,
            cancel_requested=False,
            reserved_for=reserved_for,
            attached_until=_lease(now, None) if attached else None,
            passive=bool(passive),
            schedule_id=schedule_id))
        session.flush()
        for target in locked:
            session.add(JobTarget(job_id=job_id, appliance=target))
        _event(session, job_id, "state", QUEUED, now)
    return job_id


def attach(job_id):
    """
    _function_: `mast.jobs.queue.attach(job_id)`

    Say that the program that submitted a job with `attached` still
    waits for it. This keeps the queued job for another lease. It
    does nothing for a job that is not queued or was not submitted
    with `attached`.
    """
    with db.session() as session:
        session.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == QUEUED,
                   Job.attached_until.is_not(None))
            .values(attached_until=_lease(_now(), None)))


def get(job_id, principal=LOCAL):
    """
    _function_: `mast.jobs.queue.get(job_id, principal=LOCAL)`

    Return the `JobInfo` of a job. Raises `JobNotFound`.
    """
    with db.session(write=False) as session:
        job = _require(session, job_id)
        info = _info(session, job)
    authorize(principal, "read", command=info.command, targets=info.targets)
    return info


def find(prefix, principal=LOCAL):
    """
    _function_: `mast.jobs.queue.find(prefix, principal=LOCAL)`

    Return the id of the job whose id starts with `prefix`, so that a
    person does not have to type all of an id. Raises `JobNotFound` if
    no job, or more than one job, has such an id.
    """
    authorize(principal, "read")
    prefix = prefix.strip().lower()
    if not prefix or not all(c in "0123456789abcdef" for c in prefix):
        raise JobNotFound(prefix)
    with db.session(write=False) as session:
        found = session.scalars(
            select(Job.id).where(Job.id.startswith(prefix, autoescape=True))
            .limit(2)).all()
    if len(found) == 1:
        return found[0]
    if found:
        raise JobNotFound("more than one job has an id that starts "
                          "with {}".format(prefix))
    raise JobNotFound(prefix)


def _filtered(query, status=None, submitted_by=None, command=None,
              appliance=None, before=None, parent_id=None,
              schedule_id=None, scheduled=None):
    """`query` with the filters of `list_jobs`."""
    if status is not None:
        statuses = [status] if isinstance(status, str) else list(status)
        query = query.where(Job.status.in_(statuses))
    if submitted_by is not None:
        query = query.where(Job.submitted_by == submitted_by)
    if command is not None:
        query = query.where(Job.command == command)
    if appliance is not None:
        query = query.where(Job.id.in_(
            select(JobTarget.job_id).where(JobTarget.appliance == appliance)))
    if before is not None:
        query = query.where(Job.created_at < before)
    if parent_id is not None:
        query = query.where(Job.parent_id == parent_id)
    if schedule_id is not None:
        query = query.where(Job.schedule_id == schedule_id)
    if scheduled is not None:
        query = query.where(SCHEDULED if scheduled else ~SCHEDULED)
    return query


def list_jobs(status=None, limit=50, principal=LOCAL, submitted_by=None,
              command=None, appliance=None, before=None, parent_id=None,
              schedule_id=None, scheduled=None):
    """
    _function_: `mast.jobs.queue.list_jobs(status=None, limit=50,
    principal=LOCAL, submitted_by=None, command=None, appliance=None,
    before=None, parent_id=None, schedule_id=None, scheduled=None)`

    Return the newest jobs first, as a list of `JobInfo`. `status`
    can be one status or a sequence of them. The other filters:

    * `submitted_by`: The name of the principal that submitted them.
    * `command`: The id of their command.
    * `appliance`: An appliance that they talk to.
    * `before`: Only jobs made before this time (a `datetime`), to
    page through older jobs.
    * `parent_id`: Only the child jobs of this job.
    * `schedule_id`: Only the jobs of this schedule.
    * `scheduled`: True for only the jobs that a schedule submitted,
    False for only the others (those that people submitted). The
    default is both.
    """
    authorize(principal, "read")
    query = _filtered(
        select(Job).order_by(Job.created_at.desc(), Job.id).limit(limit),
        status=status, submitted_by=submitted_by, command=command,
        appliance=appliance, before=before, parent_id=parent_id,
        schedule_id=schedule_id, scheduled=scheduled)
    with db.session(write=False) as session:
        return [_info(session, job) for job in session.scalars(query)]


def count_jobs(principal=LOCAL, **filters):
    """
    _function_: `mast.jobs.queue.count_jobs(principal=LOCAL, **filters)`

    Return how many jobs `list_jobs` would find with these filters and
    no limit.
    """
    authorize(principal, "read")
    query = _filtered(select(func.count()).select_from(Job), **filters)
    with db.session(write=False) as session:
        return session.scalar(query)


def events(job_id, after=0, principal=LOCAL):
    """
    _function_: `mast.jobs.queue.events(job_id, after=0,
    principal=LOCAL)`

    Return the events of a job in order, as a list of `EventInfo`. To
    follow a job, give the `id` of the last event that you have as
    `after`.
    """
    get(job_id, principal)
    with db.session(write=False) as session:
        return [EventInfo.model_validate(row) for row in session.scalars(
            select(JobEvent)
            .where(JobEvent.job_id == job_id, JobEvent.id > after)
            .order_by(JobEvent.id))]


def result(job_id, principal=LOCAL):
    """
    _function_: `mast.jobs.queue.result(job_id, principal=LOCAL)`

    Return the `ResultInfo` of a job, or `None` if it has no result
    (it is not finished, or it was cancelled or lost).
    """
    get(job_id, principal)
    with db.session(write=False) as session:
        row = session.get(JobResult, job_id)
        return None if row is None else ResultInfo.model_validate(row)


def cancel(job_id, principal=LOCAL):
    """
    _function_: `mast.jobs.queue.cancel(job_id, principal=LOCAL)`

    Cancel a job and return its `JobInfo`.

    A queued job is cancelled now. For a running job the request is
    recorded, and the job stops the next time its command reports
    progress (see `mast.jobs.runner`). What the command did before
    that is not undone. A finished job does not change.
    """
    info = get(job_id, principal)
    authorize(principal, "cancel", command=info.command, targets=info.targets,
              owner=info.submitted_by)
    now = _now()
    with db.session() as session:
        job = _require(session, job_id)
        cancelled = session.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == QUEUED)
            .values(status=CANCELLED, finished_at=now, cancel_requested=True,
                    params=secrets.scrub(job.params))).rowcount
        if cancelled:
            _event(session, job_id, "state", CANCELLED, now)
        else:
            # A worker has it, or took it a moment ago
            requested = session.execute(
                update(Job)
                .where(Job.id == job_id, Job.status == RUNNING,
                       Job.cancel_requested.is_(False))
                .values(cancel_requested=True)).rowcount
            if requested:
                _event(session, job_id, "state", "cancel requested", now)
    return get(job_id, principal)


def _reap(session, now):
    """Declare the running jobs with an expired lease lost, and cancel
    the queued jobs that nobody waits for any more. Return the ids of
    the lost jobs."""
    deserted = session.scalars(
        select(Job).where(
            Job.status == QUEUED, Job.attached_until < now)).all()
    for job in deserted:
        done = session.execute(
            update(Job)
            .where(Job.id == job.id, Job.status == QUEUED,
                   Job.attached_until < now)
            .values(status=CANCELLED, finished_at=now,
                    params=secrets.scrub(job.params),
                    error="The program that submitted the job stopped "
                          "before the job started.")).rowcount
        if done:
            _event(session, job.id, "state", CANCELLED, now)
    expired = session.scalars(
        select(Job).where(
            Job.status == RUNNING, Job.lease_expires_at < now)).all()
    lost = []
    for job in expired:
        done = session.execute(
            update(Job)
            .where(Job.id == job.id, Job.status == RUNNING,
                   Job.lease_expires_at < now)
            .values(status=LOST, finished_at=now, lease_expires_at=None,
                    params=secrets.scrub(job.params),
                    error="The worker {} stopped responding. The job was "
                          "not completed, and it is not known how much "
                          "of it was done.".format(job.worker_id))).rowcount
        if done:
            _event(session, job.id, "state", LOST, now)
            lost.append(job.id)
    return lost


def reap():
    """
    _function_: `mast.jobs.queue.reap()`

    Declare each running job whose worker is silent for longer than
    its lease `lost`, and return the ids of those jobs. Also cancel
    each queued job whose submitter waited for it and went away.
    `claim` does this too, so only a program that never claims needs
    to call it.
    """
    with db.session() as session:
        return _reap(session, _now())


def reap_if_expired(info, principal=LOCAL):
    """
    _function_: `mast.jobs.queue.reap_if_expired(info, principal=LOCAL)`

    Return `info` (a `JobInfo`), or a new `JobInfo` if the job runs and
    its lease has ended: the job is then declared `lost` (see `reap`).

    The queue finds a dead worker only when a job is claimed. A program
    that follows a job calls this, so that the job does not seem to
    run for ever when no process claims.
    """
    if info.status != RUNNING or info.lease_expires_at is None \
            or info.lease_expires_at >= _now():
        return info
    reap()
    return get(info.id, principal)


def _alive_cutoff(now):
    """A worker that was seen after this time is alive."""
    return now - timedelta(seconds=config.lease_seconds())


def _next_job(worker_id, workers_alive):
    """The query for the oldest queued job that `worker_id` can start
    now. `workers_alive` is True if a worker process is alive."""
    mine = aliased(JobTarget)
    theirs = aliased(JobTarget)
    other = aliased(Job)
    older = or_(
        other.created_at < Job.created_at,
        and_(other.created_at == Job.created_at, other.id < Job.id))
    blocked = (
        select(other.id)
        .select_from(mine)
        .join(theirs, theirs.appliance == mine.appliance)
        .join(other, other.id == theirs.job_id)
        .where(
            mine.job_id == Job.id,
            other.id != Job.id,
            # A passive job holds up nobody
            other.passive.is_(False),
            # Two jobs can share an appliance only if neither is exclusive
            or_(other.exclusive, Job.exclusive),
            # A job that waits also blocks the jobs behind it, so that
            # jobs for one appliance start in the order of submission
            or_(other.status == RUNNING,
                and_(other.status == QUEUED, older,
                     # A detached job waits for a worker. While none is
                     # alive it must not hold up the jobs that a
                     # program runs itself.
                     or_(other.attached_until.is_not(None),
                         true() if workers_alive else false()))))
        .exists())
    return (
        select(Job.id)
        # A passive job waits for nobody
        .where(Job.status == QUEUED, or_(Job.passive, ~blocked),
               or_(Job.reserved_for.is_(None),
                   Job.reserved_for == worker_id))
        .order_by(Job.created_at, Job.id)
        .limit(1))


def claim(worker_id, lease_seconds=None, job_id=None, passive_only=False):
    """
    _function_: `mast.jobs.queue.claim(worker_id, lease_seconds=None,
    job_id=None, passive_only=False)`

    Take the oldest job that can start now. Return its id, or `None`
    if there is no such job. The job is `running` and belongs to
    `worker_id` until the worker calls `finish` or stops sending
    heartbeats.

    Parameters:

    * `worker_id`: A name that only this worker uses.
    * `lease_seconds`: How long the worker may be silent. The default
    is in `jobs.conf`.
    * `job_id`: Take only this job. It still waits for its turn: the
    result is `None` while the job cannot start.
    * `passive_only`: Take only a passive job (see `submit`), for the
    slot of a worker that keeps the polls going while long jobs run.

    While no worker is alive, the queued jobs that were submitted
    without `attached` do not hold up the other jobs, see the rules at
    the top of this module.
    """
    with db.session() as session:
        db.lock_queue(session.connection())
        now = _now()
        _reap(session, now)
        # A worker records that it is alive before it claims, so its
        # own claims see True here
        workers_alive = session.scalar(
            select(Worker.id)
            .where(Worker.last_seen_at >= _alive_cutoff(now)).limit(1)) \
            is not None
        query = _next_job(worker_id, workers_alive)
        if job_id is not None:
            query = query.where(Job.id == job_id)
        if passive_only:
            query = query.where(Job.passive.is_(True))
        while True:
            found = session.scalar(query)
            if found is None:
                return None
            claimed = session.execute(
                update(Job)
                .where(Job.id == found, Job.status == QUEUED)
                .values(status=RUNNING, worker_id=worker_id, started_at=now,
                        lease_expires_at=_lease(now, lease_seconds))).rowcount
            if claimed:
                _event(session, found, "state",
                       "{} on {}".format(RUNNING, worker_id), now)
                return found
            # The job was cancelled after the query saw it. Look again.


def _owned(job_id, worker_id):
    return (Job.id == job_id, Job.worker_id == worker_id,
            Job.status == RUNNING)


def load(job_id, worker_id):
    """
    _function_: `mast.jobs.queue.load(job_id, worker_id)`

    Return `(command, values)` for a job that `worker_id` claimed:
    the `mast.cli.Command` and the parameters as a `dict`, with the
    secret parameters decrypted. Raises `NotOwner` if the worker does
    not have the job.
    """
    with db.session() as session:
        job = session.scalar(select(Job).where(*_owned(job_id, worker_id)))
        if job is None:
            raise NotOwner(job_id)
        command, params = job.command, job.params
    return registry.resolve(command), secrets.unseal(params)


def heartbeat(job_id, worker_id, lease_seconds=None):
    """
    _function_: `mast.jobs.queue.heartbeat(job_id, worker_id,
    lease_seconds=None)`

    Keep a job for another `lease_seconds`. Return a `Heartbeat`.
    """
    with db.session() as session:
        owned = session.execute(
            update(Job).where(*_owned(job_id, worker_id))
            .values(lease_expires_at=_lease(_now(), lease_seconds))).rowcount
        if not owned:
            return Heartbeat(owned=False)
        return Heartbeat(owned=True, cancel_requested=bool(session.scalar(
            select(Job.cancel_requested).where(Job.id == job_id))))


def cancel_requested(job_id):
    """
    _function_: `mast.jobs.queue.cancel_requested(job_id)`

    Return True if somebody asked to cancel the job. It only reads, so
    a running job can ask often (`cancel_poll_seconds` in `jobs.conf`).
    """
    with db.session() as session:
        return bool(session.scalar(
            select(Job.cancel_requested).where(Job.id == job_id)))


def add_event(job_id, message, kind="say", worker_id=None):
    """
    _function_: `mast.jobs.queue.add_event(job_id, message, kind="say",
    worker_id=None)`

    Add an event to a job. A worker stores each line of progress from
    the command this way. Return True if somebody asked to cancel the
    job.

    If `worker_id` is given and that worker does not have the job any
    more, nothing is stored and `NotOwner` is raised.
    """
    with db.session() as session:
        if worker_id is not None and session.scalar(
                select(Job.id).where(*_owned(job_id, worker_id))) is None:
            raise NotOwner(job_id)
        _event(session, job_id, kind, message)
        return bool(session.scalar(
            select(Job.cancel_requested).where(Job.id == job_id)))


def finish(job_id, worker_id, status, text="", html="", history="",
           error=None, data=None):
    """
    _function_: `mast.jobs.queue.finish(job_id, worker_id, status,
    text="", html="", history="", error=None, data=None)`

    Record the end of a job and its result. The secret parameters of
    the job are removed. Return False, and change nothing, if
    `worker_id` does not have the job any more (it was declared lost).

    Parameters:

    * `status`: `succeeded`, `partial`, `failed` or `cancelled`.
    * `text`, `html`, `history`, `data`: See `ResultInfo`.
    * `error`: One line that says why the job failed.
    """
    if status not in FINISHED or status == LOST:
        raise ValueError("{} is not a status to finish with".format(status))
    now = _now()
    with db.session() as session:
        job = session.scalar(select(Job).where(*_owned(job_id, worker_id)))
        if job is None:
            return False
        finished = session.execute(
            update(Job).where(*_owned(job_id, worker_id))
            .values(status=status, finished_at=now, lease_expires_at=None,
                    error=error, params=secrets.scrub(job.params))).rowcount
        if not finished:
            return False
        session.add(JobResult(
            job_id=job_id, text=text, html=html, history=history,
            data=data))
        _event(session, job_id, "state", status, now)
    return True


def abandon(job_id, worker_id, reason):
    """
    _function_: `mast.jobs.queue.abandon(job_id, worker_id, reason)`

    Declare a job that `worker_id` has `lost`, because the worker
    must stop before the command ends. `reason` is stored as the error
    of the job. Return False if the worker does not have the job.
    """
    now = _now()
    with db.session() as session:
        job = session.scalar(select(Job).where(*_owned(job_id, worker_id)))
        if job is None:
            return False
        abandoned = session.execute(
            update(Job).where(*_owned(job_id, worker_id))
            .values(status=LOST, finished_at=now, lease_expires_at=None,
                    error=reason, params=secrets.scrub(job.params))).rowcount
        if abandoned:
            _event(session, job_id, "state", LOST, now)
        return bool(abandoned)


# The row of a worker that did not say goodbye is removed after this long
_FORGET_WORKERS_AFTER = timedelta(days=1)


def worker_seen(worker_id, hostname, pid, concurrency):
    """
    _function_: `mast.jobs.queue.worker_seen(worker_id, hostname, pid,
    concurrency)`

    Record that a worker process is alive. A worker calls this when it
    starts and then several times in each lease.
    """
    now = _now()
    with db.session() as session:
        worker = session.get(Worker, worker_id)
        if worker is None:
            session.add(Worker(
                id=worker_id, hostname=hostname, pid=pid,
                concurrency=concurrency, started_at=now, last_seen_at=now))
        else:
            worker.last_seen_at = now
        session.execute(delete(Worker).where(
            Worker.last_seen_at < now - _FORGET_WORKERS_AFTER))


def worker_gone(worker_id):
    """
    _function_: `mast.jobs.queue.worker_gone(worker_id)`

    Record that a worker process stopped.
    """
    with db.session() as session:
        session.execute(delete(Worker).where(Worker.id == worker_id))


def workers(alive=True, principal=LOCAL):
    """
    _function_: `mast.jobs.queue.workers(alive=True, principal=LOCAL)`

    Return the worker processes as a list of `WorkerInfo`, oldest
    first. With `alive`, only the workers that were seen within the
    last lease (`lease_seconds` in `jobs.conf`).
    """
    authorize(principal, "read")
    query = select(Worker).order_by(Worker.started_at, Worker.id)
    if alive:
        query = query.where(Worker.last_seen_at >= _alive_cutoff(_now()))
    with db.session(write=False) as session:
        return [WorkerInfo.model_validate(row)
                for row in session.scalars(query)]

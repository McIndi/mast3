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
_module_: `mast.jobs.models`

The tables of the job queue.

* `Job`: One invocation of a command.
* `JobTarget`: One appliance that a job talks to.
* `JobEvent`: One line of progress from a job, or one change of state.
* `JobResult`: What a finished job returned.
* `Worker`: One worker process that is, or was, running.

A change to these tables needs a migration in
`mast/jobs/migrations/versions`.
"""
from datetime import datetime, timezone

from sqlalchemy import (
    JSON, Boolean, DateTime, ForeignKey, Index, Integer, MetaData, String,
    Text)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

__all__ = [
    "Base", "Job", "JobTarget", "JobEvent", "JobResult", "Worker",
    "Schedule",
    "QUEUED", "RUNNING", "SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED",
    "LOST", "FINISHED",
]

QUEUED = "queued"
RUNNING = "running"
SUCCEEDED = "succeeded"
# The command ended, and its result says that some of what it did
# failed: an action failed on one of three appliances, for example.
PARTIAL = "partial"
# The command raised an exception, or its result says that all of
# what it did failed.
FAILED = "failed"
CANCELLED = "cancelled"
# The worker stopped without a result. Nobody knows how far the job got.
LOST = "lost"

FINISHED = (SUCCEEDED, PARTIAL, FAILED, CANCELLED, LOST)


class UTCDateTime(TypeDecorator):
    """A time in UTC. SQLite does not keep the time zone, so the zone
    is put back when a value is read."""
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("a time without a time zone is not accepted")
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class Base(DeclarativeBase):
    # Constraints need names, or a migration cannot change them
    metadata = MetaData(naming_convention={
        "ix": "ix_%(column_0_label)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    })


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_status_created_at", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    # The id of the command, for example "backups.normal-backup"
    command: Mapped[str] = mapped_column(String(200))
    # The parameters as JSON. Secret parameters are encrypted, see
    # mast.jobs.secrets
    params: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16))
    # An exclusive job does not run while another job uses one of its
    # appliances
    exclusive: Mapped[bool] = mapped_column(Boolean)
    # The name of the principal that submitted the job
    submitted_by: Mapped[str] = mapped_column(String(200))
    # The job that made this job. Not used yet.
    parent_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("jobs.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    worker_id: Mapped[str | None] = mapped_column(String(200))
    # The worker must send a heartbeat before this time
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    cancel_requested: Mapped[bool] = mapped_column(Boolean)
    # If set, only this worker may claim the job. A program that does
    # its own job reserves it.
    reserved_for: Mapped[str | None] = mapped_column(String(200))
    # If set, a program waits for the job, and says so again and again
    # while the job is queued. If the program is silent past this
    # time, the job is cancelled.
    attached_until: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # One line that says why the job failed
    error: Mapped[str | None] = mapped_column(Text)
    # A passive job only reads, and neither waits for nor holds up
    # other jobs on its appliances (the status polls)
    passive: Mapped[bool] = mapped_column(Boolean, default=False)
    # The schedule that submitted the job
    schedule_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("schedules.id", ondelete="SET NULL"), index=True)


class JobTarget(Base):
    __tablename__ = "job_targets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    # The address of the appliance, after environments and aliases
    # are resolved. Or "resource:<name>" for something else that the
    # job locks, see mast.jobs.queue
    appliance: Mapped[str] = mapped_column(String(255), index=True)


class JobEvent(Base):
    __tablename__ = "job_events"
    __table_args__ = (
        Index("ix_job_events_job_id_id", "job_id", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("jobs.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    # "say" for progress from the command, "state" for a change of status
    kind: Mapped[str] = mapped_column(String(16))
    message: Mapped[str] = mapped_column(Text)


class JobResult(Base):
    __tablename__ = "job_results"

    job_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("jobs.id", ondelete="CASCADE"),
        primary_key=True)
    # The result for a terminal
    text: Mapped[str] = mapped_column(Text)
    # The result for the web GUI
    html: Mapped[str] = mapped_column(Text)
    # The requests and responses, or the traceback of a failed job
    history: Mapped[str] = mapped_column(Text)
    # The result as JSON types, for the web UI (Result.data())
    data: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class Worker(Base):
    __tablename__ = "workers"

    # The name of the worker process. The jobs of its slots have the
    # worker_id "<id>/<slot>".
    id: Mapped[str] = mapped_column(String(200), primary_key=True)
    hostname: Mapped[str] = mapped_column(String(255))
    pid: Mapped[int] = mapped_column(Integer)
    # How many jobs the worker runs at the same time
    concurrency: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime)
    # The worker writes this time again and again while it is alive
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime)


class Schedule(Base):
    """A command that the scheduler of mastd puts in the queue when its
    cron expression is due (see mast.jobs.schedules)."""
    __tablename__ = "schedules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    # The id of the command, for example "system.list-domains"
    command: Mapped[str] = mapped_column(String(200))
    # The parameters as JSON. Never a secret: a schedule uses a stored
    # credential
    params: Mapped[dict] = mapped_column(JSON)
    # Five fields, in UTC
    cron: Mapped[str] = mapped_column(String(100))
    enabled: Mapped[bool] = mapped_column(Boolean)
    description: Mapped[str] = mapped_column(Text)
    # The principal that made it. Its jobs run with the role of this
    # principal, as long as it may submit jobs.
    created_by: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)
    # The last time the schedule was due and handled: a job was
    # submitted, or it was skipped (see last_skip_reason)
    last_fired_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # Not a foreign key: retention can delete the job
    last_job_id: Mapped[str | None] = mapped_column(String(32))
    last_skip_reason: Mapped[str | None] = mapped_column(Text)

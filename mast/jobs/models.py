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
    "Base", "Job", "JobTarget", "JobEvent", "JobResult",
    "QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED", "LOST",
    "FINISHED",
]

QUEUED = "queued"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"
CANCELLED = "cancelled"
# The worker stopped without a result. Nobody knows how far the job got.
LOST = "lost"

FINISHED = (SUCCEEDED, FAILED, CANCELLED, LOST)


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
    # One line that says why the job failed
    error: Mapped[str | None] = mapped_column(Text)


class JobTarget(Base):
    __tablename__ = "job_targets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    # The address of the appliance, after environments and aliases
    # are resolved
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

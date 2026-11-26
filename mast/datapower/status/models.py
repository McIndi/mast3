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
_module_: `mast.datapower.status.models`

The tables of the status of the appliances, in the job database
(migration 0008). `mast datapower collect-status` writes them, the Appliances
page reads them, and `mast.jobs.retention` prunes them.

* `status_latest`: the last poll of each registered appliance.
* `status_samples`: a few numbers of each poll, for the charts of the
last hours.
* `status_rollups`: the samples summed up by hour and by day, for longer
ranges.

No configuration is stored: only numbers, states and names.
"""
from datetime import datetime

from sqlalchemy import (
    JSON, Boolean, Float, ForeignKey, Index, Integer, String, Text)
from sqlalchemy.orm import Mapped, mapped_column

from mast.jobs.models import Base, UTCDateTime

__all__ = ["StatusLatest", "StatusSample", "StatusRollup", "HOUR", "DAY"]

HOUR = "hour"
DAY = "day"

def _appliance():
    return ForeignKey("appliances.id", ondelete="CASCADE")


class StatusLatest(Base):
    __tablename__ = "status_latest"

    appliance_id: Mapped[int] = mapped_column(_appliance(), primary_key=True)
    collected_at: Mapped[datetime] = mapped_column(UTCDateTime)
    ok: Mapped[bool] = mapped_column(Boolean)
    # The last poll that worked: the values are from it
    last_ok_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # Why the poll failed; None when it worked
    error: Mapped[str | None] = mapped_column(Text)
    # The name of the stored credential of the poll, if any
    credential: Mapped[str | None] = mapped_column(String(64))
    firmware: Mapped[str | None] = mapped_column(String(64))
    delivery: Mapped[str | None] = mapped_column(String(16))
    model: Mapped[str | None] = mapped_column(String(64))
    serial: Mapped[str | None] = mapped_column(String(64))
    uptime_seconds: Mapped[int | None] = mapped_column(Integer)
    appliance_time: Mapped[str | None] = mapped_column(String(64))
    cpu_10s: Mapped[float | None] = mapped_column(Float)
    cpu_1m: Mapped[float | None] = mapped_column(Float)
    mem_used_pct: Mapped[float | None] = mapped_column(Float)
    load_pct: Mapped[float | None] = mapped_column(Float)
    fs_free_encrypted_mb: Mapped[int | None] = mapped_column(Integer)
    fs_free_temporary_mb: Mapped[int | None] = mapped_column(Integer)
    fs_total_temporary_mb: Mapped[int | None] = mapped_column(Integer)
    fs_free_internal_mb: Mapped[int | None] = mapped_column(Integer)
    domains_total: Mapped[int | None] = mapped_column(Integer)
    domains_down: Mapped[int | None] = mapped_column(Integer)
    objects_down: Mapped[int | None] = mapped_column(Integer)
    # {"domains": [{name, op_state, admin_state}],
    #  "objects_down": [{class, name, domain, event_code}],
    #  "objects_checked_at": {domain: ISO time}}
    detail: Mapped[dict | None] = mapped_column(JSON)


class StatusSample(Base):
    __tablename__ = "status_samples"
    __table_args__ = (
        Index("ix_status_samples_appliance_at", "appliance_id",
              "collected_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    appliance_id: Mapped[int] = mapped_column(_appliance())
    collected_at: Mapped[datetime] = mapped_column(UTCDateTime)
    ok: Mapped[bool] = mapped_column(Boolean)
    cpu_1m: Mapped[float | None] = mapped_column(Float)
    mem_used_pct: Mapped[float | None] = mapped_column(Float)
    load_pct: Mapped[float | None] = mapped_column(Float)
    fs_free_temporary_mb: Mapped[int | None] = mapped_column(Integer)
    objects_down: Mapped[int | None] = mapped_column(Integer)


class StatusRollup(Base):
    __tablename__ = "status_rollups"

    appliance_id: Mapped[int] = mapped_column(_appliance(), primary_key=True)
    # HOUR or DAY
    period: Mapped[str] = mapped_column(String(8), primary_key=True)
    start: Mapped[datetime] = mapped_column(UTCDateTime, primary_key=True)
    samples: Mapped[int] = mapped_column(Integer)
    ok_ratio: Mapped[float] = mapped_column(Float)
    cpu_avg: Mapped[float | None] = mapped_column(Float)
    cpu_max: Mapped[float | None] = mapped_column(Float)
    mem_avg: Mapped[float | None] = mapped_column(Float)
    mem_max: Mapped[float | None] = mapped_column(Float)
    load_avg: Mapped[float | None] = mapped_column(Float)
    objects_down_max: Mapped[int | None] = mapped_column(Integer)

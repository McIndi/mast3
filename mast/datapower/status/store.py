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
_module_: `mast.datapower.status.store`

Keep and read the status of the appliances
(`mast.datapower.status.models`).

* `save`: the result of a poll: `status_latest` and a sample, then the
hourly rollups of the hours that ended.
* `roll_up_days`: the daily rollups of the days that ended, from the
hourly ones. `mast.jobs.retention` calls it each day, before it
deletes old samples and rollups.
* `latest` and `history`: what the Appliances page shows.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from mast.datapower.status.models import (
    DAY, HOUR, StatusLatest, StatusRollup, StatusSample)
from mast.jobs import db

__all__ = ["save", "previous_details", "roll_up_hours", "roll_up_days",
           "latest", "history", "MAX_CATCH_UP_HOURS"]

# How far back the rollups catch up after a gap in the polls
MAX_CATCH_UP_HOURS = 48
MAX_CATCH_UP_DAYS = 31

_COLUMNS = [c.name for c in StatusLatest.__table__.columns
            if c.name not in ("appliance_id",)]


def _hour(when):
    return when.astimezone(timezone.utc).replace(
        minute=0, second=0, microsecond=0)


def _day(when):
    return _hour(when).replace(hour=0)


def previous_details(appliance_ids):
    """The `detail` of the last poll of each appliance, by id."""
    if not appliance_ids:
        return {}
    with db.session(write=False) as session:
        return {row.appliance_id: row.detail for row in session.scalars(
            select(StatusLatest).where(
                StatusLatest.appliance_id.in_(list(appliance_ids))))}


def save(polled, registered, now, credential=None):
    """
    _function_: `mast.datapower.status.store.save(polled, registered,
    now, credential=None)`

    Keep the result of a poll. `polled` has `(appliance, values, error)`
    for each appliance (see `mast.datapower.status.collector.poll`),
    and `registered` maps the hostname of each registered appliance to
    its registry record. An appliance that is not registered is not
    kept. A failed poll keeps the values of the last one that worked.
    """
    ids = []
    with db.session() as session:
        for appliance, values, error in polled:
            record = registered.get(appliance.hostname)
            if record is None:
                continue
            ids.append(record.id)
            row = session.get(StatusLatest, record.id)
            if row is None:
                row = StatusLatest(appliance_id=record.id)
                session.add(row)
            row.collected_at = now
            row.ok = error is None
            row.error = error
            row.credential = credential
            if error is None:
                row.last_ok_at = now
                for name, value in values.items():
                    setattr(row, name, value)
            session.add(StatusSample(
                appliance_id=record.id, collected_at=now, ok=error is None,
                cpu_1m=row.cpu_1m if error is None else None,
                mem_used_pct=row.mem_used_pct if error is None else None,
                load_pct=row.load_pct if error is None else None,
                fs_free_temporary_mb=row.fs_free_temporary_mb
                if error is None else None,
                objects_down=row.objects_down if error is None else None))
    roll_up_hours(ids, now)


def _summary(rows, weights=None):
    """Two functions of a column name: its average over `rows`
    (weighted by `weights`), and its highest value. Both skip None."""
    weights = weights or [1] * len(rows)

    def values(name):
        return [(getattr(r, name), w) for r, w in zip(rows, weights)
                if getattr(r, name) is not None]

    def avg(name):
        found = values(name)
        total = sum(w for _, w in found)
        return sum(v * w for v, w in found) / total if total else None

    def top(name):
        found = [v for v, _ in values(name)]
        return max(found) if found else None
    return avg, top


def _hour_rollup(appliance_id, start, samples):
    avg, top = _summary(samples)
    return StatusRollup(
        appliance_id=appliance_id, period=HOUR, start=start,
        samples=len(samples),
        ok_ratio=sum(1 for s in samples if s.ok) / len(samples),
        cpu_avg=avg("cpu_1m"), cpu_max=top("cpu_1m"),
        mem_avg=avg("mem_used_pct"), mem_max=top("mem_used_pct"),
        load_avg=avg("load_pct"), objects_down_max=top("objects_down"))


def _day_rollup(appliance_id, start, hours):
    weights = [h.samples for h in hours]
    avg, top = _summary(hours, weights)
    total = sum(weights)
    return StatusRollup(
        appliance_id=appliance_id, period=DAY, start=start, samples=total,
        ok_ratio=sum(h.ok_ratio * h.samples for h in hours) / total,
        cpu_avg=avg("cpu_avg"), cpu_max=top("cpu_max"),
        mem_avg=avg("mem_avg"), mem_max=top("mem_max"),
        load_avg=avg("load_avg"), objects_down_max=top("objects_down_max"))


def _each(appliance_ids, fn):
    """Call `fn(session, appliance_id)` in a transaction of its own for
    each appliance. Another process that made the same rollups first
    (two collects at once) is not an error."""
    made = 0
    for appliance_id in appliance_ids:
        try:
            with db.session() as session:
                made += fn(session, appliance_id)
        except IntegrityError:
            pass
    return made


def _last_start(session, appliance_id, period):
    return session.scalar(
        select(func.max(StatusRollup.start)).where(
            StatusRollup.appliance_id == appliance_id,
            StatusRollup.period == period))


def roll_up_hours(appliance_ids, now=None):
    """
    _function_: `mast.datapower.status.store.roll_up_hours(appliance_ids,
    now=None)`

    Make the hourly rollups of the hours that ended before `now` and
    have none yet, up to `MAX_CATCH_UP_HOURS` back.
    """
    now = now or datetime.now(timezone.utc)
    current = _hour(now)
    earliest = current - timedelta(hours=MAX_CATCH_UP_HOURS)

    def hours(session, appliance_id):
        last = _last_start(session, appliance_id, HOUR)
        start = max(earliest, last + timedelta(hours=1)) \
            if last is not None else earliest
        if start >= current:
            return 0
        samples = session.scalars(
            select(StatusSample).where(
                StatusSample.appliance_id == appliance_id,
                StatusSample.collected_at >= start,
                StatusSample.collected_at < current)
            .order_by(StatusSample.collected_at))
        by_hour = {}
        for sample in samples:
            by_hour.setdefault(_hour(sample.collected_at), []).append(sample)
        for hour, found in sorted(by_hour.items()):
            session.add(_hour_rollup(appliance_id, hour, found))
        return len(by_hour)
    return _each(appliance_ids, hours)


def roll_up_days(now=None):
    """
    _function_: `mast.datapower.status.store.roll_up_days(now=None)`

    Make the daily rollups of the days that ended before `now` and have
    none yet, from the hourly rollups, for every appliance. Return how
    many it made.
    """
    now = now or datetime.now(timezone.utc)
    today = _day(now)
    with db.session(write=False) as session:
        appliance_ids = list(session.scalars(
            select(StatusRollup.appliance_id).where(
                StatusRollup.period == HOUR).distinct()))

    def days(session, appliance_id):
        last = _last_start(session, appliance_id, DAY)
        start = last + timedelta(days=1) if last is not None \
            else today - timedelta(days=MAX_CATCH_UP_DAYS)
        hours = session.scalars(
            select(StatusRollup).where(
                StatusRollup.appliance_id == appliance_id,
                StatusRollup.period == HOUR,
                StatusRollup.start >= start,
                StatusRollup.start < today)
            .order_by(StatusRollup.start))
        by_day = {}
        for hour in hours:
            by_day.setdefault(_day(hour.start), []).append(hour)
        for day, found in sorted(by_day.items()):
            session.add(_day_rollup(appliance_id, day, found))
        return len(by_day)
    return _each(appliance_ids, days)


def latest():
    """
    _function_: `mast.datapower.status.store.latest()`

    The `status_latest` of every appliance that has one, as dicts, by
    appliance id.
    """
    with db.session(write=False) as session:
        return {row.appliance_id: {name: getattr(row, name)
                                   for name in _COLUMNS}
                for row in session.scalars(select(StatusLatest))}


def history(appliance_id, since, until=None, samples_hours=48):
    """
    _function_: `mast.datapower.status.store.history(appliance_id,
    since, until=None, samples_hours=48)`

    The points for the charts of one appliance: the samples, if `since`
    is less than `samples_hours` before `until`, otherwise the hourly
    rollups (or daily ones beyond 31 days). Returns `(kind, points)`,
    where each point is a dict with `at`, `ok`, `cpu`, `mem`, `load`
    and `objects_down`.
    """
    until = until or datetime.now(timezone.utc)
    span = until - since
    with db.session(write=False) as session:
        if span <= timedelta(hours=samples_hours):
            rows = session.scalars(
                select(StatusSample).where(
                    StatusSample.appliance_id == appliance_id,
                    StatusSample.collected_at >= since,
                    StatusSample.collected_at <= until)
                .order_by(StatusSample.collected_at))
            return "samples", [
                {"at": r.collected_at.isoformat(), "ok": r.ok,
                 "cpu": r.cpu_1m, "mem": r.mem_used_pct, "load": r.load_pct,
                 "objects_down": r.objects_down} for r in rows]
        period = HOUR if span <= timedelta(days=31) else DAY
        rows = session.scalars(
            select(StatusRollup).where(
                StatusRollup.appliance_id == appliance_id,
                StatusRollup.period == period,
                StatusRollup.start >= since,
                StatusRollup.start <= until)
            .order_by(StatusRollup.start))
        return period, [
            {"at": r.start.isoformat(), "ok": r.ok_ratio,
             "cpu": r.cpu_avg, "mem": r.mem_avg, "load": r.load_avg,
             "objects_down": r.objects_down_max} for r in rows]

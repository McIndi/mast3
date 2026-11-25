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
_module_: `mast.jobs.retention`

Delete what is older than `[retention]` of `jobs.conf` says:

* finished jobs, with their progress, results, appliances and files
(`tmp/web/jobs/<id>`). Jobs of schedules have rules of their own. A
child job goes with its parent, and a parent waits while one of its
children has not finished;
* the uses of stored credentials and the audit events;
* the status of the appliances: the samples of each poll, and the
hourly and daily rollups (`mast.datapower.status`). The daily rollups
of the days that ended are made first;
* the snapshots of the crypto inventory, but the newest of each
appliance (`mast.datapower.crypto`);
* the uploads of the web UI (`var/www/uploads`);
* the log files in `var/log`, and the folders that they leave empty;
* the files in `tmp/` (reports, exports and downloads of the command
line), and the folders that they leave empty, but not the folders of
the jobs of the web UI (`tmp/web/jobs`), which go with their jobs.
Backups go to `var/backups` by default, which is never emptied.

`prune` deletes in batches of `BATCH` rows, each in its own
transaction, so that it never holds the SQLite lock for long. The
workers run it each day (`[retention] schedule`, see
`mast.jobs.worker`); if several do, the first deletes and the others
find little left. `mast-jobs prune` runs it now, and with `--dry-run`
only counts.
"""
import os
import shutil
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, delete, func, select

from mast.config import MAST_HOME
from mast.jobs import config, db
from mast.jobs.models import FINISHED, SCHEDULED as _SCHEDULED, SUCCEEDED, Job
from mast.logging import make_logger

__all__ = ["prune", "BATCH"]

BATCH = 1000


def _now():
    return datetime.now(timezone.utc)


def _cutoff(now, rule):
    days = config.retention_days(rule)
    return None if days is None else now - timedelta(days=days)


def _job_rules(now):
    """The conditions on the finished jobs that are old enough."""
    ended = func.coalesce(Job.finished_at, Job.created_at)
    rules = []
    for rule, condition in (
            ("jobs_days", ~_SCHEDULED),
            ("scheduled_jobs_days", and_(_SCHEDULED, Job.status == SUCCEEDED)),
            ("scheduled_failed_days", and_(_SCHEDULED, Job.status != SUCCEEDED))):
        cutoff = _cutoff(now, rule)
        if cutoff is not None:
            rules.append((rule, and_(condition, ended < cutoff)))
    return rules


def _family(session, ids):
    """`ids` and all their descendants, or None if one of the
    descendants has not finished."""
    family = list(ids)
    level = list(ids)
    while level:
        rows = session.execute(
            select(Job.id, Job.status).where(Job.parent_id.in_(level))).all()
        if any(status not in FINISHED for _, status in rows):
            return None
        level = [job_id for job_id, _ in rows if job_id not in family]
        family += level
    return family


def _remove_job_files(job_ids):
    # Imported here: the directory of the files is the web UI's
    from mast.web.catalog import job_dir
    for job_id in job_ids:
        path = os.path.join(MAST_HOME, job_dir(job_id))
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)


def _prune_jobs(now, dry_run, counts):
    for rule, condition in _job_rules(now):
        counted = 0
        skipped = set()
        while True:
            with db.session() as session:
                query = (select(Job.id)
                         .where(Job.status.in_(FINISHED), Job.parent_id.is_(None),
                                condition)
                         .order_by(Job.created_at, Job.id))
                if skipped:
                    query = query.where(Job.id.not_in(skipped))
                ids = list(session.scalars(query.limit(BATCH)))
                if not ids:
                    break
                doomed = []
                for job_id in ids:
                    family = _family(session, [job_id])
                    if family is None:
                        skipped.add(job_id)
                    else:
                        doomed += family
                if dry_run:
                    counted += len(doomed)
                    skipped.update(ids)
                    continue
                if doomed:
                    # One statement: the children go with their parents
                    session.execute(delete(Job).where(Job.id.in_(doomed)))
                counted += len(doomed)
            if not dry_run:
                _remove_job_files(doomed)
        counts[rule] = counted


def _prune_rows(now, dry_run, counts):
    # Imported here: the credential store is needed only for this
    from mast.credentials.models import AuditEvent, CredentialUse
    for rule, model, column in (
            ("credential_uses_days", CredentialUse, CredentialUse.used_at),
            ("audit_events_days", AuditEvent, AuditEvent.at)):
        cutoff = _cutoff(now, rule)
        if cutoff is None:
            continue
        counted = 0
        while True:
            with db.session() as session:
                ids = list(session.scalars(
                    select(model.id).where(column < cutoff)
                    .order_by(model.id).limit(BATCH)
                    .offset(counted if dry_run else 0)))
                if not ids:
                    break
                if not dry_run:
                    session.execute(delete(model).where(model.id.in_(ids)))
                counted += len(ids)
            if len(ids) < BATCH:
                break
        counts[rule] = counted


def _prune_status(now, dry_run, counts):
    try:
        # Imported here: the status is the DataPower plugin's
        from mast.datapower.status import store
        from mast.datapower.status.models import (
            DAY, HOUR, StatusRollup, StatusSample)
    except ImportError:
        return
    if not dry_run:
        store.roll_up_days(now)
    cutoff = _cutoff(now, "status_samples_days")
    if cutoff is not None:
        counted = 0
        while True:
            with db.session() as session:
                ids = list(session.scalars(
                    select(StatusSample.id)
                    .where(StatusSample.collected_at < cutoff)
                    .order_by(StatusSample.id).limit(BATCH)
                    .offset(counted if dry_run else 0)))
                if ids and not dry_run:
                    session.execute(delete(StatusSample).where(
                        StatusSample.id.in_(ids)))
                counted += len(ids)
            if len(ids) < BATCH:
                break
        counts["status_samples_days"] = counted
    # There are few rollups (24 an appliance a day): one statement
    for rule, period in (("status_hourly_days", HOUR),
                         ("status_daily_days", DAY)):
        cutoff = _cutoff(now, rule)
        if cutoff is None:
            continue
        where = (StatusRollup.period == period, StatusRollup.start < cutoff)
        with db.session() as session:
            if dry_run:
                counts[rule] = session.scalar(
                    select(func.count()).select_from(StatusRollup)
                    .where(*where))
            else:
                counts[rule] = session.execute(
                    delete(StatusRollup).where(*where)).rowcount


def _prune_crypto(now, dry_run, counts):
    cutoff = _cutoff(now, "crypto_snapshots_days")
    if cutoff is None:
        return
    try:
        # Imported here: the inventory is the DataPower plugin's
        from mast.datapower.crypto.models import (CryptoProbeResult,
                                                  CryptoSnapshot)
    except ImportError:
        return
    with db.session() as session:
        # The probes go by the same rule; the newest of each port stays
        newest = select(func.max(CryptoProbeResult.id)).group_by(
            CryptoProbeResult.appliance_id, CryptoProbeResult.host,
            CryptoProbeResult.port)
        where = (CryptoProbeResult.probed_at < cutoff,
                 CryptoProbeResult.id.not_in(newest))
        if not dry_run:
            session.execute(delete(CryptoProbeResult).where(*where))
    with db.session() as session:
        newest = select(func.max(CryptoSnapshot.id)).group_by(
            CryptoSnapshot.appliance_id)
        ids = list(session.scalars(
            select(CryptoSnapshot.id).where(
                CryptoSnapshot.collected_at < cutoff,
                CryptoSnapshot.id.not_in(newest))))
        if ids and not dry_run:
            # Their objects, certificates, files and edges go with them
            for start in range(0, len(ids), BATCH):
                session.execute(delete(CryptoSnapshot).where(
                    CryptoSnapshot.id.in_(ids[start:start + BATCH])))
    counts["crypto_snapshots_days"] = len(ids)


def _old(path, cutoff):
    try:
        return datetime.fromtimestamp(os.path.getmtime(path), timezone.utc) < cutoff
    except OSError:
        return False


def _prune_uploads(now, dry_run, counts, root):
    cutoff = _cutoff(now, "uploads_days")
    if cutoff is None:
        return
    # Imported here: the uploads are the web UI's
    from mast.web.uploads import UPLOADS_DIR
    directory = os.path.join(root, UPLOADS_DIR)
    counted = 0
    if os.path.isdir(directory):
        for name in os.listdir(directory):
            path = os.path.join(directory, name)
            if os.path.isdir(path) and _old(path, cutoff):
                counted += 1
                if not dry_run:
                    shutil.rmtree(path, ignore_errors=True)
    counts["uploads_days"] = counted


# The file that keeps an empty folder in the MAST home
PLACEHOLDER = ".empty"


def _key(path):
    return os.path.normcase(os.path.abspath(path))


def _inside(path, skip):
    """True if `path` is one of the folders `skip` (keys), or in one."""
    path = _key(path)
    return any(path == folder or path.startswith(folder + os.sep)
               for folder in skip)


def _prune_tree(directory, cutoff, dry_run, skip=()):
    """Delete the files under `directory` older than `cutoff`, and the
    folders that they leave empty, but not `directory`, the folders of
    `skip` (and what is in them), links or placeholders. Return how
    many files."""
    if not os.path.isdir(directory):
        return 0
    skip = [_key(folder) for folder in skip]
    counted = 0
    # The folders that this run emptied of old things
    emptied = set()
    for parent, folders, names in os.walk(directory, topdown=False):
        if _inside(parent, skip):
            continue
        for name in names:
            path = os.path.join(parent, name)
            if name == PLACEHOLDER or os.path.islink(path) \
                    or not _old(path, cutoff):
                continue
            counted += 1
            if not dry_run:
                try:
                    os.remove(path)
                    emptied.add(parent)
                except OSError:
                    counted -= 1
        # Only an empty folder goes, and only one that is old or that
        # this run emptied: a command may have just made it
        if not dry_run and parent != directory \
                and (parent in emptied or _old(parent, cutoff)):
            try:
                os.rmdir(parent)
                emptied.add(os.path.dirname(parent))
            except OSError:
                pass
    return counted


def _prune_logs(now, dry_run, counts, root):
    cutoff = _cutoff(now, "logs_days")
    if cutoff is None:
        return
    counts["logs_days"] = _prune_tree(
        os.path.join(root, "var", "log"), cutoff, dry_run)


def _prune_tmp(now, dry_run, counts, root):
    cutoff = _cutoff(now, "tmp_days")
    if cutoff is None:
        return
    # Imported here: the job folders are the web UI's, and they go with
    # their jobs (jobs_days)
    from mast.web.catalog import JOBS_DIR
    counts["tmp_days"] = _prune_tree(
        os.path.join(root, "tmp"), cutoff, dry_run,
        skip=[os.path.join(root, JOBS_DIR)])


def prune(now=None, dry_run=False, root=None):
    """
    _function_: `mast.jobs.retention.prune(now=None, dry_run=False, root=None)`

    Delete what is older than `[retention]` says, and return
    `{rule: how many}`: jobs (with their children) for `jobs_days`,
    `scheduled_jobs_days` and `scheduled_failed_days`, rows for
    `credential_uses_days`, `audit_events_days` and the `status_*`
    rules (before which the daily rollups are made), upload folders for
    `uploads_days`, log files for `logs_days` and files in `tmp/` for
    `tmp_days`. A rule that keeps for
    ever is not in it. With `dry_run`, nothing is deleted. `root` is
    the MAST home (for the tests).
    """
    now = now or _now()
    root = root or MAST_HOME
    counts = {}
    _prune_jobs(now, dry_run, counts)
    _prune_rows(now, dry_run, counts)
    _prune_status(now, dry_run, counts)
    _prune_crypto(now, dry_run, counts)
    _prune_uploads(now, dry_run, counts, root)
    _prune_logs(now, dry_run, counts, root)
    _prune_tmp(now, dry_run, counts, root)
    if not dry_run:
        make_logger("mast.retention").info("Deleted what is old: {}".format(
            ", ".join("{} {}".format(n, rule) for rule, n in counts.items())))
    return counts


def daily():
    """The built-in task of the scheduler of mastd."""
    prune()

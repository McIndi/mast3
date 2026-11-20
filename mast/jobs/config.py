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
_module_: `mast.jobs.config`

The configuration of the job queue, from `jobs.conf`.

* `[database] url`: The SQLAlchemy URL of the database. A relative
SQLite path is relative to `$MAST_HOME`. The environment variable
`MAST_JOBS_DB_URL` overrides it.
* `[worker] lease_seconds`: How long a worker may be silent before
its job is declared lost.
* `[worker] concurrency`: How many jobs a worker runs at the same time.
* `[worker] poll_seconds`: How long a worker waits before it looks
again when there was no job to take.
* `[worker] mastd`: Whether `mastd` hosts a worker.
* `[secrets] key_file`: The file that holds the key for the secret
parameters of queued jobs.
"""
import os

from mast.config import MAST_HOME, get_config

__all__ = ["database_url", "lease_seconds", "concurrency", "poll_seconds",
           "cancel_poll_seconds", "mastd_worker", "key_file"]

_DEFAULTS = {
    ("database", "url"): "sqlite:///var/jobs/jobs.db",
    ("worker", "lease_seconds"): "60",
    ("worker", "concurrency"): "1",
    ("worker", "poll_seconds"): "2",
    ("worker", "cancel_poll_seconds"): "1",
    ("worker", "mastd"): "true",
    ("secrets", "key_file"): "etc/local/jobs.key",
}


def _get(section, option):
    config = get_config("jobs.conf")
    # raw: a database password can contain a percent sign
    if config.has_option(section, option):
        return config.get(section, option, raw=True).strip()
    return _DEFAULTS[(section, option)]


def _in_home(path):
    return path if os.path.isabs(path) else os.path.join(MAST_HOME, path)


def database_url():
    """
    _function_: `mast.jobs.config.database_url()`

    Return the URL of the job database.
    """
    url = os.environ.get("MAST_JOBS_DB_URL") or _get("database", "url")
    prefix = "sqlite:///"
    if url.startswith(prefix) and not url.startswith(prefix + ":memory:"):
        url = prefix + _in_home(url[len(prefix):])
    return url


def lease_seconds():
    """
    _function_: `mast.jobs.config.lease_seconds()`

    Return the number of seconds for which a claim or a heartbeat
    keeps a job.
    """
    return int(_get("worker", "lease_seconds"))


def concurrency():
    """
    _function_: `mast.jobs.config.concurrency()`

    Return the number of jobs that a worker runs at the same time.
    """
    return int(_get("worker", "concurrency"))


def poll_seconds():
    """
    _function_: `mast.jobs.config.poll_seconds()`

    Return the number of seconds that a worker waits before it looks
    again when there was no job to take.
    """
    return float(_get("worker", "poll_seconds"))


def cancel_poll_seconds():
    """
    _function_: `mast.jobs.config.cancel_poll_seconds()`

    Return the number of seconds between two looks of a running job at
    the queue, for a request to cancel it. A value of 0 or less is
    taken as the default, 1.
    """
    try:
        value = float(_get("worker", "cancel_poll_seconds"))
    except ValueError:
        value = 0
    return value if value > 0 else 1.0


def mastd_worker():
    """
    _function_: `mast.jobs.config.mastd_worker()`

    Return True if `mastd` hosts a worker.
    """
    return _get("worker", "mastd").lower() in ("on", "true", "t", "yes", "y")


def key_file():
    """
    _function_: `mast.jobs.config.key_file()`

    Return the path of the file that holds the secrets key.
    """
    return _in_home(_get("secrets", "key_file"))

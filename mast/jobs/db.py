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
_module_: `mast.jobs.db`

The connection to the job database.

SQLite and PostgreSQL are supported. The tables are made, and brought
up to date, the first time a process opens a session.
"""
import contextlib
import os
import sqlite3
import threading
import time

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from mast.jobs import config

__all__ = ["configure", "engine", "session", "upgrade", "lock_queue"]

SUPPORTED = ("sqlite", "postgresql")

# The newest revision in mast/jobs/migrations/versions. Alembic is only
# imported if the database is at another revision, because every MAST
# command opens the database and Alembic is slow to import.
HEAD = "0001"

# An arbitrary number. Every process that changes the schema or claims
# a job takes the PostgreSQL advisory lock with this key.
_ADVISORY_LOCK = 6277800551

_lock = threading.Lock()
_engine = None
_url = None


def configure(url=None):
    """
    _function_: `mast.jobs.db.configure(url=None)`

    Use the database at `url` from now on. With no `url`, use the one
    from `jobs.conf`. A program only needs this to use another
    database than the configured one.
    """
    global _engine, _url
    with _lock:
        if _engine is not None:
            _engine.dispose()
        _engine = None
        _url = url


def engine():
    """
    _function_: `mast.jobs.db.engine()`

    Return the SQLAlchemy engine. The first call connects and brings
    the tables up to date.
    """
    global _engine
    with _lock:
        if _engine is None:
            created = _create_engine(_url or config.database_url())
            upgrade(created)
            _engine = created
        return _engine


def _create_engine(url):
    url = make_url(url)
    backend = url.get_backend_name()
    if backend not in SUPPORTED:
        raise ValueError(
            "the job queue supports {}, not {}".format(
                " and ".join(SUPPORTED), backend))
    if backend == "postgresql":
        return create_engine(url, pool_pre_ping=True)

    if url.database and url.database != ":memory:":
        os.makedirs(os.path.dirname(os.path.abspath(url.database)),
                    exist_ok=True)
    created = create_engine(url, connect_args={"timeout": 30})

    @event.listens_for(created, "connect")
    def _on_connect(dbapi_connection, record):
        # The sqlite3 module must not begin transactions itself, see
        # _on_begin
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        _use_wal(cursor)
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    @event.listens_for(created, "begin")
    def _on_begin(connection):
        # Take the write lock at the start. A transaction that reads
        # and then writes would otherwise fail if another process
        # wrote in between. This also makes claim() atomic.
        connection.exec_driver_sql("BEGIN IMMEDIATE")

    return created


def _use_wal(cursor):
    """Put a SQLite database in WAL mode, in which readers and one
    writer work at the same time. The mode is kept in the file, so
    this changes something only for a new database. SQLite does not
    wait for a lock for this change, so several processes that open a
    new database together must try again."""
    deadline = time.monotonic() + 30
    while True:
        try:
            if cursor.execute("PRAGMA journal_mode").fetchone()[0] == "wal":
                return
            cursor.execute("PRAGMA journal_mode=WAL")
            return
        except sqlite3.OperationalError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.05)


def lock_queue(connection):
    """
    _function_: `mast.jobs.db.lock_queue(connection)`

    Make the current transaction the only one that claims a job or
    changes the schema, until it ends. SQLite does this for every
    transaction (see `_create_engine`).
    """
    if connection.dialect.name == "postgresql":
        connection.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": _ADVISORY_LOCK})


def _revision(connection):
    """Return the revision of the tables in the database, or `None`
    if there are no tables."""
    if not inspect(connection).has_table("alembic_version"):
        return None
    return connection.execute(
        text("SELECT version_num FROM alembic_version")).scalar()


def upgrade(target=None):
    """
    _function_: `mast.jobs.db.upgrade(target=None)`

    Bring the tables in the database of the engine `target` up to
    date. Nothing happens if they are. Several processes can call this
    at the same time.
    """
    target = target if target is not None else engine()
    with target.connect() as connection:
        if _revision(connection) == HEAD:
            return
    from alembic import command as alembic_command
    from alembic.config import Config
    cfg = Config()
    cfg.set_main_option("script_location", "mast.jobs:migrations")
    with target.begin() as connection:
        lock_queue(connection)
        # The migration reads the revision again, inside the lock
        cfg.attributes["connection"] = connection
        alembic_command.upgrade(cfg, "head")


@contextlib.contextmanager
def session():
    """
    _function_: `mast.jobs.db.session()`

    A context manager for one transaction. It commits at the end, or
    rolls back if there was an exception.
    """
    with Session(engine(), expire_on_commit=False) as _session:
        with _session.begin():
            yield _session

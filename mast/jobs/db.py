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

__all__ = [
    "configure", "engine", "session", "upgrade", "downgrade", "revision",
    "lock_queue"]

SUPPORTED = ("sqlite", "postgresql")

# The newest revision in mast/jobs/migrations/versions. Alembic is only
# imported if the database is at another revision, because every MAST
# command opens the database and Alembic is slow to import.
HEAD = "4006"

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
        # A transaction that writes takes the write lock at the start.
        # A transaction that reads and then writes would otherwise fail
        # if another process wrote in between. This also makes claim()
        # atomic. A transaction that only reads (session(write=False))
        # takes no lock, so that a program that follows a job does not
        # wait for the heartbeats, and they do not wait for it.
        if connection.get_execution_options().get("mast_read_only"):
            connection.exec_driver_sql("BEGIN")
        else:
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
    transaction that writes (see `_create_engine`). A transaction that
    only reads is not stopped by it.
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
    _migrate(target, "upgrade", "head")


def _migrate(target, direction, revision):
    from alembic import command as alembic_command
    from alembic.config import Config
    cfg = Config()
    cfg.set_main_option("script_location", "mast.jobs:migrations")
    with target.connect() as connection:
        sqlite = connection.dialect.name == "sqlite"
        if sqlite:
            # SQLite changes a table that it cannot alter in place by
            # making a new one and dropping the old one. With foreign
            # keys on, dropping it would delete the rows of the tables
            # that refer to it (ON DELETE CASCADE): the results of the
            # jobs, the members of the environments... So the keys are
            # off during a migration, as the SQLite documentation says,
            # and checked after it. This can only be changed outside a
            # transaction.
            connection.connection.execute("PRAGMA foreign_keys=OFF")
        try:
            with connection.begin():
                lock_queue(connection)
                # The migration reads the revision again, inside the lock
                cfg.attributes["connection"] = connection
                getattr(alembic_command, direction)(cfg, revision)
                if sqlite:
                    broken = connection.exec_driver_sql(
                        "PRAGMA foreign_key_check").fetchall()
                    if broken:
                        raise RuntimeError(
                            "The migration left rows whose foreign keys "
                            "point nowhere: {}".format(broken[:5]))
        finally:
            if sqlite:
                connection.connection.execute("PRAGMA foreign_keys=ON")


def revision(target=None):
    """
    _function_: `mast.jobs.db.revision(target=None)`

    Return the revision of the tables in the database of the engine
    `target` (the configured database by default), for example
    `"0002"`, or `None` if it has no tables. It does not upgrade them.
    """
    target = target if target is not None else _create_engine(
        _url or config.database_url())
    with target.connect() as connection:
        return _revision(connection)


def downgrade(revision, target=None):
    """
    _function_: `mast.jobs.db.downgrade(revision, target=None)`

    Take the tables in the database back to `revision`, for example
    `"0001"` for the tables of MAST 3.15.2. The tables and columns of
    the newer revisions are dropped, with what they hold. The jobs
    stay.

    This is the way back to an older MAST. Stop every MAST process of
    the home first, and start only the older MAST after it: a newer
    MAST upgrades the tables again when it starts.

    Parameters:

    * `revision`: The revision to go back to.
    * `target`: An engine. The default is the configured database,
    which is not upgraded first.
    """
    global _engine
    if target is None:
        with _lock:
            if _engine is not None:
                _engine.dispose()
                _engine = None
        target = _create_engine(_url or config.database_url())
    _migrate(target, "downgrade", revision)


@contextlib.contextmanager
def session(write=True):
    """
    _function_: `mast.jobs.db.session(write=True)`

    A context manager for one transaction. It commits at the end, or
    rolls back if there was an exception.

    Give `write=False` for a transaction that only reads. On SQLite
    such a transaction does not wait for the transactions that write,
    and they do not wait for it. It must not write.
    """
    bind = engine()
    if not write:
        bind = bind.execution_options(mast_read_only=True)
    with Session(bind, expire_on_commit=False) as _session:
        with _session.begin():
            yield _session

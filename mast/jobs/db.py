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
import threading

from alembic import command as alembic_command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from mast.jobs import config

__all__ = ["configure", "engine", "session", "upgrade", "lock_queue"]

SUPPORTED = ("sqlite", "postgresql")

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
        # WAL lets readers and one writer work at the same time
        cursor.execute("PRAGMA journal_mode=WAL")
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


def _alembic_config():
    cfg = Config()
    cfg.set_main_option("script_location", "mast.jobs:migrations")
    return cfg


def upgrade(target=None):
    """
    _function_: `mast.jobs.db.upgrade(target=None)`

    Bring the tables in the database of the engine `target` up to
    date. Nothing happens if they are. Several processes can call this
    at the same time.
    """
    target = target if target is not None else engine()
    cfg = _alembic_config()
    head = ScriptDirectory.from_config(cfg).get_current_head()
    with target.connect() as connection:
        current = MigrationContext.configure(connection).get_current_revision()
    if current == head:
        return
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

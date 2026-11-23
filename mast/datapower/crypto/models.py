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
_module_: `mast.datapower.crypto.models`

The tables of the crypto inventory, in the job database (migration
0009). `mast-crypto inventory` writes one snapshot of each appliance at
each run; nothing is changed afterwards.

* `crypto_snapshots`: one run on one appliance.
* `crypto_objects`: the crypto and TLS objects of each domain, and the
objects that refer to them (listeners, services...). `attrs` has the
configuration of a crypto or TLS object without its secrets.
* `crypto_certs`: the certificate of each `CryptoCertificate`, parsed.
* `crypto_files`: the files of `cert:`, `pubcert:` and `sharedcert:`.
* `crypto_edges`: which object refers to which, and through which
element.
* `crypto_cert_cache`: the parsed certificates by SHA-256, so that one
that has not changed is not parsed (or exported) again.

No password, key or file content is stored. A certificate is public.
"""
from datetime import datetime

from sqlalchemy import (
    JSON, Boolean, ForeignKey, Index, Integer, String, Text)
from sqlalchemy.orm import Mapped, mapped_column

from mast.jobs.models import Base, UTCDateTime

__all__ = ["CryptoSnapshot", "CryptoObject", "CryptoCert", "CryptoFile",
           "CryptoEdge", "CryptoCertCache", "DETAILS", "EXPORT"]

# How the certificates were read: do-view-certificate-details, or
# CryptoExport to a temporary file (older firmware)
DETAILS = "details"
EXPORT = "export"


def _snapshot():
    return ForeignKey("crypto_snapshots.id", ondelete="CASCADE")


class CryptoSnapshot(Base):
    __tablename__ = "crypto_snapshots"
    __table_args__ = (
        Index("ix_crypto_snapshots_appliance_at", "appliance_id",
              "collected_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    appliance_id: Mapped[int] = mapped_column(
        ForeignKey("appliances.id", ondelete="CASCADE"))
    job_id: Mapped[str | None] = mapped_column(String(32))
    collected_at: Mapped[datetime] = mapped_column(UTCDateTime)
    firmware: Mapped[str | None] = mapped_column(String(64))
    spec_file: Mapped[str | None] = mapped_column(String(255))
    method: Mapped[str | None] = mapped_column(String(16))
    ok: Mapped[bool] = mapped_column(Boolean)
    error: Mapped[str | None] = mapped_column(Text)
    # The domains that could not be read, with the reason
    problems: Mapped[list | None] = mapped_column(JSON)


class CryptoObject(Base):
    __tablename__ = "crypto_objects"
    __table_args__ = (
        Index("ix_crypto_objects_snapshot_class", "snapshot_id", "class"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(_snapshot())
    domain: Mapped[str] = mapped_column(String(128))
    class_: Mapped[str] = mapped_column("class", String(128))
    name: Mapped[str] = mapped_column(String(255))
    admin_state: Mapped[str | None] = mapped_column(String(16))
    # In the running configuration, and saved as it runs
    running: Mapped[bool] = mapped_column(Boolean)
    saved: Mapped[bool] = mapped_column(Boolean)
    # A crypto or TLS object: its configuration without secrets. None
    # for an object that only refers to one (a listener, a service).
    attrs: Mapped[dict | None] = mapped_column(JSON)
    has_plaintext_password: Mapped[bool] = mapped_column(Boolean)


class CryptoCert(Base):
    __tablename__ = "crypto_certs"
    __table_args__ = (
        Index("ix_crypto_certs_snapshot", "snapshot_id"),
        Index("ix_crypto_certs_sha256", "sha256"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(_snapshot())
    object_id: Mapped[int] = mapped_column(
        ForeignKey("crypto_objects.id", ondelete="CASCADE"))
    sha256: Mapped[str] = mapped_column(String(64))
    subject: Mapped[str | None] = mapped_column(Text)
    issuer: Mapped[str | None] = mapped_column(Text)
    serial: Mapped[str | None] = mapped_column(String(128))
    sig_alg: Mapped[str | None] = mapped_column(String(64))
    key_alg: Mapped[str | None] = mapped_column(String(64))
    key_bits: Mapped[int | None] = mapped_column(Integer)
    curve: Mapped[str | None] = mapped_column(String(64))
    not_before: Mapped[datetime | None] = mapped_column(UTCDateTime)
    not_after: Mapped[datetime | None] = mapped_column(UTCDateTime)
    sans: Mapped[list | None] = mapped_column(JSON)
    self_signed: Mapped[bool | None] = mapped_column(Boolean)


class CryptoFile(Base):
    __tablename__ = "crypto_files"
    __table_args__ = (Index("ix_crypto_files_snapshot", "snapshot_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(_snapshot())
    domain: Mapped[str] = mapped_column(String(128))
    location: Mapped[str] = mapped_column(String(16))
    # As a configuration names it: cert:///name
    name: Mapped[str] = mapped_column(String(512))
    size: Mapped[int | None] = mapped_column(Integer)
    modified: Mapped[str | None] = mapped_column(String(32))


class CryptoEdge(Base):
    __tablename__ = "crypto_edges"

    snapshot_id: Mapped[int] = mapped_column(_snapshot(), primary_key=True)
    from_object_id: Mapped[int] = mapped_column(
        ForeignKey("crypto_objects.id", ondelete="CASCADE"), primary_key=True)
    to_object_id: Mapped[int] = mapped_column(
        ForeignKey("crypto_objects.id", ondelete="CASCADE"), primary_key=True)
    # The element of the referring object, Idcred for example
    via: Mapped[str] = mapped_column(String(128), primary_key=True)


class CryptoCertCache(Base):
    __tablename__ = "crypto_cert_cache"

    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    parsed: Mapped[dict] = mapped_column(JSON)

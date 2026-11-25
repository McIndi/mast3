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
_module_: `mast.datapower.crypto.page`

The Crypto page of the web UI: the crypto inventory and its posture.
It has five views, which its script (`static/crypto.js`) fills from
these routes:

* `GET /api/v1/crypto/overview`: the posture of each appliance (score,
findings by severity, nearest expiry, last snapshot), the
certificates that expire in the next 12 months, the PQC readiness of
each appliance (firmware, profiles, probe), the schedules of the
inventory, and what the user may do.
* `GET /api/v1/crypto/inventory`: the certificates, keys, credentials,
profiles, aliases and files of the newest snapshot of each appliance.
* `GET /api/v1/crypto/objects/<id>`: one object with its configuration
(without secrets), its certificate, what it uses, what uses it, its
findings, and for a certificate the tree of what breaks if it expires.
* `GET /api/v1/crypto/findings`: the findings of the newest snapshots
and the MAST self-checks, with the exceptions that cover them, and the
rules.
* `GET|POST /api/v1/crypto/exceptions`, `DELETE
/api/v1/crypto/exceptions/<id>`: the exceptions; adding or removing
one needs `manage-exception` (an admin).
* `GET /api/v1/crypto/export?format=cbom|xlsx|csv`: the newest
snapshot that worked of each appliance as a download, as
`mast-crypto export` writes it.
* `GET /api/v1/crypto/snapshots?appliance=NAME`: the snapshots of an
appliance, and `GET /api/v1/crypto/diff?from=ID&to=ID` what changed
between two of them.
* `POST /api/v1/crypto/run`: `{credential}`, an operator runs the
inventory now with a stored credential for collecting.
* `POST /api/v1/crypto/schedule`: `{credential}`, an admin makes (or
changes) the schedule `crypto-inventory`, which runs it each day.
* `GET /api/v1/crypto/probes`: the newest TLS probe of each port.
`POST /api/v1/crypto/probe`: an operator probes every appliance now
(no credential needed). `POST /api/v1/crypto/probe-schedule`: an admin
makes the weekly schedule `crypto-probe`.

A snapshot has no secret, so no answer has one. A viewer does not see
the names of the stored credentials.
"""
from datetime import datetime, timedelta, timezone

import flask
from sqlalchemy import func, select

from mast.auth import roles
from mast.jobs.auth import Forbidden
from mast.web.page import Page

from . import posture
from .inventory import CLASSES
from .models import (CryptoCert, CryptoEdge, CryptoFile, CryptoFinding,
                     CryptoObject, CryptoSnapshot)
from .rules import RULES, SEVERITIES, _load

__all__ = ["page", "COMMAND", "SCHEDULE", "PROBE_COMMAND", "PROBE_SCHEDULE"]

COMMAND = "crypto.inventory"
SCHEDULE = "crypto-inventory"
PROBE_COMMAND = "crypto.probe"
PROBE_SCHEDULE = "crypto-probe"
# Sunday at 03:00
PROBE_CRON = "0 3 * * 0"
# The certificates of the expiry timeline: those that end within this
EXPIRY_DAYS = 365
MAX_SNAPSHOTS = 200
# The depth of "what breaks if this expires"
BREAK_DEPTH = 8

KINDS = {
    "CryptoCertificate": "certificate",
    "CryptoKey": "key", "CryptoSSKey": "key",
    "CryptoIdentCred": "credential", "CryptoValCred": "credential",
    "CryptoProfile": "credential", "CryptoFWCred": "credential",
    "CryptoKerberosKeytab": "credential",
    "SSLServerProfile": "profile", "SSLClientProfile": "profile",
    "SSLSNIServerProfile": "profile", "SSLProxyProfile": "profile",
    "PasswordAlias": "alias", "SecureBackupMode": "other",
}
_PROTOCOLS = (("SSLv3", "SSL 3.0"), ("TLSv1d0", "TLS 1.0"),
              ("TLSv1d1", "TLS 1.1"), ("TLSv1d2", "TLS 1.2"),
              ("TLSv1d3", "TLS 1.3"))

blueprint = flask.Blueprint(
    "crypto_page", __name__, template_folder="templates",
    static_folder="static", static_url_path="/static/crypto")


def _user():
    return flask.g.mast_user


def _now():
    return datetime.now(timezone.utc)


def _iso(value):
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return value


def _error(status, message):
    return flask.jsonify({"error": message}), status


def _may(action):
    return roles.allowed(_user().role, action)


def _names():
    from mast import registry
    return {a.id: a for a in registry.list_appliances()}


def _latest(session):
    return posture.latest_snapshots(session)


def _protocols(attrs):
    found = (attrs or {}).get("Protocols")
    if not isinstance(found, dict):
        return []
    return [name for key, name in _PROTOCOLS if found.get(key) == "on"]


def _reference(value):
    if isinstance(value, dict):
        return value.get("name", "")
    if isinstance(value, list):
        return ", ".join(filter(None, (_reference(v) for v in value)))
    return value or ""


def _detail(obj, cert):
    """One line about an object, for the inventory table."""
    attrs = obj.attrs or {}
    if cert is not None:
        key = "{} {}".format(cert.key_alg or "", cert.key_bits or "").strip()
        return "{} · {} · {}".format(cert.subject or "", key,
                                     cert.sig_alg or "")
    if obj.class_ in ("CryptoKey", "CryptoSSKey"):
        return attrs.get("Filename", "")
    if KINDS.get(obj.class_) == "profile":
        parts = [", ".join(_protocols(attrs))]
        credential = _reference(attrs.get("Idcred") or attrs.get("Valcred"))
        if credential:
            parts.append(credential)
        return " · ".join(p for p in parts if p)
    if obj.class_ == "CryptoIdentCred":
        return " · ".join("{} {}".format(label, _reference(attrs.get(key)))
                          for key, label in (("Key", "key"),
                                             ("Certificate", "certificate"))
                          if _reference(attrs.get(key)))
    if obj.class_ == "CryptoValCred" and _reference(attrs.get("Certificate")):
        return "certificates {}".format(_reference(attrs.get("Certificate")))
    return ""


def _object_json(obj, appliance=None, via=None):
    return {"id": obj.id, "domain": obj.domain, "class": obj.class_,
            "name": obj.name, "state": obj.admin_state, "running": obj.running,
            "saved": obj.saved, "kind": KINDS.get(obj.class_, "user"),
            "appliance": appliance, "via": via}


def _cert_json(cert):
    if cert is None:
        return None
    return {"subject": cert.subject, "issuer": cert.issuer,
            "serial": cert.serial, "sig_alg": cert.sig_alg,
            "key_alg": cert.key_alg, "key_bits": cert.key_bits,
            "curve": cert.curve, "not_before": _iso(cert.not_before),
            "not_after": _iso(cert.not_after), "sans": cert.sans or [],
            "self_signed": cert.self_signed, "sha256": cert.sha256}


@blueprint.get("/crypto")
def crypto_page():
    """
    _function_: `mast.datapower.crypto.page.crypto_page()`

    The Crypto page.

    Exposed at: `/crypto` via GET
    """
    from mast.jobs import db
    with db.session(write=False) as session:
        exportable = session.scalar(
            select(CryptoSnapshot.id).where(CryptoSnapshot.ok.is_(True))
            .limit(1)) is not None
    return flask.render_template("crypto.html", exportable=exportable)


# The overview
# ============

def _schedules(command=COMMAND):
    from mast.jobs import db
    from mast.jobs.models import Schedule
    with db.session(write=False) as session:
        rows = session.scalars(select(Schedule).where(
            Schedule.command == command).order_by(Schedule.name))
        return [{"name": row.name, "enabled": row.enabled, "cron": row.cron,
                 "credential": (row.params or {}).get("credential")
                 if _may("submit") else None} for row in rows]


_SUPPORT = {
    "control": "can choose its PQC groups (10.6.4 or newer)",
    "preview": "has the ML-KEM preview only (10.6.2, 10.6.3)",
    "default-only": "negotiates X25519MLKEM768 by default, no control",
    "none": "classical key exchange only",
    "unknown": "firmware not known",
}


def _readiness(appliances, latest):
    """The PQC readiness of each appliance, at three levels: the
    firmware (can it?), the profiles of its newest snapshot (are they
    configured?) and the probe (do the peers see it?)."""
    from mast.jobs import db

    from . import pqc
    from .probe import latest_results
    from .rules.pqc import PROFILES
    ids = list(latest.values()) or [0]
    profiles = {}
    with db.session(write=False) as session:
        snapshots = {i: (a, f) for i, a, f in session.execute(
            select(CryptoSnapshot.id, CryptoSnapshot.appliance_id,
                   CryptoSnapshot.firmware)
            .where(CryptoSnapshot.id.in_(ids)))}
        for obj in session.scalars(
                select(CryptoObject)
                .where(CryptoObject.snapshot_id.in_(ids))
                .where(CryptoObject.class_.in_(PROFILES))):
            if not obj.running or obj.admin_state == "disabled":
                continue
            appliance_id, firmware = snapshots[obj.snapshot_id]
            groups = pqc.effective_groups(firmware, pqc.kem_algs_of(obj.attrs),
                                          (obj.attrs or {}).get("Protocols"))
            mine = profiles.setdefault(appliance_id, {"total": 0, "hybrid": 0,
                                                      "down": 0})
            mine["total"] += 1
            if groups.down or obj.op_state == "down":
                mine["down"] += 1
            elif groups.hybrid:
                mine["hybrid"] += 1
    firmware_of = dict(snapshots.values())
    peers = {}
    for item in latest_results(list(appliances)):
        result = item["result"]
        if not result.get("ok"):
            continue
        offers = result.get("offers") or {}
        mine = peers.setdefault(item["appliance_id"], {
            "probed": 0, "hybrid": 0, "unknown": 0, "at": None})
        mine["probed"] += 1
        seen = [offers.get(g) for g in pqc.HYBRID]
        if any(v is True for v in seen):
            mine["hybrid"] += 1
        elif all(v is None for v in seen):
            mine["unknown"] += 1
        at = _iso(item["probed_at"])
        mine["at"] = max(mine["at"] or at, at)
    found = []
    for appliance in appliances.values():
        if appliance.id not in latest and appliance.id not in peers:
            continue
        support = pqc.firmware_support(firmware_of.get(appliance.id))
        found.append({
            "appliance": appliance.name,
            "firmware": firmware_of.get(appliance.id),
            "support": support, "support_text": _SUPPORT[support],
            "profiles": profiles.get(appliance.id,
                                     {"total": 0, "hybrid": 0, "down": 0}),
            "peers": peers.get(appliance.id)})
    return found


@blueprint.get("/api/v1/crypto/overview")
def overview():
    """
    _function_: `mast.datapower.crypto.page.overview()`

    The posture of each appliance, the certificates that expire in the
    next 12 months, the schedules of the inventory, and what the user
    may do.

    Exposed at: `/api/v1/crypto/overview` via GET
    """
    from mast.jobs import db
    now = _now()
    appliances = _names()
    summary = posture.summary(now)
    checks = posture.self_check_findings(now)
    with db.session(write=False) as session:
        newest = posture.latest_snapshots(session, ok=False)
        last = {row.id: row for row in session.scalars(
            select(CryptoSnapshot).where(
                CryptoSnapshot.id.in_(list(newest.values()) or [0])))}
        latest = _latest(session)
        expiring = []
        for cert, obj, appliance_id in session.execute(
                select(CryptoCert, CryptoObject, CryptoSnapshot.appliance_id)
                .join(CryptoObject, CryptoObject.id == CryptoCert.object_id)
                .join(CryptoSnapshot, CryptoSnapshot.id == CryptoCert.snapshot_id)
                .where(CryptoCert.snapshot_id.in_(list(latest.values()) or [0]))
                .where(CryptoCert.not_after.is_not(None))
                .where(CryptoCert.not_after <= now + timedelta(days=EXPIRY_DAYS))
                .order_by(CryptoCert.not_after)):
            appliance = appliances.get(appliance_id)
            expiring.append({
                "appliance": appliance.name if appliance else "",
                "domain": obj.domain, "name": obj.name, "object_id": obj.id,
                "state": obj.admin_state, "subject": cert.subject,
                "not_after": _iso(cert.not_after)})
    rows = []
    for appliance in appliances.values():
        snapshot = last.get(newest.get(appliance.id))
        rows.append({
            "name": appliance.name, "environments": list(appliance.environments),
            "last": None if snapshot is None else {
                "id": snapshot.id, "collected_at": _iso(snapshot.collected_at),
                "ok": snapshot.ok, "error": snapshot.error,
                "firmware": snapshot.firmware,
                "problems": len(snapshot.problems or [])},
            "posture": summary.get(appliance.id)})
    return flask.jsonify({
        "now": now.isoformat(), "appliances": rows, "expiring": expiring,
        "self_checks": posture.counts(checks),
        "schedules": _schedules(), "pqc": _readiness(appliances, latest),
        "exportable": bool(latest),
        "may": {"run": _may("submit"), "schedule": _may("manage-schedule"),
                "exceptions": _may(posture.MANAGE)}})


_DOWNLOAD_TYPES = {
    "cbom": "application/vnd.cyclonedx+json",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv",
}


@blueprint.get("/api/v1/crypto/export")
def download():
    """
    _function_: `mast.datapower.crypto.page.download()`

    The newest snapshot that worked of each appliance, as a file to
    download: `format` is `cbom` (CycloneDX 1.6 JSON, the default),
    `xlsx` or `csv` (the certificates). See
    `mast.datapower.crypto.export`. A snapshot has no secret, so the
    file has none.

    Exposed at: `/api/v1/crypto/export?format=cbom|xlsx|csv` via GET
    """
    from . import export
    format = flask.request.args.get("format", "cbom")
    if format not in export.FORMATS:
        return _error(400, "format is one of {}.".format(
            ", ".join(export.FORMATS)))
    snapshots = export.load()
    if not snapshots:
        return _error(404, "There is no snapshot yet: run the inventory "
                           "first.")
    name = "crypto-inventory-{}{}".format(
        _now().strftime("%Y%m%d%H%M%S"), export.FORMATS[format])
    response = flask.Response(export.render(snapshots, format),
                              mimetype=_DOWNLOAD_TYPES[format])
    response.headers["Content-Disposition"] = \
        'attachment; filename="{}"'.format(name)
    return response


# The inventory
# =============

@blueprint.get("/api/v1/crypto/inventory")
def inventory_rows():
    """
    _function_: `mast.datapower.crypto.page.inventory_rows()`

    The crypto and TLS objects and the files of the newest snapshot of
    each appliance, one row each, with the number of findings that no
    exception covers.

    Exposed at: `/api/v1/crypto/inventory` via GET
    """
    from mast.jobs import db
    appliances = _names()
    findings = {}
    with db.session(write=False) as session:
        latest = _latest(session)
        ids = list(latest.values()) or [0]
        snapshot_of = {v: appliances[k].name for k, v in latest.items()
                       if k in appliances}
        for object_id, count in session.execute(
                select(CryptoFinding.object_id, func.count())
                .where(CryptoFinding.snapshot_id.in_(ids))
                .where(CryptoFinding.exception_id.is_(None))
                .group_by(CryptoFinding.object_id)):
            findings[object_id] = count
        certs = {c.object_id: c for c in session.scalars(
            select(CryptoCert).where(CryptoCert.snapshot_id.in_(ids)))}
        used = {object_id: count for object_id, count in session.execute(
            select(CryptoEdge.to_object_id, func.count())
            .where(CryptoEdge.snapshot_id.in_(ids))
            .group_by(CryptoEdge.to_object_id))}
        rows = []
        for obj in session.scalars(
                select(CryptoObject).where(CryptoObject.snapshot_id.in_(ids))
                .where(CryptoObject.class_.in_(CLASSES))
                .order_by(CryptoObject.domain, CryptoObject.class_,
                          CryptoObject.name)):
            cert = certs.get(obj.id)
            rows.append(dict(
                _object_json(obj, snapshot_of.get(obj.snapshot_id)),
                detail=_detail(obj, cert),
                not_after=_iso(cert.not_after) if cert else None,
                findings=findings.get(obj.id, 0), used_by=used.get(obj.id, 0)))
        for item in session.scalars(
                select(CryptoFile).where(CryptoFile.snapshot_id.in_(ids))
                .order_by(CryptoFile.domain, CryptoFile.name)):
            rows.append({
                "id": None, "kind": "file", "class": item.location,
                "appliance": snapshot_of.get(item.snapshot_id),
                "domain": item.domain, "name": item.name, "state": None,
                "detail": "{} bytes".format(item.size) if item.size else "",
                "not_after": None, "findings": 0,
                "used_by": None if item.users is None else len(item.users),
                "users": item.users})
    return flask.jsonify({"rows": rows})


def _breaks(objects, used_by, start, depth=BREAK_DEPTH):
    """The objects that refer to `start`, and to those, and so on: a
    tree of what stops working if `start` does."""
    def node(object_id, via, seen):
        obj = objects.get(object_id)
        item = _object_json(obj, via=via) if obj else {"id": object_id,
                                                       "via": via}
        if len(seen) >= depth or object_id in seen:
            item["children"] = []
            return item
        item["children"] = [node(source, how, seen | {object_id})
                            for source, how in sorted(
                                used_by.get(object_id, ()),
                                key=lambda e: (objects[e[0]].class_,
                                               objects[e[0]].name)
                                if e[0] in objects else ("", ""))]
        return item
    return node(start, None, frozenset())["children"]


def _groups_json(obj, firmware):
    """What a server or client profile can negotiate (None for another
    object)."""
    from . import pqc
    from .rules.pqc import PROFILES
    if obj.class_ not in PROFILES:
        return None
    groups = pqc.effective_groups(firmware, pqc.kem_algs_of(obj.attrs),
                                  (obj.attrs or {}).get("Protocols"))
    return {"tls13": sorted(groups.tls13), "tls12": groups.tls12,
            "down": groups.down, "hybrid": list(groups.hybrid),
            "reason": groups.reason}


@blueprint.get("/api/v1/crypto/objects/<int:object_id>")
def one_object(object_id):
    """
    _function_: `mast.datapower.crypto.page.one_object(object_id)`

    One object of a snapshot: its configuration without secrets, its
    certificate, what it uses and what uses it, its findings, and for a
    certificate what breaks if it expires.

    Exposed at: `/api/v1/crypto/objects/<id>` via GET
    """
    from mast.jobs import db
    appliances = _names()
    with db.session(write=False) as session:
        obj = session.get(CryptoObject, object_id)
        if obj is None:
            return _error(404, "There is no object {}.".format(object_id))
        snapshot = session.get(CryptoSnapshot, obj.snapshot_id)
        objects = {o.id: o for o in session.scalars(select(CryptoObject).where(
            CryptoObject.snapshot_id == obj.snapshot_id))}
        uses, used_by = {}, {}
        for edge in session.scalars(select(CryptoEdge).where(
                CryptoEdge.snapshot_id == obj.snapshot_id)):
            uses.setdefault(edge.from_object_id, []).append(
                (edge.to_object_id, edge.via))
            used_by.setdefault(edge.to_object_id, []).append(
                (edge.from_object_id, edge.via))
        cert = session.scalars(select(CryptoCert).where(
            CryptoCert.object_id == obj.id)).first()
        appliance = appliances.get(snapshot.appliance_id)
        answer = dict(
            _object_json(obj, appliance.name if appliance else None),
            attrs=obj.attrs, has_plaintext_password=obj.has_plaintext_password,
            op_state=obj.op_state, status_error=obj.status_error,
            listen=obj.listen, groups=_groups_json(obj, snapshot.firmware),
            snapshot={"id": snapshot.id,
                      "collected_at": _iso(snapshot.collected_at)},
            cert=_cert_json(cert),
            uses=[_object_json(objects[i], via=v) for i, v in sorted(
                uses.get(obj.id, ())) if i in objects],
            used_by=[_object_json(objects[i], via=v) for i, v in sorted(
                used_by.get(obj.id, ())) if i in objects],
            breaks=_breaks(objects, used_by, obj.id)
            if obj.class_ == "CryptoCertificate" else None)
    answer["findings"] = [f for f in posture.findings_of([obj.snapshot_id])
                          if f["object_id"] == object_id]
    return flask.jsonify(answer)


# The findings and the exceptions
# ===============================

def _rules_json():
    _load()
    from .rules import settings
    current = settings()
    return [{"id": r.id, "title": r.title, "pack": r.pack,
             "severity": r.severity, "doc": r.doc.replace("`", ""),
             "on": current.enabled(r)} for r in RULES.values()]


@blueprint.get("/api/v1/crypto/findings")
def findings():
    """
    _function_: `mast.datapower.crypto.page.findings()`

    The findings of the newest snapshot of each appliance and of the
    MAST self-checks, each with the exception that covers it (or null),
    the rules, and the severities from the worst.

    Exposed at: `/api/v1/crypto/findings` via GET
    """
    from mast.jobs import db
    now = _now()
    with db.session(write=False) as session:
        latest = _latest(session)
    found = posture.findings_of(latest.values(), now)
    found += posture.self_check_findings(now)
    return flask.jsonify({
        "findings": found, "rules": _rules_json(),
        "severities": list(SEVERITIES),
        "appliances": sorted(a.name for a in _names().values()),
        "may_manage": _may(posture.MANAGE), "now": now.isoformat()})


@blueprint.get("/api/v1/crypto/exceptions")
def exceptions():
    """
    _function_: `mast.datapower.crypto.page.exceptions()`

    Every exception, the newest first, with those that have expired.

    Exposed at: `/api/v1/crypto/exceptions` via GET
    """
    return flask.jsonify({"exceptions": posture.list_exceptions(),
                          "may_manage": _may(posture.MANAGE)})


@blueprint.post("/api/v1/crypto/exceptions")
def add_exception():
    """
    _function_: `mast.datapower.crypto.page.add_exception()`

    Add an exception: `{rule_id, object_pattern, reason, expires,
    appliance, domain}`. Admins only.

    Exposed at: `/api/v1/crypto/exceptions` via POST
    """
    from mast.web.auth import principal
    body = flask.request.get_json(silent=True) or {}
    try:
        found = posture.add_exception(
            body.get("rule_id"), body.get("object_pattern"),
            body.get("reason"), body.get("expires"), principal(),
            appliance=body.get("appliance"), domain=body.get("domain"))
    except Forbidden as exc:
        return _error(403, str(exc))
    except posture.ExceptionError as exc:
        return _error(422, str(exc))
    return flask.jsonify(found), 201


@blueprint.delete("/api/v1/crypto/exceptions/<int:exception_id>")
def remove_exception(exception_id):
    """
    _function_: `mast.datapower.crypto.page.remove_exception(
    exception_id)`

    Remove an exception. Admins only.

    Exposed at: `/api/v1/crypto/exceptions/<id>` via DELETE
    """
    from mast.web.auth import principal
    try:
        posture.remove_exception(exception_id, principal())
    except Forbidden as exc:
        return _error(403, str(exc))
    except posture.ExceptionError as exc:
        return _error(404, str(exc))
    return "", 204


# The history
# ===========

@blueprint.get("/api/v1/crypto/snapshots")
def snapshots():
    """
    _function_: `mast.datapower.crypto.page.snapshots()`

    The snapshots of the appliance `appliance`, the newest first, with
    the number of objects, certificates and findings of each.

    Exposed at: `/api/v1/crypto/snapshots?appliance=NAME` via GET
    """
    from mast import registry
    from mast.jobs import db
    name = flask.request.args.get("appliance", "")
    appliance = registry.by_name(name) if name else None
    if appliance is None:
        return _error(404, "There is no appliance {}.".format(name))
    with db.session(write=False) as session:
        rows = list(session.scalars(
            select(CryptoSnapshot)
            .where(CryptoSnapshot.appliance_id == appliance.id)
            .order_by(CryptoSnapshot.id.desc()).limit(MAX_SNAPSHOTS)))
        ids = [r.id for r in rows] or [0]

        def count(model, extra=None):
            query = select(model.snapshot_id, func.count()) \
                .where(model.snapshot_id.in_(ids))
            if extra is not None:
                query = query.where(extra)
            return dict(tuple(r) for r in session.execute(
                query.group_by(model.snapshot_id)))
        objects = count(CryptoObject, CryptoObject.class_.in_(CLASSES))
        certs = count(CryptoCert)
        severities = {}
        for snapshot_id, severity, number in session.execute(
                select(CryptoFinding.snapshot_id, CryptoFinding.severity,
                       func.count())
                .where(CryptoFinding.snapshot_id.in_(ids))
                .group_by(CryptoFinding.snapshot_id, CryptoFinding.severity)):
            severities.setdefault(snapshot_id, {})[severity] = number
        found = [{"id": r.id, "collected_at": _iso(r.collected_at),
                  "ok": r.ok, "error": r.error, "firmware": r.firmware,
                  "job_id": r.job_id, "objects": objects.get(r.id, 0),
                  "certificates": certs.get(r.id, 0),
                  "findings": severities.get(r.id, {})} for r in rows]
    return flask.jsonify({"appliance": appliance.name, "snapshots": found})


def _state(session, snapshot_id):
    objects = {}
    certs = {c.object_id: c.sha256 for c in session.scalars(
        select(CryptoCert).where(CryptoCert.snapshot_id == snapshot_id))}
    for obj in session.scalars(select(CryptoObject).where(
            CryptoObject.snapshot_id == snapshot_id)
            .where(CryptoObject.class_.in_(CLASSES))):
        objects[(obj.domain, obj.class_, obj.name)] = (
            obj.admin_state, obj.attrs, obj.has_plaintext_password,
            certs.get(obj.id))
    found = {(f.rule_id, f.subject): {"rule_id": f.rule_id,
                                      "severity": f.severity,
                                      "subject": f.subject,
                                      "message": f.message}
             for f in session.scalars(select(CryptoFinding).where(
                 CryptoFinding.snapshot_id == snapshot_id))}
    return objects, found


@blueprint.get("/api/v1/crypto/diff")
def diff():
    """
    _function_: `mast.datapower.crypto.page.diff()`

    What changed from the snapshot `from` to the snapshot `to` of one
    appliance: the objects added, removed and changed (configuration,
    state or certificate), and the findings opened and closed.

    Exposed at: `/api/v1/crypto/diff?from=ID&to=ID` via GET
    """
    from mast.jobs import db
    try:
        first = int(flask.request.args.get("from", ""))
        second = int(flask.request.args.get("to", ""))
    except ValueError:
        return _error(422, "from and to are the ids of two snapshots.")
    with db.session(write=False) as session:
        rows = [session.get(CryptoSnapshot, i) for i in (first, second)]
        if None in rows:
            return _error(404, "There is no such snapshot.")
        if rows[0].appliance_id != rows[1].appliance_id:
            return _error(422, "Compare two snapshots of the same appliance.")
        before, before_findings = _state(session, first)
        after, after_findings = _state(session, second)

    def key_json(key):
        return {"domain": key[0], "class": key[1], "name": key[2]}
    changed = []
    for key in sorted(set(before) & set(after)):
        old, new = before[key], after[key]
        if old == new:
            continue
        what = []
        if old[0] != new[0]:
            what.append("state {} → {}".format(old[0], new[0]))
        if old[3] != new[3]:
            what.append("certificate")
        if old[1] != new[1]:
            what.append("configuration")
        if old[2] != new[2]:
            what.append("plaintext password {}".format(
                "added" if new[2] else "removed"))
        changed.append(dict(key_json(key), changes=what))
    return flask.jsonify({
        "from": {"id": first, "collected_at": _iso(rows[0].collected_at)},
        "to": {"id": second, "collected_at": _iso(rows[1].collected_at)},
        "added": [key_json(k) for k in sorted(set(after) - set(before))],
        "removed": [key_json(k) for k in sorted(set(before) - set(after))],
        "changed": changed,
        "opened": [after_findings[k] for k in sorted(
            set(after_findings) - set(before_findings))],
        "closed": [before_findings[k] for k in sorted(
            set(before_findings) - set(after_findings))]})


# Running it
# ==========

def _credential(body):
    return str(body.get("credential") or "").strip()


@blueprint.post("/api/v1/crypto/run")
def run_now():
    """
    _function_: `mast.datapower.crypto.page.run_now()`

    Run the inventory of every registered appliance now, with the
    stored credential `credential`. Operators and admins.

    Exposed at: `/api/v1/crypto/run` via POST
    """
    from pydantic import ValidationError

    from mast import jobs
    from mast.web.auth import principal
    credential = _credential(flask.request.get_json(silent=True) or {})
    if not credential:
        return _error(422, "Choose a stored credential for collecting.")
    try:
        job_id = jobs.submit(COMMAND, {"credential": credential},
                             principal=principal())
    except Forbidden as exc:
        return _error(403, str(exc))
    except (ValidationError, ValueError) as exc:
        return _error(422, _first_error(exc))
    return flask.jsonify({"job_id": job_id}), 202


def _first_error(exc):
    errors = getattr(exc, "errors", None)
    if callable(errors):
        try:
            return str(errors()[0].get("msg", exc))
        except Exception:
            pass
    return str(exc).splitlines()[0]


@blueprint.post("/api/v1/crypto/schedule")
def schedule():
    """
    _function_: `mast.datapower.crypto.page.schedule()`

    Make (or change, and enable) the schedule `crypto-inventory`, which
    runs the inventory of every registered appliance each day at
    `hour` (2 by default) with the stored credential `credential`.
    Admins only.

    Exposed at: `/api/v1/crypto/schedule` via POST
    """
    from mast.jobs import schedules
    from mast.web.auth import principal
    body = flask.request.get_json(silent=True) or {}
    credential = _credential(body)
    try:
        hour = int(body.get("hour", 2))
    except (TypeError, ValueError):
        hour = -1
    if not credential:
        return _error(422, "Choose a stored credential for collecting.")
    if not 0 <= hour <= 23:
        return _error(422, "The hour is from 0 to 23.")
    cron_text = "0 {} * * *".format(hour)
    params = {"credential": credential}
    who = principal()
    try:
        try:
            schedules.get(SCHEDULE, principal=who)
        except schedules.UnknownSchedule:
            info = schedules.add(
                SCHEDULE, COMMAND, params, cron_text, principal=who,
                description="The crypto inventory of every appliance, for "
                            "the Crypto page")
        else:
            info = schedules.update(SCHEDULE, principal=who,
                                    cron_text=cron_text, params=params,
                                    enabled=True)
    except schedules.ScheduleError as exc:
        return _error(422, str(exc))
    except Forbidden as exc:
        return _error(403, str(exc))
    return flask.jsonify({"schedule": info.name, "cron": info.cron,
                          "enabled": info.enabled})


# The TLS probe
# =============

@blueprint.get("/api/v1/crypto/probes")
def probes():
    """
    _function_: `mast.datapower.crypto.page.probes()`

    The newest probe of each port (`mast-crypto probe`), the schedules
    of the probe, and what the user may do.

    Exposed at: `/api/v1/crypto/probes` via GET
    """
    from .probe import latest_results
    appliances = _names()
    rows = []
    for item in latest_results():
        appliance = appliances.get(item["appliance_id"])
        result = item["result"]
        rows.append({
            "appliance": appliance.name if appliance else "",
            "host": item["host"], "port": item["port"],
            "probed_at": _iso(item["probed_at"]), "job_id": item["job_id"],
            "target": result.get("target") or {}, "ok": result.get("ok"),
            "error": result.get("error"), "protocol": result.get("protocol"),
            "cipher": result.get("cipher"), "group": result.get("group"),
            "offers": result.get("offers") or {},
            "notes": result.get("notes") or {},
            "chain": result.get("chain") or [],
            "python": result.get("python")})
    rows.sort(key=lambda r: (r["appliance"], r["host"], r["port"]))
    return flask.jsonify({
        "results": rows, "schedules": _schedules(PROBE_COMMAND),
        "may": {"run": _may("submit"), "schedule": _may("manage-schedule")}})


@blueprint.post("/api/v1/crypto/probe")
def probe_now():
    """
    _function_: `mast.datapower.crypto.page.probe_now()`

    Probe the TLS ports of every registered appliance now. It needs no
    credential. Operators and admins.

    Exposed at: `/api/v1/crypto/probe` via POST
    """
    from pydantic import ValidationError

    from mast import jobs
    from mast.web.auth import principal
    try:
        job_id = jobs.submit(PROBE_COMMAND, {}, principal=principal())
    except Forbidden as exc:
        return _error(403, str(exc))
    except (ValidationError, ValueError) as exc:
        return _error(422, _first_error(exc))
    return flask.jsonify({"job_id": job_id}), 202


@blueprint.post("/api/v1/crypto/probe-schedule")
def probe_schedule():
    """
    _function_: `mast.datapower.crypto.page.probe_schedule()`

    Make (or enable) the schedule `crypto-probe`, which probes every
    registered appliance each Sunday at 03:00. Admins only.

    Exposed at: `/api/v1/crypto/probe-schedule` via POST
    """
    from mast.jobs import schedules
    from mast.web.auth import principal
    who = principal()
    try:
        try:
            schedules.get(PROBE_SCHEDULE, principal=who)
        except schedules.UnknownSchedule:
            info = schedules.add(
                PROBE_SCHEDULE, PROBE_COMMAND, {}, PROBE_CRON, principal=who,
                description="The TLS probe of every appliance, for the "
                            "Crypto page")
        else:
            info = schedules.update(PROBE_SCHEDULE, principal=who,
                                    enabled=True)
    except schedules.ScheduleError as exc:
        return _error(422, str(exc))
    except Forbidden as exc:
        return _error(403, str(exc))
    return flask.jsonify({"schedule": info.name, "cron": info.cron,
                          "enabled": info.enabled})


page = Page(name="crypto", title="Crypto", order=20, min_role="viewer",
            blueprint=blueprint)

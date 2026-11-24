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
_module_: `mast.datapower.crypto.export`

`mast-crypto export`: write snapshots of the crypto inventory
(`mast.datapower.crypto.inventory`) to a file.

* `cbom`: a CycloneDX 1.6 JSON bill of materials of the cryptography,
with a `cryptographic-asset` for each certificate, key, credential,
password alias and TLS profile, the algorithms of the certificates,
an `application` for each listener or service that uses them, and the
references as `dependencies`.
* `xlsx`: one sheet each for the certificates, the keys, the
credentials, the TLS profiles, the files and the findings of the
posture rules (`mast.datapower.crypto.posture`).
* `csv`: the certificates.

Nothing secret is in a snapshot, so nothing secret is in an export.
"""
import csv
import io
import json
import os
import uuid
from datetime import datetime, timezone

from pydantic import model_validator
from sqlalchemy import func, select

from mast.cli import Option, Params, Table, say
from mast.plugin_utils.params import OUT_FILE

from . import cli
from .models import (
    CryptoCert, CryptoEdge, CryptoFile, CryptoObject, CryptoSnapshot)

__all__ = ["load", "cbom", "render", "write_xlsx", "write_csv", "export",
           "FORMATS"]

FORMATS = {"cbom": ".cdx.json", "xlsx": ".xlsx", "csv": ".csv"}

KEYS = ("CryptoKey", "CryptoSSKey")
CREDENTIALS = ("CryptoIdentCred", "CryptoValCred", "CryptoProfile",
               "CryptoFWCred", "CryptoKerberosKeytab")
PROFILES = ("SSLServerProfile", "SSLClientProfile", "SSLSNIServerProfile",
            "SSLProxyProfile")
_PROTOCOLS = (("SSLv3", "SSL 3.0"), ("TLSv1d0", "TLS 1.0"),
              ("TLSv1d1", "TLS 1.1"), ("TLSv1d2", "TLS 1.2"),
              ("TLSv1d3", "TLS 1.3"))


def _iso(value):
    return value.isoformat() if isinstance(value, datetime) else value


def _snapshot_ids(session, snapshot=None, appliances=()):
    from mast import registry
    if snapshot is not None:
        return [snapshot]
    query = (select(CryptoSnapshot.appliance_id,
                    func.max(CryptoSnapshot.id))
             .where(CryptoSnapshot.ok.is_(True))
             .group_by(CryptoSnapshot.appliance_id))
    if appliances:
        ids = [a.id for a in (registry.by_name(n) for n in appliances) if a]
        query = query.where(CryptoSnapshot.appliance_id.in_(ids))
    return sorted(i for _, i in session.execute(query))


def load(snapshot=None, appliances=()):
    """
    _function_: `mast.datapower.crypto.export.load(snapshot=None,
    appliances=())`

    The snapshot `snapshot`, or the newest snapshot that worked of each
    appliance (of `appliances`, if given), as a list of dicts with
    `snapshot`, `appliance`, `objects`, `certs`, `files`, `edges` and
    `findings` (dicts of `posture.findings_of`).
    """
    from mast import registry
    from mast.jobs import db

    from .posture import findings_of
    names = {a.id: a.name for a in registry.list_appliances()}
    found = []
    with db.session(write=False) as session:
        for snapshot_id in _snapshot_ids(session, snapshot, appliances):
            row = session.get(CryptoSnapshot, snapshot_id)
            if row is None:
                raise ValueError("there is no snapshot {}".format(snapshot_id))
            objects = {o.id: o for o in session.scalars(
                select(CryptoObject).where(CryptoObject.snapshot_id == row.id)
                .order_by(CryptoObject.domain, CryptoObject.class_,
                          CryptoObject.name))}
            found.append({
                "snapshot": row, "appliance": names.get(row.appliance_id, ""),
                "objects": objects,
                "certs": {c.object_id: c for c in session.scalars(
                    select(CryptoCert).where(CryptoCert.snapshot_id == row.id))},
                "files": list(session.scalars(
                    select(CryptoFile).where(CryptoFile.snapshot_id == row.id)
                    .order_by(CryptoFile.domain, CryptoFile.name))),
                "edges": list(session.scalars(
                    select(CryptoEdge).where(CryptoEdge.snapshot_id == row.id))),
            })
    for snap in found:
        snap["findings"] = findings_of([snap["snapshot"].id])
    return found


def _ref(snap, obj):
    return "{}/{}/{}/{}".format(snap["appliance"], obj.domain, obj.class_,
                                obj.name)


def _properties(snap, obj, **more):
    found = [{"name": "mast:appliance", "value": snap["appliance"]},
             {"name": "mast:domain", "value": obj.domain},
             {"name": "mast:class", "value": obj.class_},
             {"name": "mast:adminState", "value": obj.admin_state or ""},
             {"name": "mast:saved", "value": str(obj.saved).lower()}]
    if obj.has_plaintext_password:
        found.append({"name": "mast:plaintextPassword", "value": "true"})
    for name, value in more.items():
        if value not in (None, "", []):
            found.append({"name": "mast:" + name, "value": str(value)})
    return found


def _protocols(attrs):
    versions = (attrs or {}).get("Protocols") or {}
    return [label for key, label in _PROTOCOLS if versions.get(key) == "on"]


def _as_list(value):
    if value in (None, ""):
        return []
    return value if isinstance(value, list) else [value]


def cbom(snapshots, now=None):
    """
    _function_: `mast.datapower.crypto.export.cbom(snapshots, now=None)`

    The CycloneDX 1.6 document (a dict) of what `load` returned.
    """
    from mast import __version__
    now = now or datetime.now(timezone.utc)
    components, dependencies, algorithms = [], {}, {}

    def algorithm(name, primitive, **extra):
        ref = "algorithm/{}".format(name)
        if ref not in algorithms:
            properties = {"primitive": primitive}
            properties.update({k: v for k, v in extra.items() if v})
            algorithms[ref] = {
                "type": "cryptographic-asset", "bom-ref": ref, "name": name,
                "cryptoProperties": {"assetType": "algorithm",
                                     "algorithmProperties": properties}}
        return ref

    for snap in snapshots:
        for obj in snap["objects"].values():
            ref = _ref(snap, obj)
            component = {"bom-ref": ref, "name": obj.name}
            if obj.class_ == "CryptoCertificate":
                cert = snap["certs"].get(obj.id)
                details = {"certificateFormat": "X.509"}
                more = {}
                if cert is not None:
                    details.update({
                        "subjectName": cert.subject or "",
                        "issuerName": cert.issuer or "",
                        "notValidBefore": _iso(cert.not_before),
                        "notValidAfter": _iso(cert.not_after)})
                    if cert.sig_alg:
                        details["signatureAlgorithmRef"] = algorithm(
                            cert.sig_alg, "signature")
                    if cert.key_alg:
                        name = cert.key_alg + (
                            "-" + cert.curve if cert.curve else
                            "-{}".format(cert.key_bits) if cert.key_bits else "")
                        details["subjectPublicKeyRef"] = algorithm(
                            name, "signature" if cert.key_alg not in ("RSA",)
                            else "pke", curve=cert.curve,
                            parameterSetIdentifier=str(cert.key_bits or ""))
                    more = {"sha256": cert.sha256, "serial": cert.serial,
                            "selfSigned": str(cert.self_signed).lower(),
                            "subjectAltNames": ", ".join(cert.sans or [])}
                component.update({
                    "type": "cryptographic-asset",
                    "cryptoProperties": {"assetType": "certificate",
                                         "certificateProperties": details}})
                component["properties"] = _properties(snap, obj, **more)
            elif obj.class_ in KEYS or obj.class_ in CREDENTIALS \
                    or obj.class_ == "PasswordAlias":
                kind = "private-key" if obj.class_ == "CryptoKey" else \
                    "secret-key" if obj.class_ == "CryptoSSKey" else \
                    "password" if obj.class_ == "PasswordAlias" else "credential"
                component.update({
                    "type": "cryptographic-asset",
                    "cryptoProperties": {
                        "assetType": "related-crypto-material",
                        "relatedCryptoMaterialProperties": {"type": kind}}})
                component["properties"] = _properties(snap, obj)
            elif obj.class_ in PROFILES:
                attrs = obj.attrs or {}
                versions = _protocols(attrs)
                protocol = {"type": "tls",
                            "cipherSuites": [{"name": c} for c in
                                             _as_list(attrs.get("Ciphers"))]}
                if versions:
                    protocol["version"] = versions[-1].split(" ")[-1]
                component.update({
                    "type": "cryptographic-asset",
                    "cryptoProperties": {"assetType": "protocol",
                                         "protocolProperties": protocol}})
                component["properties"] = _properties(
                    snap, obj, protocols=", ".join(versions),
                    kemAlgs=", ".join(_as_list(attrs.get("KEMAlgs"))),
                    ellipticCurves=", ".join(_as_list(attrs.get("EllipticCurves"))))
            else:
                # A listener or a service that uses the crypto objects
                component.update({"type": "application",
                                  "properties": _properties(snap, obj)})
            components.append(component)
        for edge in snap["edges"]:
            source = snap["objects"].get(edge.from_object_id)
            target = snap["objects"].get(edge.to_object_id)
            if source is not None and target is not None:
                dependencies.setdefault(_ref(snap, source), set()).add(
                    _ref(snap, target))
    return {
        "bomFormat": "CycloneDX", "specVersion": "1.6",
        "serialNumber": "urn:uuid:{}".format(uuid.uuid4()), "version": 1,
        "metadata": {
            "timestamp": now.replace(microsecond=0).isoformat(),
            "tools": {"components": [{"type": "application", "name": "MAST",
                                      "version": __version__}]},
            "properties": [{"name": "mast:snapshots", "value": ", ".join(
                str(s["snapshot"].id) for s in snapshots)}]},
        "components": components + sorted(algorithms.values(),
                                          key=lambda a: a["bom-ref"]),
        "dependencies": [{"ref": ref, "dependsOn": sorted(targets)}
                         for ref, targets in sorted(dependencies.items())],
    }


def _users(snap, obj):
    """The names of the objects that refer to `obj`."""
    return ", ".join(sorted(
        snap["objects"][e.from_object_id].name for e in snap["edges"]
        if e.to_object_id == obj.id and e.from_object_id in snap["objects"]))


def _reference(value):
    if isinstance(value, dict):
        return value.get("name", "")
    return ", ".join(_reference(v) for v in value) if isinstance(value, list) \
        else (value or "")


SHEETS = {
    "Certificates": ("Appliance", "Domain", "Object", "State", "Subject",
                     "Issuer", "Not after", "Key", "Key bits", "Curve",
                     "Signature", "Self-signed", "Alternative names",
                     "SHA-256", "Used by"),
    "Keys": ("Appliance", "Domain", "Class", "Object", "State", "File",
             "Plaintext password", "Used by"),
    "Credentials": ("Appliance", "Domain", "Class", "Object", "State",
                    "Key", "Certificates", "Used by"),
    "TLS profiles": ("Appliance", "Domain", "Class", "Object", "State",
                     "Protocols", "Ciphers", "KEM algorithms", "Credentials",
                     "Used by"),
    "Files": ("Appliance", "Domain", "File", "Size", "Modified"),
    "Findings": ("Appliance", "Domain", "Object", "Rule", "Severity",
                 "Finding", "Remediation", "Exception"),
}


def rows(snapshots):
    """The rows of each sheet of `SHEETS`."""
    found = {name: [] for name in SHEETS}
    for snap in snapshots:
        appliance = snap["appliance"]
        for obj in snap["objects"].values():
            attrs = obj.attrs or {}
            used = _users(snap, obj)
            if obj.class_ == "CryptoCertificate":
                cert = snap["certs"].get(obj.id)
                found["Certificates"].append([
                    appliance, obj.domain, obj.name, obj.admin_state or "",
                    getattr(cert, "subject", "") or "",
                    getattr(cert, "issuer", "") or "",
                    _iso(getattr(cert, "not_after", None)) or "",
                    getattr(cert, "key_alg", "") or "",
                    getattr(cert, "key_bits", None) or "",
                    getattr(cert, "curve", "") or "",
                    getattr(cert, "sig_alg", "") or "",
                    "" if cert is None else ("yes" if cert.self_signed else "no"),
                    ", ".join(getattr(cert, "sans", None) or []),
                    getattr(cert, "sha256", "") or "", used])
            elif obj.class_ in KEYS:
                found["Keys"].append([
                    appliance, obj.domain, obj.class_, obj.name,
                    obj.admin_state or "", attrs.get("Filename", ""),
                    "yes" if obj.has_plaintext_password else "no", used])
            elif obj.class_ in CREDENTIALS:
                found["Credentials"].append([
                    appliance, obj.domain, obj.class_, obj.name,
                    obj.admin_state or "", _reference(attrs.get("Key")),
                    _reference(attrs.get("Certificate")), used])
            elif obj.class_ in PROFILES:
                found["TLS profiles"].append([
                    appliance, obj.domain, obj.class_, obj.name,
                    obj.admin_state or "", ", ".join(_protocols(attrs)),
                    ", ".join(_as_list(attrs.get("Ciphers"))),
                    ", ".join(_as_list(attrs.get("KEMAlgs"))),
                    _reference(attrs.get("Idcred") or attrs.get("Valcred")),
                    used])
        for item in snap["files"]:
            found["Files"].append([appliance, item.domain, item.name,
                                   item.size or "", item.modified or ""])
        for item in snap.get("findings", ()):
            exception = item["exception"]
            found["Findings"].append([
                appliance, item["domain"] or "", item["subject"],
                item["rule_id"], item["severity"], item["message"],
                item["remediation"],
                "{} (until {}): {}".format(exception["id"],
                                           exception["expires_at"][:10],
                                           exception["reason"])
                if exception else ""])
    return found


def write_xlsx(snapshots, path):
    """Write the sheets of `SHEETS` to `path` (a file name or a binary
    file)."""
    import openpyxl
    book = openpyxl.Workbook()
    book.remove(book.active)
    for name, values in rows(snapshots).items():
        sheet = book.create_sheet(name)
        sheet.append(list(SHEETS[name]))
        for row in values:
            sheet.append(row)
        sheet.freeze_panes = "A2"
    book.save(path)


def write_csv(snapshots, path):
    """Write the certificates to `path`."""
    with open(path, "wb") as fout:
        fout.write(render(snapshots, "csv"))


def render(snapshots, format):
    """
    _function_: `mast.datapower.crypto.export.render(snapshots, format)`

    Return `snapshots` (from `load`) in `format` (a key of `FORMATS`)
    as bytes. The Crypto page sends these as a download.
    """
    if format == "cbom":
        return json.dumps(cbom(snapshots), indent=1).encode("utf-8")
    if format == "xlsx":
        out = io.BytesIO()
        write_xlsx(snapshots, out)
        return out.getvalue()
    if format == "csv":
        out = io.StringIO(newline="")
        writer = csv.writer(out)
        writer.writerow(SHEETS["Certificates"])
        writer.writerows(rows(snapshots)["Certificates"])
        return out.getvalue().encode("utf-8")
    raise ValueError("format is one of {}".format(", ".join(FORMATS)))


class Export(Params):
    snapshot: int | None = Option(
        None, short="s", help="The id of the snapshot to write. Without it, "
                              "the newest snapshot of each appliance.")
    appliances: list[str] = Option(
        default_factory=list, short="a",
        help="Only the newest snapshots of these appliances.")
    format: str = Option(
        "cbom", short="f",
        help="cbom (CycloneDX 1.6 JSON), xlsx or csv (the certificates)")
    out_file: str | None = Option(
        None, short="o", widget=OUT_FILE,
        help="The file to write. The default is in tmp/, with the "
             "extension of the format.")

    @model_validator(mode="after")
    def _known_format(self):
        if self.format not in FORMATS:
            raise ValueError("format is one of {}".format(", ".join(FORMATS)))
        if self.snapshot is not None and self.appliances:
            raise ValueError("give either a snapshot or appliances, not both")
        return self


@cli.command("export", category="inventory", exclusive=False)
def export(params: Export):
    """Write the crypto inventory to a file: a CycloneDX 1.6 CBOM (JSON),
a spreadsheet, or the certificates as CSV. Without --snapshot, the
newest snapshot of each appliance (or of the appliances given)."""
    snapshots = load(params.snapshot, params.appliances)
    if not snapshots:
        raise ValueError("there is no snapshot yet: run mast-crypto "
                         "inventory first")
    extension = FORMATS[params.format]
    path = params.out_file or os.path.join(
        "tmp", "crypto-inventory-{}{}".format(
            datetime.now().strftime("%Y%m%d%H%M%S"), extension))
    if not path.endswith(extension):
        path += extension
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "wb") as fout:
        fout.write(render(snapshots, params.format))
    say("Wrote {}".format(path))
    return Table(("Appliance", "Snapshot", "Collected", "File"),
                 [[s["appliance"], str(s["snapshot"].id),
                   _iso(s["snapshot"].collected_at), path] for s in snapshots])

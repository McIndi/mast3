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

* `cbom`: a CycloneDX 1.6 JSON bill of materials of the cryptography
(see `cbom`): the appliances, their certificates, keys, credentials,
password aliases, TLS profiles and certificate files, the listeners
and services that use them, what the TLS probe saw on each port, and
the findings of the posture rules as `vulnerabilities`.
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
import re
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
# The NIST security category of each parameter set of ML-KEM (FIPS 203)
_MLKEM = re.compile(r"MLKEM(512|768|1024)", re.I)
_MLKEM_LEVELS = {"512": 1, "768": 3, "1024": 5}


def _group_properties(group):
    """
    The `algorithmProperties` of a TLS key exchange group: `kem` with
    the NIST security level of its ML-KEM for a group that has one (also
    a hybrid such as X25519MLKEM768: its ML-KEM part is what makes it
    quantum-safe), so that a tool that checks the CBOM, such as
    CBOMKit, does not take it for classical. Else `key-agree`.
    """
    found = _MLKEM.search(group or "")
    if not found:
        return {"primitive": "key-agree"}
    return {"primitive": "kem",
            "nistQuantumSecurityLevel": _MLKEM_LEVELS[found.group(1)],
            "parameterSetIdentifier": found.group(1)}


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
    `snapshot`, `appliance`, `objects`, `certs`, `files`, `edges`,
    `findings` (dicts of `posture.findings_of`) and `probes` (the newest
    TLS probe of the appliance, from `probe.latest_results`).
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
    from .probe import latest_results
    probes = {}
    for result in latest_results({s["snapshot"].appliance_id for s in found}):
        probes.setdefault(result["appliance_id"], []).append(result)
    for snap in found:
        snap["findings"] = findings_of([snap["snapshot"].id])
        snap["probes"] = probes.get(snap["snapshot"].appliance_id, [])
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
    if not obj.running:
        found.append({"name": "mast:running", "value": "false"})
    if obj.has_plaintext_password:
        found.append({"name": "mast:plaintextPassword", "value": "true"})
    more = dict({"opState": obj.op_state, "statusError": obj.status_error,
                 "listen": obj.listen}, **more)
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


def _references(attrs):
    """The `(class, name, via)` of each object that `attrs` (the
    configuration of an object) names."""
    for via, value in (attrs or {}).items():
        for item in _as_list(value):
            if isinstance(item, dict) and item.get("class") and item.get("name"):
                yield item["class"], item["name"], via


def _get(item, key):
    return item.get(key) if isinstance(item, dict) else getattr(item, key, None)


def _asset_type(class_):
    """The `cryptoProperties` of an object of `class_` that is a key, a
    credential or a password alias, or None."""
    if class_ in KEYS or class_ in CREDENTIALS or class_ == "PasswordAlias":
        kind = "private-key" if class_ == "CryptoKey" else \
            "secret-key" if class_ == "CryptoSSKey" else \
            "password" if class_ == "PasswordAlias" else "credential"
        return {"assetType": "related-crypto-material",
                "relatedCryptoMaterialProperties": {"type": kind}}
    return None


def cbom(snapshots, now=None):
    """
    _function_: `mast.datapower.crypto.export.cbom(snapshots, now=None)`

    The CycloneDX 1.6 document (a dict) of what `load` returned:

    * a `device` for each appliance, with its firmware;
    * a `cryptographic-asset` for each certificate, key, credential,
    password alias and TLS profile, and for each object that one of
    them names but the configuration does not have (a built-in crypto
    profile, for example), marked `mast:inConfiguration` false;
    * the public key of each certificate (`related-crypto-material`,
    which `subjectPublicKeyRef` names), with its algorithm and size;
    * the algorithms of the certificates, of the cipher suites (see
    `mast.datapower.crypto.suites`; each suite also has its IANA code)
    and of the key exchange groups that the probe saw;
    * an `application` for each listener or service that uses them;
    * a `file` for each file of `cert:`, `pubcert:` and `sharedcert:`,
    with the objects that use it (`mast:unused` if none does);
    * a `protocol` for each port that the TLS probe reached, with what
    it negotiated, the certificates it served and what it accepted;
    * the references as `dependencies`;
    * the findings of the posture rules as `vulnerabilities`.
    """
    from mast import __version__
    from . import suites
    from .rules import RULES
    now = now or datetime.now(timezone.utc)
    components, dependencies, algorithms, certificates = [], {}, {}, {}
    public_keys, vulnerabilities = {}, []

    def depends(source, target):
        dependencies.setdefault(source, set()).add(target)

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

    def key_algorithm(cert):
        """The bom-ref of the algorithm of the key of a certificate (a
        `CryptoCert` or one that the probe parsed), or None."""
        key_alg, curve = _get(cert, "key_alg"), _get(cert, "curve")
        bits = _get(cert, "key_bits")
        if not key_alg:
            return None
        name = key_alg + ("-" + curve if curve else
                          "-{}".format(bits) if bits else "")
        return algorithm(
            name, "signature" if key_alg not in ("RSA",) else "pke",
            curve=curve, parameterSetIdentifier=str(bits or ""))

    def public_key(cert, owner):
        """The bom-ref of the public key of a certificate (a `CryptoCert`
        or one that the probe parsed), whose bom-ref is `owner`, or None.
        The certificates with the same SHA-256 share it."""
        algorithm_ref = key_algorithm(cert)
        if algorithm_ref is None:
            return None
        sha256 = _get(cert, "sha256")
        ref = "public-key/{}".format(sha256) if sha256 else \
            "{}/public-key".format(owner)
        if ref not in public_keys:
            material = {"type": "public-key", "algorithmRef": algorithm_ref}
            if _get(cert, "key_bits"):
                material["size"] = _get(cert, "key_bits")
            public_keys[ref] = {
                "type": "cryptographic-asset", "bom-ref": ref,
                "name": "public key of {}".format(_get(cert, "subject")
                                                  or owner),
                "cryptoProperties": {
                    "assetType": "related-crypto-material",
                    "relatedCryptoMaterialProperties": material}}
        return ref

    def cipher_suite(name):
        """The `cipherSuites` item of the suite `name` (an IANA name):
        with its algorithms and its IANA code where they are known."""
        suite = {"name": name}
        found = [algorithm(n, p, **extra)
                 for n, p, extra in suites.algorithms(name)]
        if found:
            suite["algorithms"] = found
        if suites.identifiers(name):
            suite["identifiers"] = suites.identifiers(name)
        return suite

    def certificate_details(cert, owner):
        """The `certificateProperties` of a certificate (a `CryptoCert`
        or a certificate that the probe parsed) whose bom-ref is `owner`,
        and its properties."""
        details = {"certificateFormat": "X.509"}
        if cert is None:
            return details, {}
        details.update({
            "subjectName": _get(cert, "subject") or "",
            "issuerName": _get(cert, "issuer") or "",
            "notValidBefore": _iso(_get(cert, "not_before")),
            "notValidAfter": _iso(_get(cert, "not_after"))})
        details = {k: v for k, v in details.items() if v is not None}
        if _get(cert, "sig_alg"):
            details["signatureAlgorithmRef"] = algorithm(
                _get(cert, "sig_alg"), "signature")
        if public_key(cert, owner):
            details["subjectPublicKeyRef"] = public_key(cert, owner)
        self_signed = _get(cert, "self_signed")
        more = {"sha256": _get(cert, "sha256"), "serial": _get(cert, "serial"),
                "selfSigned": None if self_signed is None
                else str(self_signed).lower(),
                "subjectAltNames": ", ".join(_get(cert, "sans") or [])}
        return details, more

    for snap in snapshots:
        appliance = snap["appliance"]
        snapshot = snap["snapshot"]
        components.append({
            "type": "device", "bom-ref": appliance, "name": appliance,
            "version": snapshot.firmware or "",
            "properties": [p for p in (
                {"name": "mast:snapshot", "value": str(snapshot.id)},
                {"name": "mast:collectedAt",
                 "value": _iso(snapshot.collected_at) or ""},
                {"name": "mast:method", "value": snapshot.method or ""})
                if p["value"]]})
        refs = {}  # object id -> bom-ref
        # The certificate of each private key: an identification
        # credential pairs them, so the key has the algorithm and the size
        # of the certificate's public key
        paired = {}
        for edge in snap["edges"]:
            if edge.via == "Certificate":
                paired.setdefault(edge.from_object_id, {})["cert"] = \
                    edge.to_object_id
            elif edge.via == "Key":
                paired.setdefault(edge.from_object_id, {})["key"] = \
                    edge.to_object_id
        key_certs = {}
        for pair in paired.values():
            cert = snap["certs"].get(pair.get("cert"))
            if pair.get("key") is not None and cert is not None:
                key_certs.setdefault(pair["key"], []).append(cert)
        for obj in snap["objects"].values():
            ref = _ref(snap, obj)
            refs[obj.id] = ref
            component = {"bom-ref": ref, "name": obj.name}
            if obj.class_ == "CryptoCertificate":
                cert = snap["certs"].get(obj.id)
                details, more = certificate_details(cert, ref)
                if cert is not None and cert.sha256:
                    certificates.setdefault((appliance, cert.sha256), ref)
                component.update({
                    "type": "cryptographic-asset",
                    "cryptoProperties": {"assetType": "certificate",
                                         "certificateProperties": details}})
                component["properties"] = _properties(snap, obj, **more)
            elif _asset_type(obj.class_):
                kind = _asset_type(obj.class_)
                # A key paired with certificates whose keys agree
                found = {(key_algorithm(c), _get(c, "key_bits"))
                         for c in key_certs.get(obj.id, ())}
                if obj.class_ == "CryptoKey" and len(found) == 1:
                    algorithm_ref, bits = found.pop()
                    material = kind["relatedCryptoMaterialProperties"]
                    if algorithm_ref:
                        material["algorithmRef"] = algorithm_ref
                    if bits:
                        material["size"] = bits
                component.update({"type": "cryptographic-asset",
                                  "cryptoProperties": kind})
                component["properties"] = _properties(snap, obj)
            elif obj.class_ in PROFILES:
                attrs = obj.attrs or {}
                versions = _protocols(attrs)
                protocol = {"type": "tls",
                            "cipherSuites": [
                                cipher_suite(suites.iana_name(c))
                                for c in _as_list(attrs.get("Ciphers"))]}
                if versions:
                    protocol["version"] = versions[-1].split(" ")[-1]
                component.update({
                    "type": "cryptographic-asset",
                    "cryptoProperties": {"assetType": "protocol",
                                         "protocolProperties": protocol}})
                component["properties"] = _properties(
                    snap, obj, protocols=", ".join(versions),
                    ciphers=", ".join(_as_list(attrs.get("Ciphers"))),
                    kemAlgs=", ".join(_as_list(attrs.get("KEMAlgs"))),
                    ellipticCurves=", ".join(_as_list(attrs.get("EllipticCurves"))))
            else:
                # A listener or a service that uses the crypto objects
                component.update({"type": "application",
                                  "properties": _properties(snap, obj)})
            components.append(component)
        own = set(refs.values())
        for edge in snap["edges"]:
            if edge.from_object_id in refs and edge.to_object_id in refs:
                depends(refs[edge.from_object_id], refs[edge.to_object_id])
        # What the objects name that the configuration does not have: a
        # built-in object (the crypto profile system-default), or one
        # that is missing
        known = {(o.domain, o.class_, o.name) for o in snap["objects"].values()}
        added = set()
        for obj in snap["objects"].values():
            for class_, name, _ in _references(obj.attrs):
                if (obj.domain, class_, name) in known:
                    continue
                target = "{}/{}/{}/{}".format(appliance, obj.domain, class_, name)
                if target not in added:
                    added.add(target)
                    kind = _asset_type(class_)
                    component = {"bom-ref": target, "name": name,
                                 "type": "cryptographic-asset" if kind
                                 else "application",
                                 "properties": [
                                     {"name": "mast:appliance", "value": appliance},
                                     {"name": "mast:domain", "value": obj.domain},
                                     {"name": "mast:class", "value": class_},
                                     {"name": "mast:inConfiguration",
                                      "value": "false"}]}
                    if kind:
                        component["cryptoProperties"] = kind
                    components.append(component)
                depends(_ref(snap, obj), target)
        for item in snap["files"]:
            ref = "{}/{}/file/{}".format(appliance, item.domain, item.name)
            properties = [{"name": "mast:appliance", "value": appliance},
                          {"name": "mast:domain", "value": item.domain},
                          {"name": "mast:location", "value": item.location}]
            if item.size is not None:
                properties.append({"name": "mast:size", "value": str(item.size)})
            if item.modified:
                properties.append({"name": "mast:modified",
                                   "value": item.modified})
            if item.users is not None:
                properties.append(
                    {"name": "mast:usedBy", "value": ", ".join(item.users)}
                    if item.users else {"name": "mast:unused", "value": "true"})
            components.append({"type": "file", "bom-ref": ref,
                               "name": item.name, "properties": properties})
            for user in item.users or ():
                source = "{}/{}".format(appliance, user)
                if source in own:
                    depends(source, ref)
        for probe in snap.get("probes", ()):
            result = probe["result"] or {}
            where = "{}:{}".format(probe["host"], probe["port"])
            ref = "{}/probe/{}".format(appliance, where)
            protocol = {"type": "tls"}
            if result.get("protocol"):
                protocol["version"] = result["protocol"].replace("TLSv", "")
            if result.get("cipher"):
                protocol["cipherSuites"] = [
                    cipher_suite(suites.iana_name(result["cipher"]))]
            uses = []
            if result.get("group"):
                group = result["group"]
                uses.append(algorithm(group, **_group_properties(group)))
            for cert in result.get("chain") or ():
                sha256 = cert.get("sha256")
                if not sha256:
                    continue
                cert_ref = certificates.get((appliance, sha256))
                if cert_ref is None:
                    cert_ref = "{}/certificate/{}".format(appliance, sha256)
                    certificates[(appliance, sha256)] = cert_ref
                    details, more = certificate_details(cert, cert_ref)
                    components.append({
                        "type": "cryptographic-asset", "bom-ref": cert_ref,
                        "name": cert.get("subject") or sha256,
                        "cryptoProperties": {
                            "assetType": "certificate",
                            "certificateProperties": details},
                        "properties": [{"name": "mast:appliance",
                                        "value": appliance},
                                       {"name": "mast:servedAt", "value": where}]
                        + [{"name": "mast:" + k, "value": v}
                           for k, v in more.items() if v]})
                uses.append(cert_ref)
                depends(ref, cert_ref)
            if uses:
                protocol["cryptoRefArray"] = uses
            offers = result.get("offers") or {}
            target = result.get("target") or {}
            properties = [
                {"name": "mast:appliance", "value": appliance},
                {"name": "mast:probedAt", "value": _iso(probe["probed_at"])},
                {"name": "mast:connected",
                 "value": "true" if result.get("ok") else "false"}]
            for name, value in (
                    ("error", result.get("error")),
                    ("group", result.get("group")),
                    ("accepts", ", ".join(k for k, v in offers.items() if v)),
                    ("refuses", ", ".join(k for k, v in offers.items()
                                          if v is False)),
                    ("target", "{}/{}".format(target.get("class"),
                                              target.get("name"))
                     if target.get("class") else target.get("name"))):
                if value:
                    properties.append({"name": "mast:" + name,
                                       "value": str(value)})
            components.append({
                "type": "cryptographic-asset", "bom-ref": ref,
                "name": "{} (probed)".format(where),
                "cryptoProperties": {"assetType": "protocol",
                                     "protocolProperties": protocol},
                "properties": properties})
            if target.get("kind") == "listener":
                listener = "{}/{}/{}/{}".format(
                    appliance, target.get("domain"), target.get("class"),
                    target.get("name"))
                if listener in own:
                    depends(listener, ref)
        files = {"{}/{}/file/{}".format(appliance, f.domain, f.name)
                 for f in snap["files"]}
        for finding in snap.get("findings", ()):
            affects = refs.get(finding.get("object_id"))
            if affects is None:
                subject = "{}/{}/file/{}".format(
                    appliance, finding.get("domain") or "default",
                    finding.get("subject"))
                affects = subject if subject in files else appliance
            rule = RULES.get(finding["rule_id"])
            vulnerability = {
                "bom-ref": "finding/{}".format(finding["id"]),
                "id": finding["rule_id"],
                "source": {"name": "MAST posture rules"},
                "ratings": [{"severity": finding["severity"],
                             "method": "other",
                             "source": {"name": "MAST posture rules"}}],
                "description": finding["message"],
                "recommendation": finding["remediation"],
                "affects": [{"ref": affects}],
                "properties": [p for p in (
                    {"name": "mast:title", "value": finding.get("title") or ""},
                    {"name": "mast:pack",
                     "value": finding.get("pack") or getattr(rule, "pack", "")},
                    {"name": "mast:subject", "value": finding["subject"]})
                    if p["value"]]}
            exception = finding.get("exception")
            if exception:
                vulnerability["analysis"] = {
                    "state": "exploitable", "response": ["will_not_fix"],
                    "detail": "Exception {} until {}: {}".format(
                        exception["id"], str(exception["expires_at"])[:10],
                        exception["reason"])}
            vulnerabilities.append(vulnerability)
    document = {
        "bomFormat": "CycloneDX", "specVersion": "1.6",
        "serialNumber": "urn:uuid:{}".format(uuid.uuid4()), "version": 1,
        "metadata": {
            "timestamp": now.replace(microsecond=0).isoformat(),
            "tools": {"components": [{"type": "application", "name": "MAST",
                                      "version": __version__}]},
            "properties": [{"name": "mast:snapshots", "value": ", ".join(
                str(s["snapshot"].id) for s in snapshots)}]},
        "components": components
        + sorted(public_keys.values(), key=lambda k: k["bom-ref"])
        + sorted(algorithms.values(), key=lambda a: a["bom-ref"]),
        "dependencies": [{"ref": ref, "dependsOn": sorted(targets)}
                         for ref, targets in sorted(dependencies.items())],
    }
    if vulnerabilities:
        document["vulnerabilities"] = vulnerabilities
    return document


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

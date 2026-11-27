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
_module_: `mast.datapower.crypto.inventory`

`mast crypto inventory`: what cryptography the appliances use, kept in
the job database (`mast.datapower.crypto.models`) as one snapshot of
each appliance at each run.

The parent job starts one child job (`datapower.collect-crypto`) for each
appliance (`mast.jobs.runner.run_children`). For each domain, a child:

1. reads the running and the saved configuration of the whole domain,
once each, and keeps the crypto and TLS objects (`CLASSES`) without
their secrets. A plaintext password is dropped, and the object is
marked `has_plaintext_password`;
2. follows every reference to those objects through the domain, so that
the listeners and the services that use them are linked too (the walk
of `cert-recurse.py`, over one configuration instead of one request
for each object);
3. reads each certificate: with `do-view-certificate-details` when the
SOMA spec of the firmware has it (7.5 and newer), which returns the
certificate itself, or else with `CryptoExport` to a temporary file
with a name of its own, which is deleted in any case;
4. reads the ObjectStatus of the domain, for the state of each object
(a TLS profile can be down while its configuration looks right), and
keeps the `LocalAddress:LocalPort` of each listener (for
`mast crypto probe`; the management services of `default` are kept
even when no TLS profile is theirs);
5. lists the files of `cert:`, `pubcert:` and `sharedcert:` (from the
`default` domain, once).

The certificates are parsed here with `cryptography`, and kept by their
SHA-256 in `crypto_cert_cache`. Nothing secret is kept: no password, no
key, no file content but the certificates, which are public.
"""
import base64
import hashlib
import os
import re
import time
from datetime import datetime, timezone

from lxml import etree
from pydantic import field_validator, model_validator
from sqlalchemy import select

from mast.cli import Option, Table, say
from mast.plugins import COLLECT_CRYPTO
from mast.cli.results import stored_credential_name
from mast.logging import make_logger
from mast.plugin_utils.params import ApplianceParams, timeout_option

from mast.crypto.cli import section as cli
from mast.datapower.provider import cli as _provider

_datapower = _provider.section("crypto")
from .models import (
    DETAILS, EXPORT, CryptoCert, CryptoCertCache, CryptoEdge, CryptoFile,
    CryptoObject, CryptoSnapshot)

__all__ = [
    "CLASSES", "sanitize", "objects_of", "names_of", "edges_of",
    "status_of", "listen_of", "MGMT_CLASSES", "parse_certificate",
    "certificate_from_details", "certificate_from_export", "files_of",
    "spec_for", "firmware_version", "inventory", "inventory_appliance",
    "collect"]

MGMT = "http://www.datapower.com/schemas/management"

# The classes whose configuration is kept
CLASSES = (
    "CryptoCertificate", "CryptoKey", "CryptoSSKey", "CryptoIdentCred",
    "CryptoValCred", "CryptoProfile", "CryptoFWCred", "CryptoKerberosKeytab",
    "SSLServerProfile", "SSLClientProfile", "SSLSNIServerProfile",
    "SSLProxyProfile", "PasswordAlias", "SecureBackupMode")

# Only the name of these is kept: their configuration is a secret
NAMES_ONLY = ("PasswordAlias",)

FILE_LOCATIONS = ("cert:", "pubcert:", "sharedcert:")

_SECRET = re.compile(r"password|passphrase|secret", re.I)
_FLAGS = ("on", "off", "")


def _now():
    return datetime.now(timezone.utc)


# Parsing (no appliance needed)
# =============================

def _config_root(xml):
    """The `config` element of a get-config response (bytes, str or an
    element)."""
    if isinstance(xml, (bytes, str)):
        xml = etree.fromstring(xml.encode() if isinstance(xml, str) else xml)
    found = xml.find(".//{%s}config" % MGMT)
    return found if found is not None else xml


def _tag(element):
    return etree.QName(element).localname


def sanitize(element):
    """
    _function_: `mast.datapower.crypto.inventory.sanitize(element)`

    The configuration of one object as JSON types, without its secrets.
    Returns `(attrs, has_plaintext_password)`. A child whose name says
    it is a password or a secret is dropped, unless it is a switch
    (`on`/`off`) or a reference to another object (`class`). A
    reference becomes `{"class": ..., "name": ...}`; repeated children
    become lists.
    """
    plaintext = [False]

    def value(node):
        children = [c for c in node if isinstance(c.tag, str)]
        if node.get("class"):
            return {"class": node.get("class"), "name": (node.text or "").strip()}
        if not children:
            return (node.text or "").strip()
        found = {}
        for child in children:
            name = _tag(child)
            text = (child.text or "").strip()
            if _SECRET.search(name) and not child.get("class") \
                    and not len(child) and text not in _FLAGS:
                if name == "Password":
                    plaintext[0] = True
                continue
            item = value(child)
            if name in found:
                if not isinstance(found[name], list):
                    found[name] = [found[name]]
                found[name].append(item)
            else:
                found[name] = item
        return found
    attrs = value(element)
    return (attrs if isinstance(attrs, dict) else {}), plaintext[0]


def objects_of(config):
    """
    _function_: `mast.datapower.crypto.inventory.objects_of(config)`

    The objects of `CLASSES` in a configuration (a get-config response
    of a whole domain): a dict `(class, name) -> {admin_state, attrs,
    has_plaintext_password}`.
    """
    found = {}
    for element in _config_root(config):
        if not isinstance(element.tag, str):
            continue
        _class = _tag(element)
        if _class not in CLASSES:
            continue
        attrs, plaintext = sanitize(element)
        state = attrs.pop("mAdminState", None)
        if _class in NAMES_ONLY:
            attrs = {}
        found[(_class, element.get("name"))] = {
            "admin_state": state if isinstance(state, str) else None,
            "attrs": attrs, "has_plaintext_password": plaintext}
    return found


def status_of(xml):
    """
    _function_: `mast.datapower.crypto.inventory.status_of(xml)`

    The ObjectStatus of a domain (a get-status response): `{(class,
    name): (op_state, error)}`, where `error` is the `ErrorCode` (None
    if empty).
    """
    if isinstance(xml, (bytes, str)):
        xml = etree.fromstring(xml.encode() if isinstance(xml, str) else xml)
    found = {}
    for element in xml.iter():
        if not isinstance(element.tag, str) or \
                _tag(element) != "ObjectStatus":
            continue
        values = {_tag(c): (c.text or "").strip() for c in element
                  if isinstance(c.tag, str)}
        if values.get("Class") and values.get("Name"):
            found[(values["Class"], values["Name"])] = (
                values.get("OpState") or None, values.get("ErrorCode") or None)
    return found


# The management services, whose ports the TLS probe tries too
MGMT_CLASSES = ("WebGUI", "MgmtInterface", "RestMgmtInterface")


def listen_of(config):
    """
    _function_: `mast.datapower.crypto.inventory.listen_of(config)`

    The `LocalAddress:LocalPort` of each object of a configuration that
    listens (a front side handler, the WebGUI...): `{(class, name):
    "address:port"}`. An address of 0.0.0.0 or none is `0.0.0.0`.
    """
    found = {}
    for element in _config_root(config):
        if not isinstance(element.tag, str):
            continue
        port = (element.findtext("LocalPort") or "").strip()
        if not port.isdigit():
            continue
        address = (element.findtext("LocalAddress") or "").strip() or "0.0.0.0"
        found[(_tag(element), element.get("name"))] = "{}:{}".format(
            address, port)
    return found


def names_of(config):
    """The `(class, name)` of every object of a configuration."""
    return {(_tag(e), e.get("name")) for e in _config_root(config)
            if isinstance(e.tag, str)}


def edges_of(config, start):
    """
    _function_: `mast.datapower.crypto.inventory.edges_of(config, start)`

    The references to the objects `start` (`(class, name)` pairs) in a
    configuration, and to the objects that refer to them, and so on:
    `{(from_class, from_name, to_class, to_name, via)}`, where `via` is
    the element of the referring object. Also returns the
    `(class, name, admin_state)` of every referring object.
    """
    referrers = {}
    states = {}
    for element in _config_root(config):
        if not isinstance(element.tag, str):
            continue
        source = (_tag(element), element.get("name"))
        state = element.find("mAdminState")
        states[source] = state.text if state is not None else None
        for node in element.iter():
            if node is element or not isinstance(node.tag, str):
                continue
            if node.get("class") and (node.text or "").strip():
                target = (node.get("class"), node.text.strip())
                referrers.setdefault(target, set()).add((source, _tag(node)))
    edges = set()
    seen = set(start)
    frontier = list(start)
    while frontier:
        target = frontier.pop()
        for source, via in referrers.get(target, ()):
            edges.add(source + target + (via,))
            if source not in seen:
                seen.add(source)
                frontier.append(source)
    found = {(c, n, states.get((c, n))) for c, n in seen if (c, n) not in start}
    return edges, found


_FILE_NAME = re.compile(r"^(cert|sharedcert|pubcert):/*(\S+)$")


def file_name(text):
    """
    _function_: `mast.datapower.crypto.inventory.file_name(text)`

    `cert:///a/b.pem` in the spelling of `files_of` (three slashes), or
    None if `text` is not a file of `cert:`, `pubcert:` or `sharedcert:`.
    """
    found = _FILE_NAME.match(text.strip()) if isinstance(text, str) else None
    if not found:
        return None
    return "{}:///{}".format(found.group(1), found.group(2))


def file_users_of(config):
    """
    _function_: `mast.datapower.crypto.inventory.file_users_of(config)`

    The files of `cert:`, `pubcert:` and `sharedcert:` that the objects
    of a configuration name, whatever their class (a key, but also an
    AAA policy with an LTPA key): `{file: {"Class/name", ...}}`.
    """
    found = {}
    for element in _config_root(config):
        if not isinstance(element.tag, str):
            continue
        user = "{}/{}".format(_tag(element), element.get("name"))
        for node in element.iter():
            if node is element or not isinstance(node.tag, str) or len(node):
                continue
            name = file_name(node.text)
            if name:
                found.setdefault(name, set()).add(user)
    return found


def _name_text(name):
    try:
        return name.rfc4514_string()
    except Exception:
        return str(name)


def parse_certificate(der, details=None):
    """
    _function_: `mast.datapower.crypto.inventory.parse_certificate(der,
    details=None)`

    The facts of one certificate (DER bytes) as JSON types. `details`
    (the `CertificateDetails` of DataPower, if any) gives the key
    algorithm and size of a key that `cryptography` cannot read, such
    as ML-DSA.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives.asymmetric import (
        dsa, ec, ed448, ed25519, rsa)
    found = {"sha256": hashlib.sha256(der).hexdigest()}
    cert = x509.load_der_x509_certificate(der)
    found["subject"] = _name_text(cert.subject)
    found["issuer"] = _name_text(cert.issuer)
    found["serial"] = format(cert.serial_number, "x")
    found["not_before"] = cert.not_valid_before_utc.isoformat()
    found["not_after"] = cert.not_valid_after_utc.isoformat()
    found["self_signed"] = cert.subject == cert.issuer
    try:
        found["sig_alg"] = cert.signature_algorithm_oid._name
    except Exception:
        found["sig_alg"] = cert.signature_algorithm_oid.dotted_string
    try:
        sans = cert.extensions.get_extension_for_class(
            x509.SubjectAlternativeName).value
        found["sans"] = [str(v.value) for v in sans]
    except x509.ExtensionNotFound:
        found["sans"] = []
    found["key_alg"] = found["key_bits"] = found["curve"] = None
    try:
        key = cert.public_key()
        if isinstance(key, rsa.RSAPublicKey):
            found["key_alg"], found["key_bits"] = "RSA", key.key_size
        elif isinstance(key, ec.EllipticCurvePublicKey):
            found["key_alg"], found["key_bits"] = "EC", key.curve.key_size
            found["curve"] = key.curve.name
        elif isinstance(key, ed25519.Ed25519PublicKey):
            found["key_alg"], found["key_bits"] = "Ed25519", 256
        elif isinstance(key, ed448.Ed448PublicKey):
            found["key_alg"], found["key_bits"] = "Ed448", 456
        elif isinstance(key, dsa.DSAPublicKey):
            found["key_alg"], found["key_bits"] = "DSA", key.key_size
        else:
            found["key_alg"] = type(key).__name__
    except Exception:
        # A key that this cryptography cannot read (ML-DSA, for example)
        pass
    if details is not None:
        algorithm = details.findtext("SubjectPublicKeyAlgorithm")
        bits = details.findtext("SubjectPublicKeyBitLength")
        if found["key_alg"] is None and algorithm:
            found["key_alg"] = algorithm
        if found["key_bits"] is None and bits and bits.isdigit():
            found["key_bits"] = int(bits)
    return found


def certificate_from_details(xml):
    """`(der, details)` from a do-view-certificate-details response, or
    `(None, None)`."""
    root = etree.fromstring(xml) if isinstance(xml, bytes) else \
        etree.fromstring(xml.encode()) if isinstance(xml, str) else xml
    details = root.find(".//CertificateDetails")
    if details is None:
        return None, None
    text = (details.findtext("Base64") or "").strip()
    return (base64.b64decode(text) if text else None), details


def certificate_from_export(data):
    """The DER of a certificate from the file that CryptoExport wrote."""
    root = etree.fromstring(data if isinstance(data, bytes) else data.encode())
    text = "".join((root.findtext(".//certificate") or "").split())
    return base64.b64decode(text) if text else None


def files_of(xml, location):
    """
    _function_: `mast.datapower.crypto.inventory.files_of(xml, location)`

    The files of a get-filestore response of `location`, read in the
    `default` domain: `[{domain, location, name, size, modified}]`. In
    `cert:`, a folder is the `cert:` of the domain of that name.
    `pubcert:` and `sharedcert:` belong to every domain (`default`).
    """
    root = etree.fromstring(xml) if isinstance(xml, bytes) else \
        etree.fromstring(xml.encode()) if isinstance(xml, str) else xml
    found = []
    top = root.find(".//location")
    if top is None:
        return found

    def walk(node, domain):
        for child in node:
            if not isinstance(child.tag, str):
                continue
            if child.tag == "file":
                size = child.findtext("size")
                found.append({
                    "domain": domain, "location": location,
                    "name": re.sub(r":[/]*", ":///", "{}{}".format(
                        location, child.get("name")), count=1),
                    "size": int(size) if size and size.isdigit() else None,
                    "modified": child.findtext("modified")})
            elif child.tag == "directory":
                name = child.get("name", "")
                sub = name.split("/", 1)[1] if "/" in name else name
                walk(child, sub if location == "cert:" and domain == "default"
                     else domain)
    walk(top, "default")
    return found


def firmware_version(text):
    """`IDG.10.6.0.11` -> (10, 6, 0, 11): the numbers at the end of a
    firmware name (`XI52.7.6.0.0` is 7.6.0.0). A firmware of 2018 ranks
    between 7.6 and 10.0. None if it has no version."""
    parts = []
    for part in reversed((text or "").strip().split(".")):
        if not part.isdigit():
            break
        parts.insert(0, int(part))
    if not parts:
        return None
    parts = (parts + [0, 0, 0, 0])[:4]
    if parts[0] == 2018:
        return (9, parts[1], parts[2], parts[3])
    return tuple(parts)


def _spec_version(name):
    """The version of a spec file: v10.6.0.11-C.xml, v7500-xi52.xml,
    v2018-4-1-3_IDG.xml."""
    found = re.match(r"v(\d{4})-", name)
    if found and not found.group(1).startswith("20"):
        digits = found.group(1)
        return (int(digits[0]), int(digits[1]), int(digits[2]), int(digits[3]))
    found = re.match(r"v([\d.-]+)", name)
    if not found:
        return None
    return firmware_version(found.group(1).strip(".-").replace("-", "."))


_SPEC_CACHE = {}


def _has_details(path):
    if path not in _SPEC_CACHE:
        try:
            with open(path, "rb") as fin:
                _SPEC_CACHE[path] = b"do-view-certificate-details" in fin.read()
        except OSError:
            _SPEC_CACHE[path] = True
    return _SPEC_CACHE[path]


def spec_for(firmware, etc_dir, configured=None):
    """
    _function_: `mast.datapower.crypto.inventory.spec_for(firmware,
    etc_dir, configured=None)`

    `(spec_file, method)`: the SOMA spec of the firmware (the one that
    the registry names, or the newest in `etc_dir` that is not newer
    than the firmware), and how to read the certificates with it:
    `DETAILS` if it has `do-view-certificate-details`, else `EXPORT`.
    """
    if configured:
        path = configured if os.path.isabs(configured) else \
            os.path.join(os.path.dirname(etc_dir), configured)
        return configured, DETAILS if _has_details(path) else EXPORT
    version = firmware_version(firmware)
    best = None
    for name in sorted(os.listdir(etc_dir)):
        if not (name.startswith("v") and name.endswith(".xml")):
            continue
        found = _spec_version(name)
        if found is None or (version is not None and found > version):
            continue
        if best is None or found > best[0]:
            best = (found, name)
    if best is None:
        return None, DETAILS
    path = os.path.join(etc_dir, best[1])
    return "etc/" + best[1], DETAILS if _has_details(path) else EXPORT


# The appliance
# =============

class _Requests(object):
    """The requests to one appliance, with a pause between them."""

    def __init__(self, appliance, delay):
        self.appliance = appliance
        self.delay = delay
        self.first = True

    def __call__(self, method, *args, **kwargs):
        if not self.first and self.delay:
            time.sleep(self.delay)
        self.first = False
        return getattr(self.appliance, method)(*args, **kwargs)


def _text(response):
    text = getattr(response, "text", response)
    return text.encode() if isinstance(text, str) else text


def _read_certificates(call, domain, names, method, job_id, problems):
    """The parsed certificates of the `CryptoCertificate` objects
    `names` of `domain`, by name."""
    found = {}
    for number, name in enumerate(names, start=1):
        try:
            if method == DETAILS:
                der, details = certificate_from_details(_text(call(
                    "get_certificate_details", domain, name)))
            else:
                der, details = _export(call, domain, name, job_id, number), None
            if der is None:
                raise ValueError("the appliance did not return it")
            found[name] = _parse_cached(der, details)
        except Exception as exc:
            problems.append({"domain": domain, "object": name,
                             "problem": "certificate not read: {}".format(
                                 _error(exc))})
    return found


def _export(call, domain, name, job_id, number):
    """Method B: CryptoExport to a temporary file of this job, read it,
    and delete it in any case."""
    temporary = "mast-{}-{}".format((job_id or "local")[:12], number)
    try:
        call("do_action", "CryptoExport", domain=domain, ObjectType="cert",
             ObjectName=name, OutputFilename=temporary)
        return certificate_from_export(
            call("getfile", domain, "temporary:///" + temporary))
    finally:
        try:
            call("do_action", "DeleteFile", domain=domain,
                 File="temporary:///" + temporary)
        except Exception:
            make_logger("mast.crypto").warning(
                "Could not delete temporary:///{} in {}".format(
                    temporary, domain))


def _parse_cached(der, details):
    from mast.jobs import db
    sha256 = hashlib.sha256(der).hexdigest()
    with db.session(write=False) as session:
        cached = session.get(CryptoCertCache, sha256)
        if cached is not None and cached.parsed.get("key_alg"):
            return dict(cached.parsed)
    parsed = parse_certificate(der, details)
    try:
        with db.session() as session:
            if session.get(CryptoCertCache, sha256) is None:
                session.add(CryptoCertCache(sha256=sha256, parsed=parsed))
    except Exception:
        # Another job cached it at the same time
        pass
    return parsed


def _error(exc):
    text = "{}: {}".format(type(exc).__name__, exc).strip()
    return text.splitlines()[0][:300] if text else type(exc).__name__


def collect(appliance, domains=None, delay=0.1, job_id=None, etc_dir=None):
    """
    _function_: `mast.datapower.crypto.inventory.collect(appliance,
    domains=None, delay=0.1, job_id=None, etc_dir=None)`

    Read the crypto inventory of one appliance (a `DataPower`). Return
    a dict with `firmware`, `spec_file`, `method`, `objects`
    (`{(domain, class, name): {...}}`), `certs` (`{(domain, name):
    parsed}`), `files`, `edges` (`{(domain, from, to, via)}`),
    `file_users` (`{file: {"domain/Class/name"}}`, the objects of the
    running configurations that name each file) and `problems`. Raises
    what the appliance raises for its firmware or its list of domains;
    a domain that cannot be read is a problem.
    """
    from mast.config import MAST_HOME
    from mast.datapower.status.collector import _Poller
    call = _Requests(appliance, delay)
    firmware = _Poller(appliance, delay).firmware()["firmware"]
    record = getattr(appliance, "_record", None)
    spec_file, method = spec_for(
        firmware, etc_dir or os.path.join(MAST_HOME, "etc"),
        getattr(record, "soma_spec_file", None))
    names = list(domains) if domains else list(appliance.domains)
    found = {"firmware": firmware, "spec_file": spec_file, "method": method,
             "objects": {}, "certs": {}, "files": [], "edges": set(),
             "file_users": {}, "problems": []}
    for domain in names:
        try:
            running = _text(call("get_config", domain=domain, persisted=False))
            saved = _text(call("get_config", domain=domain, persisted=True))
        except Exception as exc:
            found["problems"].append({"domain": domain, "problem": _error(exc)})
            continue
        objects = objects_of(running)
        saved_objects = objects_of(saved)
        for name, users in file_users_of(running).items():
            found["file_users"].setdefault(name, set()).update(
                "{}/{}".format(domain, user) for user in users)
        for key in set(objects) | set(saved_objects):
            now, then = objects.get(key), saved_objects.get(key)
            item = dict(now or then)
            item["running"] = now is not None
            item["saved"] = then is not None and (
                now is None or then["attrs"] == now["attrs"])
            found["objects"][(domain,) + key] = item
        edges, referrers = edges_of(running, set(objects))
        saved_names = names_of(saved)
        listening = listen_of(running)
        if domain == "default":
            # The management services, for the probe, even when they use
            # no TLS profile
            root = _config_root(running)
            for _class in MGMT_CLASSES:
                for element in root.findall(_class):
                    if (_class, element.get("name")) in listening:
                        referrers.add((_class, element.get("name"),
                                       element.findtext("mAdminState")))
        for _class, name, state in referrers:
            found["objects"].setdefault((domain, _class, name), {
                "admin_state": state, "attrs": None,
                "has_plaintext_password": False, "running": True,
                "saved": (_class, name) in saved_names})
        for key, item in found["objects"].items():
            if key[0] == domain and key[1:] in listening:
                item["listen"] = listening[key[1:]]
        try:
            status = status_of(_text(call("get_status", "ObjectStatus",
                                          domain=domain)))
        except Exception as exc:
            status = {}
            found["problems"].append({
                "domain": domain, "object": "ObjectStatus",
                "problem": "status not read: {}".format(_error(exc))})
        for key, item in found["objects"].items():
            if key[0] == domain and key[1:] in status:
                item["op_state"], item["status_error"] = status[key[1:]]
        found["edges"] |= {(domain,) + edge for edge in edges}
        certificates = [n for (c, n) in objects if c == "CryptoCertificate"]
        for name, parsed in _read_certificates(
                call, domain, certificates, method, job_id,
                found["problems"]).items():
            found["certs"][(domain, name)] = parsed
        say("Read {} objects and {} certificates of {} in {}".format(
            len(objects), len(certificates), domain, appliance.hostname))
    for location in FILE_LOCATIONS:
        try:
            found["files"] += files_of(_text(call(
                "get_filestore", domain="default", location=location)),
                location)
        except Exception as exc:
            found["problems"].append({"location": location,
                                      "problem": _error(exc)})
    return found


# Keeping it
# ==========

def _time(text):
    return datetime.fromisoformat(text) if text else None


def save(appliance_id, collected, now, job_id=None, error=None):
    """
    _function_: `mast.datapower.crypto.inventory.save(appliance_id,
    collected, now, job_id=None, error=None)`

    Keep one snapshot: what `collect` returned, or the `error` that
    stopped it. Return the id of the snapshot.
    """
    from mast.jobs import db
    collected = collected or {}
    with db.session() as session:
        snapshot = CryptoSnapshot(
            appliance_id=appliance_id, job_id=job_id, collected_at=now,
            firmware=collected.get("firmware"),
            spec_file=collected.get("spec_file"),
            method=collected.get("method"), ok=error is None, error=error,
            problems=collected.get("problems") or None)
        session.add(snapshot)
        session.flush()
        ids = {}
        for (domain, _class, name), item in sorted(
                collected.get("objects", {}).items()):
            row = CryptoObject(
                snapshot_id=snapshot.id, domain=domain, class_=_class,
                name=name, admin_state=item.get("admin_state"),
                running=item["running"], saved=item["saved"],
                attrs=item.get("attrs"),
                has_plaintext_password=item["has_plaintext_password"],
                op_state=item.get("op_state"),
                status_error=item.get("status_error"),
                listen=item.get("listen"))
            session.add(row)
            session.flush()
            ids[(domain, _class, name)] = row.id
        for (domain, name), parsed in collected.get("certs", {}).items():
            session.add(CryptoCert(
                snapshot_id=snapshot.id,
                object_id=ids[(domain, "CryptoCertificate", name)],
                sha256=parsed["sha256"], subject=parsed.get("subject"),
                issuer=parsed.get("issuer"), serial=parsed.get("serial"),
                sig_alg=parsed.get("sig_alg"), key_alg=parsed.get("key_alg"),
                key_bits=parsed.get("key_bits"), curve=parsed.get("curve"),
                not_before=_time(parsed.get("not_before")),
                not_after=_time(parsed.get("not_after")),
                sans=parsed.get("sans"),
                self_signed=parsed.get("self_signed")))
        file_users = collected.get("file_users")
        for item in collected.get("files", []):
            users = None
            if file_users is not None:
                # A file of cert: is the domain's own; sharedcert: and
                # pubcert: are for every domain
                users = sorted(
                    u for u in file_users.get(item["name"], ())
                    if item["location"] != "cert:"
                    or u.split("/", 1)[0] == item["domain"])
            session.add(CryptoFile(snapshot_id=snapshot.id, users=users,
                                   **item))
        for domain, fc, fn, tc, tn, via in sorted(collected.get("edges", ())):
            source, target = ids.get((domain, fc, fn)), ids.get((domain, tc, tn))
            if source and target:
                session.add(CryptoEdge(snapshot_id=snapshot.id,
                                       from_object_id=source,
                                       to_object_id=target, via=via))
        return snapshot.id


# The commands
# ============

class Inventory(ApplianceParams):
    timeout: int = timeout_option(60)
    domains: list[str] = Option(
        default_factory=list, short="d",
        help="The domains to read. The default is every domain.")
    workers: int = Option(
        4, short="w", help="How many appliances to read at the same time.")
    request_delay: float = Option(
        0.1,
        help="The seconds to wait between two requests to one appliance.")

    @model_validator(mode="before")
    @classmethod
    def _every_appliance(cls, data):
        """With no appliances, every registered appliance."""
        if isinstance(data, dict) and not data.get("appliances"):
            from mast import registry
            found = [a.name for a in registry.list_appliances()]
            if not found:
                raise ValueError("there is no registered appliance; give "
                                 "the appliances")
            data = dict(data, appliances=found)
        return data

    @field_validator("workers")
    @classmethod
    def _at_least_one(cls, value):
        if value < 1:
            raise ValueError("must be 1 or more")
        return value


class InventoryOne(Inventory):
    pass


def _child_params(params, appliance, credentials=None):
    """The parameters of the child of one appliance. A stored credential
    goes by its name, so that the child is checked as the parent was.
    `credentials` are those of this appliance; the default is all of
    `params.credentials`."""
    found = {"appliances": [appliance], "domains": list(params.domains),
             "timeout": params.timeout, "request_delay": params.request_delay,
             "workers": 1}
    name = params.credential or stored_credential_name()
    if name:
        found["credential"] = name
    else:
        found["credentials"] = list(
            params.credentials if credentials is None else credentials)
        found["no_check_hostname"] = params.no_check_hostname
    return found


def _targets(params):
    """The appliances that `params.appliances` stand for, one for each
    child job, in order and without repeats, as `(name, credentials)`.
    An environment stands for its members, which share the credentials
    given for it, as they do in `Environment`."""
    from mast import registry
    names = list(params.appliances)
    credentials = list(params.credentials)
    paired = len(credentials) > 1 and len(credentials) == len(names)
    found, seen = [], set()
    for index, name in enumerate(names):
        for appliance in registry.resolve([name]):
            if appliance.name in seen:
                continue
            seen.add(appliance.name)
            found.append((appliance.name,
                          [credentials[index]] if paired else credentials))
    return found


@_datapower.command("collect-crypto", effect="read", group="inventory",
                    exclusive=False,
                    passive=True)
def inventory_appliance(params: InventoryOne):
    """Read the crypto inventory of one appliance and keep it as a
snapshot. mast crypto inventory runs this for each appliance; run it
yourself only to read one appliance again."""
    from mast.jobs.runner import current_job
    from mast.plugin_utils.results import history
    env = params.environment()
    rows, oks = [], []
    for appliance in env.appliances:
        record = getattr(appliance, "_record", None)
        now = _now()
        try:
            collected = collect(appliance, params.domains,
                                delay=params.request_delay,
                                job_id=current_job())
            error = None
        except Exception as exc:
            collected, error = None, _error(exc)
            say("Could not read {}: {}".format(appliance.hostname, error))
        snapshot = save(record.id, collected, now, job_id=current_job(),
                        error=error) if record is not None else None
        findings = ""
        if snapshot is not None and error is None:
            from .posture import evaluate
            try:
                findings = str(evaluate(snapshot))
            except Exception as exc:
                make_logger("mast.crypto").exception(
                    "The posture rules failed on snapshot {}".format(snapshot))
                say("Could not run the posture rules: {}".format(_error(exc)))
        collected = collected or {}
        rows.append([
            appliance.hostname, "no" if error else "yes",
            collected.get("firmware") or "", collected.get("method") or "",
            str(sum(1 for k in collected.get("objects", {})
                    if k[1] in CLASSES)),
            str(len(collected.get("certs", {}))),
            str(len(collected.get("files", []))),
            str(len(collected.get("problems", []))), findings,
            "" if snapshot else "Not registered: not kept",
            error or ""])
        oks.append(error is None)
    return _Inventoried(rows, oks, history(env))


class _Inventoried(Table):
    HEADER = ("Appliance", "Read", "Firmware", "Method", "Crypto objects",
              "Certificates", "Files", "Problems", "Findings", "Note",
              "Error")

    def __init__(self, rows, oks, history=""):
        super().__init__(self.HEADER, rows, history=history)
        self._oks = oks

    def outcomes(self):
        return list(self._oks)


@cli.command("inventory", effect="read", needs=(COLLECT_CRYPTO,),
             id="crypto-inventory", group="inventory",
             exclusive=False, passive=True)
def inventory(params: Inventory):
    """Read the crypto inventory of the appliances: the certificates,
keys, credentials and TLS profiles of every domain, what uses them, and
the files of cert:, pubcert: and sharedcert:. No password or key is
kept. Each appliance is a child job. With no appliances, every
registered appliance. Run it each day with a stored credential for
collecting."""
    from mast.jobs import queue
    from mast.jobs.runner import current_job, run_children
    if current_job() is None:
        # Not a job (a test, a script): read the appliances here
        return inventory_appliance(**dict(
            _child_params(params, None), appliances=list(params.appliances),
            credentials=list(params.credentials)))
    targets = _targets(params)
    say("Reading {} appliance(s)".format(len(targets)))
    children = run_children(
        "datapower.collect-crypto",
        [_child_params(params, name, credentials)
         for name, credentials in targets],
        workers=params.workers)
    rows, oks = [], []
    for (name, _), child in zip(targets, children):
        info = queue.get(child)
        result = queue.result(child)
        data = result.data if result is not None else None
        row = data["rows"][0] if data and data.get("rows") else None
        rows.append([name, info.status, child] + (row[2:9] if row else
                                                  [""] * 7)
                    + [info.error.splitlines()[0][:200] if info.error
                       else (row[10] if row else "")])
        oks.append(info.status == "succeeded")
    return _Children(rows, oks)


class _Children(Table):
    HEADER = ("Appliance", "Status", "Job", "Firmware", "Method",
              "Crypto objects", "Certificates", "Files", "Problems",
              "Findings", "Error")

    def __init__(self, rows, oks):
        super().__init__(self.HEADER, rows)
        self._oks = oks

    def outcomes(self):
        return list(self._oks)

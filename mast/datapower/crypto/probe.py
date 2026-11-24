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
_module_: `mast.datapower.crypto.probe`

`mast-crypto probe`: connect to the TLS ports of the appliances, as a
peer would, and record what they accept. It needs no credential: it
logs in to nothing, and sends nothing but TLS handshakes.

**What it probes:** the ports of the listeners of the newest snapshot
(the front side handlers that use a server profile, and the management
services), the XML management port and the WebGUI port of the
registry, and any `host:port` given with `--targets`. For the XML
management and the WebGUI, the port of the registry is used if it has
one: behind NAT or a port mapping it is the one that MAST reaches.

**What it records** for each port, in `crypto_probe_results`:

* the protocol, cipher and group that a default client negotiates, and
the certificate chain (SHA-256, subject, key);
* whether each of these offers is accepted: TLS 1.0, TLS 1.1, TLS 1.2,
TLS 1.3, each hybrid group (`X25519MLKEM768`, `SecP256r1MLKEM768`,
`SecP384r1MLKEM1024`), `MLKEM768` alone, and the classical groups alone
on TLS 1.3.

It uses the `ssl` module only, with one context for each offer. Python
3.15 can choose the groups to offer (`SSLContext.set_groups`) and read
the one negotiated (`SSLSocket.group`); on an older Python the group
checks are reported as not available, and the rest is done. If the
OpenSSL that Python links does not know a group, that check is
reported as not supported. Certificates are not checked: the probe
reports what is there, and sends no secret.

After the probe of an appliance, the posture rules of its newest
snapshot run again, so that `pqc-probe-mismatch` sees the new results.
"""
import ipaddress
import socket
import ssl
import sys
import warnings
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from pydantic import field_validator, model_validator
from sqlalchemy import select

from mast.cli import Option, Params, Table, say
from mast.logging import make_logger

from . import cli
from .models import CryptoEdge, CryptoObject, CryptoProbeResult

__all__ = ["probe_port", "targets_of", "probe", "OFFERS", "save",
           "latest_results"]

# The offers, in order: (name, minimum and maximum version, groups)
_V = ssl.TLSVersion
OFFERS = (
    ("TLSv1.0", _V.TLSv1, None),
    ("TLSv1.1", _V.TLSv1_1, None),
    ("TLSv1.2", _V.TLSv1_2, None),
    ("TLSv1.3", _V.TLSv1_3, None),
    ("X25519MLKEM768", _V.TLSv1_3, "X25519MLKEM768"),
    ("SecP256r1MLKEM768", _V.TLSv1_3, "SecP256r1MLKEM768"),
    ("SecP384r1MLKEM1024", _V.TLSv1_3, "SecP384r1MLKEM1024"),
    ("MLKEM768", _V.TLSv1_3, "MLKEM768"),
    ("classical", _V.TLSv1_3, "X25519:P-256:P-384:X448"),
)
NO_GROUPS = "not available on this Python (needs 3.15)"
NO_OPENSSL = "not supported by this OpenSSL"
# The services whose ports are probed even without a server profile
_MGMT = ("WebGUI", "MgmtInterface", "RestMgmtInterface")
_SERVER_SIDE = ("SSLServerProfile", "SSLSNIServerProfile", "SSLProxyProfile")
# The management services whose port the registry has
_MAPPED = {"MgmtInterface": "soma_port", "WebGUI": "web_port"}


def _now():
    return datetime.now(timezone.utc)


def _context(version=None, groups=None):
    """A client context that checks nothing, for one offer. Raises
    `LookupError` (with the reason) if this Python or OpenSSL cannot
    make the offer."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    if version is not None:
        try:
            with warnings.catch_warnings():
                # TLS 1.0 and 1.1 are deprecated: offering them is the
                # point, to see whether the appliance still accepts them
                warnings.simplefilter("ignore", DeprecationWarning)
                context.minimum_version = version
                context.maximum_version = version
        except (ValueError, ssl.SSLError):
            raise LookupError(NO_OPENSSL)
        if version < _V.TLSv1_2:
            # OpenSSL 3 does not offer TLS 1.0 and 1.1 at the default
            # security level
            try:
                context.set_ciphers("ALL:@SECLEVEL=0")
            except ssl.SSLError:
                raise LookupError(NO_OPENSSL)
    if groups is not None:
        if not hasattr(context, "set_groups"):
            raise LookupError(NO_GROUPS)
        try:
            context.set_groups(groups)
        except (ValueError, ssl.SSLError):
            raise LookupError(NO_OPENSSL)
    return context


def _handshake(context, host, port, timeout, server_name):
    with socket.create_connection((host, port), timeout=timeout) as raw:
        with context.wrap_socket(raw, server_hostname=server_name) as tls:
            group = tls.group() if hasattr(tls, "group") else None
            chain = []
            if hasattr(tls, "get_unverified_chain"):
                try:
                    chain = [bytes(c) for c in tls.get_unverified_chain() or ()]
                except Exception:
                    chain = []
            if not chain:
                der = tls.getpeercert(binary_form=True)
                chain = [der] if der else []
            cipher = tls.cipher()
            return {"protocol": tls.version(),
                    "cipher": cipher[0] if cipher else None,
                    "group": group, "chain": chain}


def _error(exc):
    text = "{}: {}".format(type(exc).__name__, exc).strip()
    return text.splitlines()[0][:300]


def _certificate(der):
    from .inventory import parse_certificate
    try:
        parsed = parse_certificate(der)
    except Exception as exc:
        import hashlib
        return {"sha256": hashlib.sha256(der).hexdigest(),
                "error": _error(exc)}
    return {key: parsed.get(key) for key in (
        "sha256", "subject", "issuer", "key_alg", "key_bits", "curve",
        "sig_alg", "not_after", "self_signed")}


def _server_name(host):
    try:
        ipaddress.ip_address(host)
        return None
    except ValueError:
        return host


def probe_port(host, port, timeout=10):
    """
    _function_: `mast.datapower.crypto.probe.probe_port(host, port,
    timeout=10)`

    Probe one port. Returns a dict:

    * `ok`: a default client could connect; else `error` says why.
    * `protocol`, `cipher`, `group` (None on a Python without
    `SSLSocket.group`), `chain` (the certificates, parsed).
    * `offers`: `{name: True | False | None}` for each of `OFFERS`; None
    if it could not be tried, and `notes[name]` says why.
    * `python`, `openssl`: what made the offers.
    """
    name = _server_name(host)
    found = {"ok": False, "error": None, "protocol": None, "cipher": None,
             "group": None, "chain": [], "offers": {}, "notes": {},
             "python": "{}.{}.{}".format(*sys.version_info[:3]),
             "openssl": ssl.OPENSSL_VERSION}
    try:
        first = _handshake(_context(), host, port, timeout, name)
    except ssl.SSLError as exc:
        # TLS, but not what a default client offers (TLS 1.0 only, for
        # example): the offers say more
        found["error"] = _error(exc)
        first = None
    except OSError as exc:
        # Nothing to talk to
        found["error"] = _error(exc)
        return found
    if first is not None:
        found.update(ok=True, protocol=first["protocol"],
                     cipher=first["cipher"], group=first["group"],
                     chain=[_certificate(der) for der in first["chain"]])
    for offer, version, groups in OFFERS:
        try:
            context = _context(version, groups)
        except LookupError as exc:
            found["offers"][offer] = None
            found["notes"][offer] = str(exc)
            continue
        try:
            _handshake(context, host, port, timeout, name)
            found["offers"][offer] = True
        except socket.timeout as exc:
            found["offers"][offer] = None
            found["notes"][offer] = _error(exc)
        except ConnectionRefusedError as exc:
            found["offers"][offer] = None
            found["notes"][offer] = _error(exc)
        except (OSError, ssl.SSLError) as exc:
            # Refused by the server: an alert, a reset, a close
            found["offers"][offer] = False
            found["notes"][offer] = _error(exc)
    return found


# What to probe
# =============

def _host(listen, address):
    """The host to connect to for a `LocalAddress:LocalPort`: the
    address of the appliance, unless the listener has an IP address of
    its own."""
    local = listen.rsplit(":", 1)[0]
    try:
        if not ipaddress.ip_address(local).is_unspecified:
            return local
    except ValueError:
        # A host alias, or nothing: the appliance's own address
        pass
    return address


def targets_of(appliance, snapshot_id=None):
    """
    _function_: `mast.datapower.crypto.probe.targets_of(appliance,
    snapshot_id=None)`

    The ports to probe for one registered appliance (a
    `mast.registry.Appliance`): `[(host, port, target)]`, where
    `target` says what it is (`kind` is `listener`, with the `domain`,
    `class` and `name` of the object, or `management`). The listeners
    come from the snapshot `snapshot_id` (the newest that worked if
    None).
    """
    from mast.jobs import db

    from .posture import latest_snapshots
    found = {}
    with db.session(write=False) as session:
        if snapshot_id is None:
            snapshot_id = latest_snapshots(session, [appliance.id]).get(
                appliance.id)
        if snapshot_id is not None:
            objects = {o.id: o for o in session.scalars(
                select(CryptoObject).where(
                    CryptoObject.snapshot_id == snapshot_id))}
            tls = set()
            for edge in session.scalars(select(CryptoEdge).where(
                    CryptoEdge.snapshot_id == snapshot_id)):
                to = objects.get(edge.to_object_id)
                if to is not None and to.class_ in _SERVER_SIDE:
                    tls.add(edge.from_object_id)
            for obj in sorted(objects.values(), key=lambda o: o.id):
                if not obj.listen or obj.admin_state == "disabled" or \
                        not obj.running:
                    continue
                if obj.id not in tls and obj.class_ not in _MGMT:
                    continue
                port = int(obj.listen.rsplit(":", 1)[1])
                host = _host(obj.listen, appliance.address)
                # Behind NAT or a port mapping, the registry has the
                # ports that MAST reaches; the configuration has those
                # of the appliance itself
                mapped = _MAPPED.get(obj.class_)
                if mapped and getattr(appliance, mapped):
                    host, port = appliance.address, getattr(appliance, mapped)
                key = (host, port)
                found.setdefault(key, {
                    "kind": "listener", "domain": obj.domain,
                    "class": obj.class_, "name": obj.name})
    for port, what in ((appliance.soma_port or 5550, "XML management"),
                       (appliance.web_port or 9090, "WebGUI")):
        found.setdefault((appliance.address, port),
                         {"kind": "management", "name": what})
    return [(host, port, target) for (host, port), target in found.items()], \
        snapshot_id


def _target(text):
    host, _, port = (text or "").strip().rpartition(":")
    host = host.strip("[]")
    if not host or not port.isdigit() or not 0 < int(port) < 65536:
        raise ValueError("{!r} is not host:port".format(text))
    return host, int(port)


# Keeping it
# ==========

def save(appliance_id, snapshot_id, host, port, result, now, job_id=None):
    """Keep one probe result. Returns its id."""
    from mast.jobs import db
    with db.session() as session:
        row = CryptoProbeResult(
            appliance_id=appliance_id, snapshot_id=snapshot_id,
            job_id=job_id, host=host, port=port, probed_at=now,
            result=result)
        session.add(row)
        session.flush()
        return row.id


def latest_results(appliance_ids=None):
    """
    _function_: `mast.datapower.crypto.probe.latest_results(
    appliance_ids=None)`

    The results of the newest probe of each appliance (all of its ports
    have the time of that run, so a port that is no longer probed is
    left out), and the newest result of each port that belongs to no
    appliance: dicts with `id`, `appliance_id`, `host`, `port`,
    `probed_at`, `job_id` and `result`.
    """
    from sqlalchemy import and_, func

    from mast.jobs import db
    with db.session(write=False) as session:
        runs = select(CryptoProbeResult.appliance_id,
                      func.max(CryptoProbeResult.probed_at).label("at")) \
            .where(CryptoProbeResult.appliance_id.is_not(None)) \
            .group_by(CryptoProbeResult.appliance_id).subquery()
        query = select(CryptoProbeResult).join(runs, and_(
            runs.c.appliance_id == CryptoProbeResult.appliance_id,
            runs.c.at == CryptoProbeResult.probed_at))
        if appliance_ids is not None:
            query = query.where(
                CryptoProbeResult.appliance_id.in_(list(appliance_ids)))
        rows = list(session.scalars(query))
        if appliance_ids is None:
            newest = select(func.max(CryptoProbeResult.id)) \
                .where(CryptoProbeResult.appliance_id.is_(None)) \
                .group_by(CryptoProbeResult.host, CryptoProbeResult.port)
            rows += list(session.scalars(select(CryptoProbeResult).where(
                CryptoProbeResult.id.in_(newest))))
        found = {}
        for row in sorted(rows, key=lambda r: r.id):
            # Two results of one port at the same time: the later one
            found[(row.appliance_id, row.host, row.port)] = {
                "id": row.id, "appliance_id": row.appliance_id,
                "host": row.host, "port": row.port,
                "probed_at": row.probed_at, "job_id": row.job_id,
                "result": row.result or {}}
        return sorted(found.values(), key=lambda r: (
            r["appliance_id"] is None, r["appliance_id"] or 0, r["host"],
            r["port"]))


# The command
# ===========

class Probe(Params):
    appliances: list[str] = Option(
        default_factory=list, short="a",
        help="The registered appliances or environments to probe. The "
             "default is every registered appliance.")
    targets: list[str] = Option(
        default_factory=list, short="t",
        help="More ports to probe, as host:port. They belong to no "
             "appliance.")
    timeout: int = Option(10, help="The seconds to wait for each handshake.")
    workers: int = Option(8, short="w",
                          help="How many ports to probe at the same time.")

    @model_validator(mode="before")
    @classmethod
    def _every_appliance(cls, data):
        """With no appliances and no targets, every registered
        appliance."""
        if isinstance(data, dict) and not data.get("appliances") and \
                not data.get("targets"):
            from mast import registry
            found = [a.name for a in registry.list_appliances()]
            if not found:
                raise ValueError("there is no registered appliance; give "
                                 "the appliances or the targets")
            data = dict(data, appliances=found)
        return data

    @field_validator("targets")
    @classmethod
    def _targets(cls, value):
        for text in value:
            _target(text)
        return value

    @field_validator("workers", "timeout")
    @classmethod
    def _at_least_one(cls, value):
        if value < 1:
            raise ValueError("must be 1 or more")
        return value


def _summary(result):
    if not result.get("ok"):
        return result.get("error") or "no TLS"
    offers = result.get("offers") or {}
    hybrid = [g for g in ("X25519MLKEM768", "SecP256r1MLKEM768",
                          "SecP384r1MLKEM1024") if offers.get(g)]
    old = [p for p in ("TLSv1.0", "TLSv1.1") if offers.get(p)]
    parts = ["hybrid: " + (", ".join(hybrid) if hybrid else
                           "not available" if offers.get("X25519MLKEM768")
                           is None else "none")]
    if old:
        parts.append("old: " + ", ".join(old))
    return "; ".join(parts)


@cli.command("probe", category="inventory", exclusive=False, passive=True)
def probe(params: Probe):
    """Probe the TLS ports of the appliances as a peer would: the
listeners of the newest crypto inventory, the XML management and WebGUI
ports, and any host:port given. Records the protocol, cipher, group and
certificates, and which of TLS 1.0 to 1.3 and the hybrid PQC groups
each port accepts. Needs no credential. The group checks need Python
3.15."""
    from mast import registry
    from mast.jobs.runner import current_job

    from .posture import evaluate
    job_id = current_job()
    work = []
    snapshots = {}
    for appliance in registry.resolve(params.appliances):
        if appliance.id is None:
            raise ValueError("{} is not registered: give it as a target "
                             "(host:port)".format(appliance.name))
        targets, snapshot_id = targets_of(appliance)
        snapshots[appliance.id] = snapshot_id
        work += [(appliance, snapshot_id, host, port, target)
                 for host, port, target in targets]
    for text in params.targets:
        host, port = _target(text)
        work.append((None, None, host, port, {"kind": "extra",
                                              "name": text.strip()}))
    say("Probing {} port(s)".format(len(work)))
    # Every port of a run has the time of the run: the newest run of an
    # appliance is what it is now (latest_results)
    run_at = _now()

    def one(item):
        appliance, snapshot_id, host, port, target = item
        result = probe_port(host, port, timeout=params.timeout)
        result["target"] = target
        save(appliance.id if appliance else None, snapshot_id, host, port,
             result, run_at, job_id=job_id)
        return item, result

    with ThreadPoolExecutor(max_workers=params.workers) as pool:
        done = list(pool.map(one, work))
    for snapshot_id in filter(None, set(snapshots.values())):
        try:
            evaluate(snapshot_id)
        except Exception:
            make_logger("mast.crypto").exception(
                "The posture rules failed on snapshot {}".format(snapshot_id))
    rows, oks = [], []
    for (appliance, _, host, port, target), result in done:
        what = target.get("name", "")
        if target.get("kind") == "listener":
            what = "{} {} ({})".format(target["class"], target["name"],
                                       target["domain"])
        rows.append([appliance.name if appliance else "", host, str(port),
                     what, result.get("protocol") or "",
                     result.get("group") or "", _summary(result)])
        oks.append(True)
    return _Probed(rows, oks)


class _Probed(Table):
    HEADER = ("Appliance", "Host", "Port", "What", "Protocol", "Group",
              "Summary")

    def __init__(self, rows, oks):
        super().__init__(self.HEADER, rows)
        self._oks = oks

    def outcomes(self):
        return list(self._oks)

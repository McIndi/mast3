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
_module_: `mast.datapower.datapower.tls`

How MAST checks that it talks to the right appliance before it sends a
password: the TLS certificate of SOMA and the SSH host key.

Each appliance of the registry has a `tls_mode`:

* `ca`: The certificate must be signed by a trusted authority (the
`ca_bundle` of the appliance, or the trust store of the system) and be
for the address of the appliance.
* `pin`: The SHA-256 of the certificate must be `pin_sha256`, which
`mast-appliances pin` stored. This is for the self-signed certificates
that DataPower appliances often have.
* `none`: Nothing is checked. A stored credential is never sent this
way.

An address that is not registered is checked as `ca`, or not at all
with `--no-check-hostname` (`tls="none"`).

A pinned SSH host key (`ssh_host_key`) is checked the same way, see
`host_key_policy`.

The checks happen after the connection is made and before the request
is sent, so no credential leaves MAST for a certificate or a host key
that does not match.

A job that uses a stored credential runs inside `stored_credential()`
(of `mast.cli.results`).
Then `none` and plain `http` are refused, and so is SSH to an appliance
without a pinned host key.
"""
import base64
import hashlib
import hmac
import http.client
import io
import re
import socket
import ssl
import urllib.error
import urllib.parse
from dataclasses import dataclass

from mast.cli.results import stored_credential, using_stored_credential

__all__ = [
    "TLSError", "PinMismatch", "NotPinned", "Refused", "HostKeyMismatch",
    "Policy", "policy_for", "context_for", "check_peer", "post", "get",
    "stored_credential", "using_stored_credential", "fingerprint",
    "normalize_fingerprint", "fetch_certificate", "describe_certificate",
    "ssh_fingerprint", "normalize_ssh_pin", "fetch_host_key",
    "host_key_policy", "MODES",
]

MODES = ("ca", "pin", "none")




class TLSError(ConnectionError):
    """A connection that MAST refuses, before any credential is sent."""


class PinMismatch(TLSError):
    """The certificate is not the one that was pinned."""

    def __init__(self, name, expected, got):
        self.name = name
        self.expected = expected
        self.got = got
        super().__init__(
            "The certificate of {} changed (expected {}, got {}). An admin "
            "must check it and pin again.".format(name, expected, got))


class HostKeyMismatch(TLSError):
    """The SSH host key is not the one that was pinned."""

    def __init__(self, name, expected, got):
        self.name = name
        self.expected = expected
        self.got = got
        super().__init__(
            "The SSH host key of {} changed (expected {}, got {}). An admin "
            "must check it and pin again.".format(name, expected, got))


class NotPinned(TLSError):
    """A pin is needed, and there is none."""


class Refused(TLSError):
    """A connection that would send a stored credential unchecked."""


@dataclass(frozen=True)
class Policy:
    """
    _class_: `mast.datapower.datapower.tls.Policy`

    How to check one appliance: `mode` (one of `MODES`), the PEM
    `ca_bundle` for `ca` (None: the trust store of the system), the
    `pin_sha256` for `pin`, and the `name` for the messages. `registered`
    is True for an appliance of the registry.
    """
    mode: str = "ca"
    name: str = ""
    ca_bundle: str | None = None
    pin_sha256: str | None = None
    registered: bool = False


def policy_for(name, record=None, tls=None):
    """
    _function_: `mast.datapower.datapower.tls.policy_for(name, record=None, tls=None)`

    Return the `Policy` for the appliance `name`. `record` is its
    `mast.registry.Appliance`, or None if it is not registered. `tls`
    is what the caller asks for: None (what the registry says, or `ca`
    for an unregistered address), or one of `MODES`.

    `tls="none"` (`--no-check-hostname`) does not turn off a pin: a
    pinned certificate is always checked, as checking it works for a
    self-signed certificate too.
    """
    if tls is not None and tls not in MODES:
        raise ValueError("tls is one of {}".format(", ".join(MODES)))
    if record is None:
        mode = tls or "ca"
        return Policy(mode=mode, name=name)
    mode = record.tls_mode or "ca"
    if tls == "none" and mode != "pin":
        mode = "none"
    elif tls in ("ca", "pin"):
        mode = tls
    return Policy(mode=mode, name=name, ca_bundle=record.ca_bundle,
                  pin_sha256=record.pin_sha256, registered=True)


def _check_allowed(policy, scheme="https"):
    if not using_stored_credential():
        return
    if scheme != "https":
        raise Refused(
            "A stored credential is never sent to {} without TLS ({}). "
            "Set the SOMA scheme to https.".format(policy.name, scheme))
    if policy.mode == "none":
        raise Refused(
            "A stored credential is never sent to {} without checking its "
            "certificate. Pin it (mast-appliances pin -n {}) or give it a "
            "certificate that MAST trusts.".format(policy.name, policy.name))


def context_for(policy):
    """
    _function_: `mast.datapower.datapower.tls.context_for(policy)`

    Return the `ssl.SSLContext` for `policy`. For `pin` and `none` the
    context checks nothing; `check_peer` checks the pin.
    """
    if policy.mode == "ca":
        if policy.ca_bundle:
            return ssl.create_default_context(cadata=policy.ca_bundle)
        return ssl.create_default_context()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def fingerprint(der):
    """The SHA-256 of a DER certificate, as 64 lowercase hex digits."""
    return hashlib.sha256(der).hexdigest()


_HEX = re.compile(r"^[0-9a-f]{64}$")


def normalize_fingerprint(text):
    """
    Return the SHA-256 fingerprint `text` (hex, with or without colons
    or spaces, in either case) as 64 lowercase hex digits. Raises
    ValueError for anything else.
    """
    value = re.sub(r"[\s:]", "", str(text or "")).lower()
    if value.startswith("sha256="):
        value = value[len("sha256="):]
    if not _HEX.match(value):
        raise ValueError(
            "A SHA-256 fingerprint is 64 hex digits (colons are allowed): "
            "{!r}".format(text))
    return value


def check_peer(sock, policy):
    """
    _function_: `mast.datapower.datapower.tls.check_peer(sock, policy)`

    For `pin`, raise `PinMismatch` unless the certificate of the TLS
    socket `sock` is the pinned one, and `NotPinned` if there is no pin.
    Call it after the handshake and before anything is sent.
    """
    if policy.mode != "pin":
        return
    if not policy.pin_sha256:
        raise NotPinned(
            "The TLS check of {} is pin, but no certificate is pinned. Pin "
            "it with mast-appliances pin -n {}.".format(policy.name, policy.name))
    der = sock.getpeercert(binary_form=True)
    got = fingerprint(der or b"")
    expected = normalize_fingerprint(policy.pin_sha256)
    if not hmac.compare_digest(got, expected):
        _flag_mismatch(policy, "tls", got)
        raise PinMismatch(policy.name, expected, got)


def _flag_mismatch(policy, kind, got):
    """Remember the mismatch in the registry, for the Appliances page."""
    if not policy.registered:
        return
    try:
        # Imported here: the registry uses the job database
        from mast import registry
        registry.flag_mismatch(policy.name, kind, got)
    except Exception:
        # The mismatch is reported to the caller anyway
        pass


def _split(url):
    parts = urllib.parse.urlsplit(url)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return parts.scheme, parts.hostname, parts.port, path


def _request(policy, method, url, body=None, headers=None, timeout=None):
    """Connect, check the certificate, then send. Return the answer as
    bytes; an HTTP error status raises `urllib.error.HTTPError`, as
    `urllib.request.urlopen` does."""
    scheme, host, port, path = _split(url)
    _check_allowed(policy, scheme)
    if timeout is None:
        timeout = socket.getdefaulttimeout()
    if scheme == "https":
        connection = http.client.HTTPSConnection(
            host, port, timeout=timeout, context=context_for(policy))
    elif scheme == "http":
        connection = http.client.HTTPConnection(host, port, timeout=timeout)
    else:
        raise ValueError("The scheme is https or http: {!r}".format(url))
    try:
        try:
            connection.connect()
        except OSError as exc:
            raise urllib.error.URLError(exc)
        if scheme == "https":
            check_peer(connection.sock, policy)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            data = response.read()
        except (OSError, http.client.HTTPException) as exc:
            raise urllib.error.URLError(exc)
        if response.status >= 400:
            raise urllib.error.HTTPError(
                url, response.status, response.reason, response.headers,
                io.BytesIO(data))
        return response.status, data
    finally:
        connection.close()


def post(policy, url, body, headers=None, timeout=None):
    """
    _function_: `mast.datapower.datapower.tls.post(policy, url, body, headers=None, timeout=None)`

    POST `body` to `url`, checked as `policy` says, and return the
    body of the answer.
    """
    return _request(policy, "POST", url, body, headers, timeout)[1]


def get(policy, url, headers=None, timeout=None):
    """
    _function_: `mast.datapower.datapower.tls.get(policy, url, headers=None, timeout=None)`

    GET `url`, checked as `policy` says. Return `(status, body)`.
    """
    return _request(policy, "GET", url, None, headers, timeout)


def fetch_certificate(host, port, timeout=30):
    """
    _function_: `mast.datapower.datapower.tls.fetch_certificate(host, port, timeout=30)`

    Connect to `host:port` without checking anything and return the
    DER certificate that it shows. Nothing is sent but the TLS
    handshake.
    """
    context = context_for(Policy(mode="none"))
    with socket.create_connection((host, int(port)), timeout=timeout) as raw:
        with context.wrap_socket(raw, server_hostname=host) as sock:
            return sock.getpeercert(binary_form=True)


def describe_certificate(der):
    """
    _function_: `mast.datapower.datapower.tls.describe_certificate(der)`

    Return the subject, issuer, validity and SHA-256 of a DER
    certificate as a dict of strings.
    """
    from cryptography import x509
    cert = x509.load_der_x509_certificate(der)
    return {
        "subject": cert.subject.rfc4514_string(),
        "issuer": cert.issuer.rfc4514_string(),
        "not_before": cert.not_valid_before_utc.isoformat(),
        "not_after": cert.not_valid_after_utc.isoformat(),
        "self_signed": cert.subject == cert.issuer,
        "sha256": fingerprint(der),
    }


# SSH host keys
# =============


def ssh_fingerprint(key_type_and_base64):
    """
    Return the OpenSSH fingerprint (`SHA256:<base64 without padding>`)
    of a host key given as `"<type> <base64>"`.
    """
    parts = str(key_type_and_base64).split()
    if len(parts) < 2:
        raise ValueError("A host key is '<type> <base64>'")
    try:
        blob = base64.b64decode(parts[1], validate=True)
    except ValueError:
        raise ValueError("The host key is not base64")
    digest = hashlib.sha256(blob).digest()
    return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


_SSH_FP = re.compile(r"^SHA256:[A-Za-z0-9+/]{43}$")


def normalize_ssh_pin(text):
    """
    Return the pin of an SSH host key as it is stored: `"<type>
    <base64>"` for a key, or `SHA256:...` for an OpenSSH fingerprint.
    Raises ValueError for anything else.
    """
    value = str(text or "").strip()
    if value.startswith("SHA256:"):
        value = value.rstrip("=")
        if not _SSH_FP.match(value):
            raise ValueError(
                "An SSH fingerprint is SHA256: and 43 base64 characters, as "
                "ssh-keygen -l shows it: {!r}".format(text))
        return value
    parts = value.split()
    ssh_fingerprint(value)
    return "{} {}".format(parts[0], parts[1])


def _ssh_pin_fingerprint(pin):
    pin = normalize_ssh_pin(pin)
    return pin if pin.startswith("SHA256:") else ssh_fingerprint(pin)


def fetch_host_key(host, port, timeout=30):
    """
    _function_: `mast.datapower.datapower.tls.fetch_host_key(host, port, timeout=30)`

    Connect to the SSH server `host:port` and return its host key as
    `"<type> <base64>"`. Only the key exchange happens: there is no
    login.
    """
    import paramiko
    sock = socket.create_connection((host, int(port)), timeout=timeout)
    transport = paramiko.Transport(sock)
    try:
        transport.start_client(timeout=timeout)
        key = transport.get_remote_server_key()
        return "{} {}".format(key.get_name(), key.get_base64())
    finally:
        transport.close()
        sock.close()


def host_key_policy(name, pinned=None, registered=False, auto_add=True,
                    warn=None):
    """
    _function_: `mast.datapower.datapower.tls.host_key_policy(name, pinned=None, registered=False, auto_add=True, warn=None)`

    Return the paramiko `MissingHostKeyPolicy` for the appliance
    `name`. paramiko asks it after the key exchange and before the
    login, so a key that is refused never gets a password.

    * With `pinned` (the `ssh_host_key` of the registry), only that key
    is accepted; another one raises `HostKeyMismatch`.
    * Without a pin and inside `stored_credential()`, the connection is
    refused (`NotPinned`).
    * Otherwise any key is accepted if `auto_add` (`auto_add_keys` of
    ssh.conf), and `warn` is called with a message; if not, the key is
    refused.
    """
    import paramiko

    class PinnedHostKeyPolicy(paramiko.MissingHostKeyPolicy):
        def missing_host_key(self, client, hostname, key):
            got = ssh_fingerprint("{} {}".format(key.get_name(), key.get_base64()))
            if pinned:
                expected = _ssh_pin_fingerprint(pinned)
                if not hmac.compare_digest(got, expected):
                    _flag_mismatch(Policy(name=name, registered=registered),
                                   "ssh", got)
                    raise HostKeyMismatch(name, expected, got)
                return
            if using_stored_credential():
                raise NotPinned(
                    "A stored credential is never sent over SSH to {} before "
                    "its host key is pinned. Pin it with mast-appliances pin "
                    "-n {} --ssh.".format(name, name))
            if not auto_add:
                raise paramiko.SSHException(
                    "The SSH host key of {} is not pinned ({}), and "
                    "auto_add_keys of ssh.conf is false.".format(name, got))
            if warn is not None:
                warn("The SSH host key of {} is not checked ({}). Pin it with "
                     "mast-appliances pin --ssh.".format(name, got))

    return PinnedHostKeyPolicy()


# Pinning
# =======


def _endpoint(record, field):
    port = getattr(record, field)
    if port is None:
        # Imported here: the defaults are in MAST_HOME
        from mast.config import get_config
        port = get_config("appliances.conf").getint("global", field)
    return record.address.strip("[]"), int(port)


def preview(record, tls=True, ssh=False, timeout=15):
    """
    _function_: `mast.datapower.datapower.tls.preview(record, tls=True, ssh=False, timeout=15)`

    Connect to the appliance `record` (a `mast.registry.Appliance`) and
    return what an admin must see before pinning it:

    * `"tls"`: the subject, issuer, validity and `sha256` of the
    certificate of the SOMA port (see `describe_certificate`);
    * `"ssh"`: the `type`, the `key` (`"<type> <base64>"`) and the
    OpenSSH `fingerprint` of the SSH host key.

    Each one that cannot be read is `{"error": "..."}` instead. No
    credential is sent.
    """
    found = {}
    if tls:
        host, port = _endpoint(record, "soma_port")
        try:
            found["tls"] = describe_certificate(
                fetch_certificate(host, port, timeout))
            found["tls"]["port"] = port
        except Exception as exc:
            found["tls"] = {"error": "Could not read the certificate of "
                                     "{}:{}: {}: {}".format(
                                         host, port, type(exc).__name__, exc)}
    if ssh:
        host, port = _endpoint(record, "ssh_port")
        try:
            key = fetch_host_key(host, port, timeout)
            found["ssh"] = {"type": key.split()[0], "key": key,
                            "fingerprint": ssh_fingerprint(key),
                            "port": port}
        except Exception as exc:
            found["ssh"] = {"error": "Could not read the SSH host key of "
                                     "{}:{}: {}: {}".format(
                                         host, port, type(exc).__name__, exc)}
    return found

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
Unit tests for mast.trust: the TLS checks (ca, pin,
none) against a local TLS server, the SSH host key checks against a
local paramiko server, and that no credential is sent before a check
passes. Also `mast targets pin` and the pin API.
"""
import base64
import contextlib
import datetime
import io
import ipaddress
import os
import socket
import ssl
import tempfile
import threading
import unittest
import urllib.error
from unittest import mock

import paramiko
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from mast import registry
from mast.cli.results import stored_credential
from mast import trust as tls
from mast.datapower.datapower import DataPower
from mast.registry import cli as registry_cli
from mast.testsuite.unit.test_mast_jobs import PostgresMixin, SqliteCase
from mast.testsuite.unit.web_helpers import WebCase

SECRET = "Tls-Test-Secret-41"
AUTH = base64.b64encode("admin:{}".format(SECRET).encode()).decode()
ANSWER = b"<env:Envelope>datapower ok</env:Envelope>"


def _name(text):
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, text)])


def _certificate(subject, issuer_key, issuer_name, key, ca=False, san=None):
    now = datetime.datetime.now(datetime.timezone.utc)
    builder = (x509.CertificateBuilder()
               .subject_name(_name(subject)).issuer_name(issuer_name)
               .public_key(key.public_key())
               .serial_number(x509.random_serial_number())
               .not_valid_before(now - datetime.timedelta(minutes=5))
               .not_valid_after(now + datetime.timedelta(days=1))
               .add_extension(x509.BasicConstraints(ca=ca, path_length=None),
                              True))
    if ca:
        builder = builder.add_extension(x509.KeyUsage(
            digital_signature=True, key_cert_sign=True, crl_sign=True,
            content_commitment=False, key_encipherment=False,
            data_encipherment=False, key_agreement=False,
            encipher_only=False, decipher_only=False), True)
    else:
        builder = builder.add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), False)
    builder = builder.add_extension(
        x509.SubjectKeyIdentifier.from_public_key(key.public_key()), False)
    builder = builder.add_extension(
        x509.AuthorityKeyIdentifier.from_issuer_public_key(
            issuer_key.public_key()), False)
    if san is not None:
        builder = builder.add_extension(x509.SubjectAlternativeName(san), False)
    return builder.sign(issuer_key, hashes.SHA256())


class Certificates(object):
    """A test CA, a certificate from it for 127.0.0.1, one from it for
    another name, and a self-signed one (as DataPower has)."""

    def __init__(self, directory):
        ca_key = ec.generate_private_key(ec.SECP256R1())
        ca = _certificate("mast unit CA", ca_key, _name("mast unit CA"),
                          ca_key, ca=True)
        self.ca_pem = ca.public_bytes(serialization.Encoding.PEM).decode()
        self.files = {}
        self.der = {}
        for label, san in (
                ("good", [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
                ("other", [x509.DNSName("other.example.test")]),
                ("self", None)):
            key = ec.generate_private_key(ec.SECP256R1())
            if label == "self":
                cert = _certificate("webgui", key, _name("webgui"), key)
            else:
                cert = _certificate(label, ca_key, ca.subject, key, san=san)
            path = os.path.join(directory, label + ".pem")
            with open(path, "wb") as fout:
                fout.write(cert.public_bytes(serialization.Encoding.PEM))
                fout.write(key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption()))
            self.files[label] = path
            self.der[label] = cert.public_bytes(serialization.Encoding.DER)

    def sha256(self, label):
        return tls.fingerprint(self.der[label])


class TLSServer(object):
    """A TLS server on 127.0.0.1 that answers each request with ANSWER
    and keeps every byte that it received after the handshake."""

    def __init__(self, cert_file):
        self.received = []
        self.accepted = 0
        self.status = b"200 OK"
        self._context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self._context.load_cert_chain(cert_file)
        self._socket = socket.create_server(("127.0.0.1", 0))
        self.port = self._socket.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                raw, _ = self._socket.accept()
            except OSError:
                return
            self.accepted += 1
            threading.Thread(target=self._serve, args=(raw,), daemon=True).start()

    def _serve(self, raw):
        raw.settimeout(10)
        data = b""
        try:
            with self._context.wrap_socket(raw, server_side=True) as sock:
                while b"\r\n\r\n" not in data:
                    chunk = sock.recv(65536)
                    if not chunk:
                        break
                    data += chunk
                head, _, body = data.partition(b"\r\n\r\n")
                length = 0
                for line in head.split(b"\r\n"):
                    if line.lower().startswith(b"content-length:"):
                        length = int(line.split(b":", 1)[1])
                while len(body) < length:
                    chunk = sock.recv(65536)
                    if not chunk:
                        break
                    body += chunk
                    data += chunk
                if head:
                    sock.sendall(b"HTTP/1.1 %s\r\nContent-Length: %d\r\n"
                                 b"Connection: close\r\n\r\n%s"
                                 % (self.status, len(ANSWER), ANSWER))
        except (OSError, ssl.SSLError):
            pass
        finally:
            self.received.append(data)

    def url(self):
        return "https://127.0.0.1:{}/service/mgmt/current".format(self.port)

    def wait(self, count):
        """Wait until `count` connections are over, and return what
        they received."""
        import time
        deadline = time.monotonic() + 10
        while len(self.received) < count and time.monotonic() < deadline:
            time.sleep(0.02)
        return self.received

    def close(self):
        self._socket.close()

    def reset(self):
        self.wait(self.accepted)
        self.received.clear()
        self.accepted = 0

    def got_secret(self):
        self.wait(self.accepted)
        return any(AUTH.encode() in data or SECRET.encode() in data
                   for data in self.received)


class TLSCase(SqliteCase):

    @classmethod
    def setUpClass(cls):
        cls.certdir = tempfile.mkdtemp(prefix="mast-tls-test-")
        cls.certs = Certificates(cls.certdir)
        cls.servers = {}
        for label in ("good", "other", "self"):
            cls.servers[label] = TLSServer(cls.certs.files[label])

    @classmethod
    def tearDownClass(cls):
        for server in cls.servers.values():
            server.close()
        import shutil
        shutil.rmtree(cls.certdir, ignore_errors=True)

    def setUp(self):
        super().setUp()
        for server in self.servers.values():
            server.reset()

    def post(self, label, policy):
        return tls.post(policy, self.servers[label].url(), b"<request/>",
                        headers={"Authorization": "Basic " + AUTH}, timeout=10)


class TestTLS(TLSCase):

    def test_a_good_pin_sends_the_request(self):
        policy = tls.Policy(mode="pin", name="dp1",
                            pin_sha256=self.certs.sha256("self"))
        self.assertEqual(self.post("self", policy), ANSWER)
        self.assertTrue(self.servers["self"].got_secret())

    def test_a_pin_with_colons_and_capitals_is_the_same(self):
        pin = ":".join(self.certs.sha256("self").upper()[i:i + 2]
                       for i in range(0, 64, 2))
        self.assertEqual(tls.normalize_fingerprint(pin), self.certs.sha256("self"))

    def test_a_bad_pin_sends_nothing(self):
        policy = tls.Policy(mode="pin", name="dp1",
                            pin_sha256=self.certs.sha256("good"))
        with self.assertRaises(tls.PinMismatch) as caught:
            self.post("self", policy)
        self.assertEqual(caught.exception.got, self.certs.sha256("self"))
        self.assertIn("The certificate of dp1 changed", str(caught.exception))
        self.assertIn("pin again", str(caught.exception))
        self.assertFalse(self.servers["self"].got_secret())
        self.assertEqual(self.servers["self"].wait(1), [b""])

    def test_pin_without_a_pin_sends_nothing(self):
        with self.assertRaises(tls.NotPinned):
            self.post("self", tls.Policy(mode="pin", name="dp1"))
        self.assertFalse(self.servers["self"].got_secret())

    def test_ca_with_the_bundle_and_the_right_name(self):
        policy = tls.Policy(mode="ca", name="dp1", ca_bundle=self.certs.ca_pem)
        self.assertEqual(self.post("good", policy), ANSWER)

    def test_ca_with_the_wrong_name_sends_nothing(self):
        policy = tls.Policy(mode="ca", name="dp1", ca_bundle=self.certs.ca_pem)
        with self.assertRaises(urllib.error.URLError) as caught:
            self.post("other", policy)
        self.assertIsInstance(caught.exception.reason, ssl.SSLCertVerificationError)
        self.assertFalse(self.servers["other"].got_secret())

    def test_ca_without_a_bundle_refuses_a_self_signed_certificate(self):
        with self.assertRaises(urllib.error.URLError):
            self.post("self", tls.Policy(mode="ca", name="dp1"))
        self.assertFalse(self.servers["self"].got_secret())

    def test_none_checks_nothing(self):
        self.assertEqual(self.post("self", tls.Policy(mode="none")), ANSWER)

    def test_an_error_status_is_an_http_error(self):
        server = self.servers["self"]
        server.status = b"500 Internal Server Error"
        self.addCleanup(setattr, server, "status", b"200 OK")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post("self", tls.Policy(mode="none"))
        self.assertEqual(caught.exception.code, 500)
        self.assertEqual(caught.exception.read(), ANSWER)

    def test_with_a_stored_credential_none_and_http_are_refused(self):
        with stored_credential():
            with self.assertRaises(tls.Refused):
                self.post("self", tls.Policy(mode="none", name="dp1"))
            with self.assertRaises(tls.Refused):
                tls.post(tls.Policy(mode="pin", name="dp1",
                                    pin_sha256=self.certs.sha256("self")),
                         "http://127.0.0.1:{}/".format(self.servers["self"].port),
                         b"x", headers={"Authorization": "Basic " + AUTH})
            # A pin is fine
            self.assertEqual(self.post("self", tls.Policy(
                mode="pin", name="dp1",
                pin_sha256=self.certs.sha256("self"))), ANSWER)
        self.assertEqual(len(self.servers["self"].wait(1)), 1)

    def test_fetch_and_describe(self):
        der = tls.fetch_certificate("127.0.0.1", self.servers["self"].port)
        self.assertEqual(der, self.certs.der["self"])
        found = tls.describe_certificate(der)
        self.assertEqual(found["subject"], "CN=webgui")
        self.assertTrue(found["self_signed"])
        self.assertEqual(found["sha256"], self.certs.sha256("self"))
        self.assertFalse(self.servers["self"].got_secret())

    def test_bad_fingerprints(self):
        for bad in ("", "abc", "zz" * 32, "ab" * 33, None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                tls.normalize_fingerprint(bad)


class TestPolicy(SqliteCase):

    def test_the_mode_comes_from_the_registry(self):
        registry.add("dp1", "datapower")
        registry.add("dp2", "datapower")
        registry.set_trust("dp2", "soma", "none")
        registry.add("dp3", "datapower")
        registry.pin("dp3", "soma", "ab" * 32, "os:me")
        cases = [
            # name, tls asked, mode
            ("dp1", None, "ca"), ("dp1", "none", "none"),
            ("dp2", None, "none"), ("dp3", None, "pin"),
            # -n does not turn a pin off
            ("dp3", "none", "pin"),
            ("10.0.0.9", None, "ca"), ("10.0.0.9", "none", "none"),
        ]
        for name, asked, mode in cases:
            with self.subTest(name=name, asked=asked):
                record = registry.by_name(name)
                policy = tls.policy_for(name, record, asked, "soma")
                self.assertEqual(policy.mode, mode)
                self.assertEqual(policy.registered, record is not None)
        self.assertEqual(tls.policy_for("dp3", registry.get("dp3"),
                                        endpoint="soma").pin_sha256,
                         "ab" * 32)
        with self.assertRaises(ValueError):
            tls.policy_for("dp1", None, "maybe")

    def test_dataPower_and_environment_take_tls(self):
        from mast.datapower.datapower import Environment
        registry.add("dp1", "datapower")
        self.assertEqual(DataPower("dp1", "u:p").tls_policy.mode, "ca")
        self.assertEqual(DataPower("dp1", "u:p", check_hostname=False)
                         .tls_policy.mode, "none")
        self.assertEqual(DataPower("dp1", "u:p", tls="none").tls_policy.mode,
                         "none")
        env = Environment(["dp1"], ["u:p"], check_hostname=False)
        self.assertEqual(env.appliances[0].tls_policy.mode, "none")
        env = Environment(["dp1"], ["u:p"], tls="none")
        self.assertEqual(env.appliances[0].tls, "none")


class TestDataPowerSends(TLSCase):
    """DataPower sends SOMA requests and the firmware through the
    checks, and flags a pin that does not match."""

    def appliance(self, label, **pin):
        registry.add("dp1", "datapower", "127.0.0.1", attributes={
            "soma_port": self.servers[label].port, "retry_interval": 0})
        if pin:
            registry.pin("dp1", "soma", pin["sha256"], "os:me")
        return DataPower("dp1", "admin:" + SECRET)

    def test_soma_with_a_good_pin(self):
        dp = self.appliance("self", sha256=self.certs.sha256("self"))
        self.assertEqual(dp.request.send(policy=dp.tls_policy), ANSWER)
        self.assertTrue(self.servers["self"].got_secret())

    def test_soma_with_a_bad_pin_flags_the_appliance(self):
        dp = self.appliance("self", sha256=self.certs.sha256("good"))
        with self.assertRaises(tls.PinMismatch):
            dp.get_status("FirmwareVersion")
        self.assertFalse(self.servers["self"].got_secret())
        # Not tried again: nothing can change
        self.assertEqual(len(self.servers["self"].wait(1)), 1)
        found = registry.get("dp1").trust_for("soma")
        self.assertEqual(found.mismatch, self.certs.sha256("self"))
        self.assertIsNotNone(found.mismatch_at)
        # Pinning again clears the flag
        self.assertIsNone(registry.pin(
            "dp1", "soma", self.certs.sha256("self"), "os:me")
            .trust_for("soma").mismatch)

    def test_firmware_with_a_bad_pin_sends_nothing(self):
        dp = self.appliance("self", sha256=self.certs.sha256("good"))
        image = os.path.join(self.tmp, "image.scrypt4")
        with open(image, "wb") as fout:
            fout.write(b"firmware " + SECRET.encode())
        with self.assertRaises(tls.PinMismatch):
            dp.set_firmware(image, AcceptLicense=True, timeout=10)
        self.assertFalse(self.servers["self"].got_secret())

    def test_a_stored_credential_is_refused_without_a_check(self):
        registry.add("dp1", "datapower", "127.0.0.1", attributes={
            "soma_port": self.servers["self"].port, "retry_interval": 0})
        registry.set_trust("dp1", "soma", "none")
        dp = DataPower("dp1", "admin:" + SECRET)
        with stored_credential():
            with self.assertRaises(tls.Refused):
                dp.get_status("FirmwareVersion")
        self.assertEqual(self.servers["self"].received, [])

    def test_nothing_is_sent_to_a_pin_that_must_be_reviewed(self):
        port = self.servers["self"].port
        self.appliance("self", sha256=self.certs.sha256("self"))
        # Another port: the pin is no longer known to be right
        registry.update("dp1", attributes={"soma_port": port + 1})
        registry.update("dp1", attributes={"soma_port": port})
        found = registry.get("dp1").trust_for("soma")
        self.assertEqual(found.review, "the port changed")
        dp = DataPower("dp1", "admin:" + SECRET)
        with self.assertRaises(tls.NeedsReview) as caught:
            dp.get_status("FirmwareVersion")
        self.assertIn("mast targets pin -n dp1 -e soma", str(caught.exception))
        self.assertEqual(self.servers["self"].received, [])
        # An admin reviews it: the pin is kept, and MAST connects again
        registry.review("dp1", "soma", "os:me")
        dp = DataPower("dp1", "admin:" + SECRET)
        self.assertEqual(dp.request.send(policy=dp.tls_policy), ANSWER)

    def test_a_new_address_marks_every_pin(self):
        self.appliance("self", sha256=self.certs.sha256("self"))
        registry.pin("dp1", "ssh", "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKq0nO"
                     "+UdqqhTHmTsH+Xyrjz6iuN33t5b4bbGRMtahmh", "os:me")
        registry.update("dp1", address="127.0.0.2")
        target = registry.get("dp1")
        for endpoint in ("soma", "ssh"):
            self.assertIn("the address changed from 127.0.0.1 to 127.0.0.2",
                          target.trust_for(endpoint).review)


class TestDataPowerSendsPostgres(PostgresMixin, TestDataPowerSends):
    pass


# SSH
# ===


class _SSHServer(paramiko.ServerInterface):
    def __init__(self, passwords):
        self.passwords = passwords

    def check_auth_password(self, username, password):
        self.passwords.append(password)
        return paramiko.AUTH_FAILED

    def get_allowed_auths(self, username):
        return "password"


class SSHServer(object):
    """An SSH server on 127.0.0.1 with a new host key. It refuses every
    login and keeps the passwords that it was sent."""

    def __init__(self):
        self.key = paramiko.ECDSAKey.generate()
        self.passwords = []
        self._socket = socket.create_server(("127.0.0.1", 0))
        self.port = self._socket.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    def host_key(self):
        return "{} {}".format(self.key.get_name(), self.key.get_base64())

    def _accept(self):
        while True:
            try:
                raw, _ = self._socket.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(raw,), daemon=True).start()

    def _serve(self, raw):
        transport = paramiko.Transport(raw)
        transport.add_server_key(self.key)
        try:
            transport.start_server(server=_SSHServer(self.passwords))
            transport.accept(5)
        except Exception:
            pass
        finally:
            transport.close()

    def close(self):
        self._socket.close()


class SSHCase(SqliteCase):

    @classmethod
    def setUpClass(cls):
        cls.server = SSHServer()

    @classmethod
    def tearDownClass(cls):
        cls.server.close()

    def setUp(self):
        super().setUp()
        self.server.passwords.clear()

    def connect(self, policy):
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(policy)
        self.addCleanup(client.close)
        client.connect("127.0.0.1", port=self.server.port, username="admin",
                       password=SECRET, timeout=10, look_for_keys=False,
                       allow_agent=False)


class TestSSH(SSHCase):

    def test_fingerprints(self):
        key = self.server.host_key()
        fingerprint = tls.ssh_fingerprint(key)
        self.assertRegex(fingerprint, r"^SHA256:[A-Za-z0-9+/]{43}$")
        self.assertEqual(tls.normalize_ssh_pin(fingerprint + "="), fingerprint)
        self.assertEqual(tls.normalize_ssh_pin(key + " comment"), key)
        for bad in ("", "SHA256:short", "ssh-ed25519", "ssh-ed25519 !!!"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                tls.normalize_ssh_pin(bad)

    def test_fetch_host_key(self):
        self.assertEqual(tls.fetch_host_key("127.0.0.1", self.server.port),
                         self.server.host_key())
        self.assertEqual(self.server.passwords, [])

    def test_the_pinned_key_gets_the_password(self):
        for pin in (self.server.host_key(),
                    tls.ssh_fingerprint(self.server.host_key())):
            with self.subTest(pin=pin[:12]):
                self.server.passwords.clear()
                with self.assertRaises(paramiko.AuthenticationException):
                    self.connect(tls.host_key_policy("dp1", pinned=pin))
                self.assertEqual(self.server.passwords, [SECRET])

    def test_another_key_gets_nothing(self):
        other = paramiko.ECDSAKey.generate()
        pin = "{} {}".format(other.get_name(), other.get_base64())
        with self.assertRaises(tls.HostKeyMismatch) as caught:
            self.connect(tls.host_key_policy("dp1", pinned=pin))
        self.assertEqual(caught.exception.got,
                         tls.ssh_fingerprint(self.server.host_key()))
        self.assertIn("The SSH host key of dp1 changed", str(caught.exception))
        self.assertEqual(self.server.passwords, [])

    def test_no_pin_with_a_stored_credential_gets_nothing(self):
        with stored_credential():
            with self.assertRaises(tls.NotPinned):
                self.connect(tls.host_key_policy("dp1", registered=True))
        self.assertEqual(self.server.passwords, [])

    def test_no_pin_with_an_ad_hoc_credential_warns(self):
        warnings = []
        with self.assertRaises(paramiko.AuthenticationException):
            self.connect(tls.host_key_policy("10.0.0.9", warn=warnings.append))
        self.assertEqual(self.server.passwords, [SECRET])
        self.assertEqual(len(warnings), 1)
        self.assertIn("not checked", warnings[0])

    def test_no_pin_and_no_auto_add_is_refused(self):
        with self.assertRaises(paramiko.SSHException):
            self.connect(tls.host_key_policy("10.0.0.9", auto_add=False))
        self.assertEqual(self.server.passwords, [])

    def test_ssh_connect_checks_the_pin_and_flags_a_change(self):
        other = paramiko.ECDSAKey.generate()
        registry.add("dp1", "datapower", "127.0.0.1", attributes={"ssh_port": self.server.port})
        registry.pin("dp1", "ssh", "{} {}".format(other.get_name(),
                                               other.get_base64()), "os:me")
        dp = DataPower("dp1", "admin:" + SECRET)
        with self.assertRaises(tls.HostKeyMismatch):
            dp.ssh_connect(timeout=10)
        self.assertEqual(self.server.passwords, [])
        self.assertEqual(registry.get("dp1").trust_for("ssh").mismatch,
                         tls.ssh_fingerprint(self.server.host_key()))

    def test_ssh_connect_without_a_pin_with_a_stored_credential(self):
        registry.add("dp1", "datapower", "127.0.0.1", attributes={"ssh_port": self.server.port})
        dp = DataPower("dp1", "admin:" + SECRET)
        with stored_credential():
            with self.assertRaises(tls.NotPinned) as caught:
                dp.ssh_connect(timeout=10)
        self.assertIn("mast targets pin -n dp1 -e ssh", str(caught.exception))
        self.assertEqual(self.server.passwords, [])


# mast targets pin and the API
# ===============================


class TestPinCommand(TLSCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ssh = SSHServer()

    @classmethod
    def tearDownClass(cls):
        cls.ssh.close()
        super().tearDownClass()

    def run_cli(self, *args, answer="y\n"):
        out = io.StringIO()
        code = 0
        with contextlib.redirect_stdout(out), \
                mock.patch("builtins.input",
                           side_effect=lambda prompt: (print(prompt, end=""),
                                                       answer)[1]):
            try:
                registry_cli.targets.run(list(args))
            except SystemExit as exc:
                code = exc.code or 0
        return code, out.getvalue()

    def setUp(self):
        super().setUp()
        registry.add("dp1", "datapower", "127.0.0.1", attributes={"soma_port": self.servers["self"].port, "ssh_port": self.ssh.port})

    def test_pin_shows_the_certificate_and_asks(self):
        code, out = self.run_cli("pin", "-n", "dp1", "-e", "soma", answer="n")
        self.assertEqual(code, 0)
        self.assertIn("Subject:     CN=webgui", out)
        self.assertIn(self.certs.sha256("self"), out)
        self.assertIn("soma of dp1 was not pinned", out)
        self.assertEqual(registry.get("dp1").trust_for("soma").mode, "ca")
        code, out = self.run_cli("pin", "-n", "dp1", "-e", "soma")
        found = registry.get("dp1").trust_for("soma")
        self.assertEqual((found.mode, found.pin),
                         ("pin", self.certs.sha256("self")))
        self.assertTrue(found.pinned_by.startswith("os:"))
        self.assertFalse(self.servers["self"].got_secret())

    def test_pin_every_endpoint_and_unpin(self):
        code, out = self.run_cli("pin", "-n", "dp1", "-y")
        self.assertEqual(code, 0, out)
        self.assertIn(tls.ssh_fingerprint(self.ssh.host_key()), out)
        found = registry.get("dp1")
        self.assertEqual(found.trust_for("ssh").pin, self.ssh.host_key())
        self.assertEqual(found.trust_for("soma").mode, "pin")
        code, out = self.run_cli("list")
        self.assertRegex(out, r"dp1 +datapower .*soma:pin ssh:pin")
        self.run_cli("unpin", "-n", "dp1", "-e", "ssh")
        found = registry.get("dp1")
        self.assertIsNone(found.trust_for("ssh").pin)
        self.assertEqual(found.trust_for("ssh").mode, "none")
        self.assertEqual(found.trust_for("soma").mode, "pin")
        self.run_cli("unpin", "-n", "dp1", "-e", "soma")
        self.assertEqual(registry.get("dp1").trust_for("soma").mode, "ca")
        self.assertEqual(self.ssh.passwords, [])

    def test_pin_a_fingerprint_without_connecting(self):
        registry.add("far", "datapower", "192.0.2.1")
        pin = self.certs.sha256("good").upper()
        code, out = self.run_cli("pin", "-n", "far", "-e", "soma", "-f", pin)
        self.assertEqual(code, 0, out)
        self.assertEqual(registry.get("far").trust_for("soma").pin,
                         pin.lower())
        fingerprint = tls.ssh_fingerprint(self.ssh.host_key())
        code, out = self.run_cli("pin", "-n", "far", "-e", "ssh",
                                 "-f", fingerprint)
        self.assertEqual(registry.get("far").trust_for("ssh").pin,
                         fingerprint)
        for args in (("-e", "soma", "-f", "nope"),
                     ("-e", "ssh", "-f", "ab" * 32), ("-f", pin),
                     ("-e", "nothing", "-f", pin)):
            with self.subTest(args=args):
                code, _ = self.run_cli("pin", "-n", "far", *args)
                self.assertEqual(code, 1)

    def test_pin_an_appliance_that_does_not_answer(self):
        registry.update("dp1", attributes={"soma_port": self._free_port()})
        code, out = self.run_cli("pin", "-n", "dp1", "-e", "soma", "-y")
        self.assertEqual(code, 1)
        self.assertIn("Could not read the certificate", out)
        self.assertEqual(registry.get("dp1").trust_for("soma").mode, "ca")

    def test_ca_bundle_file(self):
        path = os.path.join(self.tmp, "ca.pem")
        with open(path, "w") as fout:
            fout.write(self.certs.ca_pem)
        code, out = self.run_cli("trust", "-n", "dp1", "-e", "soma",
                                 "--ca-bundle-file", path)
        self.assertEqual(code, 0, out)
        self.assertIn("soma (tls): ca, 1 CA certificate(s)", out)
        self.assertEqual(registry.get("dp1").trust_for("soma").ca_bundle,
                         self.certs.ca_pem)
        code, out = self.run_cli("trust", "-n", "dp1", "-e", "soma",
                                 "--system-ca", "-m", "none")
        self.assertEqual(code, 0, out)
        found = registry.get("dp1").trust_for("soma")
        self.assertEqual((found.mode, found.ca_bundle), ("none", None))
        code, _ = self.run_cli("trust", "-n", "dp1", "-e", "soma", "-m", "pin")
        self.assertEqual(code, 1)

    def test_review_keeps_a_pin_that_is_still_right(self):
        self.run_cli("pin", "-n", "dp1", "-e", "soma", "-y")
        # The port is changed and back: the pin must be reviewed
        port = registry.get("dp1").attributes["soma_port"]
        registry.update("dp1", attributes={"soma_port": port + 1})
        code, out = self.run_cli("update", "-n", "dp1", "-s",
                                 "soma_port={}".format(port))
        self.assertIn("REVIEW: the port changed", out)
        code, out = self.run_cli("review", "-n", "dp1", "-e", "soma", "-y")
        self.assertEqual(code, 0, out)
        self.assertIn("Kept the pin of soma of dp1", out)
        self.assertIsNone(registry.get("dp1").trust_for("soma").review)

    def test_review_refuses_another_certificate(self):
        registry.pin("dp1", "soma", self.certs.sha256("good"), "os:me")
        registry.update("dp1", address="localhost")
        code, out = self.run_cli("review", "-n", "dp1", "-e", "soma", "-y")
        self.assertEqual(code, 1)
        self.assertIsNotNone(registry.get("dp1").trust_for("soma").review)

    @staticmethod
    def _free_port():
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]


class TestPinApi(WebCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.certdir = tempfile.mkdtemp(prefix="mast-tls-test-")
        cls.certs = Certificates(cls.certdir)
        cls.tls_server = TLSServer(cls.certs.files["self"])
        cls.ssh = SSHServer()

    @classmethod
    def tearDownClass(cls):
        cls.tls_server.close()
        cls.ssh.close()
        import shutil
        shutil.rmtree(cls.certdir, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        registry.add("dp1", "datapower", "127.0.0.1", attributes={"soma_port": self.tls_server.port, "ssh_port": self.ssh.port})

    def call(self, method, path, body=None):
        return self.client.open("/api/v1" + path, method=method, json=body,
                                headers={"X-CSRF-Token": self.token()})

    def test_an_admin_previews_and_pins(self):
        self.login("admin")
        answer = self.call("POST", "/targets/dp1/pin/preview", {})
        self.assertEqual(answer.status_code, 200)
        found = answer.get_json()
        self.assertEqual(found["soma"]["sha256"], self.certs.sha256("self"))
        self.assertEqual(found["ssh"]["key"], self.ssh.host_key())
        answer = self.call("POST", "/targets/dp1/pin", {"pins": [
            {"endpoint": "soma", "value": found["soma"]["sha256"]},
            {"endpoint": "ssh", "value": found["ssh"]["key"]}]})
        self.assertEqual(answer.status_code, 200, answer.get_json())
        trust = {t["endpoint"]: t for t in answer.get_json()["trust"]}
        self.assertEqual(trust["soma"]["mode"], "pin")
        self.assertEqual(trust["soma"]["pinned_by"], "admin")
        self.assertEqual(registry.get("dp1").trust_for("ssh").pin,
                         self.ssh.host_key())
        for body, status in (
                ({"pins": [{"endpoint": "soma", "value": "nope"}]}, 422),
                ({}, 422),
                ({"pins": [{"endpoint": "nothing", "value": "ab" * 32}]}, 422)):
            with self.subTest(body=body):
                self.assertEqual(self.call("POST", "/targets/dp1/pin",
                                           body).status_code, status)
        self.assertEqual(self.call("POST", "/targets/nothing/pin", {
            "pins": [{"endpoint": "soma", "value": "ab" * 32}]}).status_code,
            404)
        answer = self.call("POST", "/targets/dp1/unpin", {"endpoint": "soma"})
        trust = {t["endpoint"]: t for t in answer.get_json()["trust"]}
        self.assertEqual(trust["soma"]["mode"], "ca")
        self.assertEqual(trust["ssh"]["mode"], "pin")
        self.assertEqual(self.call("POST", "/targets/dp1/unpin",
                                   {"endpoint": "nothing"}).status_code, 422)

    def test_only_an_admin_pins(self):
        for role in ("operator", "viewer"):
            with self.subTest(role=role):
                self.login(role)
                for path, body in (
                        ("/targets/dp1/pin/preview", {}),
                        ("/targets/dp1/pin", {"pins": [
                            {"endpoint": "soma", "value": "ab" * 32}]}),
                        ("/targets/dp1/unpin", {"endpoint": "soma"}),
                        ("/targets/dp1/review", {"endpoint": "soma"})):
                    self.assertEqual(self.call("POST", path, body).status_code,
                                     403)
        self.assertEqual(registry.get("dp1").trust_for("soma").mode, "ca")


class TestMigration(SqliteCase):
    """The pins and flags go down to the appliances of 3.x (0005), and up
    again to the trust of the endpoints (4001)."""

    def columns(self):
        # An engine that does not upgrade the tables, as db.engine() does
        from sqlalchemy import inspect, text
        from mast.jobs import db
        engine = db._create_engine(self.url)
        self.addCleanup(engine.dispose)
        with engine.connect() as connection:
            names = {c["name"] for c in inspect(connection).get_columns(
                "appliances")}
            rows = connection.execute(text(
                "SELECT name, pin_sha256, tls_mode, ssh_host_key, soma_port "
                "FROM appliances")).all()
        return names, [tuple(row) for row in rows]

    def test_down_and_up(self):
        from mast.jobs import db
        registry.add("dp1", "datapower", "10.0.0.1",
                     attributes={"soma_port": 5551})
        registry.pin("dp1", "soma", "ab" * 32, "os:me")
        registry.pin("dp1", "ssh", "SHA256:" + "A" * 43, "os:me")
        registry.flag_mismatch("dp1", "soma", "cd" * 32)
        registry.flag_mismatch("dp1", "ssh", "SHA256:x")
        db.downgrade("0005")
        self.assertEqual(db.revision(), "0005")
        names, rows = self.columns()
        self.assertFalse({"tls_mismatch", "ssh_mismatch"} & names)
        # The pins and the settings stay
        self.assertEqual(rows, [("dp1", "ab" * 32, "pin", "SHA256:" + "A" * 43,
                                 5551)])
        # A newer MAST upgrades the tables again when it opens them
        db.configure(self.url)
        found = registry.get("dp1")
        self.assertEqual(db.revision(), db.HEAD)
        self.assertEqual((found.kind, found.attributes),
                         ("datapower", {"soma_port": 5551, "poll": False}))
        soma, ssh = found.trust_for("soma"), found.trust_for("ssh")
        self.assertEqual((soma.mode, soma.pin, soma.mismatch),
                         ("pin", "ab" * 32, None))
        self.assertEqual((ssh.mode, ssh.pin), ("pin", "SHA256:" + "A" * 43))


class TestMigrationPostgres(PostgresMixin, TestMigration):
    pass


if __name__ == "__main__":
    unittest.main()

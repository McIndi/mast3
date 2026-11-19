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
A slow stand-in for the SOMA interface of a DataPower appliance.

The tests of the installed product need commands that take a known
time, so that they can cancel a job or stop a worker while it runs.
`FakeSoma` answers every request with the status of two domains
(`default` and `fake_domain`), after `delay` seconds, over HTTPS with
a certificate that it makes. Run the commands with `-n` (no hostname
check).
"""
import datetime
import http.server
import os
import ssl
import tempfile
import threading
import time

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

__all__ = ["FakeSoma", "DOMAINS"]

DOMAINS = ("default", "fake_domain")

_RESPONSE = """<?xml version="1.0" encoding="UTF-8"?>
<env:Envelope xmlns:env="http://schemas.xmlsoap.org/soap/envelope/">
<env:Body>
<dp:response xmlns:dp="http://www.datapower.com/schemas/management">
<dp:timestamp>2026-01-01T00:00:00-00:00</dp:timestamp>
<dp:status>
{}
</dp:status>
</dp:response>
</env:Body>
</env:Envelope>
""".format("\n".join(
    "<DomainStatus><Domain>{}</Domain></DomainStatus>".format(name)
    for name in DOMAINS))


def _certificate(directory):
    """Write a self-signed certificate and its key. Return their paths."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=1))
            .sign(key, hashes.SHA256()))
    cert_path = os.path.join(directory, "fake-soma.crt")
    key_path = os.path.join(directory, "fake-soma.key")
    with open(cert_path, "wb") as fout:
        fout.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(key_path, "wb") as fout:
        fout.write(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()))
    return cert_path, key_path


class FakeSoma(object):
    """
    Answer SOMA requests on `127.0.0.1:port` after `delay` seconds.

    Use it as a context manager. `requests` counts the requests that
    were answered or are being answered.
    """
    def __init__(self, delay=0.0):
        self.delay = delay
        self.requests = 0
        self._lock = threading.Lock()
        fake = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(length)
                with fake._lock:
                    fake.requests += 1
                time.sleep(fake.delay)
                body = _RESPONSE.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/xml")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self._tmp = tempfile.TemporaryDirectory(prefix="mast-fake-soma-")
        cert, key = _certificate(self._tmp.name)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._server.socket = context.wrap_socket(
            self._server.socket, server_side=True)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()
        self._tmp.cleanup()

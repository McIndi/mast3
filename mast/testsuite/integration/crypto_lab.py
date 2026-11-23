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
A domain of crypto objects on a DataPower container, for the tests of
the crypto inventory: files, keys, certificates, credentials, TLS
profiles and a listener that uses them, made through SOMA.

`make_lab(dp, domain)` makes the domain (again, if it is there) and
returns `LAB`, the names of what it made. Nothing in it is secret: the
keys are made for the test and thrown away with the domain.
"""
import base64
import datetime
import re

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import NameOID
from lxml import etree

__all__ = ["make_lab", "LAB", "PLAINTEXT_PASSWORD", "ALIAS_PASSWORD"]

MGMT = "http://www.datapower.com/schemas/management"

# Two secrets that the inventory must never keep
PLAINTEXT_PASSWORD = "Lab-Plain-Key-Pass-1"
ALIAS_PASSWORD = "Lab-Alias-Pass-1"

LAB = {
    "files": ["lab-ca.crt", "lab-server.crt", "lab-server.key",
              "lab-old.crt", "lab-old.key", "lab-unused.crt"],
    "CryptoCertificate": ["lab-ca", "lab-server-cert", "lab-old-cert"],
    "CryptoKey": ["lab-server-key", "lab-old-key"],
    "CryptoIdentCred": ["lab-idcred"],
    "CryptoValCred": ["lab-valcred"],
    "SSLServerProfile": ["lab-server-profile"],
    "SSLClientProfile": ["lab-client-profile"],
    "PasswordAlias": ["lab-alias"],
    "HTTPSSourceProtocolHandler": ["lab-https"],
}


def _name(common_name):
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])


def _certificate(subject, key, issuer, issuer_key, days=365, ca=False,
                 san=None, expired=False):
    now = datetime.datetime.now(datetime.timezone.utc)
    start = now - datetime.timedelta(days=400 if expired else 1)
    end = now - datetime.timedelta(days=30) if expired \
        else now + datetime.timedelta(days=days)
    builder = (x509.CertificateBuilder()
               .subject_name(_name(subject)).issuer_name(issuer)
               .public_key(key.public_key())
               .serial_number(x509.random_serial_number())
               .not_valid_before(start).not_valid_after(end)
               .add_extension(x509.BasicConstraints(ca=ca, path_length=None),
                              True))
    if san:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.DNSName(n) for n in san]),
            False)
    return builder.sign(issuer_key, hashes.SHA256())


def _pem(cert):
    return cert.public_bytes(serialization.Encoding.PEM)


def _key_pem(key, password=None):
    algorithm = serialization.BestAvailableEncryption(password.encode()) \
        if password else serialization.NoEncryption()
    return key.private_bytes(serialization.Encoding.PEM,
                             serialization.PrivateFormat.TraditionalOpenSSL
                             if password else serialization.PrivateFormat.PKCS8,
                             algorithm)


def _send(dp, domain, body, kind="set-config"):
    """Send `<man:KIND>BODY</man:KIND>` to `domain`; return the text of
    the response."""
    dp.domain = domain
    dp.request.clear()
    request = dp.request.request
    request.set("domain", domain)
    request.append(etree.fromstring(
        '<man:{0} xmlns:man="{1}">{2}</man:{0}>'.format(kind, MGMT, body)))
    response = dp.send_request()
    text = response.text if hasattr(response, "text") else str(response)
    if isinstance(text, bytes):
        text = text.decode()
    if not re.search(r"<dp:result>\s*OK\s*</dp:result>", text):
        raise RuntimeError("{} in {} failed: {}".format(kind, domain, text[:800]))
    return text


def make_lab(dp, domain):
    """Make `domain` with the objects of `LAB`. Return `LAB`."""
    if domain in dp.domains:
        dp.del_domain(domain)
    assert dp.add_domain(domain)

    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca = _certificate("MAST lab CA", ca_key, _name("MAST lab CA"), ca_key,
                      days=3650, ca=True)
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    server = _certificate("lab.example.test", server_key, ca.subject, ca_key,
                          san=["lab.example.test", "lab2.example.test"])
    old_key = ec.generate_private_key(ec.SECP384R1())
    old = _certificate("old.example.test", old_key, _name("old.example.test"),
                       old_key, expired=True)
    unused = _certificate("unused.example.test", old_key,
                          _name("unused.example.test"), old_key)
    files = {
        "lab-ca.crt": _pem(ca), "lab-server.crt": _pem(server),
        "lab-server.key": _key_pem(server_key),
        "lab-old.crt": _pem(old),
        "lab-old.key": _key_pem(old_key, PLAINTEXT_PASSWORD),
        "lab-unused.crt": _pem(unused),
    }
    for name, data in files.items():
        dp._set_file(base64.b64encode(data).decode(), "cert:///" + name, domain)

    objects = [
        '<PasswordAlias name="lab-alias"><Password>{}</Password>'
        '</PasswordAlias>'.format(ALIAS_PASSWORD),
        '<CryptoKey name="lab-server-key"><Filename>cert:///lab-server.key'
        '</Filename></CryptoKey>',
        # A legacy plaintext password, which the inventory must drop
        '<CryptoKey name="lab-old-key"><Filename>cert:///lab-old.key'
        '</Filename><Password>{}</Password><PasswordAlias>off'
        '</PasswordAlias></CryptoKey>'.format(PLAINTEXT_PASSWORD),
        '<CryptoCertificate name="lab-ca"><Filename>cert:///lab-ca.crt'
        '</Filename></CryptoCertificate>',
        '<CryptoCertificate name="lab-server-cert"><Filename>'
        'cert:///lab-server.crt</Filename></CryptoCertificate>',
        '<CryptoCertificate name="lab-old-cert"><Filename>cert:///lab-old.crt'
        '</Filename><IgnoreExpiration>on</IgnoreExpiration>'
        '</CryptoCertificate>',
        '<CryptoIdentCred name="lab-idcred"><Key class="CryptoKey">'
        'lab-server-key</Key><Certificate class="CryptoCertificate">'
        'lab-server-cert</Certificate><CA class="CryptoCertificate">lab-ca'
        '</CA></CryptoIdentCred>',
        '<CryptoValCred name="lab-valcred"><Certificate '
        'class="CryptoCertificate">lab-ca</Certificate></CryptoValCred>',
        '<SSLServerProfile name="lab-server-profile"><Protocols>'
        '<TLSv1d2>on</TLSv1d2><TLSv1d3>on</TLSv1d3></Protocols>'
        '<Ciphers>AES_256_GCM_SHA384</Ciphers>'
        '<Ciphers>ECDHE_RSA_WITH_AES_256_GCM_SHA384</Ciphers>'
        '<Idcred class="CryptoIdentCred">lab-idcred</Idcred>'
        '</SSLServerProfile>',
        '<SSLClientProfile name="lab-client-profile"><Protocols>'
        '<TLSv1d2>on</TLSv1d2><TLSv1d3>on</TLSv1d3></Protocols>'
        '<Ciphers>AES_256_GCM_SHA384</Ciphers>'
        '<Ciphers>ECDHE_RSA_WITH_AES_256_GCM_SHA384</Ciphers>'
        '<Valcred class="CryptoValCred">lab-valcred</Valcred>'
        '</SSLClientProfile>',
        '<HTTPSSourceProtocolHandler name="lab-https"><mAdminState>enabled'
        '</mAdminState><LocalAddress>0.0.0.0</LocalAddress><LocalPort>18443'
        '</LocalPort><SSLServerConfigType>server</SSLServerConfigType>'
        '<SSLServer class="SSLServerProfile">lab-server-profile</SSLServer>'
        '</HTTPSSourceProtocolHandler>',
    ]
    for body in objects:
        _send(dp, domain, body)
    return LAB

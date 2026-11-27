"""Put crypto config on the lab DataPowers, with some weak spots on purpose.

Usage: mast.bat labcrypto.py PORT LISTEN_BASE [--no-save-extra]
  PORT is the SOMA port on 127.0.0.1, LISTEN_BASE the first of two HTTPS
  ports (modern, then legacy).
"""
import base64
import sys
from datetime import datetime, timedelta, timezone

import requests
import urllib3
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import NameOID

urllib3.disable_warnings()
PORT, BASE = int(sys.argv[1]), int(sys.argv[2])
URL = "https://127.0.0.1:{}/service/mgmt/current".format(PORT)
AUTH = ("admin", "admin")
NOW = datetime.now(timezone.utc)


def soma(body, domain="default"):
    xml = ('<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">'
           '<soapenv:Body><dp:request domain="{}" '
           'xmlns:dp="http://www.datapower.com/schemas/management">{}'
           '</dp:request></soapenv:Body></soapenv:Envelope>').format(domain, body)
    r = requests.post(URL, data=xml.encode(), auth=AUTH, verify=False, timeout=60)
    ok = "<dp:result>" in r.text and "OK" in r.text.split("<dp:result>", 1)[1][:40]
    return ok, r.text


def name(cn):
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, "MAST lab")])


def cert(cn, key, issuer_key, issuer_name, start, end, alg=hashes.SHA256(),
         ca=False, san=None):
    b = (x509.CertificateBuilder().subject_name(name(cn)).issuer_name(issuer_name)
         .public_key(key.public_key()).serial_number(x509.random_serial_number())
         .not_valid_before(start).not_valid_after(end)
         .add_extension(x509.BasicConstraints(ca=ca, path_length=None), True))
    if san:
        b = b.add_extension(x509.SubjectAlternativeName(
            [x509.DNSName(s) for s in san]), False)
    return b.sign(issuer_key, alg)


def pem_cert(c):
    return c.public_bytes(serialization.Encoding.PEM)


def pem_key(k, password=None):
    enc = serialization.BestAvailableEncryption(password) if password \
        else serialization.NoEncryption()
    return k.private_bytes(serialization.Encoding.PEM,
                           serialization.PrivateFormat.TraditionalOpenSSL, enc)


def upload(path, data):
    ok, text = soma('<dp:set-file name="{}">{}</dp:set-file>'.format(
        path, base64.b64encode(data).decode()))
    print("file", path, "ok" if ok else text[-400:])


def config(xml, label):
    ok, text = soma("<dp:set-config>{}</dp:set-config>".format(xml))
    print("config", label, "ok" if ok else text[-600:])


host = "dp{}".format(PORT - 5550)
ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
ca = cert("MAST lab CA", ca_key, ca_key, name("MAST lab CA"),
          NOW - timedelta(days=30), NOW + timedelta(days=1825), ca=True)
server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
server = cert(host + ".lab", server_key, ca_key, ca.subject,
              NOW - timedelta(days=1), NOW + timedelta(days=365),
              san=[host + ".lab", "localhost"])
ec_key = ec.generate_private_key(ec.SECP384R1())
ec_cert = cert(host + "-ec.lab", ec_key, ec_key, name(host + "-ec.lab"),
               NOW - timedelta(days=1), NOW + timedelta(days=365),
               alg=hashes.SHA384())
expiring = cert(host + "-partner.lab", server_key, ca_key, ca.subject,
                NOW - timedelta(days=350), NOW + timedelta(days=12))
expired = cert(host + "-old-partner.lab", server_key, ca_key, ca.subject,
               NOW - timedelta(days=400), NOW - timedelta(days=5))
import os
HERE = os.path.dirname(os.path.abspath(__file__))
weak_pem = open(os.path.join(HERE, host + "-legacy.pem"), "rb").read()
weak_key_pem = open(os.path.join(HERE, host + "-legacy-key.pem"), "rb").read()
orphan_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
orphan = cert(host + "-retired.lab", orphan_key, ca_key, ca.subject,
              NOW - timedelta(days=200), NOW + timedelta(days=100))

for path, data in [
        ("cert:///lab-ca.pem", pem_cert(ca)),
        ("cert:///lab-server.pem", pem_cert(server)),
        ("cert:///lab-server-key.pem", pem_key(server_key)),
        ("cert:///lab-ec.pem", pem_cert(ec_cert)),
        ("cert:///lab-ec-key.pem", pem_key(ec_key)),
        ("cert:///lab-partner.pem", pem_cert(expiring)),
        ("cert:///lab-old-partner.pem", pem_cert(expired)),
        ("cert:///lab-legacy.pem", weak_pem),
        ("cert:///lab-legacy-key.pem", weak_key_pem),
        ("cert:///lab-retired.pem", pem_cert(orphan))]:
    upload(path, data)

ON = "<mAdminState>enabled</mAdminState>"
MODERN = ('<Ciphers>AES_256_GCM_SHA384</Ciphers><Ciphers>CHACHA20_POLY1305_SHA256</Ciphers>'
       '<Ciphers>AES_128_GCM_SHA256</Ciphers><Ciphers>ECDHE_RSA_WITH_AES_256_GCM_SHA384</Ciphers>'
       '<Ciphers>ECDHE_ECDSA_WITH_AES_256_GCM_SHA384</Ciphers>')


def certificate(n, f, extra=""):
    return ('<CryptoCertificate name="{}">{}<Filename>cert:///{}</Filename>'
            '<IgnoreExpiration>on</IgnoreExpiration>{}</CryptoCertificate>'
            .format(n, ON, f, extra))


config('<PasswordAlias name="lab-key-alias">{}<Password>labsecret</Password>'
       '</PasswordAlias>'.format(ON), "password alias")
for n, f in [("lab-ca", "lab-ca.pem"), ("lab-server", "lab-server.pem"),
             ("lab-ec", "lab-ec.pem"), ("lab-partner", "lab-partner.pem"),
             ("lab-old-partner", "lab-old-partner.pem"),
             ("lab-legacy", "lab-legacy.pem")]:
    config(certificate(n, f), n)
config('<CryptoKey name="lab-server-key">{}<Filename>cert:///lab-server-key.pem'
       '</Filename></CryptoKey>'.format(ON), "server key")
config('<CryptoKey name="lab-ec-key">{}<Filename>cert:///lab-ec-key.pem'
       '</Filename></CryptoKey>'.format(ON), "ec key")
# A plaintext password on the legacy key
config('<CryptoKey name="lab-legacy-key">{}<Filename>cert:///lab-legacy-key.pem'
       '</Filename><Password>labsecret</Password><PasswordAlias>off'
       '</PasswordAlias></CryptoKey>'.format(ON), "legacy key")
config('<CryptoIdentCred name="lab-server-idcred">{}<Key class="CryptoKey">'
       'lab-server-key</Key><Certificate class="CryptoCertificate">lab-server'
       '</Certificate><CA class="CryptoCertificate">lab-ca</CA>'
       '</CryptoIdentCred>'.format(ON), "server idcred")
config('<CryptoIdentCred name="lab-ec-idcred">{}<Key class="CryptoKey">'
       'lab-ec-key</Key><Certificate class="CryptoCertificate">lab-ec'
       '</Certificate></CryptoIdentCred>'.format(ON), "ec idcred")
config('<CryptoIdentCred name="lab-legacy-idcred">{}<Key class="CryptoKey">'
       'lab-legacy-key</Key><Certificate class="CryptoCertificate">lab-legacy'
       '</Certificate></CryptoIdentCred>'.format(ON), "legacy idcred")
config('<CryptoValCred name="lab-partners">{}'
       '<Certificate class="CryptoCertificate">lab-ca</Certificate>'
       '<Certificate class="CryptoCertificate">lab-partner</Certificate>'
       '<Certificate class="CryptoCertificate">lab-old-partner</Certificate>'
       '</CryptoValCred>'.format(ON), "valcred")


def protocols(**on):
    keys = ("SSLv3", "TLSv1d0", "TLSv1d1", "TLSv1d2", "TLSv1d3")
    return "<Protocols>{}</Protocols>".format("".join(
        "<{0}>{1}</{0}>".format(k, "on" if on.get(k) else "off") for k in keys))


config('<SSLServerProfile name="lab-tls-modern">{}{}{}<Idcred class="CryptoIdentCred">'
       'lab-server-idcred</Idcred></SSLServerProfile>'.format(
           ON, protocols(TLSv1d2=1, TLSv1d3=1), MODERN), "modern server profile")
config('<SSLServerProfile name="lab-tls-ec">{}{}{}<Idcred class="CryptoIdentCred">'
       'lab-ec-idcred</Idcred></SSLServerProfile>'.format(
           ON, protocols(TLSv1d3=1), MODERN), "ec server profile")
config('<SSLServerProfile name="lab-tls-legacy">{}{}<Ciphers>RSA_WITH_AES_128_CBC_SHA'
       '</Ciphers><Ciphers>RSA_WITH_3DES_EDE_CBC_SHA</Ciphers>'
       '<Ciphers>ECDHE_RSA_WITH_AES_128_CBC_SHA</Ciphers>'
       '<Idcred class="CryptoIdentCred">lab-legacy-idcred</Idcred>'
       '</SSLServerProfile>'.format(
           ON, protocols(TLSv1d0=1, TLSv1d1=1, TLSv1d2=1)), "legacy server profile")
config('<SSLClientProfile name="lab-partner-client">{}{}{}<ValidateServerCert>on'
       '</ValidateServerCert><Valcred class="CryptoValCred">lab-partners</Valcred>'
       '</SSLClientProfile>'.format(ON, protocols(TLSv1d2=1, TLSv1d3=1), MODERN),
       "client profile")
config('<CryptoProfile name="lab-old-cp">{}<IdentCredential class="CryptoIdentCred">'
       'lab-server-idcred</IdentCredential></CryptoProfile>'.format(ON),
       "crypto profile")
config('<SSLProxyProfile name="lab-old-proxy">{}<Direction>reverse</Direction>'
       '<ReverseCryptoProfile class="CryptoProfile">lab-old-cp</ReverseCryptoProfile>'
       '</SSLProxyProfile>'.format(ON), "ssl proxy profile")


def handler(n, port, profile):
    return ('<HTTPSSourceProtocolHandler name="{}">{}<LocalAddress>0.0.0.0'
            '</LocalAddress><LocalPort>{}</LocalPort><SSLServerConfigType>server'
            '</SSLServerConfigType><SSLServer class="SSLServerProfile">{}'
            '</SSLServer></HTTPSSourceProtocolHandler>'.format(
                n, ON, port, profile))


config(handler("lab-https-modern", BASE, "lab-tls-modern"), "modern handler")
config(handler("lab-https-legacy", BASE + 1, "lab-tls-legacy"), "legacy handler")
soma('<dp:del-config><HTTPSSourceProtocolHandler name="lab-t"/></dp:del-config>')
ok, text = soma("<dp:do-action><SaveConfig/></dp:do-action>")
print("save", "ok" if ok else text[-400:])
# Left unsaved on purpose
config('<CryptoCertificate name="lab-partner">{}<Filename>cert:///lab-partner.pem'
       '</Filename><IgnoreExpiration>off</IgnoreExpiration></CryptoCertificate>'
       .format(ON), "unsaved change")

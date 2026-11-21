"""TLS probe for the PQC research. Run with the bundled Python 3.15.
usage: probe.py PORT"""
import socket
import ssl
import sys

PORT = int(sys.argv[1])
OFFERS = [
    ("X25519MLKEM768 only", "X25519MLKEM768", ssl.TLSVersion.TLSv1_3),
    ("SecP256r1MLKEM768 only", "SecP256r1MLKEM768", ssl.TLSVersion.TLSv1_3),
    ("SecP384r1MLKEM1024 only", "SecP384r1MLKEM1024", ssl.TLSVersion.TLSv1_3),
    ("MLKEM768 only", "MLKEM768", ssl.TLSVersion.TLSv1_3),
    ("X25519 only (TLS1.3)", "X25519", ssl.TLSVersion.TLSv1_3),
    ("P-256 only (TLS1.3)", "P-256", ssl.TLSVersion.TLSv1_3),
    ("hybrid first, then X25519", "X25519MLKEM768:X25519", ssl.TLSVersion.TLSv1_3),
    ("X25519MLKEM768 on TLS1.2", "X25519MLKEM768:X25519", ssl.TLSVersion.TLSv1_2),
]


def attempt(label, groups, version):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.minimum_version = version
    ctx.maximum_version = version
    try:
        ctx.set_groups(groups)
    except Exception as e:
        return "{:30} client cannot offer: {}".format(label, e)
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=10) as raw:
            with ctx.wrap_socket(raw) as s:
                return "{:30} OK  {} group={} cipher={}".format(
                    label, s.version(), s.group(), s.cipher()[0])
    except Exception as e:
        return "{:30} FAIL {}: {}".format(label, type(e).__name__, str(e)[:90])


print(ssl.OPENSSL_VERSION)
for offer in OFFERS:
    print(attempt(*offer))

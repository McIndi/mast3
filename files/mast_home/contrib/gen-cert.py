"""Make a self-signed certificate and its private key for the web GUI.

By default the files go where the default server.conf expects them:
etc/crypto/cert/mast.crt and etc/crypto/private/mast.key. Only the owner
can read the private key. An existing key is not replaced unless you
give --force.

The certificate names this computer (its host name and its fully
qualified name), localhost, 127.0.0.1 and ::1, and each --altName, so
that a browser accepts it for those names once you trust it.
"""
import datetime
import ipaddress
import os
import secrets
import socket
import sys

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from mast.cli import Cli
from mast.cli import Params, Option
from mast.logging import make_logger


class Main(Params):
    emailAddress: str = Option("", short="e", help="The e-mail address in the subject.")
    commonName: str = Option(
        "", short="c",
        help="The common name of the subject, at most 64 characters. The "
             "default is the fully qualified name of this computer, or its "
             "host name if that is too long.")
    countryName: str = Option("", short="C", help="The two-letter country code in the subject.")
    localityName: str = Option("", short="l", help="The city in the subject.")
    stateOrProvinceName: str = Option("", short="s", help="The state or province in the subject.")
    organizationName: str = Option("", short="o", help="The organization in the subject.")
    organizationUnitName: str = Option("", short="O", help="The organizational unit in the subject.")
    altName: list[str] = Option(
        default_factory=list, short="a",
        help="Another host name or IP address for the certificate. Give it "
             "once for each name.")
    serialNumber: int = Option(0, short="S", help="The serial number. 0 means a random one.")
    validityStartInSeconds: int = Option(
        0, short="v", help="Seconds from now until the certificate is valid.")
    validityEndInSeconds: int = Option(
        365*24*60*60, short="V", help="Seconds from now until the certificate expires.")
    KEY_FILE: str = Option("etc/crypto/private/mast.key", short="K",
                           help="Where to write the private key.")
    CERT_FILE: str = Option("etc/crypto/cert/mast.crt", help="Where to write the certificate.")
    force: bool = Option(False, short="f", help="Replace a key and certificate that are there.")


# The longest common name that a certificate can have (RFC 5280, ub-common-name)
MAX_COMMON_NAME = 64


def default_common_name(fqdn, hostname):
    """The first of the names of this computer that fits in a common
    name. Browsers only look at the subject alternative names, which
    have every name, also a longer one."""
    for name in (fqdn, hostname):
        if name and len(name) <= MAX_COMMON_NAME:
            return name
    return "localhost"


def _subject(params, common_name):
    """The subject, with the fields that have a value. Raises
    ValueError, which names the option, for a value that a certificate
    cannot have (too long, or a country that is not two letters)."""
    fields = (
        ("countryName", NameOID.COUNTRY_NAME, params.countryName),
        ("stateOrProvinceName", NameOID.STATE_OR_PROVINCE_NAME, params.stateOrProvinceName),
        ("localityName", NameOID.LOCALITY_NAME, params.localityName),
        ("organizationName", NameOID.ORGANIZATION_NAME, params.organizationName),
        ("organizationUnitName", NameOID.ORGANIZATIONAL_UNIT_NAME, params.organizationUnitName),
        ("commonName", NameOID.COMMON_NAME, common_name),
        ("emailAddress", NameOID.EMAIL_ADDRESS, params.emailAddress),
    )
    attributes = []
    for option, oid, value in fields:
        if not value:
            continue
        try:
            attributes.append(x509.NameAttribute(oid, value))
        except ValueError as e:
            raise ValueError("--{}: {}".format(option, e)) from None
    return x509.Name(attributes)


def _alt_names(names):
    """Return the subject alternative names: IP addresses as such, the
    others as DNS names, each once, in the order given."""
    seen, result = set(), []
    for name in names:
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        try:
            result.append(x509.IPAddress(ipaddress.ip_address(name)))
        except ValueError:
            result.append(x509.DNSName(name))
    return result


def _write(path, data, mode, force):
    """Write `data` to `path`, readable as `mode` allows (POSIX)."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_TRUNC if force else os.O_EXCL)
    fd = os.open(path, flags, mode)
    with os.fdopen(fd, "wb") as fout:
        fout.write(data)
    # A file that was there keeps its mode with O_TRUNC
    os.chmod(path, mode)


def main(params: Main):
    logger = make_logger("gen-cert")
    if not params.force:
        there = [p for p in (params.KEY_FILE, params.CERT_FILE) if os.path.exists(p)]
        if there:
            print("{} {} already there. Give --force to replace {}.".format(
                " and ".join(there), "are" if len(there) > 1 else "is",
                "them" if len(there) > 1 else "it"), file=sys.stderr)
            sys.exit(1)

    fqdn = socket.getfqdn()
    hostname = socket.gethostname()
    common_name = params.commonName or default_common_name(fqdn, hostname)
    names = [common_name, fqdn, hostname, "localhost", "127.0.0.1", "::1"] + list(params.altName)

    try:
        subject = _subject(params, common_name)
    except ValueError as e:
        # A field of the subject that is too long, or a country name that
        # is not two letters
        print("gen-cert.py: error: {}".format(e), file=sys.stderr)
        sys.exit(2)
    key = rsa.generate_private_key(public_exponent=65537, key_size=4096)
    now = datetime.datetime.now(datetime.timezone.utc)
    serial = params.serialNumber or x509.random_serial_number()
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(serial)
        .not_valid_before(now + datetime.timedelta(seconds=params.validityStartInSeconds))
        .not_valid_after(now + datetime.timedelta(seconds=params.validityEndInSeconds))
        .add_extension(x509.SubjectAlternativeName(_alt_names(names)), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]),
                       critical=False)
        .sign(key, hashes.SHA256())
    )

    _write(params.KEY_FILE, key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()), 0o600, params.force)
    _write(params.CERT_FILE, cert.public_bytes(serialization.Encoding.PEM), 0o644, params.force)

    alt = ", ".join(str(getattr(n, "value", n)) for n in _alt_names(names))
    logger.info("Wrote {} and {} for {}".format(params.CERT_FILE, params.KEY_FILE, alt))
    print("Wrote the certificate {} and its private key {}.".format(
        params.CERT_FILE, params.KEY_FILE))
    print("It is valid for: {}".format(alt))
    print("It expires on {:%Y-%m-%d}. Restart the web GUI to use it.".format(
        now + datetime.timedelta(seconds=params.validityEndInSeconds)))
    print("Browsers warn about a self-signed certificate until you trust it.")


if __name__ == '__main__':
    cli = Cli(main=main, description=__doc__)
    try:
        cli.run()
    except SystemExit:
        raise
    except Exception:
        make_logger("error").exception("An unhandled exception occurred")
        raise

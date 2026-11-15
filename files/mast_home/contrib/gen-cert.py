"""Script to generate a self signed cert and private key
"""
from OpenSSL import crypto, SSL

from mast.cli import Cli
from mast.cli import Params, Option
from mast.logging import make_logger

class Main(Params):
    emailAddress: str = Option("emailAddress", short="e")
    commonName: str = Option("commonName", short="c")
    countryName: str = Option("NT", short="C")
    localityName: str = Option("localityName", short="l")
    stateOrProvinceName: str = Option("stateOrProvinceName", short="s")
    organizationName: str = Option("organizationName", short="o")
    organizationUnitName: str = Option("organizationUnitName", short="O")
    serialNumber: int = Option(0, short="S")
    validityStartInSeconds: int = Option(0, short="v")
    validityEndInSeconds: str = Option(365*24*60*60, short="V")
    KEY_FILE: str = Option("etc/crypto/private/mast.key", short="K")
    CERT_FILE: str = Option("etc/crypto/cert/mast.crt")


def main(params: Main):
    emailAddress = params.emailAddress
    commonName = params.commonName
    countryName = params.countryName
    localityName = params.localityName
    stateOrProvinceName = params.stateOrProvinceName
    organizationName = params.organizationName
    organizationUnitName = params.organizationUnitName
    serialNumber = params.serialNumber
    validityStartInSeconds = params.validityStartInSeconds
    validityEndInSeconds = params.validityEndInSeconds
    KEY_FILE = params.KEY_FILE
    CERT_FILE = params.CERT_FILE
    #openssl x509 -inform pem -in selfsigned.crt -noout -text
    # create a key pair
    k = crypto.PKey()
    k.generate_key(crypto.TYPE_RSA, 4096)
    # create a self-signed cert
    cert = crypto.X509()
    cert.get_subject().C = countryName
    cert.get_subject().ST = stateOrProvinceName
    cert.get_subject().L = localityName
    cert.get_subject().O = organizationName
    cert.get_subject().OU = organizationUnitName
    cert.get_subject().CN = commonName
    cert.get_subject().emailAddress = emailAddress
    cert.set_serial_number(serialNumber)
    cert.gmtime_adj_notBefore(validityStartInSeconds)
    cert.gmtime_adj_notAfter(validityEndInSeconds)
    cert.set_issuer(cert.get_subject())
    cert.set_pubkey(k)
    cert.sign(k, 'sha512')
    with open(CERT_FILE, "wt") as f:
        f.write(crypto.dump_certificate(crypto.FILETYPE_PEM, cert).decode("utf-8"))
    with open(KEY_FILE, "wt") as f:
        f.write(crypto.dump_privatekey(crypto.FILETYPE_PEM, k).decode("utf-8"))

if __name__ == '__main__':
    cli = Cli(main=main, description=__doc__)
    try:
        cli.run()
    except SystemExit:
        pass
    except Exception:
        make_logger("error").exception("An unhandled exception occurred")
        raise
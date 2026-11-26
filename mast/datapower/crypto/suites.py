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
_module_: `mast.datapower.crypto.suites`

What a TLS cipher suite is made of, for the CBOM
(`mast.datapower.crypto.export`): its IANA name, its code in the IANA
registry, and the algorithms that its name gives (the key exchange, the
authentication, the cipher and the MAC or the hash).

A suite of TLS 1.3 (`TLS_AES_256_GCM_SHA384`) names only the cipher and
the hash: the key exchange group and the signature are negotiated apart
from it.
"""
import functools
import re

__all__ = ["iana_name", "identifiers", "algorithms", "CODES"]

# The codes of the IANA registry of TLS cipher suites, for the suites
# that DataPower and OpenSSL know
CODES = {
    "TLS_RSA_WITH_NULL_MD5": 0x0001,
    "TLS_RSA_WITH_NULL_SHA": 0x0002,
    "TLS_RSA_EXPORT_WITH_RC4_40_MD5": 0x0003,
    "TLS_RSA_WITH_RC4_128_MD5": 0x0004,
    "TLS_RSA_WITH_RC4_128_SHA": 0x0005,
    "TLS_RSA_EXPORT_WITH_RC2_CBC_40_MD5": 0x0006,
    "TLS_RSA_WITH_IDEA_CBC_SHA": 0x0007,
    "TLS_RSA_EXPORT_WITH_DES40_CBC_SHA": 0x0008,
    "TLS_RSA_WITH_DES_CBC_SHA": 0x0009,
    "TLS_RSA_WITH_3DES_EDE_CBC_SHA": 0x000A,
    "TLS_DHE_DSS_WITH_DES_CBC_SHA": 0x0012,
    "TLS_DHE_DSS_WITH_3DES_EDE_CBC_SHA": 0x0013,
    "TLS_DHE_RSA_WITH_DES_CBC_SHA": 0x0015,
    "TLS_DHE_RSA_WITH_3DES_EDE_CBC_SHA": 0x0016,
    "TLS_RSA_WITH_AES_128_CBC_SHA": 0x002F,
    "TLS_DHE_DSS_WITH_AES_128_CBC_SHA": 0x0032,
    "TLS_DHE_RSA_WITH_AES_128_CBC_SHA": 0x0033,
    "TLS_RSA_WITH_AES_256_CBC_SHA": 0x0035,
    "TLS_DHE_DSS_WITH_AES_256_CBC_SHA": 0x0038,
    "TLS_DHE_RSA_WITH_AES_256_CBC_SHA": 0x0039,
    "TLS_RSA_WITH_NULL_SHA256": 0x003B,
    "TLS_RSA_WITH_AES_128_CBC_SHA256": 0x003C,
    "TLS_RSA_WITH_AES_256_CBC_SHA256": 0x003D,
    "TLS_DHE_DSS_WITH_AES_128_CBC_SHA256": 0x0040,
    "TLS_RSA_WITH_CAMELLIA_128_CBC_SHA": 0x0041,
    "TLS_DHE_DSS_WITH_CAMELLIA_128_CBC_SHA": 0x0044,
    "TLS_DHE_RSA_WITH_CAMELLIA_128_CBC_SHA": 0x0045,
    "TLS_DHE_RSA_WITH_AES_128_CBC_SHA256": 0x0067,
    "TLS_DHE_DSS_WITH_AES_256_CBC_SHA256": 0x006A,
    "TLS_DHE_RSA_WITH_AES_256_CBC_SHA256": 0x006B,
    "TLS_RSA_WITH_CAMELLIA_256_CBC_SHA": 0x0084,
    "TLS_DHE_DSS_WITH_CAMELLIA_256_CBC_SHA": 0x0087,
    "TLS_DHE_RSA_WITH_CAMELLIA_256_CBC_SHA": 0x0088,
    "TLS_RSA_WITH_SEED_CBC_SHA": 0x0096,
    "TLS_RSA_WITH_AES_128_GCM_SHA256": 0x009C,
    "TLS_RSA_WITH_AES_256_GCM_SHA384": 0x009D,
    "TLS_DHE_RSA_WITH_AES_128_GCM_SHA256": 0x009E,
    "TLS_DHE_RSA_WITH_AES_256_GCM_SHA384": 0x009F,
    "TLS_DHE_DSS_WITH_AES_128_GCM_SHA256": 0x00A2,
    "TLS_DHE_DSS_WITH_AES_256_GCM_SHA384": 0x00A3,
    "TLS_AES_128_GCM_SHA256": 0x1301,
    "TLS_AES_256_GCM_SHA384": 0x1302,
    "TLS_CHACHA20_POLY1305_SHA256": 0x1303,
    "TLS_AES_128_CCM_SHA256": 0x1304,
    "TLS_AES_128_CCM_8_SHA256": 0x1305,
    "TLS_ECDHE_ECDSA_WITH_NULL_SHA": 0xC006,
    "TLS_ECDHE_ECDSA_WITH_RC4_128_SHA": 0xC007,
    "TLS_ECDHE_ECDSA_WITH_3DES_EDE_CBC_SHA": 0xC008,
    "TLS_ECDHE_ECDSA_WITH_AES_128_CBC_SHA": 0xC009,
    "TLS_ECDHE_ECDSA_WITH_AES_256_CBC_SHA": 0xC00A,
    "TLS_ECDHE_RSA_WITH_NULL_SHA": 0xC010,
    "TLS_ECDHE_RSA_WITH_RC4_128_SHA": 0xC011,
    "TLS_ECDHE_RSA_WITH_3DES_EDE_CBC_SHA": 0xC012,
    "TLS_ECDHE_RSA_WITH_AES_128_CBC_SHA": 0xC013,
    "TLS_ECDHE_RSA_WITH_AES_256_CBC_SHA": 0xC014,
    "TLS_ECDHE_ECDSA_WITH_AES_128_CBC_SHA256": 0xC023,
    "TLS_ECDHE_ECDSA_WITH_AES_256_CBC_SHA384": 0xC024,
    "TLS_ECDHE_RSA_WITH_AES_128_CBC_SHA256": 0xC027,
    "TLS_ECDHE_RSA_WITH_AES_256_CBC_SHA384": 0xC028,
    "TLS_ECDHE_ECDSA_WITH_AES_128_GCM_SHA256": 0xC02B,
    "TLS_ECDHE_ECDSA_WITH_AES_256_GCM_SHA384": 0xC02C,
    "TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256": 0xC02F,
    "TLS_ECDHE_RSA_WITH_AES_256_GCM_SHA384": 0xC030,
    "TLS_RSA_WITH_AES_128_CCM": 0xC09C,
    "TLS_RSA_WITH_AES_256_CCM": 0xC09D,
    "TLS_DHE_RSA_WITH_AES_128_CCM": 0xC09E,
    "TLS_DHE_RSA_WITH_AES_256_CCM": 0xC09F,
    "TLS_ECDHE_ECDSA_WITH_AES_128_CCM": 0xC0AC,
    "TLS_ECDHE_ECDSA_WITH_AES_256_CCM": 0xC0AD,
    "TLS_ECDHE_RSA_WITH_CHACHA20_POLY1305_SHA256": 0xCCA8,
    "TLS_ECDHE_ECDSA_WITH_CHACHA20_POLY1305_SHA256": 0xCCA9,
    "TLS_DHE_RSA_WITH_CHACHA20_POLY1305_SHA256": 0xCCAA,
}

# The key exchange (the first word before WITH)
_EXCHANGE = {"ECDHE": ("ECDHE", "key-agree"), "DHE": ("DHE", "key-agree"),
             "ECDH": ("ECDH", "key-agree"), "DH": ("DH", "key-agree"),
             # The key is sent encrypted with the server's RSA key
             "RSA": ("RSA", "pke")}
# The authentication (the words after the key exchange)
_SIGNATURE = {"RSA": "RSA-signature", "ECDSA": "ECDSA", "DSS": "DSA"}
_HASHES = {"SHA": "SHA-1", "SHA256": "SHA-256", "SHA384": "SHA-384",
           "MD5": "MD5"}
_HMACS = {"SHA": "HMAC-SHA1", "SHA256": "HMAC-SHA256",
          "SHA384": "HMAC-SHA384", "MD5": "HMAC-MD5"}
# The first word of each bulk cipher that a suite can name
_CIPHERS = ("AES", "CHACHA20", "3DES", "DES", "DES40", "RC4", "RC2", "IDEA",
            "CAMELLIA", "SEED", "ARIA", "NULL")
_MODES = {"GCM": "gcm", "CCM": "ccm", "CBC": "cbc"}


@functools.lru_cache(maxsize=1)
def _openssl_codes():
    """The IANA code of each cipher suite that this Python's OpenSSL
    knows, by its OpenSSL name (`ECDHE-RSA-AES256-GCM-SHA384`)."""
    import ssl
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.set_ciphers("ALL:@SECLEVEL=0")
        return {c["name"]: c["id"] & 0xFFFF for c in context.get_ciphers()}
    except (ssl.SSLError, ValueError):
        return {}


def iana_name(name):
    """
    _function_: `mast.datapower.crypto.suites.iana_name(name)`

    The IANA name of the cipher suite `name`: a name of DataPower
    (`ECDHE_RSA_WITH_AES_256_GCM_SHA384`, or `AES_256_GCM_SHA384` for TLS
    1.3), of OpenSSL (`ECDHE-RSA-AES256-GCM-SHA384`) or of IANA. `name`
    itself if it is none of these.
    """
    name = (name or "").strip()
    if not name:
        return name
    if name.startswith("SSL_"):
        name = "TLS_" + name[4:]
    if "-" in name:
        code = _openssl_codes().get(name)
        by_code = {c: n for n, c in CODES.items()}
        return by_code.get(code, name)
    return name if name.startswith("TLS_") else "TLS_" + name


def identifiers(name):
    """The two bytes of the IANA code of the suite `name` (an IANA
    name), as CycloneDX writes them (`["0xC0", "0x30"]`), or []."""
    code = CODES.get(name)
    if code is None:
        return []
    return ["0x{:02X}".format(code >> 8), "0x{:02X}".format(code & 0xFF)]


def _cipher(words):
    """The `(name, primitive, properties)` of the bulk cipher of
    `words` (`["AES", "256", "GCM"]`), or None for NULL."""
    if not words or words == ["NULL"]:
        return None
    if words[:2] == ["CHACHA20", "POLY1305"]:
        return "ChaCha20-Poly1305", "ae", {"parameterSetIdentifier": "256"}
    properties = {}
    bits = next((w for w in words[1:] if w.isdigit()), None)
    if bits and words[0] not in ("3DES", "DES", "DES40"):
        properties["parameterSetIdentifier"] = bits
    mode = next((_MODES[w] for w in words if w in _MODES), None)
    if mode:
        properties["mode"] = mode
    primitive = "ae" if mode in ("gcm", "ccm") else \
        "stream-cipher" if words[0] == "RC4" else "block-cipher"
    return "-".join(words), primitive, properties


def algorithms(name):
    """
    _function_: `mast.datapower.crypto.suites.algorithms(name)`

    The algorithms of the cipher suite `name` (an IANA name) as a list
    of `(name, primitive, properties)`: the key exchange, the signature,
    the cipher, then the MAC (HMAC, for a cipher that is not AEAD) or the
    hash (of the PRF or the HKDF). [] for a name that it cannot read.
    """
    if not (name or "").startswith("TLS_"):
        return []
    words = name[4:].split("_")
    found = []
    if "WITH" in words:
        at = words.index("WITH")
        parties, words = [w for w in words[:at] if w != "EXPORT"], \
            words[at + 1:]
        if not parties or parties[0] not in _EXCHANGE:
            return []
        exchange, primitive = _EXCHANGE[parties[0]]
        found.append((exchange, primitive, {}))
        for party in parties[1:]:
            if party in _SIGNATURE:
                found.append((_SIGNATURE[party], "signature", {}))
    if not words or words[0] not in _CIPHERS:
        return []
    digest = words[-1] if words[-1] in _HASHES else None
    cipher = _cipher(words[:-1] if digest else words)
    if cipher:
        found.append(cipher)
    if digest:
        if cipher and cipher[1] == "ae":
            found.append((_HASHES[digest], "hash", {}))
        else:
            found.append((_HMACS[digest], "mac", {}))
    return found
